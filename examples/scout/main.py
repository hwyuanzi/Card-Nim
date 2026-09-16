#!/usr/bin/env python3
"""Team Scout: an entry made of several files, which is the point of it.

    python3 main.py --server http://localhost:8000 --game K7PX --name Scout [--seat 1]

CARDNIM_SERVER, CARDNIM_GAME, CARDNIM_NAME and CARDNIM_SEAT are used as
defaults, which is how the server starts a submission for a seat.

The three files:

    main.py       this one: the entry point, the arguments and the game loop
    protocol.py   the HTTP calls and the "key value" state format
    strategy.py   choose_card -- the part a team actually works on

The server runs `main.py`; Python finds the other two beside it, so a folder
(or a .zip of one) can be uploaded exactly like a single file.  Name the entry
point main.py, or tell the page which file starts the bot.
"""

import os
import sys
import time
import urllib.parse

from protocol import nums, parse_state, request
from strategy import choose_card


def option(flag, env_name, fallback):
    """Purpose: the command line over the environment over a default."""
    if flag in sys.argv[:-1]:
        return sys.argv[sys.argv.index(flag) + 1]
    return os.environ.get(env_name) or fallback


def safest_card(stones, my_cards):
    """Purpose: the smallest card that fits, played when the server refuses our
    choice, so a bug in the strategy cannot burn the clock."""
    fitting = [c for c in my_cards if c <= stones]
    return min(fitting) if fitting else (min(my_cards) if my_cards else 1)


def main():
    server = option("--server", "CARDNIM_SERVER", "http://localhost:8000").rstrip("/")
    game = option("--game", "CARDNIM_GAME", "")
    name = option("--name", "CARDNIM_NAME", "Scout")
    seat = option("--seat", "CARDNIM_SEAT", "")
    if not game:
        sys.exit("usage: python3 main.py --game ID [--server http://host:8000] [--name NAME] [--seat 1|2]")

    base = f"{server}/api/games/{game}"
    query = "?format=text&name=" + urllib.parse.quote(name) + (f"&seat={seat}" if seat else "")

    status, body = request("POST", base + "/join" + query)
    me = parse_state(body)
    if status != 200 or "token" not in me:
        sys.exit(f"[{name}] join failed ({status}): {me.get('error', body)}")
    token = me["token"]
    my_seat = int(me.get("seat", 0))
    print(f"[{name}] joined {game} as seat {my_seat}", flush=True)

    failures = 0
    while True:
        # getstate answers only when it is our turn or the game is over, so this
        # loop does not poll and waiting costs nothing on the clock.
        status, body = request("GET", f"{base}/getstate?format=text&timeout=60&token={token}")
        if status == 0:
            failures += 1
            if failures >= 30:
                sys.exit(f"[{name}] server gone, giving up")
            time.sleep(1)
            continue
        failures = 0
        if status != 200:
            sys.exit(f"[{name}] getstate failed ({status})")

        state = parse_state(body)
        if state.get("status") == "finished":
            winner = int(state.get("winner", 0))
            verdict = "WIN" if winner == my_seat else ("no winner" if not winner else "LOSS")
            print(f"[{name}] game over: {verdict}. {state.get('reason', '')}", flush=True)
            sys.exit(0 if winner == my_seat else 2)
        if state.get("your_turn") != "1":
            continue

        stones = int(state["stones"])
        mine = nums(state.get("your_cards"))
        card = choose_card(stones, mine, nums(state.get("opp_cards")))
        print(f"[{name}] {stones} stones, playing {card}", flush=True)

        status, _ = request("POST", f"{base}/move?format=text&token={token}&card={card}")
        if status == 200:
            continue
        print(f"[{name}] move {card} rejected ({status})", file=sys.stderr, flush=True)
        back = safest_card(stones, mine)
        if back != card:
            request("POST", f"{base}/move?format=text&token={token}&card={back}")
        else:
            time.sleep(1)    # not our turn any more or a hiccup: do not hammer it


if __name__ == "__main__":
    main()
