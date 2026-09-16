"""
Bots the server can seat by itself, so a table can be filled from the browser
without opening a terminal.

Two deliberately simple bots ship with the server: `greedy` and `random`.
If a file `private/server_bots.py` exists next to the repository root it is
loaded too; it must define `BOTS = {"key": ("Label", function)}`.  That folder
is not distributed, which lets the architects keep a stronger demo bot for
themselves.

Clients written in other languages are a different thing and live in
clients.py: the server starts those as child processes and they play over
HTTP.  The bots here run inside the server.

A bot function has the signature  fn(stones, my_cards, opp_cards) -> card
(lists are sorted).  It runs outside the game lock, so it may take time; the
move is then applied under the lock like any other move, and it is validated
the same way.
"""

from __future__ import annotations

import importlib.util
import os
import random
import sys
import threading
import time
from typing import Callable

from engine import STATUS_FINISHED, STATUS_PLAYING, IllegalMove

BotFn = Callable[[int, list, list], int]


def random_bot(stones: int, my_cards: list, opp_cards: list) -> int:
    """Wins if it can, otherwise plays a random card that fits."""
    if stones in my_cards:
        return stones
    fitting = [c for c in my_cards if c <= stones]
    return random.choice(fitting) if fitting else (min(my_cards) if my_cards else 1)


def greedy_bot(stones: int, my_cards: list, opp_cards: list) -> int:
    """Wins if it can; never hands the opponent an exact match if avoidable;
    otherwise plays the smallest fitting card to keep options open."""
    if stones in my_cards:
        return stones
    fitting = sorted(c for c in my_cards if c <= stones)
    if not fitting:
        return min(my_cards) if my_cards else 1
    safe = [c for c in fitting if (stones - c) not in opp_cards]
    return min(safe) if safe else min(fitting)


BUILTIN: dict[str, tuple[str, BotFn]] = {
    "greedy": ("Greedy bot", greedy_bot),
    "random": ("Random bot", random_bot),
}


def load_private(root: str) -> dict:
    """Purpose: pick up the architects' own bots from private/server_bots.py.
    Inputs: the repository root.  Outputs: {key: (label, fn)} or {}.
    Side effects: imports that file if it exists; a broken file is reported
    on stderr and ignored."""
    path = os.path.join(root, "private", "server_bots.py")
    if not os.path.isfile(path):
        return {}
    try:
        spec = importlib.util.spec_from_file_location("private_server_bots", path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        bots = getattr(module, "BOTS", {})
        return {str(k): (str(v[0]), v[1]) for k, v in bots.items() if callable(v[1])}
    except Exception as exc:  # noqa: BLE001 - a private add-on must never stop the server
        print(f"[warn] could not load {path}: {exc}", file=sys.stderr)
        return {}


def registry(root: str) -> dict[str, tuple[str, BotFn]]:
    """Purpose: every bot the server can seat: built-ins plus private ones."""
    bots = dict(BUILTIN)
    bots.update(load_private(root))
    return bots


class BotRunner(threading.Thread):
    """Plays one seat of one game until the game is over.

    Waits on the game's condition until it is the bot's turn, thinks outside
    the lock, sleeps `delay` seconds so people can follow the moves, then
    plays under the lock.  A strategy that raises or returns an illegal card
    falls back to the smallest fitting card, so a bug never forfeits on time.
    """

    def __init__(self, store, game, seat: int, fn: BotFn, delay: float = 0.8) -> None:
        super().__init__(name=f"bot-{game.id}-{seat}", daemon=True)
        self.store = store
        self.game = game
        self.seat = seat
        self.fn = fn
        self.delay = max(0.0, float(delay))
        self.token = game.seats[seat].token      # if the seat changes hands, this bot is done

    def _gone(self) -> bool:
        return self.game.seats[self.seat].token != self.token

    def _my_turn(self) -> bool:
        return (self.game.status == STATUS_PLAYING and not self.game.paused
                and self.game.turn == self.seat and not self._gone())

    def run(self) -> None:
        game, store, seat = self.game, self.store, self.seat
        other = game.other(seat)
        while True:
            with store.lock:
                store.wait_for(game, lambda: game.status == STATUS_FINISHED or self._gone() or self._my_turn(), 60)
                if game.status == STATUS_FINISHED or self._gone():
                    return
                if not self._my_turn():
                    continue
                stones = game.stones
                mine = sorted(game.seats[seat].cards)
                theirs = sorted(game.seats[other].cards)
            try:
                card = int(self.fn(stones, mine, theirs))
            except Exception:  # noqa: BLE001
                fitting = [c for c in mine if c <= stones]
                card = min(fitting) if fitting else (mine[0] if mine else 1)
            if self.delay:
                time.sleep(self.delay)
            with store.lock:
                if not self._my_turn():
                    continue
                try:
                    game.play(seat, card)
                except IllegalMove:
                    fitting = [c for c in game.seats[seat].cards if c <= game.stones]
                    if fitting:
                        try:
                            game.play(seat, min(fitting))
                        except IllegalMove:
                            pass
                store.changed(game)
