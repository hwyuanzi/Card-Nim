"""Clients in any language: the client.json manifests, the registry that finds
them, and a brand-new language folder playing a real game over HTTP.

The point of these tests is that no language is special-cased anywhere: a
folder with a manifest is all the server needs.
Run:  python3 -m pytest tests/ -q"""

import json
import os
import re
import stat
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import cardnim_server as srv  # noqa: E402
import clients as cl  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLIENTS = os.path.join(ROOT, "clients")


def call(base, method, path, body=None, token=None):
    from test_api import call as _call
    return _call(base, method, path, body, token)


# ============================================================ the manifests in the repo

def test_every_client_folder_has_a_usable_manifest():
    """Each clients/<language>/ folder describes itself, and the source file it
    names is really there."""
    folders = [f for f in sorted(os.listdir(CLIENTS)) if os.path.isdir(os.path.join(CLIENTS, f))]
    assert len(folders) >= 4, folders
    for folder in folders:
        path = os.path.join(CLIENTS, folder, cl.MANIFEST)
        assert os.path.isfile(path), f"clients/{folder}/ has no {cl.MANIFEST}"
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        spec = cl.ClientSpec(ROOT, os.path.join(CLIENTS, folder), data)
        assert spec.kind == f"client-{folder}"
        assert os.path.isfile(spec.path), f"{spec.rel_path} is missing"
        assert spec.language and spec.edit and spec.note


def test_registry_finds_every_language():
    reg = cl.ClientRegistry(ROOT, [CLIENTS])
    languages = {spec.language for spec in reg.values()}
    assert {"Python", "C++", "Java", "q", "JavaScript", "Ruby", "Shell"} <= languages
    assert reg.errors == []
    # Python is always available: it is running these tests
    assert reg["client-python"].available


def test_an_unavailable_language_says_why_instead_of_disappearing():
    """A team without the toolchain must see the language and the reason, not
    an empty list."""
    spec = cl.ClientSpec(ROOT, os.path.join(CLIENTS, "python"), {
        "language": "Piet", "file": "client.py", "run": ["{piet}", "{file}"],
        "tools": {"piet": "no-such-compiler-anywhere"}})
    assert spec.available is False
    assert spec.reason == "needs no-such-compiler-anywhere"


def test_a_broken_manifest_is_reported_and_the_others_still_load(tmp_path):
    (tmp_path / "good").mkdir()
    (tmp_path / "good" / "run.py").write_text("")
    (tmp_path / "good" / cl.MANIFEST).write_text(json.dumps(
        {"language": "Good", "file": "run.py", "run": ["{python}", "{file}"]}))
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / cl.MANIFEST).write_text("{ not json")
    (tmp_path / "incomplete").mkdir()
    (tmp_path / "incomplete" / cl.MANIFEST).write_text(json.dumps({"language": "Nope"}))

    reg = cl.ClientRegistry(str(tmp_path), [str(tmp_path)])
    assert set(reg.keys()) == {"client-good"}
    assert len(reg.errors) == 2
    assert any("broken" in e for e in reg.errors) and any("incomplete" in e for e in reg.errors)


# ============================================================ how a seat reaches the client

def _spec(tmp_path, **manifest):
    folder = tmp_path / "x"
    folder.mkdir(exist_ok=True)
    (folder / "s.txt").write_text("")
    data = {"language": "X", "file": "s.txt", "run": ["/bin/echo"]}
    data.update(manifest)
    return cl.ClientSpec(str(tmp_path), str(folder), data)


def test_flags_are_appended_by_default(tmp_path):
    spec = _spec(tmp_path, run=["run.sh", "{file}"])
    argv = spec.argv("http://h:1", "K7PX", 2, "Team A", 5)
    assert argv[:2] == ["run.sh", os.path.join(str(tmp_path), "x", "s.txt")]
    assert argv[2:] == ["--server", "http://h:1", "--game", "K7PX", "--seat", "2",
                        "--name", "Team A", "--avatar", "5"]


def test_a_manifest_can_place_the_arguments_itself(tmp_path):
    spec = _spec(tmp_path, run=["run.sh", "-g", "{game}", "-s", "{seat}", "-n", "{name}"])
    assert spec.argv("http://h:1", "K7PX", 1, "Team A", None) == \
        ["run.sh", "-g", "K7PX", "-s", "1", "-n", "Team A"]


def test_a_manifest_can_ask_for_the_environment_only(tmp_path):
    spec = _spec(tmp_path, run=["run.sh"], args="env")
    assert spec.argv("http://h:1", "K7PX", 1, "Team A", None) == ["run.sh"]


def test_the_environment_carries_the_seat_and_drops_empty_values(tmp_path):
    """A client that parses CARDNIM_AVATAR as a number must never be handed an
    empty string, and a stale value of ours must not leak into the child."""
    os.environ["CARDNIM_AVATAR"] = "13"
    try:
        spec = _spec(tmp_path, env={"EXTRA": "{dir}"})
        env = spec.environment(CARDNIM_GAME="K7PX", CARDNIM_SEAT="1", CARDNIM_AVATAR="")
        assert env["CARDNIM_GAME"] == "K7PX" and env["CARDNIM_SEAT"] == "1"
        assert "CARDNIM_AVATAR" not in env
        assert env["EXTRA"] == os.path.join(str(tmp_path), "x")
    finally:
        os.environ.pop("CARDNIM_AVATAR", None)


def test_a_build_runs_once_and_again_when_the_source_changes(tmp_path):
    folder = tmp_path / "b"
    folder.mkdir()
    (folder / "src.txt").write_text("one")
    spec = cl.ClientSpec(str(tmp_path), str(folder), {
        "language": "B", "file": "src.txt", "output": "built",
        "build": ["/bin/cp", "{file}", "{out}"], "run": ["/bin/cat", "{out}"]})
    spec.build()
    assert (folder / "built").read_text() == "one"
    (folder / "built").write_text("stale but newer")
    spec.build()                                   # artifact is newer: no rebuild
    assert (folder / "built").read_text() == "stale but newer"
    time.sleep(0.01)
    (folder / "src.txt").write_text("two")
    os.utime(folder / "src.txt", (time.time() + 1, time.time() + 1))
    spec.build()                                   # source is newer: rebuilt
    assert (folder / "built").read_text() == "two"


def test_a_failing_build_reports_the_compilers_own_message(tmp_path):
    folder = tmp_path / "c"
    folder.mkdir()
    (folder / "src.txt").write_text("")
    spec = cl.ClientSpec(str(tmp_path), str(folder), {
        "language": "C", "file": "src.txt", "output": "built",
        "build": ["/bin/sh", "-c", "echo 'syntax error on line 3' >&2; exit 1"],
        "run": ["{out}"]})
    with pytest.raises(RuntimeError) as exc:
        spec.build()
    assert "syntax error on line 3" in str(exc.value)


# ============================================================ a new language, end to end

# A complete Card Nim client written for this test in a language the server has
# never heard of.  It is a shell script, but nothing in the server knows that:
# it is found, launched and played exactly like C++ or Ruby.
NEW_LANGUAGE_CLIENT = r"""#!/bin/sh
# join, then play the smallest fitting card until the game ends
S="$CARDNIM_SERVER"; G="$CARDNIM_GAME"; N="$CARDNIM_NAME"; T=""
B="$S/api/games/$G"
F=$(mktemp)
curl -s -X POST --data-urlencode "name=$N" --data-urlencode "seat=$CARDNIM_SEAT" \
     "$B/join?format=text" -o "$F"
T=$(awk '$1=="token"{print $2}' "$F")
[ -n "$T" ] || { echo "join failed: $(cat "$F")" >&2; exit 1; }
while :; do
  curl -s --max-time 90 "$B/getstate?format=text&timeout=60&token=$T" -o "$F"
  [ "$(awk '$1=="status"{print $2}' "$F")" = "finished" ] && exit 0
  [ "$(awk '$1=="your_turn"{print $2}' "$F")" = "1" ] || continue
  ST=$(awk '$1=="stones"{print $2}' "$F")
  C=$(awk -v s="$ST" '$1=="your_cards"{for(i=2;i<=NF;i++) if($i<=s){print $i; exit}}' "$F")
  [ -n "$C" ] || C=$(awk '$1=="your_cards"{print $2}' "$F")
  curl -s -X POST "$B/move?format=text&token=$T&card=$C" -o /dev/null
done
"""


@pytest.fixture(scope="module")
def polyglot_server(tmp_path_factory):
    """A server told to look in an extra folder, the way --clients-dir does."""
    extra = tmp_path_factory.mktemp("extra-clients")
    store = srv.GameStore(results_dir=None, client_dirs=[str(extra)])
    server = srv.CardNimServer(("127.0.0.1", 0), store, quiet=True)
    t = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    store.local_url = base
    yield base, str(extra), store
    server.shutdown()
    server.server_close()


def test_a_language_added_while_the_server_runs_is_found_and_plays(polyglot_server):
    """Drop a folder in, and the new language is in /api/bots and can play a
    whole game -- no restart, no server change."""
    base, extra, store = polyglot_server
    assert "client-bandit" not in {b["kind"] for b in call(base, "GET", "/api/bots")[1]["bots"]}

    folder = os.path.join(extra, "bandit")
    os.makedirs(folder)
    script = os.path.join(folder, "play.sh")
    with open(script, "w", encoding="utf-8") as fh:
        fh.write(NEW_LANGUAGE_CLIENT)
    os.chmod(script, os.stat(script).st_mode | stat.S_IEXEC)
    with open(os.path.join(folder, cl.MANIFEST), "w", encoding="utf-8") as fh:
        json.dump({"language": "Bandit", "label": "Bandit client", "file": "play.sh",
                   "edit": "the awk line", "note": "smallest fitting card",
                   "badge": "Bd", "color": "#123456",
                   "tools": {"sh": "sh", "curl": "curl", "awk": "awk"},
                   "run": ["{sh}", "{file}"], "args": "env"}, fh)

    store.externals.scan()          # what the 10-second rescan does on its own
    bots = {b["kind"]: b for b in call(base, "GET", "/api/bots")[1]["bots"]}
    assert "client-bandit" in bots and bots["client-bandit"]["language"] == "Bandit"

    listed = next(c for c in call(base, "GET", "/api/strategies")[1]["clients"]
                  if c["language"] == "Bandit")
    assert listed["available"] and listed["badge"] == "Bd" and listed["color"] == "#123456"

    # it plays a whole game against a bot the server runs itself
    _, game = call(base, "POST", "/api/games", {"stones": 30, "cards": 10, "time_limit": 60})
    gid = game["id"]
    call(base, "POST", f"/api/games/{gid}/bot", {"kind": "greedy", "seat": 1, "delay": 0})
    status, state = call(base, "POST", f"/api/games/{gid}/bot", {"kind": "client-bandit", "seat": 2})
    assert status == 200, state
    assert state["players"][1]["bot"] is True

    deadline = time.time() + 40
    while time.time() < deadline:
        _, state = call(base, "GET", f"/api/games/{gid}")
        if state["status"] == "finished":
            break
        time.sleep(0.1)
    assert state["status"] == "finished" and state["winner"] in (1, 2), state["status"]
    assert any(m["seat"] == 2 for m in state["moves"]), "the new language never moved"


def test_two_different_languages_play_each_other_in_a_tournament(polyglot_server):
    """The competition case: a bracket whose entrants are programs in different
    languages, played start to finish by the server."""
    base, _, store = polyglot_server
    kinds = [b["kind"] for b in call(base, "GET", "/api/bots")[1]["bots"]
             if b["kind"].startswith("client-")]
    if len(kinds) < 2:
        pytest.skip(f"only {kinds} available on this machine")
    entering = kinds[:4]

    _, t = call(base, "POST", "/api/tournaments",
                {"stones": 25, "cards": 8, "time_limit": 60, "label": "Polyglot", "bot_delay": 0})
    tid = t["id"]
    for kind in entering:
        status, r = call(base, "POST", f"/api/tournaments/{tid}/join", {"kind": kind, "name": kind})
        assert status == 200, r
    status, t = call(base, "POST", f"/api/tournaments/{tid}/start")
    assert status == 200, t

    deadline = time.time() + 120
    while time.time() < deadline:
        _, t = call(base, "GET", f"/api/tournaments/{tid}")
        if t["status"] == "finished":
            break
        start_ready_matches(base, tid)
        time.sleep(0.2)
    assert t["status"] == "finished", [m["status"] for r in t["rounds"] for m in r["matches"]]
    assert t["champion_name"] in entering
    # every match was decided by a real game, not by a walkover or a no-show:
    # both languages sat down and both of them moved
    played = [m for r in t["rounds"] for m in r["matches"] if not m["bye"]]
    assert len(played) == 3, [m["status"] for m in played]
    for match in played:
        assert match["walkover"] is False, match
        _, game = call(base, "GET", f"/api/games/{match['game']}")
        seats_that_moved = {m["seat"] for m in game["moves"]}
        assert seats_that_moved == {1, 2}, (match["game"], game["moves"], game["reason"])
        assert game["winner"] in (1, 2), game["reason"]


# ============================================================ uploaded strategies

# What a team on another device sends: one self-contained file that plays a
# seat.  Deliberately distinctive (always the biggest fitting card) so the move
# log proves it was really this code that ran, not a fallback.
UPLOADED_STRATEGY = r"""#!/usr/bin/env python3
import os, sys, urllib.parse, urllib.request

def fetch(url, method="GET"):
    req = urllib.request.Request(url, method=method, data=b"" if method == "POST" else None)
    try:
        with urllib.request.urlopen(req, timeout=70) as r:
            return r.status, r.read().decode()
    except Exception:
        return 0, ""

def parse(text):
    out = {}
    for line in text.splitlines():
        if " " in line:
            k, v = line.split(" ", 1)
            out[k] = v
    return out

opt = lambda f, e, d: (sys.argv[sys.argv.index(f) + 1] if f in sys.argv else os.environ.get(e) or d)
server = opt("--server", "CARDNIM_SERVER", "http://localhost:8000").rstrip("/")
game = opt("--game", "CARDNIM_GAME", "")
name = opt("--name", "CARDNIM_NAME", "Uploaded")
seat = opt("--seat", "CARDNIM_SEAT", "")
base = f"{server}/api/games/{game}"
q = "?format=text&name=" + urllib.parse.quote(name) + (f"&seat={seat}" if seat else "")
status, body = fetch(base + "/join" + q, "POST")
me = parse(body)
if status != 200 or "token" not in me:
    sys.exit(f"join failed: {body}")
token = me["token"]
while True:
    status, body = fetch(f"{base}/getstate?format=text&timeout=60&token={token}")
    if status != 200:
        sys.exit(1)
    s = parse(body)
    if s.get("status") == "finished":
        sys.exit(0)
    if s.get("your_turn") != "1":
        continue
    stones = int(s["stones"])
    mine = [int(c) for c in s.get("your_cards", "").split() if c]
    fit = [c for c in mine if c <= stones]
    card = stones if stones in mine else (max(fit) if fit else min(mine))
    fetch(f"{base}/move?format=text&token={token}&card={card}", "POST")
"""


@pytest.fixture(scope="module")
def upload_server(tmp_path_factory):
    """A server started the way --accept-uploads starts one."""
    uploads = tmp_path_factory.mktemp("uploads")
    store = srv.GameStore(results_dir=None, uploads_dir=str(uploads))
    server = srv.CardNimServer(("127.0.0.1", 0), store, quiet=True)
    t = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    store.local_url = base
    yield base, str(uploads), store
    server.shutdown()
    server.server_close()


def start_ready_matches(base, tid):
    """Matches do not start themselves; start the next one that is ready.
    Returns True if a match was started or one is already running."""
    _, t = call(base, "GET", f"/api/tournaments/{tid}")
    if t["status"] == "finished":
        return False
    if any(m["game_state"] and m["game_state"]["status"] != "finished"
           for r in t["rounds"] for m in r["matches"]):
        return True                       # one is running; leave it alone
    for r in t["rounds"]:
        for m in r["matches"]:
            if m["status"] == "ready":
                call(base, "POST", f"/api/tournaments/{tid}/play",
                     {"round": m["round"], "match": m["index"]})
                return True
    return False


def post_file(base, filename, data, team):
    """Raw-body upload, exactly what the browser's fetch(body: file) sends."""
    import urllib.error
    q = f"?filename={urllib.parse.quote(filename)}&team={urllib.parse.quote(team)}"
    req = urllib.request.Request(base + "/api/uploads" + q, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


@pytest.fixture(scope="module")
def plain_server():
    """A server started the ordinary way: no uploads_dir, so uploads are off."""
    store = srv.GameStore(results_dir=None)
    server = srv.CardNimServer(("127.0.0.1", 0), store, quiet=True)
    t = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def test_uploads_are_refused_unless_the_server_was_started_for_them(plain_server):
    """The default server runs nobody's code but its own."""
    base_url = plain_server
    status, body = call(base_url, "GET", "/api/uploads")
    assert status == 200 and body["enabled"] is False
    status, body = post_file(base_url, "x.py", b"print(1)", "Team X")
    assert status == 403 and "--accept-uploads" in body["error"]


def test_an_uploaded_strategy_becomes_a_client_and_plays(upload_server):
    """The whole point: a file arrives from another device and the server
    builds, launches and plays it."""
    base, uploads, store = upload_server
    status, body = call(base, "GET", "/api/uploads")
    assert status == 200 and body["enabled"] is True and ".py" in body["accept"]

    status, up = post_file(base, "hungry.py", UPLOADED_STRATEGY.encode(), "Team Hungry")
    assert status == 201, up
    assert up["kind"] == "upload-team-hungry" and up["language"] == "Python" and up["available"]
    assert os.path.isfile(os.path.join(uploads, "team-hungry", "hungry.py"))
    assert os.path.isfile(os.path.join(uploads, "team-hungry", cl.MANIFEST))

    # it is seatable straight away, without waiting for the ten-second rescan
    kinds = {b["kind"] for b in call(base, "GET", "/api/bots")[1]["bots"]}
    assert "upload-team-hungry" in kinds

    _, game = call(base, "POST", "/api/games", {"stones": 40, "cards": 12, "time_limit": 60})
    gid = game["id"]
    status, _ = call(base, "POST", f"/api/games/{gid}/bot", {"kind": "upload-team-hungry", "seat": 1})
    assert status == 200
    call(base, "POST", f"/api/games/{gid}/bot", {"kind": "greedy", "seat": 2, "delay": 0})
    deadline = time.time() + 40
    while time.time() < deadline:
        _, state = call(base, "GET", f"/api/games/{gid}")
        if state["status"] == "finished":
            break
        time.sleep(0.1)
    assert state["status"] == "finished", state["status"]
    played = [m["card"] for m in state["moves"] if m["seat"] == 1]
    assert played, "the uploaded strategy never moved"
    # its signature: the biggest card that fits, so the first move is the
    # largest card in hand.  The built-in bots would play the smallest.
    assert played[0] == 12, played


def test_an_uploaded_strategy_can_enter_a_tournament(upload_server):
    base, _, _ = upload_server
    post_file(base, "hungry.py", UPLOADED_STRATEGY.encode(), "Team Hungry")
    _, t = call(base, "POST", "/api/tournaments",
                {"stones": 25, "cards": 8, "time_limit": 60, "bot_delay": 0})
    tid = t["id"]
    status, _ = call(base, "POST", f"/api/tournaments/{tid}/join",
                     {"kind": "upload-team-hungry", "name": "Team Hungry"})
    assert status == 200
    status, _ = call(base, "POST", f"/api/tournaments/{tid}/join", {"kind": "greedy", "name": "House"})
    assert status == 200
    assert call(base, "POST", f"/api/tournaments/{tid}/start")[0] == 200
    deadline = time.time() + 60
    while time.time() < deadline:
        _, t = call(base, "GET", f"/api/tournaments/{tid}")
        if t["status"] == "finished":
            break
        start_ready_matches(base, tid)
        time.sleep(0.2)
    assert t["status"] == "finished" and t["champion_name"] in ("Team Hungry", "House")
    final = t["rounds"][-1]["matches"][0]
    _, game = call(base, "GET", f"/api/games/{final['game']}")
    assert {m["seat"] for m in game["moves"]} == {1, 2}, "one side never played"


def test_what_the_server_refuses_to_accept(upload_server):
    base, uploads, _ = upload_server
    body = UPLOADED_STRATEGY.encode()
    assert post_file(base, "evil.exe", body, "X")[0] == 400        # not a language we run
    assert post_file(base, "noextension", body, "X")[0] == 400
    assert post_file(base, "empty.py", b"", "X")[0] == 400         # nothing in it
    status, err = post_file(base, "big.py", b"#" * (cl.UPLOAD_MAX_BYTES + 1), "X")
    assert status == 413, err


def test_an_upload_cannot_escape_the_uploads_folder(upload_server):
    """Path separators in the file name and in the team name are both stripped,
    so a hostile upload lands inside the folder like any other."""
    base, uploads, _ = upload_server
    status, up = post_file(base, "../../../../etc/passwd.py", UPLOADED_STRATEGY.encode(), "../../escape")
    assert status == 201, up
    root = os.path.realpath(uploads)
    for folder, _dirs, files in os.walk(root):
        for f in files:
            assert os.path.realpath(os.path.join(folder, f)).startswith(root + os.sep)
    assert not os.path.exists(os.path.join(root, "..", "passwd.py"))


def test_re_uploading_replaces_that_team_s_strategy(upload_server):
    """A team fixes a bug and sends it again; they do not end up with two."""
    base, uploads, _ = upload_server
    post_file(base, "first.py", UPLOADED_STRATEGY.encode(), "Team Twice")
    status, up = post_file(base, "second.py", UPLOADED_STRATEGY.encode(), "Team Twice")
    assert status == 201 and up["kind"] == "upload-team-twice"
    folder = os.path.join(uploads, "team-twice")
    assert sorted(os.listdir(folder)) == [cl.MANIFEST, "second.py"]


def test_a_java_upload_is_given_its_class_name(tmp_path):
    """javac requires the public class to match the file, so the generated
    manifest has to run whatever the uploaded file was called."""
    saved = cl.save_upload(str(tmp_path), "Strategy.java", b"class Strategy {}", "Team J")
    with open(os.path.join(saved["folder"], cl.MANIFEST), encoding="utf-8") as fh:
        manifest = json.load(fh)
    assert saved["file"] == "Strategy.java"
    assert manifest["run"][-1] == "Strategy"
    assert manifest["output"] == "out/Strategy.class"


def test_a_compiler_may_be_missing_but_a_runtime_may_not(tmp_path):
    """A built binary runs without its compiler, which is why `prebuilt`
    exists.  Java is the counter-example: javac is only needed to build, but
    `java` launches the result, so a missing runtime must make the client
    unavailable instead of leaving {java} unsubstituted in the command."""
    folder = tmp_path / "x"
    folder.mkdir()
    (folder / "Main.java").write_text("class Main {}")
    (folder / "out").mkdir()
    (folder / "out" / "Main.class").write_text("")     # already built
    spec = cl.ClientSpec(str(tmp_path), str(folder), {
        "language": "J", "file": "Main.java",
        "tools": {"javac": "definitely-not-installed", "java": "also-not-installed"},
        "build": ["{javac}", "-d", "{dir}/out", "{file}"],
        "output": "out/Main.class",
        "run": ["{java}", "-cp", "{dir}/out", "Main"]})
    assert spec.available is False
    assert spec.reason == "needs also-not-installed"   # the runtime, not the compiler

    # the same folder with a run command that needs no tool: the artifact is
    # enough, so a missing compiler does not matter
    compiled = cl.ClientSpec(str(tmp_path), str(folder), {
        "language": "K", "file": "Main.java",
        "tools": {"cc": "definitely-not-installed"},
        "build": ["{cc}", "-o", "{out}", "{file}"],
        "output": "out/Main.class",
        "run": ["{out}"]})
    assert compiled.available is True and compiled.reason == ""


def test_a_tool_no_command_mentions_is_still_required(tmp_path):
    """The shell and Lua clients call curl themselves, so it never appears as
    a placeholder.  It is still a runtime requirement."""
    folder = tmp_path / "y"
    folder.mkdir()
    (folder / "run.sh").write_text("")
    spec = cl.ClientSpec(str(tmp_path), str(folder), {
        "language": "Sh", "file": "run.sh",
        "tools": {"sh": "sh", "curl": "definitely-not-installed"},
        "run": ["{sh}", "{file}"]})
    assert spec.available is False
    assert spec.reason == "needs definitely-not-installed"


def test_every_accepted_extension_uploads_and_plays(upload_server):
    """A team can send their strategy in any language the server advertises.
    Each extension is uploaded, built if it needs building, and made to play a
    real game -- so /api/uploads never claims a language it cannot run."""
    base, uploads, store = upload_server
    _, info = call(base, "GET", "/api/uploads")
    extensions = info["accept"].split(",")

    # the repo's own sample client stands in for a team's file
    sample = {".py": "python/client.py", ".js": "javascript/client.js",
              ".ts": "typescript/client.ts", ".rb": "ruby/client.rb",
              ".R": "r/client.R", ".pl": "perl/client.pl", ".php": "php/client.php",
              ".lua": "lua/client.lua", ".sh": "shell/client.sh", ".q": "q/client.q",
              ".c": "c/client.c", ".cpp": "cpp/client.cpp", ".cc": "cpp/client.cpp",
              ".go": "go/client.go", ".rs": "rust/client.rs", ".java": "java/Client.java"}
    assert set(sample) >= set(extensions), \
        f"no sample for {sorted(set(extensions) - set(sample))}"

    played = []
    for ext in extensions:
        path = os.path.join(CLIENTS, *sample[ext].split("/"))
        with open(path, "rb") as fh:
            body = fh.read()
        # javac wants the file named after the class
        filename = "Client.java" if ext == ".java" else "strategy" + ext
        status, up = post_file(base, filename, body, f"Ext {ext.lstrip('.')}")
        assert status == 201, (ext, up)
        if not up["available"]:
            continue                      # this machine has no toolchain for it
        _, game = call(base, "POST", "/api/games",
                       {"stones": 30, "cards": 10, "time_limit": 60, "label": ext})
        gid = game["id"]
        call(base, "POST", f"/api/games/{gid}/bot", {"kind": "greedy", "seat": 1, "delay": 0})
        status, state = call(base, "POST", f"/api/games/{gid}/bot",
                             {"kind": up["kind"], "seat": 2})
        assert status == 200, (ext, state)
        deadline = time.time() + 60
        while time.time() < deadline:
            _, state = call(base, "GET", f"/api/games/{gid}")
            if state["status"] == "finished":
                break
            time.sleep(0.1)
        assert state["status"] == "finished", (ext, state["status"])
        assert any(m["seat"] == 2 for m in state["moves"]), f"{ext} never moved"
        played.append(ext)

    assert len(played) >= 8, f"only {played} could be run here"


def test_a_java_upload_keeps_its_own_class_name(upload_server):
    """javac insists the public class matches the file, so a team calling
    theirs MyStrategy.java must still build and run."""
    base, _, _ = upload_server
    with open(os.path.join(CLIENTS, "java", "Client.java"), encoding="utf-8") as fh:
        source = re.sub(r"\bClient\b", "MyStrategy", fh.read())
    status, up = post_file(base, "MyStrategy.java", source.encode(), "Team Named")
    assert status == 201, up
    if not up["available"]:
        pytest.skip("no JDK on this machine")
    _, game = call(base, "POST", "/api/games", {"stones": 30, "cards": 10, "time_limit": 60})
    gid = game["id"]
    call(base, "POST", f"/api/games/{gid}/bot", {"kind": "greedy", "seat": 1, "delay": 0})
    status, state = call(base, "POST", f"/api/games/{gid}/bot", {"kind": up["kind"], "seat": 2})
    assert status == 200, state
    deadline = time.time() + 60
    while time.time() < deadline:
        _, state = call(base, "GET", f"/api/games/{gid}")
        if state["status"] == "finished":
            break
        time.sleep(0.1)
    assert state["status"] == "finished" and any(m["seat"] == 2 for m in state["moves"])
