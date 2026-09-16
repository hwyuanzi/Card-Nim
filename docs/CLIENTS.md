# Clients in any language

A Card Nim client is any program that can make an HTTP request. There is no
SDK to install and no library to link: the protocol is three calls
(`join`, `getstate`, `move`, described in [API.md](API.md)) and the state can
come back as plain `key value` lines with `?format=text`, so no JSON parser is
needed either.

This file is about the second half of that: teaching **the server** to start
your program by itself, so your language shows up in the lobby's Bot picker
and can be entered in a tournament like any other strategy.

Sixteen languages ship with the repository:

| Folder | Language | Edit | Needs |
|---|---|---|---|
| `clients/python/` | Python | `brain()` | python3 |
| `clients/cpp/` | C++ | `choose_card()` | g++ or clang++ |
| `clients/c/` | C | `choose_card()` | cc, gcc or clang |
| `clients/java/` | Java | `chooseCard()` | JDK 11+ |
| `clients/go/` | Go | `chooseCard()` | go |
| `clients/rust/` | Rust | `choose_card()` | rustc |
| `clients/javascript/` | JavaScript | `chooseCard()` | node |
| `clients/typescript/` | TypeScript | `chooseCard()` | deno |
| `clients/ruby/` | Ruby | `choose_card` | ruby |
| `clients/r/` | R | `choose_card()` | Rscript |
| `clients/perl/` | Perl | `choose_card()` | perl |
| `clients/php/` | PHP | `choose_card()` | php |
| `clients/lua/` | Lua | `choose_card()` | lua + curl |
| `clients/julia/` | Julia | `choose_card()` | julia 1.9+ (`Downloads`, a stdlib) |
| `clients/q/` | q / kdb+ | `choose_card` | q — see below |
| `clients/shell/` | POSIX shell | `choose_card()` | sh, curl, awk |

They all do the same thing and are about a page of plumbing plus a five-line
strategy. Copy the one closest to your language and replace the strategy.

Each *sample* is a single file so it can be read in one sitting. Your own
client is not limited that way: a client is a folder, and the manifest's `file`
only names the one the server starts. Split it into as many files as you like —
`examples/scout/` is three.

### q needs an account, once

q is the one client that cannot be installed unattended: kdb+ is not in
Homebrew, the old `cdn.kx.com` downloads are gone, and KX's installer takes an
OAuth token and a licence key that only appear once you have signed up at
[developer.kx.com](https://developer.kx.com/products/kdb-x/install)
(`portal.dl.kx.com` answers `401` to everyone else). The open-source K
interpreters (kona, ngn/k) are a different language — no `.Q.hg`, no q
keywords — so they cannot run this client.

Sign up, run the install command the Developer Center shows you, and q lands
in `~/.kx` with `~/.kx/bin` added to your `~/.zshrc`. Start the server from a
shell that has it on `PATH` and the lobby lists q like any other language.

The q client also carries its own checks, which need no server and no game:

```sh
q clients/q/client.q -selftest
```

Loading the file at all proves it parses; the checks then prove the text
parsing, the percent-encoding and `choose_card` behave (including Shasha's
5-stones example). Run it after editing the strategy, and once on any machine
that has just installed q.

Two q traps worth knowing if you edit that file, because both fail *silently*:

* **A line containing only `/` opens a multi-line comment** that runs until a
  line containing only `\`. A trailing space does not help — q strips it. Use
  `//` for a blank comment line, or the rest of your program is a comment.
* **`parsestate` yields char vectors, but `"1"` in q source is a char atom**,
  and `~` distinguishes them: `"1"~enlist "1"` is `0b`. Compare fields with
  the `is[]` helper, which promotes both sides, or a one-character value like
  `your_turn` will never match.

## Adding a language

A client is a folder with your program in it and a `client.json` saying how to
build and run it.

```
clients/
  rust/
    main.rs
    client.json
```

Your program may be as many files as you want. `file` names the one the server
starts; an interpreter finds the neighbours itself (an `import`, a `require`,
an `include`), and a compiler is told about them with `sources`:

```
clients/
  rust/
    main.rs        the entry point: "file"
    search.rs      a module main.rs declares
    book.txt       an opening book it reads at startup
    client.json
```

```json
{
  "language": "C++",
  "file": "main.cpp",
  "sources": ["**/*.cpp"],
  "build": ["{cxx}", "-std=c++17", "-O2", "{sources}", "-o", "{out}"],
  "output": "player",
  "run": ["{out}"],
  "tools": {"cxx": ["g++", "clang++"]}
}
```

`{sources}` becomes one argument per matching file, and the client is rebuilt
when *any* of them is newer than `output` — so editing the fourth file of five
still triggers a build. Both build and run happen with the client's folder as
the working directory.

```json
{
  "language": "Rust",
  "file": "main.rs",
  "edit": "choose_card(stones, mine, theirs)",
  "note": "smallest fitting card",
  "badge": "Rs",
  "color": "#dea584",
  "tools": {"rustc": "rustc"},
  "build": ["{rustc}", "-O", "{file}", "-o", "{out}"],
  "output": "player",
  "run": ["{out}"]
}
```

That is the whole integration. Restart nothing: the server rescans `clients/`
every ten seconds, so the language appears in the lobby's Bot picker as soon
as the folder is there. If the toolchain is missing the language is left out
of the picker; `GET /api/strategies` still lists it with the reason ("needs
rustc"), so you can see what the machine is short of instead of wondering
where your language went.

### Or upload it from the browser

A team on another device does not need access to the repository at all. Start
the server with `--accept-uploads` and the tournament page offers a **My
strategy** form: they pick their submission, and the server writes it into
`uploads/<team>/` with exactly the kind of manifest shown above, generated from
the entry point's extension. From that moment it is an ordinary client —
seatable, enterable, buildable.

A submission is a folder, not a file. Any of these work:

* one file — `strategy.py`;
* several files picked at once — `main.py`, `protocol.py`, `strategy.py`;
* a folder, picked with **Choose a folder…**, sub-folders and all;
* a `.zip` of that folder (one wrapping folder is unwrapped, the rest kept).

Up to 1 MB a file, 8 MB and 200 files in total; `__pycache__/`, `.git/`,
`node_modules/`, `.venv/` and `.DS_Store` are dropped on the way in, so send
source, not an installed tree. **Name the file that starts
your bot `main.py`** (or `Main.java`, `main.cpp`, `main.go`, …); `client`,
`strategy`, `bot`, `player`, `run` and a few more are recognised too, and if
the server still cannot tell, the page asks instead of guessing. Extra files
are yours to use: modules, a `go.mod`, a `Cargo.toml`, a table of openings in a
text file.

One file a submission may not bring is `client.json`: the server writes that
itself, so the commands it runs are always the ones it generated. The server
then *runs* your code, which is why all of this is off unless the flag is
given; see [API.md](API.md) for the endpoints and what they refuse.

To keep a strategy outside the repository, point the server at another folder:

```sh
python3 server/cardnim_server.py --clients-dir ~/my-strategies
```

`--clients-dir` may be repeated; `clients/` is always scanned as well.

### Manifest keys

Only `language`, `file` and `run` are required.

| Key | Meaning |
|---|---|
| `language` | Shown in the lobby, e.g. `"q"` |
| `kind` | The bot key used by the API; default `client-<folder>` |
| `label` | Shown in the Bot picker; default `<language> client (sample strategy)` |
| `file` | The entry point: the source the server starts, relative to the folder |
| `sources` | Every file a build compiles: a glob or a list of them (`["**/*.cpp"]`), relative to the folder |
| `edit` | The function to replace, for the lobby's hint |
| `note` | One line about what the sample strategy does |
| `tools` | Executables needed: `{"node": "node"}`, or alternatives `{"cxx": ["g++", "clang++"]}` |
| `verify` | Run a tool to prove it works: `{"javac": ["-version"]}` (macOS ships `java` stubs that exist but only print an error) |
| `build` | Command run when `output` is older than `file` |
| `output` | What `build` produces, relative to the folder |
| `run` | The command that plays one seat |
| `args` | `"flags"` (default) or `"env"`; see below |
| `env` | Extra environment variables for build and run |
| `command` | The line shown in the lobby, if it differs from `run` |
| `badge`, `color` | The language chip in the lobby: up to 3 characters and a hex colour |
| `server_bots` | List the server's own bots under this language too |
| `build_timeout`, `join_timeout` | Seconds; default 180 and 8 |

Placeholders usable in `build`, `run`, `command`, `env` and `output`:

* `{file}` the entry point, `{dir}` its folder, `{root}` the repository, `{out}` the build output
* `{sources}` every file matched by `sources`, as one argument each
* `{python}` the interpreter running the server
* every name in `tools` (`{cxx}`, `{node}`, ...) — the absolute path that was found
* in `run` only: `{server}` `{game}` `{seat}` `{name}` `{avatar}`

### How your program learns which seat to play

Three ways, and you pick whichever suits the language:

1. **The environment**, always set: `CARDNIM_SERVER`, `CARDNIM_GAME`,
   `CARDNIM_SEAT`, `CARDNIM_NAME`, `CARDNIM_AVATAR`. An option that does not
   apply is *absent*, never an empty string. Set `"args": "env"` and parse
   nothing.
2. **Placeholders**, if you put `{game}` and friends in `run` yourself.
3. **Flags** (the default): the server appends
   `--server URL --game ID --seat N --name NAME [--avatar A]`.

The q client uses the environment; the others take flags. Either way the
program still works when a team runs it by hand from a terminal, which is what
they will actually do on competition night.

A build failure, a missing tool, or a program that starts but never takes its
seat within `join_timeout` is reported to whoever pressed the button, with the
compiler's own message. Client output goes to `results/clients/<folder>-<time>.log`.

## Trust

A manifest names commands the server will execute on the machine hosting the
game. That is the same trust you give any file in the repository, but it is
worth saying out loud before you accept a folder from someone else: read the
`client.json` before you drop it into `clients/`.

## Checklist for a new client

1. Join with `POST /api/games/{id}/join?format=text&name=...` and keep the `token`.
2. Loop on `GET /api/games/{id}/getstate?format=text&timeout=60&token=...`.
   It answers only when it is your turn or the game is over, and waiting does
   not use your clock.
3. Stop when `status finished`; compare `winner` with your seat.
4. On your turn read `stones`, `your_cards`, `opp_cards` and
   `POST /api/games/{id}/move?format=text&token=...&card=N`.
5. If a move comes back 4xx, play the smallest fitting card instead — a bug in
   the strategy should not lose on time.
6. Percent-encode the name: team names have spaces in them.
