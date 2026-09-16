"""Talking to the server: one HTTP call and the "key value" state format.

Nothing in here is strategy, which is why it sits in its own file.  The text
format (?format=text) means there is no JSON to parse:

    status playing | waiting | finished
    you 2                    your seat
    your_turn 1              1 or 0
    stones 37
    your_cards 1 2 4 5
    opp_cards 1 3 6
    winner 0                 0, 1 or 2
"""

import urllib.error
import urllib.request


def request(method, url):
    """Purpose: one HTTP request.
    Outputs: (status, body); status 0 means the server could not be reached."""
    data = b"" if method == "POST" else None
    req = urllib.request.Request(url, data=data, method=method)
    try:
        with urllib.request.urlopen(req, timeout=90) as response:  # getstate holds for 60 s
            return response.status, response.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode(errors="replace")
    except OSError:
        return 0, ""


def parse_state(text):
    """Purpose: turn "key value value" lines into a dict of strings."""
    state = {}
    for line in (text or "").splitlines():
        if not line.strip():
            continue
        key, _, rest = line.partition(" ")
        state[key] = rest
    return state


def nums(text):
    """Purpose: "1 2 4 5" -> [1, 2, 4, 5]."""
    return [int(word) for word in (text or "").split()]
