# Example submissions

Three finished entries, the kind a team hands in. Upload any of them from the
bracket page to check the whole path works before competition night.

| What to send | Language | What it does |
|---|---|---|
| `hungry.py` | Python | takes the win, else always the **biggest** card that fits |
| `cautious.rb` | Ruby | takes the win, else always the **smallest** |
| `scout/` (3 files) | Python | takes the win, else leaves the opponent **stuck** if it can |

The first two play opposite ways on purpose: put them against each other and
the move log tells you which is which without looking at the names.

    hungry.py     15  14  13
    cautious.rb    1   2  15

## How they were made

`hungry.py` and `cautious.rb` are each a copy of the sample client for their
language with **one function replaced** — everything else, the joining and the
getstate loop and the move sending, is untouched. That is all a team has to do:

    clients/python/client.py   ->  examples/hungry.py     (edited brain/greedy_bot)
    clients/ruby/client.rb     ->  examples/cautious.rb   (edited choose_card)

The samples for every language are in `clients/`, one folder each. Copy the one
for your language, change the strategy function, upload it.

## A submission in several files

`scout/` is the same client split the way a team would split it once it grows:

    scout/
      main.py       the entry point: arguments and the game loop
      protocol.py   the HTTP calls and the "key value" state format
      strategy.py   choose_card -- the only part worth arguing about

Send all three (pick them together, pick the folder, or zip it) and the server
keeps the layout, runs `main.py`, and Python finds the other two beside it.
Nothing about this is Python-only: C++ submissions are compiled together,
`Main.java` may bring as many classes as it likes, a Go submission may carry
its `go.mod`.

The one rule is that the server has to know **which file starts the bot**: call
it `main.py` (or `Main.java`, `main.cpp`, …) and it is found; otherwise the
page asks which one to run.

`docs/CLIENTS.md` has the details, including the one Java rule: the file has to
be named after your class.
