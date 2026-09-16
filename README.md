# Card Nim

Server, web UI and bot clients for the Card Nim game.

Author: Sarp Akar, sa9932@nyu.edu

The server is a single Python file with no dependencies (Python 3.9+). It can host
several games at once, keeps a clock for each player, checks every move, logs the
time per move and decides the winner. Bots talk to it over HTTP. Humans and
spectators use the browser.

## Running it

```sh
python3 server/cardnim_server.py
```

Prints the lobby address (something like `http://10.18.4.22:8000/`). Open it, create
a game with the number of stones and cards, and share the board link (`/game/K7PX`).

You can also create the game from the command line:

```sh
python3 server/cardnim_server.py --stones 100 --cards 25 --clock 120 --label "Round 1"
```

Then start two bots in other terminals:

```sh
python3 clients/python/client.py --game K7PX --name "Team A" --bot greedy
python3 clients/python/client.py --game K7PX --name "Team B" --bot random
```

Finished games are written to `results/<id>.json`.

## The board

`/game/ID` shows one game and updates live for everyone who has it open.

- The page is light like the lobby; the table itself is a felt card table. Seat
  1's pod sits on the top rail (blue), seat 2's on the bottom (amber), each
  with an avatar, name, clock and a time bar. The player on turn has a glow
  around the avatar.
- Hands are drawn as small playing cards that stay in place. Played cards show
  their back, the last one played has a gold edge.
- The pile in the middle of the felt shows the count and one chip per stone,
  in rows of 25.
- The lobby is a light three-pane page: search and filter tables on the
  left, see the selected table's seats and stats in the middle with a Join or
  Watch button, and create a table on the right. A QR code of the lobby
  address sits under the form so people can join from their phones.
  That right-hand pane is yours alone: creating tables and tournaments, the
  QR to hand out and the house rules only appear on the machine the server is
  running on. Everyone else gets the two panes they actually use, the table
  list and the table they picked. The same is true of the bracket page's QR.
  Add `?host=1` to either address to force the organiser's view on (handy when
  you project from a second machine), or `?host=0` to force it off.
- An open seat's pod is the sign-in: click the circle to pick one of 16
  pixel-art faces, type a name where the player's name will be, and press
  Sit here. Switch the pod to Bot to let the server play that seat: its
  built-in bots (greedy, random) or any of the clients under `clients/` in
  whatever language, which the server builds and runs as a child process
  exactly as a team would. That
  is the quickest way to fill a table for a demo, and a live check that the
  hand-out works on the machine. Bots joining over the API get a face from their name (or
  pass `--avatar N`). On your turn your playable cards become buttons. Cards
  bigger than the pile are disabled since playing one loses immediately.
- A seat belongs to the browser tab that took it. Two people use two tabs,
  windows or laptops. To test alone, take both seats from one tab.
- Before the game starts you can leave your seat. During play you can resign
  (asks for confirmation).
- The move log lists every move with stones left and seconds used.
- When the game ends the status card shows the winner and why. "Replay"
  plays the recorded moves back one every 2 seconds (pause, step, restart),
  which is the way to watch a bot game that finished in a blink. "Play again" makes a new game with the same settings. "Abort game"
  ends a stuck game with no winner, after a confirmation.

Screenshots are in `docs/screenshots/`.

## Tournaments

A knockout for trying strategies against each other. Create one in the
lobby (same settings as a table: stones, cards, clock), share its page
(`/tournament/Q4RT`), and everyone enters by name: a person at a browser, a
program, or one of the server's bots to fill the bracket. Press Start and
the entrants are drawn into pairs; the bracket is a power of two, so some
get a bye in round one. Each match is one game with a coin toss for who
moves first. The winner moves on, the loser is out, until the final.

Matches are played **one at a time, and you start each one**: pressing Start
draws the bracket but creates no game, and every match then waits behind a
"Start match" button until you press it. That way you can announce a match and
get the room's attention before the clocks run. Only one match can be in
progress at a time.

The page is three columns: the entrants, the bracket, and the match being
played drawn as the real table. The table pane follows whichever match is
live; clicking another match pins that one instead. Between matches it shows
the next pairing standing ready, and each live match has Pause and Resume,
which stop the clocks without costing either player time.

- Every match is an ordinary table with its two seats reserved for the
  entrants. The game starts once both have sat down.
- The bracket page offers two ways in: **My strategy**, which uploads a file
  the server then runs every round (needs `--accept-uploads`), and **Server
  bot**, which fills a slot with one of the server's own. Both are played by
  the server, so nobody has to keep a laptop open.
- The API still takes the other two kinds, they are just not on the page: a
  person playing in the browser (`kind: "human"`) and a program that sits down
  in each of its games itself (`kind: "api"`).
- A program enters with `kind: "api"` and keeps the token it gets; the
  tournament's `getstate` blocks until it has a game to sit down in, and
  the same token opens the reserved seat. The Python sample client does
  all of that:

  ```sh
  python3 clients/python/client.py --server http://10.18.4.22:8000 --tournament Q4RT --name "Team A" --bot greedy
  ```

  Any other client can play a match with `--game ID --seat N --name "Team A"`
  (the page shows the exact line): a seat reserved by name opens for that
  name.
- The bracket page updates live for everyone. If someone never shows up,
  "No-show?" on the match hands it to the other side. Aborting a game
  (from the board) makes the match be played again. Finished brackets are
  written to `results/tournament-<id>.json`.

## Writing a bot

Three HTTP calls. Details in `docs/API.md`.

1. `POST /api/games/{id}/join` with your name. You get a token and a seat.
2. `GET /api/games/{id}/getstate` with the token. Blocks until it's your turn
   (or the game is over) and returns stones left, both hands, both clocks and
   the move history.
3. `POST /api/games/{id}/move` with the token and your `card`. A rejected move
   gets a 409 with the reason and changes nothing.

Repeat 2 and 3 until `status` is `finished`. Waiting inside `getstate` doesn't
use your clock.

Python:

```python
from client import CardNimClient

def brain(stones, my_cards, opp_cards, state):
    return stones if stones in my_cards else min(c for c in my_cards if c <= stones)

CardNimClient("http://10.18.4.22:8000", "K7PX", "Team A").play(brain)
```

or edit `greedy_bot` in `clients/python/client.py` and run:

```sh
python3 clients/python/client.py --server http://10.18.4.22:8000 --game K7PX --name "Team A" --bot greedy [--seat 1]
```

## Any language

Nothing about the protocol is Python. It is three HTTP calls, and with
`?format=text` the state comes back as plain `key value` lines, so a client
needs no JSON parser and no library — only the ability to open a socket.
Fifteen sample clients ship with the repo, all the same page of plumbing
around a five-line strategy:

| Folder | Language | Edit | Run it yourself |
|---|---|---|---|
| `clients/python/` | Python | `brain()` | `python3 clients/python/client.py --game K7PX --name "Team A"` |
| `clients/cpp/` | C++ | `choose_card()` | `g++ -std=c++17 -O2 clients/cpp/client.cpp -o client && ./client --game K7PX --name "Team A"` |
| `clients/c/` | C | `choose_card()` | `cc -O2 clients/c/client.c -o client && ./client --game K7PX --name "Team A"` |
| `clients/java/` | Java | `chooseCard()` | `javac clients/java/Client.java -d out && java -cp out Client --game K7PX --name "Team A"` |
| `clients/go/` | Go | `chooseCard()` | `go build -o client clients/go/client.go && ./client --game K7PX --name "Team A"` |
| `clients/rust/` | Rust | `choose_card()` | `rustc -O clients/rust/client.rs -o client && ./client --game K7PX --name "Team A"` |
| `clients/javascript/` | JavaScript | `chooseCard()` | `node clients/javascript/client.js --game K7PX --name "Team A"` |
| `clients/typescript/` | TypeScript | `chooseCard()` | `deno run --allow-net --allow-env clients/typescript/client.ts --game K7PX --name "Team A"` |
| `clients/ruby/` | Ruby | `choose_card` | `ruby clients/ruby/client.rb --game K7PX --name "Team A"` |
| `clients/r/` | R | `choose_card()` | `Rscript clients/r/client.R --game K7PX --name "Team A"` |
| `clients/perl/` | Perl | `choose_card()` | `perl clients/perl/client.pl --game K7PX --name "Team A"` |
| `clients/php/` | PHP | `choose_card()` | `php clients/php/client.php --game K7PX --name "Team A"` |
| `clients/lua/` | Lua | `choose_card()` | `lua clients/lua/client.lua --game K7PX --name "Team A"` |
| `clients/q/` | q / kdb+ | `choose_card` | `q clients/q/client.q -game K7PX -name "Team A"` (self-check: `-selftest`; needs a free kdb+ from developer.kx.com) |
| `clients/shell/` | POSIX shell | `choose_card()` | `sh clients/shell/client.sh --game K7PX --name "Team A"` |

Each is one self-contained file: the protocol is three HTTP calls and
`?format=text` returns plain `key value` lines, so none of them needs a JSON
parser or any package. C, C++, Rust and Go speak HTTP over a raw socket
because their standard libraries ship no client; Lua and the shell script use
curl, because Lua has no sockets at all.

Add `--server http://10.18.4.22:8000` and `--seat 1` as needed; every client
also reads `CARDNIM_SERVER`, `CARDNIM_GAME`, `CARDNIM_NAME` and `CARDNIM_SEAT`
from the environment, which is how `scripts/run_match.py` and the server drive
them.

Copy the folder closest to your language and replace the strategy. To have the
server launch it too — so it appears in the lobby's Bot picker and can be
entered in a tournament — drop a `client.json` next to your source saying how
to build and run it:

```json
{
  "language": "Rust",
  "file": "main.rs",
  "edit": "choose_card(stones, mine, theirs)",
  "tools": {"rustc": "rustc"},
  "build": ["{rustc}", "-O", "{file}", "-o", "{out}"],
  "output": "player",
  "run": ["{out}"]
}
```

That is the whole integration: no server code changes, no restart (the server
rescans `clients/` every ten seconds). A language whose toolchain is missing is
still listed, greyed out, with the reason. Keep a strategy outside the repo
with `--clients-dir ~/my-strategies`. Full reference: `docs/CLIENTS.md`.

## Competition night

1. Start the server on a machine on the room's network and note the address.
2. Create the game with the announced s and k.
3. Put the board on the projector. Give both teams the game id and the address.
   Team A joins seat 1, team B seat 2 (bots can pass `--seat`). The game starts
   and seat 1's clock runs as soon as both are seated.
4. When it ends, click "Play again" for the return match with seats swapped.

Teams do not have to agree on a language: what crosses the network is HTTP, so
a q strategy and a C++ one play each other with nothing in between. Collect the
folders beforehand (`--clients-dir` if you keep them outside the repo), check
`GET /api/strategies` shows every one as available, and the whole field can be
entered in one bracket and played by the server.

### Teams who bring their strategy on the night

Start the server with `--accept-uploads` and the tournament page offers a
**My strategy** tab next to Server bot:

```sh
python3 server/cardnim_server.py --accept-uploads
```

Put the bracket on the projector, and a team scans its QR code with a phone or
opens the address on their laptop. The QR only appears on your own screen —
the server checks whether the request came from the machine it is running on,
so a teammate who scanned it does not see a QR of their own. They type a team name, pick their strategy
file, and press "Upload and enter". The page checks the file against the
guidelines one step at a time — type, size, then the server's own answers on
whether it saved and whether this machine can run it — so a rejected upload
says which rule it missed rather than just failing. The server writes it into `uploads/<team>/`
with a generated manifest, builds it if it needs building, and from then on
runs it for them every round — they can close the laptop.

`examples/` holds two finished submissions you can upload straight away to
check the path works.

Every move of a tournament match is followed by the tournament's `bot_delay`
(half a second by default) so a room can follow the play. The pause stops both
clocks, so it costs neither side any time. Without it a match between two
programs is over in a fifth of a second.

One self-contained file per team, up to 1 MB, in any of the languages under
`clients/` (`.py .js .ts .rb .r .pl .php .lua .sh .q .c .cc .cpp .go .rs
.java`) — the server generates the manifest from the extension. Uploading again replaces that
team's previous file, so a team can fix a bug and send it back. The Bot picker
lists uploaded strategies too, so you can also seat one in an ordinary game.

**This runs other people's code on your machine.** That is what the flag is
for, and why it is a flag: without it the server accepts no uploads at all.
Only turn it on for a room you trust, and stop the server when the round is
over. `uploads/` is gitignored.

Bot vs bot without humans:

```sh
python3 scripts/run_match.py --stones 100 --cards 25 --both-orders \
    --name1 "Team A" --name2 "Team B" \
    "python3 clients/python/client.py --bot greedy" "ruby clients/ruby/client.rb"
```

## Rules as enforced

1. The game starts when both seats are taken. Seat 1 moves first.
2. A move is a card from your own hand, on your turn. Anything else gets a 409
   and is ignored. Your clock keeps running.
3. Card equal to the pile: you win. Card bigger than the pile: you lose, the
   pile stays as it was.
4. Otherwise the pile shrinks and the turn passes. If the next player has no
   card small enough, they lose right away.
5. Chess clocks. Yours runs from the moment your turn starts until your move is
   accepted. Zero means you lose. A background thread checks this even if
   nobody is polling. Default is 120 seconds per player, set per game.
6. Every move is logged with stones before/after and seconds spent.

Limits: 1 ≤ s ≤ 500, 1 ≤ k ≤ 200. The lobby warns if 1+2+...+k ≤ s.

## Files

```
server/
  engine.py            rules (no I/O)
  tournament.py        the knockout bracket (no I/O)
  cardnim_server.py    HTTP API, long polling, clock thread, static files, results
  bots.py              bots the server can seat itself (greedy, random)
  clients.py           finds clients/*/client.json, builds and launches them
  web/
    index.html lobby.js lobby.css qr.js      the lobby
    game.html game.js style.css              the board
    tournament.html tournament.js            the bracket
    table.css fireworks.js                   the table and the finish, shared
                                             by the board and the bracket
clients/                 one folder per language, each with a client.json
  python/client.py     library + sample bots (random, greedy)
  c/ cpp/ go/ rust/    compiled: the server builds each on first use
  java/ javascript/ typescript/ ruby/ r/ perl/ php/ lua/ q/ shell/
scripts/
  run_match.py         bot vs bot matches
examples/              two finished submissions, for testing the upload path
  hungry.py            Python, always the biggest card that fits
  cautious.rb          Ruby, always the smallest
uploads/               strategies teams sent from their own devices (gitignored)
tests/
  test_engine.py
  test_api.py          end to end over HTTP
  test_tournament.py   the bracket, reserved seats, tournaments over HTTP
  test_clients.py      the manifests, and a new language playing a real game
docs/
  API.md               protocol
  CLIENTS.md           writing a client in any language, and uploading one
```

## Tests

```sh
python3 -m pytest tests/ -q
```

80 tests, about 25 seconds. Includes the 5 stones / cards 1-3 example (second
player wins), full games over HTTP with timeouts and long polling, and a
language added while the server is running that plays a game and a bracket.

## Problems

- Bots on another laptop can't connect: check the printed address and the
  firewall. `curl http://ADDRESS:8000/api/health` should return `{"ok": true}`.
- Port already in use: pass `--port 8001`.
- 401: the bot didn't send its token (`X-Token` header, `token` param, or cookie).
- 409 "not your turn": the bot called `move` without waiting on `getstate`.
- Stuck game: "Abort game" on the board or `POST /api/games/ID/abort`.
- Timing: measured on the server from the previous move to yours, so network
  latency counts against the mover. Both teams should be on the same network.
