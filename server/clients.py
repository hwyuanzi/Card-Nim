"""
Clients in any language.

A *client* is a program that plays one seat over the HTTP API.  The API is
plain HTTP and offers a line-based text format (`?format=text`), so a client
can be written in anything that can open a socket: Python, C++, Java, q/kdb+,
JavaScript, Ruby, or a shell script with curl and awk.

This module is what lets the *server* start such a program by itself, so a
table can be filled from the browser and a tournament can have entrants
written in six different languages.  Nothing here knows about any particular
language: every client describes itself in a `client.json` manifest next to
its source, and the server only reads manifests.

    clients/
      q/
        client.q          the strategy a team edits
        client.json       how to check for q, how to run it

Manifest keys (only `language`, `file` and `run` are required):

    language     "q"                       shown in the lobby
    kind         "client-q"                bot key; default client-<folder>
    label        "q client"                shown in the Bot picker
    file         "client.q"                the source a team edits, the entry point
    sources      "**/*.cpp" | [...]        every file a build compiles (globs,
                                           relative to the folder); `{sources}`
                                           in `build` becomes one argument per
                                           match, and any of them being newer
                                           than `output` triggers a rebuild
    edit         "choose_card[...]"        the function to replace
    note         "wins if it can, ..."     one line about the sample strategy
    tools        {"q": "q"}                executables needed; a list means
                                           alternatives ({"cxx": ["g++", "clang++"]})
    verify       {"javac": ["-version"]}   run the tool to prove it works
                                           (macOS ships java stubs that exist
                                           but only print an error)
    build        ["{cxx}", "-O2", ...]     run when `output` is older than `file`
    output       "client"                  what build produces, relative to the folder
    run          ["{q}", "{file}"]         argv that plays one seat
    args         "flags" | "env"           how the seat is passed (see below)
    env          {"QHOME": "..."}          extra environment for build and run
    command      "q {file}"                the line shown in the lobby
    badge/color  "q", "#1f6feb"            the language chip in the lobby
    server_bots  true                      also list the server's own bots here
    build_timeout / join_timeout           seconds; default 180 and 8

Placeholders usable in `build`, `run`, `command`, `env` and `output`:
`{file}` `{dir}` `{root}` `{out}` `{python}` `{sources}`, every name in
`tools`, and for `run` also `{server}` `{game}` `{seat}` `{name}` `{avatar}`.

A client is a *folder*, not a file: `file` names the entry point and everything
beside it is the team's to use.  An interpreter finds its neighbours by itself
(an `import`, a `require`, a `\\l`); a compiler is told about them with
`sources`.

How the seat reaches the client:

* the environment always carries `CARDNIM_SERVER`, `CARDNIM_GAME`,
  `CARDNIM_SEAT`, `CARDNIM_NAME` and `CARDNIM_AVATAR`;
* if `run` uses `{server}`/`{game}`/... the values are substituted there;
* otherwise, unless `args` is `"env"`, the server appends
  `--server URL --game ID --seat N --name NAME [--avatar A]`.

A manifest names commands the server will execute, so only add folders you
trust -- the same trust you give any file in the repository.
"""

from __future__ import annotations

import glob
import io
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from typing import Optional

MANIFEST = "client.json"
RESCAN_SECONDS = 10.0      # a folder added while the server runs shows up this fast
BUILD_TIMEOUT = 180.0
JOIN_TIMEOUT = 8.0

_verified: dict[tuple, bool] = {}     # (path, args) -> the tool really runs
_verify_lock = threading.Lock()


def _tool_works(path: str, args: list) -> bool:
    """Purpose: true if an executable exists and really runs.  macOS ships stub
    `java`/`javac` binaries that exist but only print an error, so finding the
    file is not enough.  Inputs: absolute path and the arguments that should
    make it exit 0.  Outputs: bool, cached per (path, args)."""
    key = (path, tuple(args))
    with _verify_lock:
        if key in _verified:
            return _verified[key]
    try:
        ok = subprocess.run([path, *args], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            timeout=20).returncode == 0
    except (OSError, subprocess.SubprocessError):
        ok = False
    with _verify_lock:
        _verified[key] = ok
    return ok


def _subst(value, mapping: dict):
    """Purpose: put the placeholders into a string, a list of strings or a dict
    of them.  Inputs: the value and {name: replacement}.  Outputs: the same
    shape with `{name}` replaced.  Unknown placeholders are left alone, so a
    manifest with a typo fails loudly when the command runs instead of
    silently losing an argument.

    A replacement may itself be a list (`{sources}` is): an argument that is
    exactly that placeholder becomes one argument per item, so a compiler is
    handed four file names rather than one string with spaces in it."""
    if isinstance(value, str):
        for key, replacement in mapping.items():
            if isinstance(replacement, (list, tuple)):
                replacement = " ".join(str(item) for item in replacement)
            value = value.replace("{" + key + "}", str(replacement))
        return value
    if isinstance(value, list):
        out = []
        for item in value:
            spliced = (isinstance(item, str) and item.startswith("{") and item.endswith("}")
                       and isinstance(mapping.get(item[1:-1]), (list, tuple)))
            if spliced:
                out.extend(str(x) for x in mapping[item[1:-1]])
            else:
                out.append(_subst(item, mapping))
        return out
    if isinstance(value, dict):
        return {k: _subst(v, mapping) for k, v in value.items()}
    return value


def _child_log(root: str, kind: str) -> str:
    """Purpose: where a launched client's output goes (results/clients/)."""
    folder = os.path.join(root, "results", "clients")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, f"{kind}-{int(time.time())}.log")


def _spawn(cmd: list, log_path: str, cwd: str, env: dict) -> subprocess.Popen:
    """Purpose: start a client with stdout and stderr appended to a log file,
    never to a pipe nobody reads (a full pipe would block the child forever)."""
    log = open(log_path, "a", encoding="utf-8")
    log.write(f"\n$ {' '.join(cmd)}\n")
    log.flush()
    child = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, text=True, cwd=cwd, env=env)
    child.log_path = log_path       # type: ignore[attr-defined]
    return child


class ClientSpec:
    """One client folder: what it is, whether this machine can run it, and how
    to launch it for a seat."""

    def __init__(self, root: str, folder: str, data: dict) -> None:
        """Inputs: the repository root, the client's folder (absolute) and the
        parsed manifest.  Raises ValueError if a required key is missing."""
        self.root = root
        self.dir = folder
        self.name = os.path.basename(folder.rstrip(os.sep))
        self.language = str(data.get("language") or "").strip()
        self.source = str(data.get("file") or "").strip()
        self.manifest_key = json.dumps(data, sort_keys=True, default=str)
        run = data.get("run")
        if not self.language or not self.source or not run:
            raise ValueError("a manifest needs language, file and run")
        if not isinstance(run, list) or not all(isinstance(x, str) for x in run):
            raise ValueError("run must be a list of strings")
        self.kind = str(data.get("kind") or f"client-{self.name}")
        self.label = str(data.get("label") or f"{self.language} client (sample strategy)")
        self.edit = str(data.get("edit") or "")
        self.note = str(data.get("note") or "")
        self.badge = str(data.get("badge") or self.language[:2])
        self.color = str(data.get("color") or "")
        self.server_bots = bool(data.get("server_bots"))
        self.args_style = str(data.get("args") or "flags").lower()
        self.build_timeout = float(data.get("build_timeout") or BUILD_TIMEOUT)
        self.join_timeout = float(data.get("join_timeout") or JOIN_TIMEOUT)
        self.tools_wanted = {str(k): ([v] if isinstance(v, str) else [str(x) for x in v])
                             for k, v in (data.get("tools") or {}).items()}
        self.verify = {str(k): ([] if v is None else list(v)) for k, v in (data.get("verify") or {}).items()}
        self.path = os.path.join(folder, self.source)
        self.rel_path = os.path.relpath(self.path, root).replace(os.sep, "/")
        self.rel_dir = os.path.relpath(folder, root).replace(os.sep, "/") + "/"
        self.output = str(data.get("output") or "")
        self.out_path = os.path.join(folder, self.output) if self.output else ""
        patterns = data.get("sources") or []
        self.sources = [patterns] if isinstance(patterns, str) else [str(p) for p in patterns]
        self._build = list(data.get("build") or [])
        self._run = list(run)
        self._env = {str(k): str(v) for k, v in (data.get("env") or {}).items()}
        self._command = str(data.get("command") or "")
        self._lock = threading.Lock()          # one build at a time per client
        self.tools: dict[str, str] = {}
        self.missing: list[str] = []
        self.refresh()

    # ---------------------------------------------------------------- state

    def _base_mapping(self) -> dict:
        mapping = {"file": self.path, "dir": self.dir, "root": self.root,
                   "out": self.out_path or self.dir, "python": sys.executable,
                   "sources": self.source_paths()}
        mapping.update(self.tools)
        return mapping

    def source_paths(self) -> list:
        """Purpose: every file a build has to compile, for `{sources}`.
        Outputs: absolute paths, sorted, the entry point first and never
        repeated; just the entry point when the manifest names no globs (a
        one-file client, which is still the common case)."""
        found = []
        for pattern in self.sources:
            found += glob.glob(os.path.join(self.dir, pattern), recursive=True)
        files = sorted({os.path.abspath(p) for p in found if os.path.isfile(p)})
        entry = os.path.abspath(self.path)
        if entry in files:
            files.remove(entry)
        return ([entry] + files) if (files or os.path.isfile(entry)) else []

    def refresh(self) -> None:
        """Purpose: look for the tools this client needs, so a language
        installed after the server started still shows up.
        Side effects: fills self.tools and self.missing (verification runs are
        cached, so this stays cheap enough to call on every lobby request)."""
        found, missing = {}, []
        for name, candidates in self.tools_wanted.items():
            for candidate in candidates:
                where = shutil.which(candidate)
                if not where:
                    continue
                if name in self.verify and not _tool_works(where, self.verify[name]):
                    continue          # it is on the PATH but it does not run
                found[name] = where
                break
            else:
                missing.append(" or ".join(candidates))
        self.tools, self.missing = found, missing

    @property
    def file_count(self) -> int:
        """How many files the team wrote, for the lobby: the manifest and
        anything the build produced are the server's, not theirs."""
        built = os.path.abspath(self.out_path) if self.out_path else ""
        total = 0
        for folder, dirs, files in os.walk(self.dir):
            dirs[:] = [d for d in dirs if d not in ("out", "target", "__pycache__", ".git")]
            for name in files:
                if name == MANIFEST or os.path.abspath(os.path.join(folder, name)) == built:
                    continue
                total += 1
        return total

    @property
    def prebuilt(self) -> bool:
        """True when the build artifact is already there, so the client can run
        without its compiler (a binary handed out with the repository)."""
        return bool(self.out_path) and os.path.exists(self.out_path)

    def _build_only(self) -> set:
        """Purpose: the tools needed only to build, never to run.  A compiler
        the run command does not mention (g++, rustc, go, javac) is one; the
        java runtime is not, because `run` launches it, and nor is curl, which
        the shell and Lua clients call without a placeholder."""
        used = lambda argv: {n for n in self.tools_wanted
                             if any("{" + n + "}" in part for part in argv)}
        return used(self._build) - used(self._run)

    def _missing(self, names) -> list:
        return [" or ".join(self.tools_wanted[n]) for n in sorted(names) if n not in self.tools]

    @property
    def available(self) -> bool:
        """A client can be launched when its source is there, everything it
        needs at RUN time is installed, and either its compiler is installed or
        the artifact is already built and current."""
        if not os.path.isfile(self.path):
            return False
        build_only = self._build_only()
        if self._missing(set(self.tools_wanted) - build_only):
            return False
        return not (self._missing(build_only) and self._stale())

    @property
    def reason(self) -> str:
        """Why this client cannot be launched here, in words for the lobby."""
        if not os.path.isfile(self.path):
            return f"{self.rel_path} is missing"
        build_only = self._build_only()
        need = self._missing(set(self.tools_wanted) - build_only)
        if not need and self._stale():
            need = self._missing(build_only)
        return "needs " + ", ".join(need) if need else ""

    @property
    def command(self) -> str:
        """The line a team would type, for the lobby's Strategies list."""
        if self._command:
            return _subst(self._command, {"file": self.rel_path, "dir": self.rel_dir,
                                          "out": self.output or "client", "python": "python3"})
        return " ".join(self._run_display()) + " --game ID --name NAME"

    def _run_display(self) -> list:
        return _subst(self._run, {"file": self.rel_path, "dir": self.rel_dir,
                                  "out": self.output or "client", "python": "python3",
                                  **{k: k for k in self.tools_wanted}})

    def to_dict(self) -> dict:
        """The shape /api/strategies publishes for one client."""
        return {"kind": self.kind, "language": self.language, "file": self.rel_path,
                "folder": self.rel_dir, "edit": self.edit, "note": self.note,
                "files": self.file_count,
                "run": self.command, "available": self.available, "reason": self.reason,
                "badge": self.badge, "color": self.color, "tools": sorted(self.tools_wanted)}

    # ---------------------------------------------------------------- build & run

    def _stale(self) -> bool:
        """True when the artifact must be rebuilt: no build step means never.
        Any source being newer than the artifact counts, so editing file four
        of five rebuilds the client."""
        if not self._build or not self.out_path:
            return bool(self._build)
        if not os.path.exists(self.out_path):
            return True
        newest = max((os.path.getmtime(p) for p in self.source_paths()
                      if os.path.exists(p)), default=0.0)
        return os.path.getmtime(self.out_path) < newest

    def build(self) -> None:
        """Purpose: compile the client if its source is newer than its artifact.
        Side effects: runs the manifest's build command in the client's folder.
        Raises RuntimeError with the compiler's own message if it fails."""
        with self._lock:
            if not self._stale():
                return
            if self.missing:
                raise RuntimeError(self.reason)
            cmd = _subst(self._build, self._base_mapping())
            if self.out_path:
                os.makedirs(os.path.dirname(self.out_path) or self.dir, exist_ok=True)
            try:
                subprocess.run(cmd, check=True, timeout=self.build_timeout, cwd=self.dir,
                               env=self.environment(), stdout=subprocess.DEVNULL,
                               stderr=subprocess.PIPE, text=True)
            except subprocess.CalledProcessError as exc:
                raise RuntimeError(f"could not build {self.kind}: {(exc.stderr or '')[-400:].strip()}") from None
            except subprocess.TimeoutExpired:
                raise RuntimeError(f"building {self.kind} took longer than {self.build_timeout:g}s") from None
            except OSError as exc:
                raise RuntimeError(f"could not build {self.kind}: {exc}") from None

    def environment(self, **extra: str) -> dict:
        """Purpose: the environment a build or a client runs in: ours, plus the
        manifest's `env`, plus the seat.  An empty value *removes* the variable
        rather than passing "" along: a client that parses CARDNIM_AVATAR as a
        number must not be handed an empty string, and a stale value inherited
        from our own environment must not leak into the child."""
        env = dict(os.environ)
        env.update(_subst(self._env, self._base_mapping()))
        for key, value in extra.items():
            if value is None or value == "":
                env.pop(key, None)
            else:
                env[key] = value
        return env

    def argv(self, server_url: str, game_id: str, seat: int, name: str, avatar) -> list:
        """Purpose: the exact command line for one seat.
        Outputs: argv with the placeholders filled in and, unless the manifest
        asked for `env` or used the placeholders itself, the standard flags
        appended."""
        seat_map = {"server": server_url, "game": game_id, "seat": str(seat),
                    "name": name, "avatar": str(avatar or "")}
        cmd = _subst(self._run, {**self._base_mapping(), **seat_map})
        used = any("{" + k + "}" in part for part in self._run for k in seat_map)
        if not used and self.args_style != "env":
            cmd += ["--server", server_url, "--game", game_id, "--seat", str(seat), "--name", name]
            if avatar:
                cmd += ["--avatar", str(avatar)]
        return cmd

    def launch(self, server_url: str, game_id: str, seat: int, name: str, avatar=None) -> subprocess.Popen:
        """Purpose: build if needed, then start the client so it joins the seat
        over HTTP exactly as a team's own program would.
        Outputs: the Popen, with `log_path` pointing at results/clients/.
        Raises RuntimeError if the client cannot be built or started."""
        if not os.path.isfile(self.path):
            raise RuntimeError(self.reason)
        self.build()
        if self.missing and not self.prebuilt:
            raise RuntimeError(self.reason)
        env = self.environment(CARDNIM_SERVER=server_url, CARDNIM_GAME=game_id,
                               CARDNIM_SEAT=str(seat), CARDNIM_NAME=name,
                               CARDNIM_AVATAR=str(avatar or ""))
        cmd = self.argv(server_url, game_id, seat, name, avatar)
        try:
            return _spawn(cmd, _child_log(self.root, self.name), self.dir, env)
        except OSError as exc:
            raise RuntimeError(f"could not start {self.kind}: {exc}") from None


class ClientRegistry:
    """Every client folder the server can launch, re-scanned as folders come
    and go so a strategy dropped in during a competition shows up without a
    restart.  Behaves like a read-only dict of kind -> ClientSpec."""

    def __init__(self, root: str, dirs: Optional[list] = None) -> None:
        """Inputs: the repository root and the directories to scan (default
        `clients/` under the root)."""
        self.root = root
        self.dirs = [d for d in (dirs or [os.path.join(root, "clients")])]
        self._specs: dict[str, ClientSpec] = {}
        self._scanned = 0.0
        self._lock = threading.Lock()
        self.errors: list[str] = []
        self.scan()

    def scan(self) -> dict:
        """Purpose: read every clients/*/client.json.
        Outputs: {kind: ClientSpec}.  Side effects: keeps the ClientSpec of a
        folder that is still there (so its build lock and cached tools
        survive); records unreadable manifests in self.errors."""
        specs, errors = {}, []
        for base in self.dirs:
            if not os.path.isdir(base):
                continue
            for entry in sorted(os.listdir(base)):
                folder = os.path.join(base, entry)
                manifest = os.path.join(folder, MANIFEST)
                if not os.path.isfile(manifest):
                    continue
                try:
                    with open(manifest, encoding="utf-8") as fh:
                        data = json.load(fh)
                    spec = ClientSpec(self.root, folder, data)
                except (OSError, ValueError) as exc:
                    errors.append(f"{os.path.relpath(manifest, self.root)}: {exc}")
                    continue
                old = self._specs.get(spec.kind)
                if old is not None and old.dir == spec.dir and old.manifest_key == spec.manifest_key:
                    old.refresh()
                    specs[spec.kind] = old
                else:
                    specs[spec.kind] = spec
        with self._lock:
            self._specs, self._scanned, self.errors = specs, time.time(), errors
        return specs

    def all(self) -> dict:
        """Purpose: the current clients, re-scanning at most every RESCAN_SECONDS."""
        if time.time() - self._scanned > RESCAN_SECONDS:
            self.scan()
        return dict(self._specs)

    # dict-ish, so callers read like they do for the server's own bots
    def __contains__(self, kind) -> bool:
        return kind in self.all()

    def __getitem__(self, kind) -> ClientSpec:
        return self.all()[kind]

    def __len__(self) -> int:
        return len(self.all())

    def __iter__(self):
        return iter(self.all())

    def get(self, kind, default=None):
        return self.all().get(kind, default)

    def keys(self):
        return self.all().keys()

    def values(self):
        return self.all().values()

    def items(self):
        return self.all().items()


# ============================================================ uploaded strategies
#
# A team on another device picks their strategy in the browser and the server
# writes it into the uploads folder with a generated manifest, so from that
# moment it is an ordinary client: the registry finds it, builds it and
# launches it for a seat like any other.
#
# A strategy is a *folder*, not a file.  A team may send one file, a handful of
# files, a folder, or a .zip of one; the server keeps the layout they sent,
# picks the entry point and writes the manifest around it.  What it never takes
# from them is the manifest itself: the commands the server runs are the ones
# it generated from the entry point's extension, never ones the uploader wrote.
#
# The server then *runs* that code.  Accepting it is accepting whatever the
# uploader wrote, which is why cardnim_server.py keeps this behind
# --accept-uploads and why the entry point has to be a language listed here.

UPLOAD_MAX_BYTES = 1024 * 1024            # one file: a strategy is a page of code
UPLOAD_MAX_TOTAL_BYTES = 8 * 1024 * 1024  # a whole submission, unpacked
UPLOAD_MAX_FILES = 200                    # files in one submission
UPLOAD_MAX_DEPTH = 8                      # folders deep inside the submission
UPLOAD_SLUG_MAX = 40

# Names that mean "start here", tried in this order when a submission has more
# than one file the server could run.
ENTRY_STEMS = ("main", "client", "strategy", "bot", "player", "run", "entry",
               "start", "app", "index", "solution", "agent")

# Editor and archive litter, dropped instead of being refused: a folder picked
# in a browser or zipped by a Mac carries these whether the team meant it or not.
JUNK_NAMES = {".ds_store", "thumbs.db", ".gitignore", ".gitattributes"}
JUNK_DIRS = {"__macosx", "__pycache__", ".git", ".svn", ".hg", ".idea", ".vscode",
             "node_modules", ".pytest_cache", ".mypy_cache", ".venv", "venv"}


class UploadTooLarge(ValueError):
    """A submission over one of the size limits, so the server can answer 413
    rather than 400."""


# extension -> the manifest to write.  These are the recipes the shipped
# clients use, with {file} pointing at the entry point and {sources} at every
# file a compiler has to be handed.
UPLOAD_LANGUAGES: dict[str, dict] = {
    ".py": {"language": "Python", "badge": "Py", "color": "#3572a5",
            "tools": {"python": "python3"}, "run": ["{python}", "{file}"]},
    ".js": {"language": "JavaScript", "badge": "JS", "color": "#b8a90a",
            "tools": {"node": "node"}, "run": ["{node}", "{file}"]},
    ".rb": {"language": "Ruby", "badge": "Rb", "color": "#cc342d",
            "tools": {"ruby": "ruby"}, "run": ["{ruby}", "{file}"]},
    ".sh": {"language": "Shell", "badge": "sh", "color": "#4d4d4d",
            "tools": {"sh": "sh", "curl": "curl", "awk": "awk"},
            "run": ["{sh}", "{file}"]},
    ".q": {"language": "q", "badge": "q", "color": "#1f6f8b",
           "tools": {"q": "q"}, "run": ["{q}", "{file}", "-q"], "args": "env"},
    ".jl": {"language": "Julia", "badge": "Jl", "color": "#9558b2",
            "tools": {"julia": "julia"},
            "run": ["{julia}", "--startup-file=no", "{file}"], "join_timeout": 25},
    ".cpp": {"language": "C++", "badge": "C++", "color": "#f34b7d",
             "tools": {"cxx": ["g++", "clang++"]},
             "sources": ["**/*.cpp", "**/*.cc", "**/*.cxx"],
             "build": ["{cxx}", "-std=c++17", "-O2", "{sources}", "-o", "{out}"],
             "output": "player", "run": ["{out}"]},
    ".cc": {"language": "C++", "badge": "C++", "color": "#f34b7d",
            "tools": {"cxx": ["g++", "clang++"]},
            "sources": ["**/*.cpp", "**/*.cc", "**/*.cxx"],
            "build": ["{cxx}", "-std=c++17", "-O2", "{sources}", "-o", "{out}"],
            "output": "player", "run": ["{out}"]},
    ".java": {"language": "Java", "badge": "Ja", "color": "#b07219",
              "tools": {"javac": "javac", "java": "java"},
              "verify": {"javac": ["-version"], "java": ["-version"]},
              "sources": ["**/*.java"],
              "build": ["{javac}", "-d", "{dir}/out", "{sources}"]},
    ".c": {"language": "C", "badge": "C", "color": "#555555",
           "tools": {"cc": ["cc", "gcc", "clang"]},
           "sources": ["**/*.c"],
           "build": ["{cc}", "-O2", "-o", "{out}", "{sources}"],
           "output": "player", "run": ["{out}"]},
    ".go": {"language": "Go", "badge": "Go", "color": "#00add8",
            "tools": {"go": "go"},
            "sources": ["*.go"],
            "build": ["{go}", "build", "-o", "{out}", "{sources}"],
            "output": "player", "run": ["{out}"],
            "env": {"GOFLAGS": "-mod=mod"}},
    ".rs": {"language": "Rust", "badge": "Rs", "color": "#dea584",
            "tools": {"rustc": "rustc"},
            "sources": ["**/*.rs"],
            "build": ["{rustc}", "-O", "--edition", "2021", "-o", "{out}", "{file}"],
            "output": "player", "run": ["{out}"]},
    ".R": {"language": "R", "badge": "R", "color": "#276dc3",
           "tools": {"rscript": "Rscript"}, "run": ["{rscript}", "--vanilla", "{file}"]},
    ".pl": {"language": "Perl", "badge": "Pl", "color": "#0298c3",
            "tools": {"perl": "perl"}, "run": ["{perl}", "{file}"]},
    ".php": {"language": "PHP", "badge": "PHP", "color": "#777bb4",
             "tools": {"php": "php"}, "run": ["{php}", "{file}"]},
    ".lua": {"language": "Lua", "badge": "Lua", "color": "#000080",
             "tools": {"lua": ["lua", "lua5.4", "luajit"], "curl": "curl"},
             "run": ["{lua}", "{file}"]},
    ".ts": {"language": "TypeScript", "badge": "TS", "color": "#3178c6",
            "tools": {"deno": "deno"},
            "run": ["{deno}", "run", "--quiet", "--allow-net", "--allow-env",
                    "--allow-read", "{file}"]},
}


def upload_languages() -> list:
    """Purpose: what the browser should offer in its file picker.
    Outputs: [{extension, language}], one row per accepted extension."""
    return [{"extension": ext, "language": spec["language"]}
            for ext, spec in sorted(UPLOAD_LANGUAGES.items())]


def slugify(text: str, fallback: str = "strategy") -> str:
    """Purpose: a safe folder name from a team's own words.
    Outputs: lower-case, digits, letters and dashes only, never empty, never
    a path (so an upload cannot escape the uploads folder)."""
    slug = re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")
    return slug[:UPLOAD_SLUG_MAX] or fallback


def language_for(name: str) -> Optional[str]:
    """Purpose: the extension key a file name would be run as, or None.
    `.R` is spelled with a capital, so the match is case-insensitive."""
    ext = os.path.splitext(name)[1]
    if ext in UPLOAD_LANGUAGES:
        return ext
    return next((k for k in UPLOAD_LANGUAGES if k.lower() == ext.lower()), None)


def safe_member(name: str) -> str:
    """Purpose: turn a path the uploader chose into one that can only land
    inside the team's folder.
    Inputs:  a member name from a browser, a zip or a query string.
    Outputs: a relative path with forward slashes, or "" for anything that
             should not be written at all (absolute paths, `..`, editor and
             archive litter, a manifest the uploader wrote).
    The server's own client.json is never taken from a submission: it is the
    file that says which commands to run."""
    raw = str(name or "").replace("\\", "/").strip()
    if not raw or raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        return ""
    parts = []
    for part in raw.split("/"):
        part = part.strip()
        if not part or part == ".":
            continue
        if part == ".." or part.lower() in JUNK_DIRS:
            return ""
        parts.append(re.sub(r"[^A-Za-z0-9._+-]", "_", part))
    if not parts or len(parts) > UPLOAD_MAX_DEPTH:
        return ""
    if parts[-1].lower() in JUNK_NAMES or parts[-1] == MANIFEST:
        return ""
    return "/".join(parts)


def unpack_zip(data: bytes) -> list:
    """Purpose: read a .zip submission into memory without trusting it.
    Outputs: [(relative path, bytes)] for the ordinary files in it.
    Raises ValueError if it is not a zip, UploadTooLarge if what it would
    unpack to is over the limits.  Symlinks, devices, absolute paths and `..`
    are dropped rather than written, so a zip cannot reach outside the folder
    it is unpacked into."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, OSError) as exc:
        raise ValueError(f"that .zip could not be read ({exc})") from None
    members, total = [], 0
    for info in archive.infolist():
        if info.is_dir():
            continue
        mode = (info.external_attr >> 16) & 0o170000
        if mode and mode != 0o100000:            # not a plain file: symlink, fifo, device
            continue
        name = safe_member(info.filename)
        if not name:
            continue
        total += info.file_size
        if total > UPLOAD_MAX_TOTAL_BYTES:       # checked before unpacking: no zip bomb
            raise UploadTooLarge(f"unpacked, that is more than "
                                 f"{UPLOAD_MAX_TOTAL_BYTES // 1024} KB")
        members.append((name, info))
    if len(members) > UPLOAD_MAX_FILES:
        raise UploadTooLarge(f"that is {len(members)} files; the limit is {UPLOAD_MAX_FILES}")
    files = []
    for name, info in members:
        try:
            files.append((name, archive.read(info)))
        except (zipfile.BadZipFile, RuntimeError, OSError) as exc:
            raise ValueError(f"{name} could not be unpacked ({exc})") from None
    if not files:
        raise ValueError("that .zip holds no files")
    return files


def _strip_common_root(files: list) -> list:
    """Purpose: a zip or a picked folder usually carries one wrapping folder
    ("my-bot/main.py"); unwrap it so the entry point sits where the manifest
    expects it.  Only a folder every file shares is removed, and only while
    every path still has one, so a submission never unwraps to nothing."""
    while files:
        roots = {name.split("/", 1)[0] for name, _ in files}
        if len(roots) != 1 or not all("/" in name for name, _ in files):
            return files
        files = [(name.split("/", 1)[1], data) for name, data in files]
    return files


# What a program that starts somewhere looks like, in the languages where the
# file name alone does not say.  Used only to break a tie.
MAIN_SIGNS = (
    r"static\s+void\s+main\s*\(",              # Java
    r"\bint\s+main\s*\(",                      # C, C++
    r"__name__\s*==\s*['\"]__main__['\"]",      # Python
    r"\bfunc\s+main\s*\(",                     # Go
    r"\bfn\s+main\s*\(",                       # Rust
)


def pick_entry(names: list, hint: str = "", read=None) -> str:
    """Purpose: decide which file of a submission is the program to run.
    Inputs:  the relative paths in it, the entry the uploader asked for, and
             read(name) -> bytes for looking inside one.
    Outputs: one of the names.
    Rules, in order: what the team asked for; the file nearest the top called
    main/client/strategy/bot/...; the only one that has a main() in it; the
    only runnable file there is.  Anything else is ambiguous and says so,
    because guessing wrong here means running the wrong program in a
    competition."""
    runnable = [n for n in names if language_for(n)]
    if not runnable:
        accepted = ", ".join(sorted(UPLOAD_LANGUAGES))
        raise ValueError("none of those files is a program this server can run; "
                         f"one of them has to end in {accepted}")
    wanted = safe_member(hint)
    if wanted:
        if wanted not in names:
            raise ValueError(f"{wanted} is not one of the files you sent")
        if not language_for(wanted):
            raise ValueError(f"{wanted} is not a language this server can run")
        return wanted
    depth = lambda n: n.count("/")
    named = [n for n in runnable
             if os.path.splitext(os.path.basename(n))[0].lower() in ENTRY_STEMS]
    if named:
        best = min(depth(n) for n in named)
        shallowest = [n for n in named if depth(n) == best]
        if len(shallowest) == 1:
            return shallowest[0]
        by_stem = {os.path.splitext(os.path.basename(n))[0].lower(): n for n in shallowest}
        for stem in ENTRY_STEMS:                 # main beats client beats strategy
            if stem in by_stem and sum(1 for n in shallowest
                                       if os.path.splitext(os.path.basename(n))[0].lower() == stem) == 1:
                return by_stem[stem]
        runnable = shallowest
    if len(runnable) == 1:
        return runnable[0]
    if read is not None:                         # the file with the main() in it
        starts = []
        for name in runnable:
            try:
                text = read(name).decode("utf-8", "replace")
            except (OSError, KeyError):
                continue
            if any(re.search(sign, text) for sign in MAIN_SIGNS):
                starts.append(name)
        if len(starts) == 1:
            return starts[0]
        if starts:
            runnable = starts
    listed = ", ".join(sorted(runnable)[:6])
    ext = os.path.splitext(runnable[0])[1]
    suggestion = "Main.java" if ext == ".java" else "main" + ext
    raise ValueError(f"which file starts your bot? ({listed}) — call it {suggestion} "
                     f"or say which one to run")


def _manifest_for(entry: str, names: list, read) -> dict:
    """Purpose: the manifest for a submission whose entry point is `entry`.
    Inputs:  the entry path, every path in the submission and read(name) ->
             bytes for looking inside a file.
    Outputs: the manifest to write, already adjusted for the things a build
             only knows by looking: the class a Java file declares, a Go
             module, a Cargo project."""
    ext = language_for(entry)
    manifest = dict(UPLOAD_LANGUAGES[ext])
    stem = os.path.splitext(os.path.basename(entry))[0]
    folder = os.path.dirname(entry)

    if ext == ".java":
        # javac insists the public class matches the file name, and a file that
        # declares a package is run by its full name from the output root.
        source = read(entry).decode("utf-8", "replace")
        found = re.search(r"^\s*package\s+([A-Za-z_][\w.]*)\s*;", source, re.M)
        package = found.group(1) if found else ""
        main = f"{package}.{stem}" if package else stem
        manifest["output"] = "out/" + main.replace(".", "/") + ".class"
        manifest["run"] = ["{java}", "-cp", "{dir}/out", main]
    elif ext == ".go" and any(n == "go.mod" or n.endswith("/go.mod") for n in names):
        # a module builds as a package, not as a list of files: that is the only
        # way a team's own sub-packages are found
        gomod = next(n for n in names if n == "go.mod" or n.endswith("/go.mod"))
        module_dir = os.path.dirname(gomod)
        package = os.path.relpath(folder or ".", module_dir or ".").replace(os.sep, "/")
        target = "." if package in (".", "") or package.startswith("..") else "./" + package
        prefix = ["{go}", "-C", "{dir}/" + module_dir] if module_dir else ["{go}"]
        manifest["sources"] = ["**/*.go"]
        manifest["build"] = prefix + ["build", "-o", "{out}", target]
    elif ext == ".rs" and any(n == "Cargo.toml" or n.endswith("/Cargo.toml") for n in names):
        cargo = next(n for n in names if n == "Cargo.toml" or n.endswith("/Cargo.toml"))
        text = read(cargo).decode("utf-8", "replace")
        found = re.search(r"^\s*name\s*=\s*[\"']([^\"']+)[\"']", text, re.M)
        name = found.group(1) if found else "player"
        manifest["tools"] = {"cargo": "cargo"}
        manifest["sources"] = ["**/*.rs", "**/Cargo.toml"]
        manifest["build"] = ["{cargo}", "build", "--release", "--quiet",
                             "--manifest-path", "{dir}/" + cargo]
        manifest["output"] = os.path.join(os.path.dirname(cargo), "target", "release",
                                          name).replace("\\", "/")
        manifest["run"] = ["{out}"]
        manifest["build_timeout"] = 600
    return manifest


def save_upload(uploads_dir: str, filename: str, data: bytes, team: str = "",
                entry: str = "") -> dict:
    """Purpose: turn one uploaded file into a client folder.  A .zip is
    unpacked; anything else is the strategy itself.
    Inputs:  where uploads live, the name the browser reported, the bytes, the
             team name the folder should be named after, and the entry point
             the team asked for (a .zip may hold more than one candidate).
    Outputs: what save_bundle returns.
    Raises ValueError for a file this server cannot run, an empty file or a
    broken archive, UploadTooLarge for one over the limits."""
    base = os.path.basename(str(filename or "").replace("\\", "/")).strip()
    if not data:
        raise ValueError("the file is empty")
    if base.lower().endswith(".zip"):
        return save_bundle(uploads_dir, unpack_zip(data), team, entry)
    if len(data) > UPLOAD_MAX_BYTES:
        raise UploadTooLarge(f"the file is larger than {UPLOAD_MAX_BYTES // 1024} KB")
    if not language_for(base):
        ext = os.path.splitext(base)[1]
        raise ValueError("cannot run a " + (ext or "file with no extension") + "; accepted: "
                         + ", ".join(sorted(UPLOAD_LANGUAGES)) + ", .zip")
    return save_bundle(uploads_dir, [(base, data)], team, entry)


def save_bundle(uploads_dir: str, files: list, team: str = "", entry: str = "") -> dict:
    """Purpose: turn a whole submission -- one file or fifty, in as many
    folders as the team used -- into a client the server can build and launch.
    Inputs:  where uploads live, [(relative path, bytes)] as sent, the team
             name the folder is named after, and the entry point the team
             asked for ("" to work it out).
    Outputs: {kind, language, folder, file, files, names} describing the new
             client; `file` is the entry point, `names` what was written.
    Side effects: creates <uploads>/<slug>/ holding the submission and a
             generated client.json.  Re-uploading for the same team replaces
             that folder, so a team can fix a bug and send it again.
    Raises ValueError for a submission this server cannot run and
    UploadTooLarge for one over the limits."""
    cleaned, seen = [], set()
    for name, data in files:
        safe = safe_member(name)
        if not safe or safe in seen:
            continue
        seen.add(safe)
        cleaned.append((safe, data))
    cleaned = _strip_common_root(cleaned)
    if not cleaned:
        raise ValueError("nothing in that submission could be saved")
    if len(cleaned) > UPLOAD_MAX_FILES:
        raise UploadTooLarge(f"that is {len(cleaned)} files; the limit is {UPLOAD_MAX_FILES}")
    total = sum(len(data) for _, data in cleaned)
    if total > UPLOAD_MAX_TOTAL_BYTES:
        raise UploadTooLarge(f"that is {total // 1024} KB; the limit is "
                             f"{UPLOAD_MAX_TOTAL_BYTES // 1024} KB")
    if not total:
        raise ValueError("every one of those files is empty")
    for name, data in cleaned:
        if len(data) > UPLOAD_MAX_BYTES:
            raise UploadTooLarge(f"{name} is larger than {UPLOAD_MAX_BYTES // 1024} KB")

    names = [name for name, _ in cleaned]
    contents = dict(cleaned)
    hint = safe_member(entry)
    while hint and hint not in names and "/" in hint:
        hint = hint.split("/", 1)[1]             # the wrapping folder was stripped above
    entry_path = pick_entry(names, hint, lambda n: contents[n])
    if not contents[entry_path].strip():
        raise ValueError(f"{entry_path} is empty")
    manifest = _manifest_for(entry_path, names, lambda n: contents[n])

    slug = slugify(team or os.path.splitext(os.path.basename(entry_path))[0])
    folder = os.path.join(uploads_dir, slug)
    os.makedirs(folder, exist_ok=True)
    for stale in os.listdir(folder):          # one strategy per team, not a pile
        try:
            path = os.path.join(folder, stale)
            shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)
        except OSError:
            pass

    root = os.path.realpath(folder)
    for name, data in cleaned:
        path = os.path.join(folder, *name.split("/"))
        if not os.path.realpath(os.path.dirname(path)).startswith(root):
            continue                          # belt and braces: safe_member already refused these
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data)

    manifest.update({"kind": f"upload-{slug}", "file": entry_path,
                     "label": f"{team.strip() or slug} ({manifest['language']})",
                     "note": f"uploaded strategy, {len(cleaned)} file"
                             f"{'s' if len(cleaned) != 1 else ''}",
                     "uploaded": True})
    with open(os.path.join(folder, MANIFEST), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    return {"kind": manifest["kind"], "language": manifest["language"],
            "folder": folder, "file": entry_path, "files": len(cleaned),
            "names": sorted(names)}
