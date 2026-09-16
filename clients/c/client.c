/* Card Nim client in C.  POSIX sockets only, no libraries:
 *
 *     cc -O2 client.c -o client
 *     ./client --server http://localhost:8000 --game K7PX --name "C bot" [--seat 1]
 *
 * CARDNIM_SERVER, CARDNIM_GAME, CARDNIM_NAME and CARDNIM_SEAT are used as
 * defaults, which is how the server starts this client for a seat.
 *
 * The state comes back as "key value" lines (?format=text), so there is no
 * JSON to parse.  Put your strategy in choose_card(); the rest is plumbing.
 */

#include <arpa/inet.h>
#include <ctype.h>
#include <netdb.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define MAX_CARDS 256
#define BUF 65536

/* ============================================================ your strategy */

/* Purpose: pick the card to play.
 * Inputs:  stones on the table, your cards, how many, the opponent's cards.
 * Output:  one card from your hand.  A card larger than stones loses at once. */
static int choose_card(int stones, const int *mine, int n_mine,
                       const int *theirs, int n_theirs) {
    int i, j, best = -1;
    for (i = 0; i < n_mine; i++)
        if (mine[i] == stones) return stones;            /* take the win */
    for (i = 0; i < n_mine; i++) {
        int opp_can_win = 0;
        if (mine[i] > stones) continue;                  /* would lose at once */
        for (j = 0; j < n_theirs; j++)
            if (theirs[j] == stones - mine[i]) opp_can_win = 1;
        if (!opp_can_win && (best < 0 || mine[i] < best)) best = mine[i];
    }
    if (best >= 0) return best;
    for (i = 0; i < n_mine; i++)                         /* nothing safe: smallest that fits */
        if (mine[i] <= stones && (best < 0 || mine[i] < best)) best = mine[i];
    return best >= 0 ? best : (n_mine ? mine[0] : 1);
}

/* Purpose: the smallest card that fits, played when the server refuses our
 * choice, so a bug in the strategy cannot burn the clock. */
static int safest_card(int stones, const int *mine, int n_mine) {
    int i, best = -1;
    for (i = 0; i < n_mine; i++)
        if (mine[i] <= stones && (best < 0 || mine[i] < best)) best = mine[i];
    return best >= 0 ? best : (n_mine ? mine[0] : 1);
}

/* ============================================================ plumbing */

static char host[256] = "localhost";
static int port = 8000;

/* Purpose: split "http://host:port" into host and port. */
static void parse_url(const char *url) {
    const char *p = url;
    char *colon;
    if (strncmp(p, "http://", 7) == 0) p += 7;
    snprintf(host, sizeof host, "%s", p);
    while (*host && host[strlen(host) - 1] == '/') host[strlen(host) - 1] = '\0';
    colon = strchr(host, ':');
    if (colon) { *colon = '\0'; port = atoi(colon + 1); }
}

/* Purpose: one HTTP request; writes the body into `out`.
 * Outputs: the HTTP status, or 0 if the server could not be reached. */
static int http(const char *method, const char *path, char *out, size_t out_len) {
    struct addrinfo hints, *res = NULL, *p;
    char portstr[16], req[2048], buf[BUF];
    int fd = -1, status = 0;
    size_t total = 0;
    char *split;

    out[0] = '\0';
    memset(&hints, 0, sizeof hints);
    hints.ai_family = AF_UNSPEC;
    hints.ai_socktype = SOCK_STREAM;
    snprintf(portstr, sizeof portstr, "%d", port);
    if (getaddrinfo(host, portstr, &hints, &res) != 0) return 0;
    for (p = res; p; p = p->ai_next) {
        fd = socket(p->ai_family, p->ai_socktype, p->ai_protocol);
        if (fd < 0) continue;
        if (connect(fd, p->ai_addr, p->ai_addrlen) == 0) break;
        close(fd);
        fd = -1;
    }
    freeaddrinfo(res);
    if (fd < 0) return 0;

    snprintf(req, sizeof req,
             "%s %s HTTP/1.1\r\nHost: %s\r\nConnection: close\r\nContent-Length: 0\r\n\r\n",
             method, path, host);
    if (write(fd, req, strlen(req)) < 0) { close(fd); return 0; }

    for (;;) {
        ssize_t n = read(fd, buf, sizeof buf);
        if (n <= 0) break;
        if (total + (size_t)n < out_len - 1) {
            memcpy(out + total, buf, (size_t)n);
            total += (size_t)n;
        }
    }
    close(fd);
    out[total] = '\0';
    if (total > 12) status = atoi(out + 9);          /* "HTTP/1.1 200 OK" */
    split = strstr(out, "\r\n\r\n");
    if (split) memmove(out, split + 4, strlen(split + 4) + 1);
    else out[0] = '\0';
    return status;
}

/* Purpose: the value of one "key value..." line, or "" if absent. */
static const char *field(const char *text, const char *key, char *out, size_t out_len) {
    const char *line = text;
    size_t klen = strlen(key);
    out[0] = '\0';
    while (line && *line) {
        const char *end = strchr(line, '\n');
        size_t len = end ? (size_t)(end - line) : strlen(line);
        if (len > klen && strncmp(line, key, klen) == 0 && line[klen] == ' ') {
            size_t vlen = len - klen - 1;
            while (vlen && (line[klen + 1 + vlen - 1] == '\r')) vlen--;
            if (vlen >= out_len) vlen = out_len - 1;
            memcpy(out, line + klen + 1, vlen);
            out[vlen] = '\0';
            return out;
        }
        line = end ? end + 1 : NULL;
    }
    return out;
}

/* Purpose: "1 2 3" -> array of ints.  Outputs: how many were read. */
static int ints(const char *s, int *out, int max) {
    int n = 0;
    while (*s && n < max) {
        while (*s == ' ') s++;
        if (!*s) break;
        out[n++] = atoi(s);
        while (*s && *s != ' ') s++;
    }
    return n;
}

/* Purpose: percent-encode a name for a query parameter. */
static void urlencode(const char *s, char *out, size_t out_len) {
    size_t o = 0;
    for (; *s && o + 4 < out_len; s++) {
        unsigned char c = (unsigned char)*s;
        if (isalnum(c) || c == '-' || c == '_' || c == '.' || c == '~') out[o++] = (char)c;
        else o += (size_t)snprintf(out + o, out_len - o, "%%%02X", c);
    }
    out[o] = '\0';
}

static const char *opt(int argc, char **argv, const char *flag, const char *env, const char *dflt) {
    int i;
    const char *v;
    for (i = 1; i + 1 < argc; i++)
        if (strcmp(argv[i], flag) == 0) return argv[i + 1];
    v = getenv(env);
    return (v && *v) ? v : dflt;
}

/* ============================================================ game loop */

int main(int argc, char **argv) {
    char body[BUF], val[1024], path[2048], token[512], encname[512];
    int mine[MAX_CARDS], theirs[MAX_CARDS], n_mine, n_theirs;
    int status, my_seat, stones, card, failures = 0;
    const char *server = opt(argc, argv, "--server", "CARDNIM_SERVER", "http://localhost:8000");
    const char *game = opt(argc, argv, "--game", "CARDNIM_GAME", "");
    const char *name = opt(argc, argv, "--name", "CARDNIM_NAME", "C bot");
    const char *seat = opt(argc, argv, "--seat", "CARDNIM_SEAT", "");

    if (!*game) {
        fprintf(stderr, "usage: client --game ID [--server http://host:8000] [--name NAME] [--seat 1|2]\n");
        return 1;
    }
    parse_url(server);
    urlencode(name, encname, sizeof encname);

    snprintf(path, sizeof path, "/api/games/%s/join?format=text&name=%s%s%s",
             game, encname, *seat ? "&seat=" : "", seat);
    status = http("POST", path, body, sizeof body);
    field(body, "token", token, sizeof token);
    if (status != 200 || !*token) {
        fprintf(stderr, "[%s] join failed (%d): %s\n", name, status, body);
        return 1;
    }
    field(body, "seat", val, sizeof val);
    my_seat = atoi(val);
    printf("[%s] joined %s as seat %d\n", name, game, my_seat);
    fflush(stdout);

    for (;;) {
        /* getstate answers only when it is our turn or the game is over, so
         * this loop does not poll and waiting costs nothing on the clock. */
        snprintf(path, sizeof path, "/api/games/%s/getstate?format=text&timeout=60&token=%s", game, token);
        status = http("GET", path, body, sizeof body);
        if (status == 0) {
            if (++failures >= 30) { fprintf(stderr, "[%s] server gone\n", name); return 1; }
            sleep(1);
            continue;
        }
        failures = 0;
        if (status != 200) { fprintf(stderr, "[%s] getstate failed (%d)\n", name, status); return 1; }

        field(body, "status", val, sizeof val);
        if (strcmp(val, "finished") == 0) {
            int winner;
            field(body, "winner", val, sizeof val);
            winner = atoi(val);
            field(body, "reason", val, sizeof val);
            printf("[%s] game over: %s. %s\n", name,
                   winner == my_seat ? "WIN" : winner ? "LOSS" : "no winner", val);
            return winner == my_seat ? 0 : 2;
        }
        field(body, "your_turn", val, sizeof val);
        if (strcmp(val, "1") != 0) continue;

        field(body, "stones", val, sizeof val);
        stones = atoi(val);
        field(body, "your_cards", val, sizeof val);
        n_mine = ints(val, mine, MAX_CARDS);
        field(body, "opp_cards", val, sizeof val);
        n_theirs = ints(val, theirs, MAX_CARDS);

        card = choose_card(stones, mine, n_mine, theirs, n_theirs);
        printf("[%s] %d stones, playing %d\n", name, stones, card);
        fflush(stdout);

        snprintf(path, sizeof path, "/api/games/%s/move?format=text&token=%s&card=%d", game, token, card);
        status = http("POST", path, body, sizeof body);
        if (status != 200) {
            int back = safest_card(stones, mine, n_mine);
            fprintf(stderr, "[%s] move %d rejected (%d)\n", name, card, status);
            if (back != card) {
                snprintf(path, sizeof path, "/api/games/%s/move?format=text&token=%s&card=%d", game, token, back);
                http("POST", path, body, sizeof body);
            } else {
                sleep(1);      /* not our turn any more: do not hammer it */
            }
        }
    }
}
