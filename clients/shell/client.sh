#!/bin/sh
# Card Nim client in POSIX shell.  Needs only curl and awk, which is the point:
# if a language can run a shell command it can play Card Nim.
#
#     sh client.sh --server http://localhost:8000 --game K7PX --name "Shell bot" [--seat 1]
#
# CARDNIM_SERVER, CARDNIM_GAME, CARDNIM_NAME and CARDNIM_SEAT are used as
# defaults, which is how the server starts this client for a seat.
#
# The state comes back as "key value" lines (?format=text), so awk can read it:
#
#     status playing | waiting | finished
#     you 2                    your seat
#     your_turn 1              1 or 0
#     stones 37
#     your_cards 1 2 4 5
#     opp_cards 1 3 6
#     winner 0                 0, 1 or 2
#     reason ...
#
# Put your strategy in choose_card(). Everything else is plumbing.

set -u

# ============================================================ your strategy

# Purpose: pick the card to play.
# Inputs:  $1 stones on the table, $2 your cards, $3 the opponent's cards
#          (space-separated, sorted).  Output: one card on stdout.
# A card larger than stones loses at once.
choose_card() {
    awk -v stones="$1" -v mine="$2" -v theirs="$3" 'BEGIN {
        n = split(mine, my, " "); split(theirs, opp, " ");
        for (i = 1; i <= n; i++) if (my[i] == stones) { print stones; exit }   # take the win
        best = -1; fallback = -1;
        for (i = 1; i <= n; i++) {
            if (my[i] > stones) continue;
            if (fallback < 0 || my[i] < fallback) fallback = my[i];
            leaves = stones - my[i];
            unsafe = 0;
            for (j in opp) if (opp[j] == leaves) unsafe = 1;    # do not leave an exact match
            if (!unsafe && (best < 0 || my[i] < best)) best = my[i];
        }
        if (best >= 0) { print best; exit }
        if (fallback >= 0) { print fallback; exit }
        print (n ? my[1] : 1);                                  # nothing fits: any card loses
    }'
}

# Purpose: the smallest card that fits, played when the server refuses our
# choice, so a bug in the strategy cannot burn the clock.
safest_card() {
    awk -v stones="$1" -v mine="$2" 'BEGIN {
        n = split(mine, my, " "); best = -1;
        for (i = 1; i <= n; i++) if (my[i] <= stones && (best < 0 || my[i] < best)) best = my[i];
        print (best >= 0 ? best : (n ? my[1] : 1));
    }'
}

# ============================================================ plumbing

# Purpose: read one key out of the saved state file.  Inputs: the key.
field() { awk -v k="$1" '$1 == k { $1 = ""; sub(/^ /, ""); print; exit }' "$STATE"; }

# Purpose: percent-encode a string for a query parameter.
urlencode() {
    awk -v s="$1" 'BEGIN {
        for (i = 0; i < 256; i++) ord[sprintf("%c", i)] = i;
        n = length(s);
        for (i = 1; i <= n; i++) {
            c = substr(s, i, 1);
            if (c ~ /[A-Za-z0-9._~-]/) printf "%s", c; else printf "%%%02X", ord[c];
        }
    }'
}

# Purpose: one request; writes the body to $STATE and prints the HTTP status.
req() { curl -s -o "$STATE" -w '%{http_code}' -X "$1" --max-time 90 "$2" 2>/dev/null || echo 000; }

# ============================================================ the command line

SERVER="${CARDNIM_SERVER:-http://localhost:8000}"
GAME="${CARDNIM_GAME:-}"
NAME="${CARDNIM_NAME:-Shell bot}"
SEAT="${CARDNIM_SEAT:-}"
while [ $# -gt 1 ]; do
    case "$1" in
        --server) SERVER="$2" ;;
        --game)   GAME="$2" ;;
        --name)   NAME="$2" ;;
        --seat)   SEAT="$2" ;;
    esac
    shift 2
done
SERVER=$(printf '%s' "$SERVER" | sed 's:/*$::')

if [ -z "$GAME" ]; then
    echo "usage: sh client.sh --game ID [--server http://host:8000] [--name NAME] [--seat 1|2]" >&2
    exit 1
fi

STATE=$(mktemp "${TMPDIR:-/tmp}/cardnim.XXXXXX")
trap 'rm -f "$STATE"' EXIT INT TERM
BASE="$SERVER/api/games/$GAME"

# ============================================================ join

QUERY="?format=text&name=$(urlencode "$NAME")"
[ -n "$SEAT" ] && QUERY="$QUERY&seat=$SEAT"
STATUS=$(req POST "$BASE/join$QUERY")
TOKEN=$(field token)
if [ "$STATUS" != "200" ] || [ -z "$TOKEN" ]; then
    echo "[$NAME] join failed ($STATUS): $(cat "$STATE")" >&2
    exit 1
fi
MYSEAT=$(field seat)
echo "[$NAME] joined $GAME as seat $MYSEAT"

# ============================================================ game loop

FAILURES=0
while :; do
    # getstate answers only when it is our turn or the game is over, so this
    # loop does not poll and waiting costs nothing on the clock.
    STATUS=$(req GET "$BASE/getstate?format=text&timeout=60&token=$TOKEN")
    if [ "$STATUS" = "000" ] || [ "$STATUS" = "0" ]; then
        FAILURES=$((FAILURES + 1))
        [ "$FAILURES" -ge 30 ] && { echo "[$NAME] server gone, giving up" >&2; exit 1; }
        sleep 1
        continue
    fi
    FAILURES=0
    [ "$STATUS" = "200" ] || { echo "[$NAME] getstate failed ($STATUS)" >&2; exit 1; }

    if [ "$(field status)" = "finished" ]; then
        WINNER=$(field winner)
        if [ "$WINNER" = "$MYSEAT" ]; then RESULT=WIN; CODE=0
        elif [ "$WINNER" = "0" ]; then RESULT="no winner"; CODE=2
        else RESULT=LOSS; CODE=2
        fi
        echo "[$NAME] game over: $RESULT. $(field reason)"
        exit $CODE
    fi
    [ "$(field your_turn)" = "1" ] || continue

    STONES=$(field stones)
    MINE=$(field your_cards)
    CARD=$(choose_card "$STONES" "$MINE" "$(field opp_cards)")
    echo "[$NAME] $STONES stones, playing $CARD"

    STATUS=$(req POST "$BASE/move?format=text&token=$TOKEN&card=$CARD")
    if [ "$STATUS" != "200" ]; then
        echo "[$NAME] move $CARD rejected ($STATUS)" >&2
        BACK=$(safest_card "$STONES" "$MINE")
        if [ "$BACK" != "$CARD" ]; then
            echo "[$NAME] playing $BACK instead" >&2
            req POST "$BASE/move?format=text&token=$TOKEN&card=$BACK" > /dev/null
        else
            sleep 1   # not our turn any more or a hiccup: do not hammer it
        fi
    fi
done
