# Card Nim HTTP API

Everything a bot or a page needs is plain HTTP. The same API drives the browser
UI, so anything the UI can do, a program can do.

Base URL: `http://<host>:<port>` (the server prints it at startup).
All ids are 4 characters like `K7PX` and are case-insensitive.

Responses are JSON unless you add `?format=text`, which gives the
[plain-text format](#plain-text-format) for languages without a JSON parser.
Errors are JSON `{"error": "<message>"}` with a 4xx status, always.

| Status | Meaning |
|---|---|
| 200 / 201 | fine |
| 400 | malformed input (missing or non-integer parameter, out of range) |
| 401 | no seat token, or an unknown one: join first |
| 404 | no such game / endpoint |
| 405 | wrong method for this endpoint |
| 409 | rule violation: seat taken, not your turn, card not in hand, game over |

## Identifying yourself

Joining returns a **token**. Send it with every later call in any one of these
ways (the first one found wins):

1. header `X-Token: <token>`
2. parameter `token=<token>` in the query string or the JSON body
3. cookie `cardnim_<GAMEID>=<token>` (accepted if you set it; the server never sets cookies)

Observers need no token.

## Endpoints

### `GET /api/health`

```json
{"ok": true, "games": 3, "tournaments": 1, "time": 1757470000.1,
 "lobby_url": "http://10.18.4.22:8000/", "local": true, "organiser": true,
 "uploads": false}
```

`lobby_url` is the address other devices on the network should use; the
lobby shows it as a QR code. Override it with `--public-url` when the server
sits behind a proxy.

`local` is true when the request came from the machine the server runs on —
loopback, or one of that machine's own addresses, so it holds whether the
organiser opened `localhost` or the LAN address. The pages use it to keep the
organiser's furniture off a visitor's screen: the lobby's create-a-table pane
and the bracket's join QR. `?host=1` or `?host=0` in the address overrides it.

`organiser` is true when this caller may **run the event**: draw a bracket,
start a match, call a no-show, abort, take someone else's entry out. That is
the machine the server runs on, plus any `--controls-from` address, or
everyone when the server was started with `--open-controls`. The bracket page
asks before drawing its buttons; the endpoints below enforce it, so hiding
them is a courtesy, not the lock. Unlike `local`, `?host=1` does not override
it.

`uploads` says whether the server was started with `--accept-uploads`.

### `GET /api/games`

Lobby list, newest first.

```json
{"games": [{"id": "K7PX", "label": "Round 1", "status": "playing",
            "stones": 37, "initial_stones": 100, "cards": 25, "time_limit": 120,
            "players": ["Alice", "Bob"], "avatars": [12, 3], "turn": 2, "winner": null,
            "moves": 6, "created_at": 1757470000.1, "finished_at": null, "version": 9}]}
```

### `POST /api/games`

Create a game. Body (JSON or form): `stones` (1–500), `cards` (1–200),
`time_limit` seconds per player (optional, default 120), `label` (optional).
Returns **201** and the full [state](#state-object).

```sh
curl -X POST localhost:8000/api/games -H 'Content-Type: application/json' \
     -d '{"stones": 100, "cards": 25, "time_limit": 120, "label": "Round 1"}'
```

### `POST /api/games/{id}/join`

Body: `name` (shown on the board), optional `seat` (1 or 2; default: first free),
optional `avatar` (1..16, the picture shown for you; see `/avatars/av01.png`
to `/avatars/av16.png`; default: picked from your name, never the same as
the opponent's). `GET` with query parameters also works. Returns:

```json
{"seat": 1, "token": "Qm9i...", "state": { ... }}
```

The game starts the moment the second seat is taken; seat 1 moves first and
their clock starts right then. Keep the token: nothing else identifies you.
409 if the seat is taken or the game is over.

### `GET /api/games/{id}/getstate`   (token required)

The course's `getstate`: **returns only when it is your turn or the game is
over.** Waiting does not use your clock. The server holds the request up to
`timeout` seconds (default and maximum 60); if nothing happened by then it
answers anyway with `your_turn: false` and you simply call again. The sample
clients hide this loop from you.

### `POST /api/games/{id}/move`   (token required)

Body: `card`. `GET /api/games/{id}/move?card=7&token=...` also works, for
testing from a browser address bar or `curl`.

The server checks that the game is in progress, that it is your turn, and that
the card is in your hand. If any check fails you get **409** and nothing
changes (your clock keeps running). Otherwise the move is applied and the new
state returned:

* card == stones left: you win;
* card > stones left: you lose at once (the pile is left untouched, the move is
  logged with `overdraw: true`);
* otherwise the turn passes. If the opponent now has no card small enough for
  the pile, they lose automatically and the game ends.

### `GET /api/games/{id}`  (alias `/api/games/{id}/state`)

The state for observers and pages. Add `since=<version>` to **long-poll**: the
response is delayed until the game's version exceeds `since`, or `timeout`
seconds pass (default 25, max 60). A token, if sent, fills in `you`/`your_turn`.

### `GET /api/bots`

```json
{"bots": [{"kind": "greedy", "label": "Greedy bot"},
          {"kind": "random", "label": "Random bot"},
          {"kind": "client-q", "label": "q/kdb+ client (sample strategy)", "language": "q"}]}
```

Bots the server can play itself. Two simple ones ship with it and the
architects may add their own in a private file that is not distributed;
those run inside the server and have no `language`.

Every client under `clients/` that this machine can actually build and run is
listed too, whatever it is written in — `client-python`, `client-cpp`,
`client-java`, `client-q`, `client-javascript`, `client-ruby`,
`client-shell`, and anything else someone has added. Seating one starts the
program as a child process that joins over HTTP, exactly as a team would run
it. A client whose toolchain is missing is left out of this list; it still
appears in `/api/strategies` with the reason.

The list is rescanned every ten seconds, so a language added while the server
is running shows up without a restart.

### `GET /api/strategies`

Every client folder and every bot the server can seat, with the file each
lives in. The lobby shows this list so teams know where to start.

```json
{"clients": [{"kind": "client-ruby", "language": "Ruby", "file": "clients/ruby/client.rb",
              "folder": "clients/ruby/", "edit": "choose_card(stones, my_cards, opp_cards)",
              "note": "wins if it can, ...", "run": "ruby clients/ruby/client.rb --game ID --name NAME",
              "available": true, "reason": "", "badge": "Rb", "color": "#cc342d",
              "tools": ["ruby"], "files": 1,
              "strategies": [{"name": "Sample strategy", "file": "...", "note": "..."}]}],
 "bots": [{"kind": "greedy", "label": "Greedy bot", "file": "server/bots.py", "private": false}],
 "problems": []}
```

`file` is the entry point and `files` how many files the client is made of (an
uploaded submission is often several). `available` is false with a `reason`
("needs rustc") when the machine has not got the toolchain: a team should see
their language greyed out, not missing.
`problems` lists manifests that could not be read, so a typo in someone's
`client.json` is visible instead of silent.

Each entry comes from a `client.json` in that folder — adding a language is
adding a folder, not changing the server. See
[CLIENTS.md](CLIENTS.md) for the manifest.

### `POST /api/games/{id}/bot`

Body: `kind` (from `/api/bots`), optional `seat`, `avatar`, `name`, and
`delay` (seconds the bot pauses before each move so people can follow;
default 0.8). The server seats the bot and plays for it until the game ends.
The bot's token stays inside the server. Returns the state; 409 if the seat
is taken. `players[i].bot` is true for such a seat, and the lobby summary
carries `bots: [bool, bool]`.

### `POST /api/games/{id}/leave`   (token required)

Before the game starts: the seat becomes open again and the token stops
working. During play: you resign and the opponent wins. 409 once the game is
over.

### `POST /api/games/{id}/abort`

Ends a game with no winner (`reason` optional). Meant for the architects when a
bot is stuck or the parameters were wrong. No authentication: this is a
classroom tool.

## Tournaments

A knockout: everyone enters by name, the entrants are drawn into pairs, each
match is one game with the tournament's settings, and the winner moves on
until the final. Entrants without an opponent in round one get a bye. Who
moves first in a match is a coin toss. A game that ends with no winner
(aborted) is played again.

Every match's game is an ordinary game (all the calls above work on it) whose
two seats are **reserved**: the state shows the entrants' names with
`reserved: true` and `occupied: false`, and the game starts once both have
sat down. A reserved seat opens for a `join` that carries the entrant's
token (as `X-Token` or `token`) or gives the reserved name. Nobody else can
take it. Server-run bots are seated the moment the game is created.

### `GET /api/tournaments`

`{"tournaments": [{"id": "Q4RT", "label": "Cup", "status": "open", "stones": 100, "cards": 25,
"time_limit": 120, "entrants": 5, "rounds": 0, "champion": null, ...}]}`, newest first.
`status` is `open` (taking entries), `running` or `finished`.

### `POST /api/tournaments`

Body: `stones`, `cards`, `time_limit` (optional, default 120), `label`
(optional), `bot_delay` (optional, seconds between the
moves of a match, default 1.5; 0 plays a match in a blink). Returns **201**
and the [bracket](#bracket-object).

### `POST /api/tournaments/{id}/join`

Body: `name`, optional `avatar` (1..16), optional `kind`:

* `human` (default): plays from the browser;
* `api`: a program that will sit down in each of its games over HTTP;
* a bot kind from `GET /api/bots`: the server plays it (no name needed).

Returns `{"entrant": {"id": 3, "name": "Team A", ...}, "token": "...", "tournament": {bracket}}`.
**Keep the token**: it identifies you in `getstate` and opens your reserved
seat in every match. Names must be unique (a repeated bot name gets a number).
409 once the bracket has started or the name is taken.

### `POST /api/tournaments/{id}/leave`

Withdraw before the start: the entrant's token, or `entrant` (its id) for a
server bot or an uploaded strategy — those have no token to prove ownership,
so that form is **403 unless the caller is the organiser**. Your own entry
goes from anywhere with your token. 409 once the bracket has started.

### `POST /api/tournaments/{id}/start`

Draws the bracket. **No game is created**: each match is started by hand with
`POST /play` below, so the organiser decides when each one begins. Byes are
resolved at once, since there is nothing to play. 409 with fewer than two
entrants. **403 unless the caller is the organiser** (see `organiser` in
`/api/health`).

### `GET /api/tournaments/{id}`

The bracket. Add `since=<version>` to **long-poll**: the response waits until
the tournament's version exceeds `since` (a move in any of its games counts)
or `timeout` seconds pass (default 25, max 60). With a token, `you` says what
the caller should do next (same shape as `getstate` below).

### `GET /api/tournaments/{id}/getstate`   (token required)

For programs. **Returns only when there is something to do**:

```json
{"status": "play", "round": 2, "round_name": "Semifinals", "match": 0,
 "game": "K7PX", "seat": 1, "opponent": "Team B", "claimed": false,
 "entrant": {...}, "tournament": {...}}
```

* `play`: sit down in game `game` at seat `seat` (`POST /api/games/K7PX/join`
  with your tournament token as `X-Token`; the reply's token is the same
  one), then play it with `getstate` and `move` as usual. Call the
  tournament's `getstate` again when the game is over.
* `eliminated` (with `lost_to` and `round_name`), `champion`, `finished`
  (the tournament was aborted): you are done.

The server holds the request up to `timeout` seconds (default and maximum
60); if nothing happened it answers with status `open` or `waiting` and you
simply call again. The Python sample client does all of this with
`--tournament ID`.

`bot_delay` paces every match, not just the server's own bots: after each
move the game is paused for that long, so a client -- which answers as fast as
the socket allows -- plays at a speed a room can follow. The pause stops both
clocks, so it costs neither player any time, and `getstate` holds its caller
in the long poll rather than waking it into a move that would be refused.

### `POST /api/tournaments/{id}/play`

Body: `round` (1-based), `match` (0-based index in that round). Starts that
match: creates its game with the tournament's settings, tosses a coin for who
moves first, reserves both seats and sits any server-run entrants down.
Returns the [bracket](#bracket-object).

Matches never start themselves, and only one runs at a time. **409** when the
match is a bye, is already decided, is still waiting for the previous round,
when another match of this tournament is in progress, or when the tournament
is not running. **403 unless the caller is the organiser**: a match begins
when the room is ready, not when the quickest team on the page presses the
button.

### `POST /api/games/{id}/pause`  ·  `POST /api/games/{id}/resume`

Stop and restart play, so a room can talk over a position. Pausing charges the
player on turn for the time they have already spent, then stops the clock;
resuming starts it again from that moment, so the pause costs neither player
anything. While paused a move gets **409** and the clock cannot run out. Both
return the [state](#state-object) and need no authentication.

`paused` appears in the state and in the game summary.

### `POST /api/tournaments/{id}/walkover`

Body: `round` (1-based), `match` (0-based index in that round), `winner`
(entrant id). Hands an undecided match to one side; a game in progress is
aborted. For no-shows. **403 unless the caller is the organiser.**

### `POST /api/tournaments/{id}/abort`

Ends the tournament with no champion; every unfinished game is aborted.
**403 unless the caller is the organiser.**

### Bracket object

```json
{
  "id": "Q4RT", "label": "Cup", "status": "running", "aborted": false, "reason": "",
  "stones": 100, "cards": 25, "time_limit": 120.0, "bot_delay": 1.5,
  "size": 8, "entrants": [{"id": 1, "name": "Team A", "avatar": 5, "kind": "api",
                           "bot": false, "wins": 1, "eliminated_in": null}, ...],
  "rounds": [
    {"number": 1, "name": "Quarterfinals", "matches": [
      {"round": 1, "index": 0, "slots": [1, 6], "bye": false, "seats": [6, 1],
       "game": "K7PX", "games": ["K7PX"], "winner": 1, "walkover": false,
       "status": "done", "game_state": {game summary as in GET /api/games}}, ...]},
    {"number": 2, "name": "Semifinals", "matches": [...]},
    {"number": 3, "name": "Final", "matches": [...]}
  ],
  "champion": null, "champion_name": null,
  "created_at": 1757470000.1, "started_at": 1757470100.5, "finished_at": null, "version": 41,
  "you": {entrant view, only with a token}
}
```

`game_state` is the summary from `GET /api/games`, which carries
`cards_left` and `time_remaining` per seat as well as the pile, so a bracket
can draw each match as a small table without fetching every game.

`slots` are the two entrants (null until the feeding match is decided; a
bye has one). `seats` lists the entrant at seat 1 and at seat 2 once the
game exists. A match's `status` is `pending`, `bye`, `ready` (game being
created), `waiting` (for the players to sit down), `playing` or `done`.
`games` keeps every game created for the match, oldest first, when one was
aborted and replayed.

## State object

```json
{
  "id": "K7PX", "label": "Round 1", "status": "playing", "version": 9,
  "initial_stones": 100, "stones": 37, "num_cards": 25, "time_limit": 120.0,
  "turn": 2,
  "players": [
    {"seat": 1, "name": "Alice", "occupied": true, "avatar": 12,
     "cards": [1, 2, 4, 5, 6, 8, 9, ...], "playable": [1, 2, 4, ...],
     "time_remaining": 101.532},
    {"seat": 2, "name": "Bob", "occupied": true, "cards": [...], "playable": [...],
     "time_remaining": 97.004}
  ],
  "moves": [
    {"number": 1, "seat": 1, "card": 7, "stones_before": 100, "stones_after": 93,
     "elapsed": 0.412, "overdraw": false, "at": 1757470012.3}
  ],
  "last_move": {"seat": 1, "card": 10},
  "winner": null, "reason": "",
  "created_at": 1757470000.1, "finished_at": null,
  "you": 2, "your_turn": true
}
```

* `status`: `waiting` (fewer than two players), `playing`, `finished`.
* `turn`: seat to move, `null` unless playing.
* `players[i].reserved`: true when a tournament keeps the seat for that
  entrant (the name is shown before they sit down).
* `tournament`: `null`, or `{"id", "label", "round", "round_name", "match",
  "rounds"}` for a game that belongs to a bracket.
* `players[i].avatar`: 1..16, the picture for that player (0 while the seat is open).
* `players[i].cards`: cards still in that hand (hands are public in Card Nim).
  `playable`: the subset not larger than the pile (empty unless playing).
* `players[i].time_remaining`: seconds left, live (the player on turn is being
  charged as you read it).
* `moves[i].elapsed`: seconds that player spent on that move, server-measured.
* `winner`: 1, 2, or `null` (game not over, or aborted). `reason` explains it
  in words, e.g. `"Bob played 12 with only 5 stones left"`.
* `you` / `your_turn`: only meaningful when you sent a token.

## Plain-text format

Add `?format=text` to any endpoint that returns a state. One `key value...`
pair per line, always in this order; lists are space-separated.

```
status playing
you 2
turn 2
your_turn 1
stones 37
initial_stones 100
num_cards 25
your_cards 1 2 3 4 5 6 7 8 9 10 11 13 14 15 16 17 18 19 20 21 22 23 24
opp_cards 1 2 4 5 6 8 9 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25
your_time 97.004
opp_time 101.532
last_move 1 10
winner 0
reason -
version 9
```

`join?format=text` prefixes two extra lines, `seat N` and `token T`.
Errors in text mode are still JSON (`{"error": ...}`) with a 4xx status; the
sample clients look at the status code first.

## Uploaded strategies

A team on another device can send their strategy to the server, which writes it
into the uploads folder with a generated manifest and from then on treats it as
an ordinary client: it appears in `/api/bots`, can be seated in a game and can
be entered in a tournament.

A submission is a **folder, not a file**: one file, several files, a folder
with sub-folders, or a `.zip` of one. What the server has to know is which file
starts the bot; everything beside it is the team's own business.

**The server executes what is uploaded.** Both endpoints are refused unless it
was started with `--accept-uploads`.

### `GET /api/uploads`

What this server accepts, so a page knows whether to offer the option at all.

```json
{"enabled": true, "accept": ".cc,.cpp,.java,.jl,.js,.py,.q,.rb,.sh,.zip",
 "languages": [{"extension": ".py", "language": "Python"}, ...],
 "max_bytes": 1048576, "max_total_bytes": 8388608, "max_files": 200,
 "multifile": true, "entry_names": ["main", "client", "strategy", ...]}
```

`enabled` is false on a server started without `--accept-uploads`; everything
else is still reported so the page can explain what would be accepted.
`max_bytes` is the limit for one file, `max_total_bytes` for a whole
submission, and `entry_names` are the stems that mark the entry point.

### `POST /api/uploads?team=Team+A`

Three shapes, all of them a POST to the same endpoint:

| Body | Query | What it is |
|---|---|---|
| raw file bytes | `?filename=strategy.py` | one file, as before |
| raw zip bytes | `?filename=bot.zip` | an archive, unpacked here |
| `multipart/form-data` | — | several files, each part's `filename` being its path inside the submission |

So a browser sends one file with `fetch(url, {method: "POST", body: file})`
and a folder with a `FormData` whose parts are named by `webkitRelativePath`;
a terminal sends `curl --data-binary @bot.zip ".../api/uploads?filename=bot.zip&team=Team+A"`.

`?entry=main.py` (or an `entry` field in the form) says which file starts the
bot.

```json
{"kind": "upload-team-a", "language": "Python", "file": "main.py", "files": 3,
 "names": ["main.py", "protocol.py", "strategy.py"],
 "available": true, "reason": ""}
```

Enter the tournament with that `kind`; `file` is the entry point the server
chose and `names` everything it kept. `available` is false with a `reason`
when the machine has not got the toolchain (an uploaded `.q` with no kdb+
installed, say) — the submission is still saved, it just cannot be run here.

* **The entry point** is, in order: the file `entry` names; the one nearest the
  top called `main`/`client`/`strategy`/`bot`/… ; the only one with a `main()`
  in it; the only runnable file there is. Otherwise `400` naming the
  candidates, because running the wrong file in a competition is worse than
  asking.
* **Compiled languages** get every file: `**/*.cpp` and `**/*.java` and
  `**/*.c` are compiled together, a `go.mod` makes it `go build .` of the
  package, a `Cargo.toml` makes it `cargo build --release`. A Java file that
  declares a package is run by its full class name.
* **The manifest is never taken from a submission.** A `client.json` inside one
  is dropped; the server writes its own from the entry point's extension, so
  the commands it runs are always the ones it generated.
* The folder is named after the team, so uploading again replaces that team's
  previous submission rather than piling up.
* Accepted extensions only; `400` for a submission with nothing runnable in it,
  an empty one, or a broken archive. `413` over `max_bytes` (one file),
  `max_total_bytes` (the submission, measured by what a zip *unpacks to*) or
  `max_files`. `403` when uploads are off.
* Absolute paths, `..` and symlinks in a zip are dropped, so a submission
  always lands inside its own folder. So is the litter a zipped folder carries
  whether the team meant it or not: `__MACOSX/`, `.DS_Store`, `__pycache__/`,
  `.git/`, `.venv/`, `node_modules/` — a submission is source, not an
  installed tree, and the limits above would not fit one anyway. One wrapping
  folder (`my-bot/main.py`) is unwrapped; the folders inside it are kept.

## Clients in other languages

Nothing above is Python-specific: the plain-text format exists so that a
client can be a shell script. Seven sample clients ship with the repository
(Python, C++, Java, q/kdb+, JavaScript, Ruby, POSIX shell), and a folder with
a `client.json` manifest teaches the server to build and launch a new one, so
it can be seated from the lobby and entered in a tournament.
[CLIENTS.md](CLIENTS.md) has the details.

## Minimal bot, in shell

```sh
S=http://localhost:8000; G=K7PX
T=$(curl -s -X POST "$S/api/games/$G/join?name=curl" | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')
while :; do
  curl -s "$S/api/games/$G/getstate?format=text&token=$T" > state.txt
  grep -q '^status finished' state.txt && { grep reason state.txt; break; }
  grep -q '^your_turn 1' state.txt || continue
  STONES=$(awk '/^stones/{print $2}' state.txt)
  CARD=$(awk '/^your_cards/{for(i=2;i<=NF;i++) if($i<='"$STONES"') {c=$i}; print c}' state.txt)  # biggest fitting card
  curl -s -X POST "$S/api/games/$G/move" -H "X-Token: $T" -d "card=$CARD" > /dev/null
done
```
