"""Knockout tournaments: the bracket on its own, then over HTTP with the real
server.  Run:  python3 -m pytest tests/ -q"""

import json
import os
import random
import sys
import threading
import time
import urllib.error
import urllib.request

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import cardnim_server as srv  # noqa: E402
from engine import Game, GameError  # noqa: E402
from tournament import Tournament  # noqa: E402


# ---------------------------------------------------------------- the bracket alone

def make(names, seed=1):
    t = Tournament(20, 6, 60, "Test")
    for n in names:
        t.add_entrant(n)
    t.start(random.Random(seed))
    return t


def test_draw_sizes_and_byes():
    for n in range(2, 12):
        t = make([f"P{i}" for i in range(n)], seed=n)
        size = 1
        while size < n:
            size *= 2
        assert t.size == size
        assert len(t.rounds) == size.bit_length() - 1
        first = t.rounds[0]
        assert len(first) == size // 2
        byes = [m for m in first if m.bye]
        assert len(byes) == size - n
        for m in first:                      # no bye against a bye: every match has a real entrant
            assert m.slots[0] is not None
            assert m.bye == (m.slots[1] is None)
        for m in byes:                       # a bye is decided at once and moves on, without a win
            assert m.winner == m.slots[0]
            assert t.entrants[m.winner].wins == 0
        seen = [s for m in first for s in m.slots if s is not None]
        assert sorted(seen) == sorted(t.entrants)     # everyone is placed exactly once
    assert make(["A", "B"]).round_name(1) == "Final"
    t = make(list("ABCDEFGH"))
    assert [t.round_name(r) for r in (1, 2, 3)] == ["Quarterfinals", "Semifinals", "Final"]
    t = make([f"P{i}" for i in range(17)])
    assert t.round_name(1) == "Round 1" and t.round_name(5) == "Final"


def test_results_flow_to_the_champion():
    t = make(["A", "B", "C"], seed=3)      # bracket of 4, one bye
    assert t.status == "running"
    ready = t.matches_needing_games()
    assert len(ready) == 1 and ready[0].round == 1
    m = ready[0]
    a, b = m.slots
    t.attach_game(m, "G1", {1: a, 2: b})
    assert t.matches_needing_games() == []
    assert t.record_result("G1", 2) is m and m.winner == b
    assert t.entrants[a].eliminated_in == 1 and t.entrants[b].wins == 1
    assert t.record_result("G1", 1) is None          # already decided: ignored
    final = t.matches_needing_games()
    assert len(final) == 1 and final[0].round == 2 and set(final[0].slots) == {b, t.rounds[0][0].winner if t.rounds[0][0].bye else t.rounds[0][1].winner}
    f = final[0]
    t.attach_game(f, "G2", {1: f.slots[0], 2: f.slots[1]})
    t.record_result("G2", 1)
    assert t.status == "finished" and t.champion == f.slots[0]
    assert t.summary()["champion"] == t.entrants[f.slots[0]].name
    with pytest.raises(GameError):
        t.add_entrant("Late")


def test_replay_walkover_and_abort():
    t = make(["A", "B"], seed=1)
    m = t.rounds[0][0]
    t.attach_game(m, "G1", {1: m.slots[0], 2: m.slots[1]})
    t.detach_game(m)                                   # aborted game: play it again
    assert m.game_id is None and m.games == ["G1"] and t.matches_needing_games() == [m]
    t.attach_game(m, "G2", {1: m.slots[1], 2: m.slots[0]})
    assert t.record_result("G1", 1) is None            # the old game no longer counts
    with pytest.raises(GameError):
        t.walkover(1, 0, 999)
    t.walkover(1, 0, m.slots[0])
    assert m.walkover and t.champion == m.slots[0] and t.status == "finished"
    with pytest.raises(GameError):
        t.walkover(1, 0, m.slots[1])
    t2 = make(["A", "B", "C", "D"])
    t2.abort("test")
    assert t2.status == "finished" and t2.aborted and t2.champion is None
    assert t2.matches_needing_games() == []


def test_entries_need_unique_names_except_bots():
    t = Tournament(20, 6, 60)
    t.add_entrant("Alice")
    with pytest.raises(GameError):
        t.add_entrant("alice")
    g1 = t.add_entrant("Greedy bot", kind="greedy")
    g2 = t.add_entrant("Greedy bot", kind="greedy")
    assert (g1.name, g2.name) == ("Greedy bot", "Greedy bot 2") and g1.is_bot
    t.remove_entrant(g1.id)
    t.remove_entrant(g2.id)
    with pytest.raises(GameError):
        t.start()                                       # one entrant is not a tournament
    with pytest.raises(GameError):
        t.remove_entrant(999)
    with pytest.raises(ValueError):
        Tournament(0, 6, 60)


# ---------------------------------------------------------------- reserved seats in the engine

def test_reserved_seat_opens_for_token_or_name_only():
    g = Game(10, 4)
    g.reserve(1, "Alice", 3, "secret-a")
    g.reserve(2, "Bob", 4, "secret-b")
    assert not g.seats[1].occupied and g.seats[1].reserved and g.summary()["players"] == ["Alice", "Bob"]
    assert g.summary()["occupied"] == [False, False]
    with pytest.raises(GameError):
        g.join("Mallory")                     # no free seat for a stranger
    with pytest.raises(GameError):
        g.join("Mallory", 1)
    s = g.join("whoever", claim="secret-a")   # the token picks the seat and keeps the reserved identity
    assert s.number == 1 and s.token == "secret-a" and s.name == "Alice" and s.avatar == 3
    assert g.status == "waiting"
    s2 = g.join("bob", 2)                     # the reserved name works too (case-insensitive)
    assert s2.number == 2 and s2.token and s2.name == "Bob"
    assert g.status == "playing" and g.seat_for_token("secret-a").number == 1
    # leaving a reserved seat before the start keeps the reservation
    g2 = Game(10, 4)
    g2.reserve(1, "Alice", 3, "secret-a")
    g2.join("Alice", 1)
    g2.leave(1)
    assert g2.seats[1].reserved and not g2.seats[1].occupied and g2.seats[1].name == "Alice"
    assert g2.to_dict()["players"][0]["reserved"] is True
    with pytest.raises(GameError):
        g.reserve(1, "Eve", 1, "x")               # taken
    with pytest.raises(GameError):
        g.reserve(2, "Eve", 1, "x")               # the game has started


# ---------------------------------------------------------------- over HTTP

# The server the base_url fixture is running, for the few tests that change a
# setting on it (who may run the event) rather than talking to it.
LIVE = {}


@pytest.fixture(scope="module")
def base_url(tmp_path_factory):
    results = tmp_path_factory.mktemp("results")
    store = srv.GameStore(results_dir=str(results))
    server = srv.CardNimServer(("127.0.0.1", 0), store, quiet=True)
    LIVE["server"] = server
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}", str(results)
    server.shutdown()
    server.server_close()


def call(base, method, path, body=None, token=None, text=False):
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["X-Token"] = token
    req = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode()
            status = resp.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        status = exc.code
    if text:
        return status, raw
    return status, (json.loads(raw) if raw else None)


def wait_until(fn, seconds=15.0):
    deadline = time.time() + seconds
    while time.time() < deadline:
        value = fn()
        if value:
            return value
        time.sleep(0.05)
    return fn()


def play_smallest(base, gid, token):
    """One move for the token's seat if it is their turn: win if possible, else the smallest card."""
    _, s = call(base, "GET", f"/api/games/{gid}", token=token)
    if s["status"] != "playing" or not s["your_turn"]:
        return s
    me = s["players"][s["you"] - 1]
    legal = [c for c in me["cards"] if c <= s["stones"]]
    card = s["stones"] if s["stones"] in legal else min(legal)
    return call(base, "POST", f"/api/games/{gid}/move", {"card": card}, token=token)[1]


def start_next_match(base, tid, timeout=30):
    """Matches do not start themselves any more: the organiser starts each
    one.  Starts the first that is ready and returns its game id, or None when
    the bracket has nothing left to play."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        _, t = call(base, "GET", f"/api/tournaments/{tid}")
        if t["status"] == "finished":
            return None
        live = [m for r in t["rounds"] for m in r["matches"]
                if m["game_state"] and m["game_state"]["status"] != "finished"]
        if live:                                   # one is already running
            return live[0]["game"]
        ready = [m for r in t["rounds"] for m in r["matches"] if m["status"] == "ready"]
        if not ready:
            return None
        m = ready[0]
        status, body = call(base, "POST", f"/api/tournaments/{tid}/play",
                            {"round": m["round"], "match": m["index"]})
        if status == 200:
            for r in body["rounds"]:
                for mm in r["matches"]:
                    if mm["round"] == m["round"] and mm["index"] == m["index"]:
                        return mm["game"]
        time.sleep(0.05)
    raise AssertionError("no match could be started")


def drive_matches(base, tid, stop, timeout=60):
    """Start each match as it becomes ready, in a thread, for tests that have
    a client blocking on the bracket at the same time."""
    def loop():
        deadline = time.time() + timeout
        while not stop.is_set() and time.time() < deadline:
            try:
                _, t = call(base, "GET", f"/api/tournaments/{tid}")
                if t["status"] == "finished":
                    return
                if not any(m["game_state"] and m["game_state"]["status"] != "finished"
                           for r in t["rounds"] for m in r["matches"]):
                    start_next_match(base, tid, timeout=5)
            except Exception:
                pass
            time.sleep(0.1)
    th = threading.Thread(target=loop, daemon=True)
    th.start()
    return th


def play_bracket_out(base, tid, timeout=120):
    """Start every match in turn and wait for the bracket to finish."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        _, t = call(base, "GET", f"/api/tournaments/{tid}")
        if t["status"] == "finished":
            return t
        start_next_match(base, tid)
        time.sleep(0.1)
    raise AssertionError("bracket did not finish")


def test_tournament_over_http(base_url):
    base, results = base_url
    assert call(base, "POST", "/api/tournaments", {"stones": 0, "cards": 3})[0] == 400
    assert call(base, "POST", "/api/tournaments", {"stones": 9, "cards": 3, "time_limit": 0})[0] == 400
    status, t = call(base, "POST", "/api/tournaments", {"stones": 9, "cards": 3, "label": "Cup", "bot_delay": 0})
    assert status == 201 and t["status"] == "open" and t["label"] == "Cup" and t["rounds"] == []
    tid = t["id"]
    assert call(base, "GET", f"/api/tournaments/{tid.lower()}")[0] == 200
    assert call(base, "GET", f"/tournament/{tid}", text=True)[0] == 200
    assert call(base, "GET", "/api/tournaments")[1]["tournaments"][0]["id"] == tid
    assert call(base, "GET", "/api/health")[1]["tournaments"] >= 1

    # entries: a person, a program, a server bot; a repeated name is refused
    _, alice = call(base, "POST", f"/api/tournaments/{tid}/join", {"name": "Alice", "avatar": 5})
    assert alice["entrant"]["kind"] == "human" and alice["entrant"]["avatar"] == 5 and alice["token"]
    assert alice["tournament"]["you"]["status"] == "open"
    _, prog = call(base, "POST", f"/api/tournaments/{tid}/join", {"name": "Prog", "kind": "api"})
    _, bot = call(base, "POST", f"/api/tournaments/{tid}/join", {"kind": "greedy"})
    assert bot["entrant"]["bot"] is True and bot["entrant"]["name"] == "Greedy bot"
    assert call(base, "POST", f"/api/tournaments/{tid}/join", {"name": "alice"})[0] == 409
    assert call(base, "POST", f"/api/tournaments/{tid}/join", {"name": "X", "kind": "nope"})[0] == 400
    # withdrawing needs the token (or an id for a bot)
    _, extra = call(base, "POST", f"/api/tournaments/{tid}/join", {"kind": "random"})
    assert call(base, "POST", f"/api/tournaments/{tid}/leave", {"entrant": extra["entrant"]["id"]})[0] == 200
    assert call(base, "POST", f"/api/tournaments/{tid}/leave", {"entrant": alice["entrant"]["id"]})[0] == 401
    _, gone = call(base, "POST", f"/api/tournaments/{tid}/join", {"name": "Gone"})
    assert call(base, "POST", f"/api/tournaments/{tid}/leave", token=gone["token"])[0] == 200
    _, t = call(base, "GET", f"/api/tournaments/{tid}")
    assert [e["name"] for e in t["entrants"]] == ["Alice", "Prog", "Greedy bot"]

    # start: bracket of 4 with one bye; one game exists with both seats reserved
    status, t = call(base, "POST", f"/api/tournaments/{tid}/start")
    assert status == 200 and t["status"] == "running" and t["size"] == 4 and len(t["rounds"]) == 2
    assert call(base, "POST", f"/api/tournaments/{tid}/start")[0] == 409
    assert call(base, "POST", f"/api/tournaments/{tid}/join", {"name": "Late"})[0] == 409
    assert call(base, "POST", f"/api/tournaments/{tid}/leave", token=alice["token"])[0] == 409
    first = t["rounds"][0]["matches"]
    played = [m for m in first if not m["bye"]]
    assert len(played) == 1 and played[0]["game"] is None, "a match must wait to be started"
    # the organiser starts it
    gid = start_next_match(base, tid)
    assert gid
    _, t = call(base, "GET", f"/api/tournaments/{tid}")
    _, g = call(base, "GET", f"/api/games/{gid}")
    assert g["tournament"]["id"] == tid and g["label"].startswith("Cup: Semifinals")
    lobby = next(x for x in call(base, "GET", "/api/games")[1]["games"] if x["id"] == gid)
    assert lobby["tournament"] == tid and set(lobby["players"]) <= {"Alice", "Prog", "Greedy bot"}

    # the entrant view: whoever is in the played match must sit down; a stranger cannot
    views = {}
    for name, rec in (("Alice", alice), ("Prog", prog)):
        _, v = call(base, "GET", f"/api/tournaments/{tid}/getstate?timeout=2", token=rec["token"])
        views[name] = v
    assert call(base, "GET", f"/api/tournaments/{tid}/getstate", token="nope")[0] == 401
    in_match = [n for n, v in views.items() if v["status"] == "play" and v["game"] == gid]
    # the bot sits at once; a person's seat stays reserved until they claim it
    assert g["players"][0]["reserved"] and g["players"][1]["reserved"]
    for p in g["players"]:
        assert p["occupied"] == (p["name"] == "Greedy bot")
    human_seats = [p for p in g["players"] if not p["occupied"]]
    if human_seats:   # the bye may have gone to the bot, in which case Alice plays Prog
        seat = human_seats[0]
        assert call(base, "POST", f"/api/games/{gid}/join", {"name": "Mallory", "seat": seat["seat"]})[0] == 409
    # everyone in the played match claims their seat: by token for the first, by name for the second
    tokens = {}
    for n in in_match:
        rec = alice if n == "Alice" else prog
        v = views[n]
        if not tokens:
            st, j = call(base, "POST", f"/api/games/{gid}/join", {"name": "ignored"}, token=rec["token"])
            assert st == 200 and j["seat"] == v["seat"] and j["token"] == rec["token"]
        else:
            st, j = call(base, "POST", f"/api/games/{gid}/join", {"name": n.lower(), "seat": v["seat"]})
            assert st == 200 and j["seat"] == v["seat"]
        tokens[n] = j["token"]
    _, g = call(base, "GET", f"/api/games/{gid}")
    assert g["status"] == "playing"

    # play the whole bracket: humans move whenever it is their turn, bots move alone
    def step():
        _, t = call(base, "GET", f"/api/tournaments/{tid}")
        if t["status"] == "finished":
            return t
        # nothing running? the organiser starts the next match
        if not any(m["game_state"] and m["game_state"]["status"] != "finished"
                   for rd in t["rounds"] for m in rd["matches"]):
            start_next_match(base, tid)
            _, t = call(base, "GET", f"/api/tournaments/{tid}")
        for rd in t["rounds"]:
            for m in rd["matches"]:
                gs = m["game_state"]
                if not gs or gs["status"] == "finished":
                    continue
                for n, rec in (("Alice", alice), ("Prog", prog)):
                    _, v = call(base, "GET", f"/api/tournaments/{tid}/getstate?timeout=0", token=rec["token"])
                    if v["status"] == "play" and v["game"] == m["game"]:
                        if not v["claimed"]:
                            call(base, "POST", f"/api/games/{m['game']}/join", {"name": n}, token=rec["token"])
                        play_smallest(base, m["game"], rec["token"])
        return None
    final = wait_until(step, 20)
    assert final and final["status"] == "finished" and final["champion_name"] in ("Alice", "Prog", "Greedy bot")
    assert all(m["winner"] for rd in final["rounds"] for m in rd["matches"])
    assert os.path.isfile(os.path.join(results, f"tournament-{tid}.json"))
    saved = json.load(open(os.path.join(results, f"tournament-{tid}.json")))
    assert saved["champion_name"] == final["champion_name"]
    # after the end: getstate answers at once for everybody
    for rec in (alice, prog):
        _, v = call(base, "GET", f"/api/tournaments/{tid}/getstate?timeout=5", token=rec["token"])
        assert v["status"] in ("eliminated", "champion")
    assert call(base, "GET", "/api/tournaments")[1]["tournaments"][0]["champion"] == final["champion_name"]


def test_all_bot_tournament_finishes_when_each_match_is_started(base_url):
    base, _ = base_url
    _, t = call(base, "POST", "/api/tournaments", {"stones": 30, "cards": 8, "bot_delay": 0})
    tid = t["id"]
    for i in range(6):
        call(base, "POST", f"/api/tournaments/{tid}/join", {"kind": "greedy" if i % 2 else "random"})
    _, t = call(base, "POST", f"/api/tournaments/{tid}/start")
    assert t["size"] == 8 and [len(r["matches"]) for r in t["rounds"]] == [4, 2, 1]
    final = play_bracket_out(base, tid, timeout=60)
    assert final["status"] == "finished" and final["champion"]
    champ = next(e for e in final["entrants"] if e["id"] == final["champion"])
    assert champ["eliminated_in"] is None
    assert sum(1 for e in final["entrants"] if e["eliminated_in"] is None) == 1
    games = [m["game"] for rd in final["rounds"] for m in rd["matches"] if m["game"]]
    assert len(games) == 5          # 2 byes: 2 + 2 + 1 games
    for gid in games:
        _, g = call(base, "GET", f"/api/games/{gid}")
        assert g["status"] == "finished" and g["winner"] in (1, 2) and g["tournament"]["id"] == tid


def test_aborted_game_is_played_again_and_walkover_settles_a_no_show(base_url):
    base, _ = base_url
    _, t = call(base, "POST", "/api/tournaments", {"stones": 5, "cards": 3})
    tid = t["id"]
    _, a = call(base, "POST", f"/api/tournaments/{tid}/join", {"name": "A"})
    _, b = call(base, "POST", f"/api/tournaments/{tid}/join", {"name": "B"})
    call(base, "POST", f"/api/tournaments/{tid}/start")
    g1 = start_next_match(base, tid)
    _, t = call(base, "GET", f"/api/tournaments/{tid}")
    m = t["rounds"][0]["matches"][0]
    # both sit, then the architects abort the game: the match gets a fresh game
    for rec in (a, b):
        assert call(base, "POST", f"/api/games/{g1}/join", {"name": "x"}, token=rec["token"])[0] == 200
    assert call(base, "GET", f"/api/games/{g1}")[1]["status"] == "playing"
    call(base, "POST", f"/api/games/{g1}/abort", {"reason": "test"})
    _, t = call(base, "GET", f"/api/tournaments/{tid}")
    m = t["rounds"][0]["matches"][0]
    # the game is gone but the match is undecided: it waits to be started again
    assert m["status"] == "ready" and m["winner"] is None
    g2 = start_next_match(base, tid)
    _, t = call(base, "GET", f"/api/tournaments/{tid}")
    m = t["rounds"][0]["matches"][0]
    assert g2 != g1 and m["games"] == [g1, g2] and m["status"] == "waiting"
    # B never shows up: walkover for A; the waiting game is aborted and A is champion
    assert call(base, "POST", f"/api/tournaments/{tid}/walkover", {"round": 1, "match": 0, "winner": 999})[0] == 409
    status, t = call(base, "POST", f"/api/tournaments/{tid}/walkover", {"round": 1, "match": 0, "winner": a["entrant"]["id"]})
    assert status == 200 and t["status"] == "finished" and t["champion"] == a["entrant"]["id"]
    assert t["rounds"][0]["matches"][0]["walkover"] is True
    assert call(base, "GET", f"/api/games/{m['game']}")[1]["status"] == "finished"
    _, v = call(base, "GET", f"/api/tournaments/{tid}/getstate", token=b["token"])
    assert v["status"] == "eliminated" and v["lost_to"] == "A"


def test_abort_tournament_ends_its_games(base_url):
    base, _ = base_url
    _, t = call(base, "POST", "/api/tournaments", {"stones": 50, "cards": 10, "bot_delay": 5})
    tid = t["id"]
    for _ in range(4):
        call(base, "POST", f"/api/tournaments/{tid}/join", {"kind": "greedy"})
    call(base, "POST", f"/api/tournaments/{tid}/start")
    # matches wait to be started, and only one runs at a time
    gid = start_next_match(base, tid)
    _, t = call(base, "GET", f"/api/tournaments/{tid}")
    games = [m["game"] for m in t["rounds"][0]["matches"] if m["game"]]
    assert games == [gid], games
    assert call(base, "GET", f"/api/games/{games[0]}")[1]["status"] == "playing"
    status, t = call(base, "POST", f"/api/tournaments/{tid}/abort", {"reason": "rain"})
    assert status == 200 and t["status"] == "finished" and t["aborted"] and t["champion"] is None and t["reason"] == "rain"
    for g in games:
        s = call(base, "GET", f"/api/games/{g}")[1]
        assert s["status"] == "finished" and s["winner"] is None
    assert call(base, "POST", f"/api/tournaments/{tid}/walkover", {"round": 1, "match": 0, "winner": 1})[0] == 409


def test_bracket_long_poll_wakes_on_a_move(base_url):
    base, _ = base_url
    _, t = call(base, "POST", "/api/tournaments", {"stones": 5, "cards": 3})
    tid = t["id"]
    _, a = call(base, "POST", f"/api/tournaments/{tid}/join", {"name": "A"})
    _, b = call(base, "POST", f"/api/tournaments/{tid}/join", {"name": "B"})
    call(base, "POST", f"/api/tournaments/{tid}/start")
    gid = start_next_match(base, tid)          # matches wait to be started
    for rec in (a, b):
        call(base, "POST", f"/api/games/{gid}/join", {"name": "x"}, token=rec["token"])
    _, t = call(base, "GET", f"/api/tournaments/{tid}")
    version = t["version"]
    got = {}

    def waiter():
        got["t0"] = time.time()
        got["resp"] = call(base, "GET", f"/api/tournaments/{tid}?since={version}&timeout=10")[1]
        got["dt"] = time.time() - got["t0"]
    th = threading.Thread(target=waiter)
    th.start()
    time.sleep(0.3)
    g = call(base, "GET", f"/api/games/{gid}")[1]
    mover = a if g["players"][g["turn"] - 1]["name"] == "A" else b
    play_smallest(base, gid, mover["token"])
    th.join(5)
    assert got["resp"]["version"] > version and got["dt"] < 3
    assert got["resp"]["rounds"][0]["matches"][0]["game_state"]["moves"] == 1


def test_python_client_plays_a_tournament(base_url):
    base, _ = base_url
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "clients", "python"))
    import client as cl  # noqa: E402
    _, t = call(base, "POST", "/api/tournaments", {"stones": 20, "cards": 6, "bot_delay": 0})
    tid = t["id"]
    for _ in range(3):
        call(base, "POST", f"/api/tournaments/{tid}/join", {"kind": "random"})
    entry = cl.CardNimTournament(base, tid, "Py", verbose=False)
    entry.join()
    assert entry.token and entry.entrant["kind"] == "api"
    results = {}

    def run():
        results["view"] = entry.play(cl.greedy_bot)
    th = threading.Thread(target=run, daemon=True)
    th.start()
    time.sleep(0.5)
    call(base, "POST", f"/api/tournaments/{tid}/start")
    stop = threading.Event()
    driver = drive_matches(base, tid, stop)
    th.join(40)
    # the client is done, but the bracket may still owe a match or two
    deadline = time.time() + 30
    while time.time() < deadline:
        if call(base, "GET", f"/api/tournaments/{tid}")[1]["status"] == "finished":
            break
        time.sleep(0.1)
    stop.set()
    driver.join(2)
    assert not th.is_alive()
    view = results["view"]
    assert view["status"] in ("champion", "eliminated")
    _, t = call(base, "GET", f"/api/tournaments/{tid}")
    assert t["status"] == "finished"
    py = next(e for e in t["entrants"] if e["name"] == "Py")
    assert (view["status"] == "champion") == (t["champion"] == py["id"])


def test_matches_are_played_one_at_a_time(base_url):
    """A bracket is watched on one screen, so the server runs the matches in
    order instead of starting a whole round at once."""
    base, _ = base_url
    _, t = call(base, "POST", "/api/tournaments", {"stones": 20, "cards": 6, "bot_delay": 0})
    tid = t["id"]
    for _ in range(4):
        call(base, "POST", f"/api/tournaments/{tid}/join", {"kind": "greedy"})
    call(base, "POST", f"/api/tournaments/{tid}/start")
    _, t = call(base, "GET", f"/api/tournaments/{tid}")
    assert not [m["game"] for r in t["rounds"] for m in r["matches"] if m["game"]], \
        "no match may start by itself"
    first = start_next_match(base, tid)
    _, t = call(base, "GET", f"/api/tournaments/{tid}")
    live = [m["game"] for r in t["rounds"] for m in r["matches"] if m["game"]]
    assert live == [first], f"expected one game at a time, got {live}"

    # as each finishes the next appears, and never two at once
    seen, deadline = set(live), time.time() + 60
    while time.time() < deadline:
        _, t = call(base, "GET", f"/api/tournaments/{tid}")
        playing = [m["game"] for r in t["rounds"] for m in r["matches"]
                   if m["game"] and call(base, "GET", f"/api/games/{m['game']}")[1]["status"] != "finished"]
        assert len(playing) <= 1, f"two matches running at once: {playing}"
        seen.update(m["game"] for r in t["rounds"] for m in r["matches"] if m["game"])
        if t["status"] == "finished":
            break
        if not playing:
            start_next_match(base, tid)       # the organiser starts the next one
        time.sleep(0.1)
    assert t["status"] == "finished" and t["champion"] is not None
    assert len(seen) == 3, f"a 4-entrant bracket is 3 matches, saw {seen}"


def test_a_match_can_only_be_started_when_the_bracket_is_running(base_url):
    """Every way of asking for a match that must not be started.  The last one
    matters most: an aborted tournament could otherwise be given a fresh game
    that nothing would ever advance."""
    base, _ = base_url
    _, t = call(base, "POST", "/api/tournaments", {"stones": 20, "cards": 6, "bot_delay": 0})
    tid = t["id"]
    for _ in range(3):                       # 3 entrants in a bracket of 4: one bye
        call(base, "POST", f"/api/tournaments/{tid}/join", {"kind": "greedy"})

    # before the draw there are no matches at all
    assert call(base, "POST", f"/api/tournaments/{tid}/play", {"round": 1, "match": 0})[0] == 409

    _, t = call(base, "POST", f"/api/tournaments/{tid}/start")
    bye = next(m for r in t["rounds"] for m in r["matches"] if m["bye"])
    pending = next(m for r in t["rounds"] for m in r["matches"] if m["status"] == "pending")
    assert bye["winner"] is not None, "a bye advances without being started"

    for label, body, want in [
        ("a bye", {"round": bye["round"], "match": bye["index"]}, 409),
        ("a pending match", {"round": pending["round"], "match": pending["index"]}, 409),
        ("a round that does not exist", {"round": 99, "match": 0}, 409),
        ("a match that does not exist", {"round": 1, "match": 99}, 409),
        ("round 0", {"round": 0, "match": 0}, 400),
    ]:
        assert call(base, "POST", f"/api/tournaments/{tid}/play", body)[0] == want, label

    # and nothing may start once it is over
    call(base, "POST", f"/api/tournaments/{tid}/abort", {"reason": "done"})
    status, err = call(base, "POST", f"/api/tournaments/{tid}/play", {"round": 1, "match": 0})
    assert status == 409 and "not running" in err["error"], err
    _, t = call(base, "GET", f"/api/tournaments/{tid}")
    assert not [m["game"] for r in t["rounds"] for m in r["matches"] if m["game"]], \
        "an aborted tournament gained a game"


def test_only_one_game_appears_when_start_is_pressed_twice(base_url):
    """Two people on the bracket page pressing Start at the same moment."""
    base, _ = base_url
    _, t = call(base, "POST", "/api/tournaments", {"stones": 40, "cards": 10, "bot_delay": 5})
    tid = t["id"]
    for _ in range(4):
        call(base, "POST", f"/api/tournaments/{tid}/join", {"kind": "greedy"})
    _, t = call(base, "POST", f"/api/tournaments/{tid}/start")
    first = next((m["round"], m["index"]) for r in t["rounds"] for m in r["matches"]
                 if m["status"] == "ready")
    replies = []
    threads = [threading.Thread(target=lambda: replies.append(
        call(base, "POST", f"/api/tournaments/{tid}/play", {"round": first[0], "match": first[1]})[0]))
        for _ in range(6)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert sorted(replies) == [200] + [409] * 5, replies
    _, t = call(base, "GET", f"/api/tournaments/{tid}")
    games = {m["game"] for r in t["rounds"] for m in r["matches"] if m["game"]}
    assert len(games) == 1, games
    call(base, "POST", f"/api/tournaments/{tid}/abort", {"reason": "done"})


def test_a_tournament_match_plays_at_a_watchable_speed(base_url):
    """A client is a separate process and answers as fast as the socket
    allows, so without pacing a whole match went by in a fifth of a second.
    The pauses between moves must not cost either player any clock."""
    base, _ = base_url
    delay = 0.3
    _, t = call(base, "POST", "/api/tournaments",
                {"stones": 30, "cards": 10, "time_limit": 60, "bot_delay": delay})
    tid = t["id"]
    for kind in ("client-python", "client-python"):
        status, _ = call(base, "POST", f"/api/tournaments/{tid}/join", {"kind": kind, "name": kind})
        if status != 200:
            pytest.skip("the python client cannot be launched here")
    call(base, "POST", f"/api/tournaments/{tid}/start")
    started = time.time()
    gid = start_next_match(base, tid)
    deadline = time.time() + 60
    while time.time() < deadline:
        _, g = call(base, "GET", f"/api/games/{gid}")
        if g["status"] == "finished":
            break
        time.sleep(0.05)
    took = time.time() - started
    moves = len(g["moves"])
    # the pauses it takes between moves are its own, not the room holding the
    # game: the bracket draws them as play, so a card does not flash "paused"
    assert g["paced"] is False and g["paused"] is False, "it ended mid-pause"
    assert g["status"] == "finished" and moves >= 4, (g["status"], moves)
    # roughly one delay per move, give or take process start-up
    assert took >= moves * delay * 0.6, f"{moves} moves in {took:.2f}s is too fast to watch"
    # and the waiting is free
    for p in g["players"]:
        assert 60.0 - p["time_remaining"] < 2.0, f"a pause was charged: {p}"


# ============================================================ who may run the event

# Entering, playing and watching belong to the room; drawing the bracket and
# starting a match belong to the machine the server runs on.  Otherwise the
# first team to find the page starts every match on the projector.
#
# Requests in these tests all come from 127.0.0.1, so "a visitor" is made by
# answering the one question the server asks about them.

@pytest.fixture
def visitor(monkeypatch):
    """Make every request look as though it came from another device."""
    monkeypatch.setattr(srv.Handler, "is_local_client", lambda self: False)
    monkeypatch.setattr(srv.Handler, "client_ip", lambda self: "10.0.0.9")


def two_entrants(base, **settings):
    """An open tournament with two server bots in it.  Outputs: its id."""
    body = {"stones": 20, "cards": 6, "time_limit": 60, "bot_delay": 0}
    body.update(settings)
    _, t = call(base, "POST", "/api/tournaments", body)
    for name in ("One", "Two"):
        call(base, "POST", f"/api/tournaments/{t['id']}/join", {"kind": "greedy", "name": name})
    return t["id"]


def test_a_visitor_cannot_start_the_bracket_or_a_match(base_url, visitor):
    base, _ = base_url
    tid = two_entrants(base)
    status, err = call(base, "POST", f"/api/tournaments/{tid}/start")
    assert status == 403 and "machine running the server" in err["error"], err
    status, err = call(base, "POST", f"/api/tournaments/{tid}/play", {"round": 1, "match": 0})
    assert status == 403, err
    status, err = call(base, "POST", f"/api/tournaments/{tid}/walkover",
                       {"round": 1, "match": 0, "winner": 1})
    assert status == 403, err
    status, err = call(base, "POST", f"/api/tournaments/{tid}/abort")
    assert status == 403, err
    # nothing happened: it is still open, with both entrants
    _, t = call(base, "GET", f"/api/tournaments/{tid}")
    assert t["status"] == "open" and len(t["entrants"]) == 2


def test_a_visitor_cannot_take_someone_else_out_but_may_withdraw(base_url, visitor):
    """A team's own entry is theirs wherever they are sitting; another team's
    is not."""
    base, _ = base_url
    tid = two_entrants(base)
    _, t = call(base, "GET", f"/api/tournaments/{tid}")
    victim = t["entrants"][0]["id"]
    status, err = call(base, "POST", f"/api/tournaments/{tid}/leave", {"entrant": victim})
    assert status == 403, err

    status, mine = call(base, "POST", f"/api/tournaments/{tid}/join", {"name": "Visiting team"})
    assert status == 200, mine
    status, _ = call(base, "POST", f"/api/tournaments/{tid}/leave", token=mine["token"])
    assert status == 200
    _, t = call(base, "GET", f"/api/tournaments/{tid}")
    assert [e["name"] for e in t["entrants"]] == ["One", "Two"]


def test_a_visitor_may_still_enter_watch_and_play(base_url, visitor):
    """The gate is on running the event, not on taking part in it."""
    base, _ = base_url
    tid = two_entrants(base)
    status, joined = call(base, "POST", f"/api/tournaments/{tid}/join", {"name": "Away team"})
    assert status == 200, joined
    assert call(base, "GET", f"/api/tournaments/{tid}")[0] == 200
    assert call(base, "GET", "/api/tournaments")[0] == 200
    assert call(base, "GET", f"/api/tournaments/{tid}/getstate?timeout=0",
                token=joined["token"])[0] == 200
    assert call(base, "POST", "/api/games", {"stones": 10, "cards": 4})[0] == 201


def test_the_page_is_told_whether_it_may_run_the_event(base_url, visitor):
    """The bracket page hides the buttons it cannot use; /api/health is where
    it finds out."""
    base, _ = base_url
    _, health = call(base, "GET", "/api/health")
    assert health["organiser"] is False and health["local"] is False


def test_the_machine_running_the_server_still_runs_the_event(base_url):
    base, _ = base_url
    _, health = call(base, "GET", "/api/health")
    assert health["organiser"] is True
    tid = two_entrants(base)
    assert call(base, "POST", f"/api/tournaments/{tid}/start")[0] == 200
    assert call(base, "POST", f"/api/tournaments/{tid}/abort")[0] == 200


def test_another_machine_can_be_given_the_controls(base_url, monkeypatch):
    """--controls-from: the laptop the bracket is projected from runs the
    event without the server moving to it."""
    base, _ = base_url
    monkeypatch.setattr(srv.Handler, "is_local_client", lambda self: False)
    monkeypatch.setattr(srv.Handler, "client_ip", lambda self: "10.0.0.9")
    tid = two_entrants(base)
    assert call(base, "POST", f"/api/tournaments/{tid}/start")[0] == 403

    monkeypatch.setattr(LIVE["server"], "organiser_addresses", {"10.0.0.9"})
    assert call(base, "POST", f"/api/tournaments/{tid}/start")[0] == 200


def test_open_controls_puts_it_back_the_way_it_was(base_url, monkeypatch):
    base, _ = base_url
    monkeypatch.setattr(srv.Handler, "is_local_client", lambda self: False)
    monkeypatch.setattr(srv.Handler, "client_ip", lambda self: "10.0.0.9")
    tid = two_entrants(base)
    assert call(base, "POST", f"/api/tournaments/{tid}/start")[0] == 403
    monkeypatch.setattr(LIVE["server"], "open_controls", True)
    assert call(base, "POST", f"/api/tournaments/{tid}/start")[0] == 200
    _, health = call(base, "GET", "/api/health")
    assert health["organiser"] is True


def test_the_pacing_pause_is_marked_as_its_own(base_url):
    """`paced` is what lets the bracket draw a paced match as playing.  The
    room's own Pause is not paced, and takes over one that is."""
    base, _ = base_url
    _, t = call(base, "POST", "/api/tournaments",
                {"stones": 30, "cards": 10, "time_limit": 60, "bot_delay": 20})
    tid = t["id"]
    for kind in ("client-python", "client-python"):
        status, _ = call(base, "POST", f"/api/tournaments/{tid}/join", {"kind": kind, "name": kind})
        if status != 200:
            pytest.skip("the python client cannot be launched here")
    call(base, "POST", f"/api/tournaments/{tid}/start")
    gid = start_next_match(base, tid)

    deadline = time.time() + 40                 # wait for the first paced pause
    while time.time() < deadline:
        _, g = call(base, "GET", f"/api/games/{gid}")
        if g["paused"]:
            break
        time.sleep(0.1)
    assert g["paused"] and g["paced"], "a tournament pause should be marked paced"

    # the room taking over: still paused, no longer the tournament's to end
    _, g = call(base, "POST", f"/api/games/{gid}/pause")
    assert g["paused"] is True and g["paced"] is False
    _, g = call(base, "POST", f"/api/games/{gid}/resume")
    assert g["paused"] is False and g["paced"] is False
    call(base, "POST", f"/api/tournaments/{tid}/abort")
