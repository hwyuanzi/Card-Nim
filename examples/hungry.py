#!/usr/bin/env python3
"""
Card Nim Python client.  Standard library only.

Two ways to use it:

  1. As a program (sample bots):
        python3 client.py --server http://localhost:8000 --game K7PX --name Randy --bot random
        python3 client.py --game K7PX --name Greta --bot greedy --seat 2
        python3 client.py --tournament Q4RT --name Greta --bot greedy     # enters a knockout, plays every round
     Options may also come from environment variables CARDNIM_SERVER,
     CARDNIM_GAME, CARDNIM_TOURNAMENT, CARDNIM_NAME, CARDNIM_SEAT, CARDNIM_BOT
     (scripts/run_match.py sets them).

  2. As a library for your own bot:
        from client import CardNimClient
        def my_brain(stones, my_cards, opp_cards, state): ...return a card...
        CardNimClient("http://host:8000", "K7PX", "MyTeam").play(my_brain)

The protocol (see docs/API.md):
    POST /api/games/{id}/join   {name, seat?}   -> {seat, token, state}
    GET  /api/games/{id}/getstate?token=T       -> state; returns when it is your turn or the game is over
    POST /api/games/{id}/move   {card, token}    -> state; 409 + {"error"} if the move is rejected

Tournaments add two calls (CardNimTournament below):
    POST /api/tournaments/{id}/join {name, kind: "api"}  -> {entrant, token}
    GET  /api/tournaments/{id}/getstate?token=T          -> returns when you have a game to sit down in,
                                                             or you are out, or you won
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Optional

BotFn = Callable[[int, list, list, dict], int]


class CardNimError(Exception):
    pass


class CardNimClient:
    """Thin wrapper over the HTTP API.  One instance = one seat in one game."""

    def __init__(self, server: str, game_id: str, name: str, seat: Optional[int] = None, verbose: bool = True,
                 avatar: Optional[int] = None):
        """Inputs: server base URL (http://host:port), game id, display name,
        optional seat preference, optional avatar number 1..16 (the server
        picks one from the name otherwise).  Side effects: none until join()."""
        self.server = server.rstrip("/")
        self.game_id = game_id.upper()
        self.name = name
        self.seat_pref = seat
        self.avatar = avatar
        self.seat: Optional[int] = None
        self.token: Optional[str] = None
        self.verbose = verbose

    # ------------------------------------------------------------ transport

    RETRIES = 5          # connection failures are retried (server restarting, Wi-Fi hiccup)
    RETRY_DELAY = 1.0

    def _request(self, method: str, path: str, body: Optional[dict] = None, timeout: float = 90.0) -> dict:
        """Purpose: one HTTP call returning the decoded JSON body.
        Raises CardNimError with the server's message on 4xx/5xx, or after
        RETRIES failed connection attempts."""
        url = f"{self.server}/api/games/{self.game_id}{path}"
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["X-Token"] = self.token
        last_error = "unknown error"
        for attempt in range(self.RETRIES):
            req = urllib.request.Request(url, data=data, method=method, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return json.loads(resp.read().decode())
            except urllib.error.HTTPError as exc:
                try:
                    message = json.loads(exc.read().decode()).get("error", exc.reason)
                except Exception:
                    message = exc.reason
                if exc.code >= 500 and attempt < self.RETRIES - 1:
                    last_error = f"{exc.code}: {message}"
                    time.sleep(self.RETRY_DELAY)
                    continue
                raise CardNimError(f"{exc.code}: {message}") from None
            except (urllib.error.URLError, OSError, ValueError) as exc:
                last_error = f"cannot reach {url}: {getattr(exc, 'reason', exc)}"
                if attempt < self.RETRIES - 1:
                    self.log(f"{last_error}; retrying")
                    time.sleep(self.RETRY_DELAY)
        raise CardNimError(last_error)

    # ------------------------------------------------------------ game API

    def join(self) -> dict:
        """Purpose: take a seat.  Outputs: the state.  Side effects: stores token/seat."""
        body = {"name": self.name}
        if self.seat_pref:
            body["seat"] = self.seat_pref
        if self.avatar:
            body["avatar"] = self.avatar
        payload = self._request("POST", "/join", body)
        self.seat, self.token = payload["seat"], payload["token"]
        self.log(f"joined game {self.game_id} as seat {self.seat} ({self.name})")
        return payload["state"]

    def getstate(self) -> dict:
        """Purpose: the course's getstate.  Blocks until it is your turn or the
        game is over, then returns the full state dict.  Waiting costs no clock."""
        while True:
            state = self._request("GET", "/getstate?timeout=60", timeout=90)
            if state["status"] == "finished" or state["your_turn"]:
                return state
            # server gave up waiting after 60 s with nothing new: ask again

    def sendmove(self, card: int) -> dict:
        """Purpose: play a card.  Outputs: the new state.
        Raises CardNimError if the server rejects the move (state unchanged)."""
        return self._request("POST", "/move", {"card": int(card)})

    def state(self) -> dict:
        """Purpose: fetch the state without waiting (for logging/debugging)."""
        return self._request("GET", "")

    # ------------------------------------------------------------ loop

    def play(self, brain: BotFn) -> dict:
        """Purpose: run a whole game.  Inputs: brain(stones, my_cards, opp_cards, state) -> card.
        Outputs: the final state.  Side effects: joins if not yet joined; prints progress."""
        if self.token is None:
            self.join()
        while True:
            state = self.getstate()
            if state["status"] == "finished":
                self.report(state)
                return state
            me = state["players"][self.seat - 1]
            opp = state["players"][2 - self.seat]
            legal = [c for c in me["cards"] if c <= state["stones"]] or list(me["cards"])
            t0 = time.time()
            try:
                card = int(brain(state["stones"], list(me["cards"]), list(opp["cards"]), state))
            except Exception as exc:  # noqa: BLE001 - a bug in the strategy must not forfeit on time
                self.log(f"strategy raised {exc!r}; playing the smallest legal card instead")
                card = min(legal) if legal else 1
            self.log(f"{state['stones']} stones, my cards {me['cards']} -> playing {card} ({time.time() - t0:.2f}s)")
            try:
                self.sendmove(card)
            except CardNimError as exc:
                self.log(f"move {card} rejected: {exc}")
                # Fall back to something legal so a bug does not cost the game on time.
                fallback = [c for c in legal if c != card]
                if fallback:
                    try:
                        self.sendmove(min(fallback))
                    except CardNimError as exc2:
                        self.log(f"fallback rejected too: {exc2}")   # not our turn any more; getstate will tell

    def report(self, state: dict) -> None:
        winner = state["winner"]
        outcome = "draw/aborted" if not winner else ("WIN" if winner == self.seat else "LOSS")
        self.log(f"game over: {outcome}. {state['reason']}")

    def log(self, msg: str) -> None:
        if self.verbose:
            print(f"[{self.name}] {msg}", flush=True)


class CardNimTournament:
    """Enter a knockout tournament and play every round with the same brain.

    The entrant token is the key to each match: sending it when joining a
    game opens the seat the bracket reserved for you, and the server then
    uses it as the seat token too.
    """

    def __init__(self, server: str, tournament_id: str, name: str, verbose: bool = True,
                 avatar: Optional[int] = None):
        self.server = server.rstrip("/")
        self.tournament_id = tournament_id.upper()
        self.name = name
        self.avatar = avatar
        self.verbose = verbose
        self.token: Optional[str] = None
        self.entrant: Optional[dict] = None

    def _request(self, method: str, path: str, body: Optional[dict] = None, timeout: float = 90.0) -> dict:
        """Purpose: one call to /api/tournaments/{id}...; same retries and
        errors as CardNimClient._request."""
        url = f"{self.server}/api/tournaments/{self.tournament_id}{path}"
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["X-Token"] = self.token
        last_error = "unknown error"
        for attempt in range(CardNimClient.RETRIES):
            req = urllib.request.Request(url, data=data, method=method, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return json.loads(resp.read().decode())
            except urllib.error.HTTPError as exc:
                try:
                    message = json.loads(exc.read().decode()).get("error", exc.reason)
                except Exception:
                    message = exc.reason
                if exc.code >= 500 and attempt < CardNimClient.RETRIES - 1:
                    last_error = f"{exc.code}: {message}"
                    time.sleep(CardNimClient.RETRY_DELAY)
                    continue
                raise CardNimError(f"{exc.code}: {message}") from None
            except (urllib.error.URLError, OSError, ValueError) as exc:
                last_error = f"cannot reach {url}: {getattr(exc, 'reason', exc)}"
                if attempt < CardNimClient.RETRIES - 1:
                    self.log(f"{last_error}; retrying")
                    time.sleep(CardNimClient.RETRY_DELAY)
        raise CardNimError(last_error)

    def join(self) -> dict:
        """Purpose: enter the bracket as a program.  Outputs: the entrant record.
        Side effects: stores the token.  Fails (409) once the bracket has started."""
        body = {"name": self.name, "kind": "api"}
        if self.avatar:
            body["avatar"] = self.avatar
        payload = self._request("POST", "/join", body)
        self.token = payload["token"]
        self.entrant = payload["entrant"]
        self.log(f"entered tournament {self.tournament_id} as {self.entrant['name']} (entrant {self.entrant['id']})")
        return self.entrant

    def wait(self) -> dict:
        """Purpose: block until there is something to do.  Outputs: the entrant
        view with status play (game and seat set), eliminated, champion or finished."""
        while True:
            view = self._request("GET", "/getstate?timeout=60", timeout=90)
            if view["status"] in ("play", "eliminated", "champion", "finished"):
                return view
            self.log(f"waiting ({view['status']})")

    def play(self, brain: BotFn) -> dict:
        """Purpose: play the whole tournament.  Inputs: brain as in CardNimClient.play.
        Outputs: the final entrant view.  Side effects: joins if needed; plays
        one game per round until out or champion."""
        if self.token is None:
            self.join()
        while True:
            view = self.wait()
            if view["status"] != "play":
                self.log(f"tournament over for me: {view['status']}"
                         + (f" (lost to {view['lost_to']} in the {view['round_name']})" if view.get("lost_to") else ""))
                return view
            self.log(f"{view['round_name']} vs {view['opponent']}: game {view['game']}, seat {view['seat']}")
            game = CardNimClient(self.server, view["game"], self.name, view["seat"], verbose=self.verbose, avatar=self.avatar)
            game.token = self.token          # opens the reserved seat; the server keeps it as the seat token
            try:
                game.join()
                game.play(brain)
            except CardNimError as exc:
                self.log(f"game {view['game']}: {exc}")
                time.sleep(1.0)              # the bracket will tell us what happened next

    def log(self, msg: str) -> None:
        if self.verbose:
            print(f"[{self.name}] {msg}", flush=True)


# ================================================================== sample bots
# Each bot is a function (stones, my_cards, opp_cards, state) -> card.

def random_bot(stones, my_cards, opp_cards, state) -> int:
    """Wins if it can, otherwise plays a random card that fits."""
    if stones in my_cards:
        return stones
    fitting = [c for c in my_cards if c <= stones]
    return random.choice(fitting) if fitting else (min(my_cards) if my_cards else 1)


def greedy_bot(stones, my_cards, opp_cards, state) -> int:
    """Team Hungry: take the win if it is there, otherwise always play the
    BIGGEST card that fits.  The opposite of cautious.rb, so in a match between
    them you can tell which is which from the move log alone."""
    if stones in my_cards:
        return stones
    fitting = [c for c in my_cards if c <= stones]
    return max(fitting) if fitting else min(my_cards)


BOTS = {"random": random_bot, "greedy": greedy_bot}


def main(argv=None) -> int:
    def env(name, default=None):
        """An unset *or empty* variable means "not given", so exporting
        CARDNIM_AVATAR= does not turn into int("")."""
        return os.environ.get(name) or default

    parser = argparse.ArgumentParser(description="Card Nim sample client")
    parser.add_argument("--server", default=env("CARDNIM_SERVER", "http://localhost:8000"))
    parser.add_argument("--game", default=env("CARDNIM_GAME"), help="game id shown in the lobby, e.g. K7PX")
    parser.add_argument("--tournament", default=env("CARDNIM_TOURNAMENT"),
                        help="enter this knockout tournament instead and play every round")
    parser.add_argument("--name", default=env("CARDNIM_NAME", "Python bot"))
    parser.add_argument("--seat", type=int, default=int(env("CARDNIM_SEAT", "0")) or None, choices=[1, 2])
    parser.add_argument("--bot", default=env("CARDNIM_BOT", "greedy"), choices=sorted(BOTS))
    parser.add_argument("--avatar", type=int, default=int(env("CARDNIM_AVATAR", "0")) or None,
                        help="picture 1..16 shown on the board (default: picked from the name)")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    if not args.game and not args.tournament:
        parser.error("--game (or CARDNIM_GAME) or --tournament is required")
    try:
        if args.tournament:
            entry = CardNimTournament(args.server, args.tournament, args.name, verbose=not args.quiet, avatar=args.avatar)
            final = entry.play(BOTS[args.bot])
            return 0 if final.get("status") == "champion" else 2
        client = CardNimClient(args.server, args.game, args.name, args.seat, verbose=not args.quiet, avatar=args.avatar)
        final = client.play(BOTS[args.bot])
    except CardNimError as exc:
        print(f"[{args.name}] error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0 if final.get("winner") == client.seat else 2


if __name__ == "__main__":
    sys.exit(main())
