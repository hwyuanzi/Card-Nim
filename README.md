# Card Nim

Server, web UI and bot clients for the Card Nim game.

Author: Sarp Akar, sa9932@nyu.edu

The server is plain Python with no dependencies (Python 3.9+). It hosts several
games at once, keeps a clock for each player, validates every move, logs the
time taken per move and decides the winner. Bots talk to it over HTTP. Players
and spectators use the browser.

## Running it

```sh
python3 server/cardnim_server.py
```

The server prints the lobby address (for example `http://10.18.4.22:8000/`).
Open it, create a game with the number of stones and cards, and share the board
link (`/game/K7PX`).

A game can also be created from the command line:

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

- The table is drawn as a felt card table. Seat 1 sits on the top rail (blue),
  seat 2 on the bottom (amber), each with an avatar, name, clock and time bar.
  The player on turn has a glow around the avatar.
- Hands are drawn as playing cards that stay in place. Played cards show their
  back; the last card played has a gold edge.
- The pile in the middle shows the count and one chip per stone, in rows of 25.
- The lobby is a three-pane page: search and filter tables on the left, the
  selected table's seats and stats in the middle with a Join or Watch button,
  and the create-a-table form on the right. A QR code of the lobby address sits
  under the form for joining from a phone.
- The right-hand pane is shown only on the machine running the server, which is
  also true of the bracket page's QR code. Other devices get the two panes they
  use: the table list and the table they selected. Add `?host=1` to either
  address to force the organiser's view on (useful when projecting from a
  second machine) or `?host=0` to force it off.
- An open seat's pod is the sign-in: click the circle to pick one of 16
  pixel-art faces, type a name, and press Sit here. Switch the pod to Bot to
  let the server play that seat, using either its built-in bots (greedy,
  random) or any client under `clients/`, which the server builds and runs as a
  child process. Bots joining over the API get a face derived from their name,
  or can pass `--avatar N`. On your turn, playable cards become buttons; cards
  larger than the pile are disabled, since playing one loses immediately.
- A seat belongs to the browser tab that took it. Two players use two tabs,
  windows or machines. To test alone, take both seats from one tab.
- A player can leave their seat before the game starts and resign during play
  (with a confirmation).
- The move log lists every move with the stones left and the seconds used.
- When the game ends, the status card shows the winner and the reason. Replay
  plays the recorded moves back at one every 2 seconds, with pause, step and
  restart, which is how to watch a bot game that finished in a fraction of a
  second. Play again creates a new game with the same settings. Abort game ends
  a stuck game with no winner, after a confirmation.

Screenshots are in `docs/screenshots/`.

## Tournaments

A knockout bracket for playing strategies against each other. Create one in the
lobby with the same settings as a table (stones, cards, clock), share its page
(`/tournament/Q4RT`), and entrants join by name: a player at a browser, a
program, or one of the server's bots to fill the bracket. Press Start and the
entrants are drawn into pairs. The bracket is a power of two, so some entrants
get a bye in round one. Each match is one game, with a coin toss for who moves
first. The winner advances and the loser is out, until the final.

Matches are played one at a time and each is started by hand. Pressing Start
draws the bracket but creates no game; every match then waits behind a Start
match button. This leaves time to announce a match before the clocks run. Only
one match can be in progress at a time.

Only the machine running the server can run the event. Drawing the bracket,
starting a match, calling a no-show and aborting are refused for requests from
any other device, so a team's phone gets the bracket, the entry form and the
live table, but cannot start a match on the projector. The page hides the
buttons it cannot use and the server rejects the calls behind them, so neither
a browser nor a direct HTTP request from another device can control the event.

- `--controls-from 10.18.4.31` also gives the controls to another machine, for
  when the bracket is projected from a second laptop. The option is repeatable.
- `--open-controls` allows anyone who can reach the page to run the event.
- `?host=1` puts the organiser's view, including the QR code, on a projected
  screen, but it does not grant the controls, which the server decides.

The page is three columns: the entrants, the bracket, and the match being
played, drawn as a real table. The table pane follows whichever match is live;
clicking another match pins that one instead. Between matches it shows the next
pairing, and each live match has Pause and Resume, which stop the clocks
without costing either player time.

- Every match is an ordinary table with its two seats reserved for the
  entrants. The game starts once both have taken their seats.
- The bracket page offers two ways in: My strategy, which uploads a submission
  (one file, several files, a folder or a `.zip`) that the server runs every
  round, and requires `--accept-uploads`; and Server bot, which fills a slot
  with one of the server's own bots. The server plays both, so no one has to
  keep a laptop open.
- The API takes two further kinds that are not on the page: a person playing in
  the browser (`kind: "human"`) and a program that takes its own seat in each of
  its games (`kind: "api"`).
- A program enters with `kind: "api"` and keeps the token it receives. The
  tournament's `getstate` blocks until the program has a game to join, and the
  same token opens the reserved seat. The Python sample client does all of
  this:

  ```sh
  python3 clients/python/client.py --server http://10.18.4.22:8000 --tournament Q4RT --name "Team A" --bot greedy
  ```

  Any other client can play a match with `--game ID --seat N --name "Team A"`
  (the page shows the exact command). A seat reserved by name opens for that
  name.
- The bracket page updates live for everyone. If an entrant never appears, the
  No-show? button on the match awards it to the other side. Aborting a game
  from the board causes the match to be replayed. Finished brackets are written
  to `results/tournament-<id>.json`.

## Writing a bot

Three HTTP calls. Details in `docs/API.md`.

1. `POST /api/games/{id}/join` with your name returns a token and a seat.
2. `GET /api/games/{id}/getstate` with the token blocks until it is your turn
   or the game is over, then returns the stones left, both hands, both clocks
   and the move history.
3. `POST /api/games/{id}/move` with the token and your `card`. A rejected move
   returns 409 with the reason and changes nothing.

Repeat 2 and 3 until `status` is `finished`. Waiting inside `getstate` does not
use your clock.

Python:

```python
from client import CardNimClient

def brain(stones, my_cards, opp_cards, state):
    return stones if stones in my_cards else min(c for c in my_cards if c <= stones)

CardNimClient("http://10.18.4.22:8000", "K7PX", "Team A").play(brain)
```

Or edit `greedy_bot` in `clients/python/client.py` and run:

```sh
python3 clients/python/client.py --server http://10.18.4.22:8000 --game K7PX --name "Team A" --bot greedy [--seat 1]
```

## Any language

Nothing about the protocol is specific to Python. It is three HTTP calls, and
with `?format=text` the state comes back as plain `key value` lines, so a
client needs no JSON parser and no library, only the ability to open a socket.
Sixteen sample clients ship with the repository, each the same page of plumbing
around a short strategy function:

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
| `clients/julia/` | Julia | `choose_card()` | `julia clients/julia/client.jl --game K7PX --name "Team A"` |
| `clients/q/` | q / kdb+ | `choose_card` | `q clients/q/client.q -game K7PX -name "Team A"` (self-check: `-selftest`; needs a free kdb+ from developer.kx.com) |
| `clients/shell/` | POSIX shell | `choose_card()` | `sh clients/shell/client.sh --game K7PX --name "Team A"` |

Each sample is kept to one file so that it can be read in a sitting. That is
not a limit on what you write. None of the samples needs a JSON parser or any
package: C, C++, Rust and Go speak HTTP over a raw socket because their
standard libraries ship no client; Lua and the shell script call curl, because
Lua has no sockets; Julia uses `Downloads` from its standard library.

Your own entry can use as many files and folders as you like. A client is a
folder with a manifest: `file` names the source the server starts, an
interpreter finds the neighbouring files itself, and a compiler is given them
by a `sources` glob. An upload is a folder as well (see below).

Add `--server http://10.18.4.22:8000` and `--seat 1` as needed. Every client
also reads `CARDNIM_SERVER`, `CARDNIM_GAME`, `CARDNIM_NAME` and `CARDNIM_SEAT`
from the environment, which is how `scripts/run_match.py` and the server start
them.

Copy the folder closest to your language and replace the strategy. To have the
server launch it, so that it appears in the lobby's Bot picker and can be
entered in a tournament, add a `client.json` next to your source describing how
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

That is the whole integration: no server changes and no restart, since the
server rescans `clients/` every ten seconds. A language whose toolchain is
missing is still listed, greyed out, with the reason. To keep a strategy
outside the repository, use `--clients-dir ~/my-strategies`. The full reference
is `docs/CLIENTS.md`.

## Competition night

1. Start the server on a machine on the room's network and note the address.
   That machine is the organiser: it is the only one that can draw a bracket or
   start a match.
2. Create the game with the announced s and k.
3. Put the board on the projector and give both teams the game id and the
   address. Team A joins seat 1, team B seat 2 (bots can pass `--seat`). The
   game starts and seat 1's clock runs as soon as both seats are taken.
4. When it ends, click Play again for the return match with the seats swapped.

Teams do not have to agree on a language, because what crosses the network is
HTTP: a q strategy and a C++ one play each other with nothing in between.
Collect the folders beforehand, using `--clients-dir` if you keep them outside
the repository, check that `GET /api/strategies` reports every one as
available, and the whole field can be entered in one bracket and played by the
server.

### Teams who bring their strategy on the night

Start the server with `--accept-uploads` and the tournament page offers a My
strategy tab next to Server bot:

```sh
python3 server/cardnim_server.py --accept-uploads
```

Put the bracket on the projector, and a team scans its QR code with a phone or
opens the address on a laptop. The QR code appears only on the organiser's
screen, since the server checks whether the request came from the machine it is
running on. A team types a name, selects their strategy (one file, several
files, a folder or a `.zip`), and presses Upload and enter. The page checks the
submission one step at a time: that something in it is a language this server
runs, then the sizes, then the server's own answers on whether it saved the
submission and whether this machine can run it. A rejected upload reports which
rule it failed. The server writes the submission into `uploads/<team>/` with a
generated manifest, builds it if it needs building, and from then on runs it
every round, so the team can close their laptop.

`examples/` holds three finished submissions that can be uploaded as they are
to check that the path works. One of them, `examples/scout/`, is split across
three files.

A submission is a folder rather than a single file. A team may send one file or
two hundred, in whatever folders they use, in any of the languages under
`clients/` (`.py .js .ts .rb .R .pl .php .lua .sh .q .jl .c .cc .cpp .go .rs
.java`), up to 1 MB per file and 8 MB in total. The server has to know which
file starts the bot: name it `main.py` (or `Main.java`, `main.cpp`, and so on)
and it is found automatically, otherwise the page asks. Everything beside it is
the team's own: modules that `main.py` imports, extra classes for javac to
compile, a `go.mod`, a `Cargo.toml`, an opening book in a text file.

A submission may not supply its own manifest. The server generates one from the
entry point's extension, so the commands it runs are always its own, and a
`client.json` inside a submission is discarded. Uploading again replaces that
team's whole folder, so a team can fix a bug and send the strategy back. The
Bot picker lists uploaded strategies as well, so one can also be seated in an
ordinary game.

**Warning: this runs other people's code on your machine.** That is why it is a
flag: without it the server accepts no uploads at all. Only enable it on a
network you trust, and stop the server when the round is over. `uploads/` is
gitignored.

Every move of a tournament match is followed by the tournament's `bot_delay`,
1.5 seconds by default, so that a room can follow the play. The pause stops
both clocks, so it costs neither side any time. Without it, a match between two
programs is over in a fifth of a second. Pass `bot_delay` when creating the
tournament to set a different pace (0 to 30 seconds).

Bot against bot without players:

```sh
python3 scripts/run_match.py --stones 100 --cards 25 --both-orders \
    --name1 "Team A" --name2 "Team B" \
    "python3 clients/python/client.py --bot greedy" "ruby clients/ruby/client.rb"
```

## Rules as enforced

1. The game starts when both seats are taken. Seat 1 moves first.
2. A move is a card from your own hand, on your turn. Anything else returns 409
   and is ignored, and your clock keeps running.
3. A card equal to the pile wins. A card larger than the pile loses, and the
   pile is unchanged.
4. Otherwise the pile shrinks and the turn passes. If the next player has no
   card small enough, they lose immediately.
5. Chess clocks. Yours runs from the moment your turn starts until your move is
   accepted. Reaching zero loses the game. A background thread checks this even
   when nobody is polling. The default is 120 seconds per player, set per game.
6. Every move is logged with the stones before and after and the seconds spent.

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
  java/ javascript/ typescript/ ruby/ r/ perl/ php/ lua/ q/ julia/ shell/
scripts/
  run_match.py         bot against bot matches
examples/              three finished submissions, for testing the upload path
  hungry.py            Python, always the biggest card that fits
  cautious.rb          Ruby, always the smallest
  scout/               Python in three files: main.py, protocol.py, strategy.py
uploads/               strategies teams sent from their own devices (gitignored)
tests/
  test_engine.py
  test_api.py          end to end over HTTP
  test_tournament.py   the bracket, reserved seats, who may run the event
  test_clients.py      the manifests, submissions of several files, and a new
                       language playing a real game
docs/
  API.md               protocol
  CLIENTS.md           writing a client in any language, and uploading one
```

## Tests

```sh
python3 -m pytest tests/ -q
```

105 tests, about 30 seconds. They cover the 5 stones / cards 1-3 example (the
second player wins), full games over HTTP with timeouts and long polling, a
language added while the server is running that plays a game and a bracket,
submissions of several files (a folder, a `.zip`, and a C++ bot split over two
translation units) uploaded and played to the end, and the rule that only the
organiser's machine can start a match.

## Problems

- Bots on another machine cannot connect: check the printed address and the
  firewall. `curl http://ADDRESS:8000/api/health` should return `{"ok": true}`.
- Port already in use: pass `--port 8001`.
- 401: the bot did not send its token (`X-Token` header, `token` parameter, or
  cookie).
- 403 on Start match: the request came from a device other than the machine
  running the server. Start it there, or pass `--controls-from ADDR`.
- 409 "not your turn": the bot called `move` without waiting on `getstate`.
- Stuck game: use Abort game on the board or `POST /api/games/ID/abort`.
- Timing: measured on the server from the previous move to yours, so network
  latency counts against the player on turn. Both teams should be on the same
  network.
