"""Team Scout's strategy: the only file most teams need to touch.

Everything else in this folder is plumbing copied from the sample client.
"""


def choose_card(stones, my_cards, opp_cards):
    """Purpose: pick the card to play.
    Inputs:  stones on the table, your cards, the opponent's cards (both
             sorted).
    Output:  one card from your hand.  A card larger than stones loses at once.

    Scout looks one move ahead: first the win, then a move that leaves the
    opponent nothing they can legally play, then anything that does not hand
    them an exact match."""
    if stones in my_cards:
        return stones                                    # take the win
    fitting = [c for c in my_cards if c <= stones]
    if not fitting:
        return min(my_cards) if my_cards else 1          # nothing fits: any card loses

    def leaves(card):
        return stones - card

    stuck = [c for c in fitting if all(o > leaves(c) for o in opp_cards)]
    if stuck:
        return min(stuck)                                # they cannot move: we win
    safe = [c for c in fitting if leaves(c) not in opp_cards]
    return min(safe) if safe else min(fitting)           # never hand over an exact match
