# Card Nim client in R.  Base R only, no packages: every call is a GET and the
# state comes back as "key value" lines (?format=text), so url() is enough.
#
#     Rscript client.R --server http://localhost:8000 --game K7PX --name "R bot" [--seat 1]
#
# CARDNIM_SERVER, CARDNIM_GAME, CARDNIM_NAME and CARDNIM_SEAT are used as
# defaults, which is how the server starts this client for a seat.
#
# Put your strategy in choose_card(). Everything else is plumbing.

# ============================================================ your strategy

# Purpose: pick the card to play.
# Inputs:  stones on the table, your cards, the opponent's cards (both sorted).
# Output:  one card from your hand. A card larger than stones loses at once.
choose_card <- function(stones, mine, theirs) {
  if (stones %in% mine) return(stones)                 # take the win
  fit <- sort(mine[mine <= stones])                    # cards that still fit
  if (length(fit) == 0) return(safest_card(stones, mine))
  safe <- fit[!((stones - fit) %in% theirs)]           # do not leave an exact match
  if (length(safe)) safe[1] else fit[1]
}

# Purpose: the smallest card that fits, played when the server refuses our
# choice, so a bug in the strategy cannot burn the clock.
safest_card <- function(stones, mine) {
  fit <- sort(mine[mine <= stones])
  if (length(fit)) fit[1] else if (length(mine)) mine[1] else 1L
}

# ============================================================ plumbing

options(timeout = 90)   # getstate may hold the line for 60 s

# Purpose: one GET; returns the body as a character vector of lines, or
# character(0) if the server refused or could not be reached.
get_lines <- function(url) {
  tryCatch({
    con <- url(url, open = "r")
    on.exit(close(con), add = TRUE)
    readLines(con, warn = FALSE)
  }, error = function(e) character(0), warning = function(w) character(0))
}

# Purpose: turn "key value value" lines into a named list of strings.
parse_state <- function(lines) {
  out <- list()
  for (line in lines) {
    line <- sub("\r$", "", line)
    if (!nzchar(line)) next
    sp <- regexpr(" ", line, fixed = TRUE)
    if (sp < 0) { out[[line]] <- "" } else {
      out[[substr(line, 1, sp - 1)]] <- substr(line, sp + 1, nchar(line))
    }
  }
  out
}

nums <- function(s) {
  if (is.null(s) || !nzchar(s)) return(integer(0))
  as.integer(strsplit(trimws(s), "[[:space:]]+")[[1]])
}

# Purpose: the command line over the environment over a default.
opt <- function(flag, env, dflt) {
  a <- commandArgs(trailingOnly = TRUE)
  i <- match(flag, a)
  if (!is.na(i) && i < length(a)) return(a[i + 1])
  v <- Sys.getenv(env)
  if (nzchar(v)) v else dflt
}

server <- sub("/+$", "", opt("--server", "CARDNIM_SERVER", "http://localhost:8000"))
game   <- opt("--game", "CARDNIM_GAME", "")
name   <- opt("--name", "CARDNIM_NAME", "R bot")
seat   <- opt("--seat", "CARDNIM_SEAT", "")

if (!nzchar(game)) {
  write("usage: Rscript client.R --game ID [--server http://host:8000] [--name NAME] [--seat 1|2]", stderr())
  quit(status = 1)
}

base <- paste0(server, "/api/games/", game)

# ============================================================ join

query <- paste0("?format=text&name=", URLencode(name, reserved = TRUE),
                if (nzchar(seat)) paste0("&seat=", seat) else "")
joined <- parse_state(get_lines(paste0(base, "/join", query)))
if (is.null(joined$token)) {
  write(paste0("[", name, "] join failed"), stderr())
  quit(status = 1)
}
token <- joined$token
my_seat <- as.integer(joined$seat)
cat(sprintf("[%s] joined %s as seat %d\n", name, game, my_seat))
flush(stdout())

# ============================================================ game loop

failures <- 0
repeat {
  # getstate answers only when it is our turn or the game is over, so this
  # loop does not poll and waiting costs nothing on the clock.
  s <- parse_state(get_lines(paste0(base, "/getstate?format=text&timeout=60&token=", token)))
  if (is.null(s$status)) {
    failures <- failures + 1
    if (failures >= 30) { write(paste0("[", name, "] server gone"), stderr()); quit(status = 1) }
    Sys.sleep(1)
    next
  }
  failures <- 0
  if (identical(s$status, "finished")) {
    w <- as.integer(s$winner)
    verdict <- if (w == my_seat) "WIN" else if (w > 0) "LOSS" else "no winner"
    cat(sprintf("[%s] game over: %s. %s\n", name, verdict, s$reason))
    quit(status = if (w == my_seat) 0 else 2)
  }
  if (!identical(s$your_turn, "1")) next

  stones <- as.integer(s$stones)
  mine <- nums(s$your_cards)
  card <- choose_card(stones, mine, nums(s$opp_cards))
  cat(sprintf("[%s] %d stones, playing %d\n", name, stones, card))
  flush(stdout())

  played <- parse_state(get_lines(paste0(base, "/move?format=text&token=", token, "&card=", card)))
  if (is.null(played$status)) {
    back <- safest_card(stones, mine)
    if (back != card) {
      write(sprintf("[%s] %d refused, playing %d", name, card, back), stderr())
      get_lines(paste0(base, "/move?format=text&token=", token, "&card=", back))
    } else {
      Sys.sleep(1)   # not our turn any more or a hiccup: do not hammer it
    }
  }
}
