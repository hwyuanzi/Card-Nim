# Example submissions

Two finished entries, the kind a team hands in. Upload either one from the
bracket page to check the whole path works before competition night.

| File | Language | What it does |
|---|---|---|
| `hungry.py` | Python | takes the win, else always the **biggest** card that fits |
| `cautious.rb` | Ruby | takes the win, else always the **smallest** |

They play opposite ways on purpose: put them against each other and the move
log tells you which is which without looking at the names.

    hungry.py     15  14  13
    cautious.rb    1   2  15

## How they were made

Each is a copy of the sample client for its language with **one function
replaced** — everything else, the joining and the getstate loop and the move
sending, is untouched. That is all a team has to do:

    clients/python/client.py   ->  examples/hungry.py     (edited brain/greedy_bot)
    clients/ruby/client.rb     ->  examples/cautious.rb   (edited choose_card)

The samples for every language are in `clients/`, one folder each. Copy the
one for your language, change the strategy function, upload the file.
`docs/CLIENTS.md` has the details, including the one Java rule: the file has
to be named after your class.
