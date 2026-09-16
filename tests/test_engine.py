"""Unit tests for the rules engine.  Run:  python3 -m pytest tests/ -q"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
from engine import Game, GameError, IllegalMove  # noqa: E402


class FakeClock:
    """A monotonic clock the tests can advance by hand."""

    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def seated_game(stones, cards, time_limit=120.0):
    clock = FakeClock()
    g = Game(stones, cards, time_limit, clock=clock)
    g.join("Alice", 1)
    g.join("Bob", 2)
    return g, clock


def test_parameters_are_validated():
    with pytest.raises(ValueError):
        Game(0, 5)
    with pytest.raises(ValueError):
        Game(501, 5)
    with pytest.raises(ValueError):
        Game(10, 0)
    with pytest.raises(ValueError):
        Game(10, 201)


def test_join_fills_seats_and_starts():
    g = Game(20, 5)
    assert g.status == "waiting"
    s1 = g.join("Alice")
    assert s1.number == 1 and s1.token
    assert g.status == "waiting"
    s2 = g.join("Bob")
    assert s2.number == 2 and s2.token != s1.token
    assert g.status == "playing" and g.turn == 1
    with pytest.raises(GameError):
        g.join("Carol")


def test_join_specific_seat():
    g = Game(20, 5)
    s2 = g.join("Bob", 2)
    assert s2.number == 2
    with pytest.raises(GameError):
        g.join("Carol", 2)
    s1 = g.join("Alice")           # takes the only free seat
    assert s1.number == 1
    assert g.status == "playing"


def test_professors_example_second_player_wins():
    """5 stones, cards 1-3.  Alice (first) is lost with perfect play from Bob."""
    g, _ = seated_game(5, 3)
    g.play(1, 1)                   # Alice must avoid 2 and 3
    assert g.stones == 4 and g.turn == 2
    g.play(2, 3)                   # Bob leaves 1
    assert g.stones == 1
    # Alice holds {2, 3}: nothing fits -> automatic loss
    assert g.status == "finished" and g.winner == 2
    assert "no card small enough" in g.reason


def test_exact_match_wins():
    g, _ = seated_game(5, 3)
    g.play(1, 2)
    g.play(2, 3)
    assert g.status == "finished" and g.winner == 2 and g.stones == 0
    assert "last 3 stones" in g.reason


def test_overdraw_loses_and_leaves_pile_untouched():
    g, _ = seated_game(5, 3)
    g.play(1, 3)                   # 2 left
    g.play(2, 3)                   # Bob overdraws
    assert g.status == "finished" and g.winner == 1
    assert g.stones == 2
    assert g.moves[-1].overdraw and g.moves[-1].stones_after == -1


def test_illegal_moves_do_not_change_state():
    g, _ = seated_game(10, 4)
    with pytest.raises(IllegalMove):
        g.play(2, 1)               # not Bob's turn
    with pytest.raises(IllegalMove):
        g.play(1, 7)               # not a card
    with pytest.raises(IllegalMove):
        g.play(1, "x")             # not an integer
    g.play(1, 2)
    with pytest.raises(IllegalMove):
        g.play(2, 0)
    g.play(2, 2)
    with pytest.raises(IllegalMove):
        g.play(1, 2)               # already used
    assert g.stones == 6 and g.turn == 1 and g.status == "playing"


def test_the_last_move_is_charged_once():
    """The move that ends the game must cost its player exactly the time they
    spent on it.  _finish() also freezes the clock of whoever is on turn, so
    the winning move used to be charged twice and the final board showed a
    clock that never ran."""
    for stones_left, card, winner in ((4, 4, 1), (3, 4, 2)):   # exact finish, overdraw
        g, clock = seated_game(50, 10, time_limit=60)
        clock.advance(4.0)
        g.play(1, 3)                       # an ordinary move first
        clock.advance(5.0)
        g.play(2, 1)
        before = g.seats[1].time_remaining
        g.stones = stones_left
        clock.advance(6.0)
        g.play(1, card)
        assert g.winner == winner
        assert before - g.seats[1].time_remaining == pytest.approx(6.0)
        assert g.moves[-1].elapsed == pytest.approx(6.0)
        assert g.time_remaining(1, clock()) == pytest.approx(before - 6.0)


def test_resigning_and_timing_out_still_charge_the_turn_in_progress():
    """The counterpart: _finish() from anywhere other than play() must still
    bill the thinking time of the player on turn."""
    g, clock = seated_game(50, 10, time_limit=60)
    clock.advance(7.0)
    g.leave(1)                             # resign while on turn
    assert g.winner == 2
    assert g.seats[1].time_remaining == pytest.approx(53.0)

    g, clock = seated_game(50, 10, time_limit=60)
    clock.advance(61.0)
    assert g.check_timeout(clock())
    assert g.winner == 2 and g.seats[1].time_remaining == 0.0


def test_clock_runs_only_on_your_turn():
    g, clock = seated_game(50, 10, time_limit=60)
    clock.advance(4.0)
    assert g.time_remaining(1, clock()) == pytest.approx(56.0)
    assert g.time_remaining(2, clock()) == pytest.approx(60.0)
    g.play(1, 5)
    assert g.moves[0].elapsed == pytest.approx(4.0)
    clock.advance(10.0)
    assert g.time_remaining(1, clock()) == pytest.approx(56.0)   # frozen
    assert g.time_remaining(2, clock()) == pytest.approx(50.0)


def test_timeout_loses():
    g, clock = seated_game(50, 10, time_limit=30)
    clock.advance(29.9)
    assert not g.check_timeout(clock())
    clock.advance(0.2)
    assert g.check_timeout(clock())
    assert g.status == "finished" and g.winner == 2
    assert "ran out of time" in g.reason
    assert g.time_remaining(1, clock()) == 0.0
    with pytest.raises(IllegalMove):
        g.play(1, 1)


def test_play_after_timeout_is_rejected_even_without_ticker():
    g, clock = seated_game(50, 10, time_limit=30)
    clock.advance(31)
    with pytest.raises(IllegalMove):
        g.play(1, 1)
    assert g.winner == 2


def test_leave_before_start_frees_the_seat():
    g = Game(20, 5)
    s1 = g.join("Alice", 1)
    old_token = s1.token
    g.leave(1)
    assert not g.seats[1].occupied and g.seats[1].name == ""
    assert g.seat_for_token(old_token) is None
    assert g.status == "waiting"
    s1b = g.join("Carol", 1)             # the seat can be taken again
    assert s1b.token != old_token
    g.join("Bob", 2)
    assert g.status == "playing"


def test_leave_during_play_is_a_resignation():
    g, _ = seated_game(20, 5)
    g.play(1, 2)
    g.leave(2)
    assert g.status == "finished" and g.winner == 1 and "resigned" in g.reason
    with pytest.raises(GameError):
        g.leave(1)


def test_names_and_labels_are_single_line():
    g = Game(3, 5, label="  Round\n1:\tA  vs B \x00 ")
    assert g.label == "Round 1: A vs B"
    g.join("Al\nice\r\n", 1)
    assert g.seats[1].name == "Al ice"
    g.join("   ", 2)                                   # blank -> anonymous
    assert g.seats[2].name == "anonymous"
    g.play(1, 1)                                       # 2 stones left
    g.play(2, 5)                                       # overdraw: reason mentions the name
    assert "\n" not in g.reason and "anonymous" in g.reason
    assert all(line.count("\n") == 0 for line in g.to_text().split("\n"))


def test_avatars_are_assigned_and_distinct():
    g = Game(20, 5)
    s1 = g.join("Alice", 1)
    assert 1 <= s1.avatar <= 16
    s1b = Game(20, 5).join("Alice", 1)
    assert s1b.avatar == s1.avatar                      # same name, same face
    s2 = g.join("Alice", 2)                             # same name at the other seat
    assert s2.avatar != s1.avatar                       # never the same as the opponent
    g2 = Game(20, 5)
    assert g2.join("X", 1, avatar=7).avatar == 7        # explicit choice wins
    with pytest.raises(GameError):
        g2.join("Y", 2, avatar=17)
    assert g2.seats[2].occupied is False                # rejected join changed nothing
    g2.join("Y", 2, avatar=7)                           # choosing the same as the opponent is allowed
    assert g2.seats[2].avatar == 7
    d = g2.to_dict()
    assert [p["avatar"] for p in d["players"]] == [7, 7]
    assert g2.summary()["avatars"] == [7, 7]


def test_abort():
    g, _ = seated_game(50, 10)
    g.abort("test")
    assert g.status == "finished" and g.winner is None and g.reason == "test"


def test_version_bumps_on_every_change():
    g = Game(20, 5)
    v = g.version
    g.join("A")
    assert g.version > v
    v = g.version
    g.join("B")
    assert g.version > v
    v = g.version
    g.play(1, 1)
    assert g.version > v
    v = g.version
    with pytest.raises(IllegalMove):
        g.play(1, 1)
    assert g.version == v


def test_to_dict_and_text_views():
    g, clock = seated_game(30, 6)
    g.play(1, 4)
    d = g.to_dict(viewer_seat=2)
    assert d["stones"] == 26 and d["turn"] == 2 and d["your_turn"] is True
    assert d["players"][0]["cards"] == [1, 2, 3, 5, 6]
    assert d["players"][1]["playable"] == [1, 2, 3, 4, 5, 6]
    assert d["last_move"] == {"seat": 1, "card": 4}
    t = g.to_text(viewer_seat=2)
    lines = dict(line.split(" ", 1) for line in t.strip().splitlines())
    assert lines["stones"] == "26" and lines["your_turn"] == "1"
    assert lines["your_cards"] == "1 2 3 4 5 6" and lines["opp_cards"] == "1 2 3 5 6"
    assert lines["last_move"] == "1 4"


def test_summary():
    g, _ = seated_game(30, 6)
    s = g.summary()
    assert s["players"] == ["Alice", "Bob"] and s["status"] == "playing" and s["turn"] == 1


def test_a_card_must_be_a_whole_number():
    """int(2.7) is 2, which would quietly play a card the caller never asked
    for.  The HTTP layer rejects these first, but the engine is the rules and
    has to refuse them on its own."""
    for bad in (2.7, -0.5, True, False, None, [3], {}):
        g, _ = seated_game(20, 6)
        with pytest.raises(IllegalMove):
            g.play(1, bad)
        assert not g.moves, f"{bad!r} was played"
    # a whole float is still a card, and so is a numeric string: query and
    # form parameters arrive as text, so "3" has to mean the card 3
    for good, expected in ((3.0, 3), ("4", 4)):
        g, _ = seated_game(20, 6)
        g.play(1, good)
        assert g.moves[-1].card == expected, good
