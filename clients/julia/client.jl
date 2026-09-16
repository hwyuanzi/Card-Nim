#!/usr/bin/env julia
# Card Nim client in Julia.  No packages: Downloads ships with Julia.
#
#     julia client.jl --server http://localhost:8000 --game K7PX --name "Julia bot" [--seat 1]
#
# CARDNIM_SERVER, CARDNIM_GAME, CARDNIM_NAME and CARDNIM_SEAT are used as
# defaults, which is how the server starts this client for a seat.
#
# The state comes back as "key value" lines (?format=text), so there is no
# JSON to parse:
#
#     status playing | waiting | finished
#     you 2                    your seat
#     your_turn 1              1 or 0
#     stones 37
#     your_cards 1 2 4 5
#     opp_cards 1 3 6
#     winner 0                 0, 1 or 2
#     reason ...
#
# Put your strategy in choose_card. Everything else is plumbing.

using Downloads

# ============================================================ your strategy

# Purpose: pick the card to play.
# Inputs:  stones on the table, your cards, the opponent's cards (both sorted).
# Output:  one card from your hand.  A card larger than stones loses at once.
function choose_card(stones::Int, my_cards::Vector{Int}, opp_cards::Vector{Int})
    stones in my_cards && return stones                  # take the win
    fit = filter(c -> c <= stones, my_cards)
    isempty(fit) && return isempty(my_cards) ? 1 : first(my_cards)  # nothing fits: any card loses
    safe = filter(c -> !(stones - c in opp_cards), fit)  # do not leave an exact match
    minimum(isempty(safe) ? fit : safe)
end

# Purpose: the smallest card that fits, played when the server refuses our
# choice, so a bug in the strategy cannot burn the clock.
function safest_card(stones::Int, my_cards::Vector{Int})
    fit = filter(c -> c <= stones, my_cards)
    isempty(fit) ? (isempty(my_cards) ? 1 : first(my_cards)) : minimum(fit)
end

# ============================================================ plumbing

# Purpose: percent-encode a name for a query parameter.
function encode(text::AbstractString)
    out = IOBuffer()
    for byte in codeunits(text)
        if byte in UInt8('A'):UInt8('Z') || byte in UInt8('a'):UInt8('z') ||
           byte in UInt8('0'):UInt8('9') || Char(byte) in ('-', '_', '.', '~')
            write(out, byte)
        else
            print(out, '%', uppercase(string(byte, base = 16, pad = 2)))
        end
    end
    String(take!(out))
end

# Purpose: one HTTP request.  Outputs: (status, body); status 0 means the
# server could not be reached.
function request(method::AbstractString, url::AbstractString)
    body = IOBuffer()
    try
        # getstate may hold the line for 60 s, so the timeout has to be longer
        res = Downloads.request(url; method = method, output = body, throw = false, timeout = 90)
        status = res isa Downloads.Response ? res.status : 0
        return status, String(take!(body))
    catch
        return 0, ""
    end
end

# Purpose: turn "key value value" lines into a Dict of strings.
function parse_state(text::AbstractString)
    out = Dict{String,String}()
    for raw in split(text, '\n')
        line = rstrip(raw, '\r')
        isempty(line) && continue
        at = findfirst(isequal(' '), line)
        if at === nothing
            out[String(line)] = ""
        else
            out[String(line[1:prevind(line, at)])] = String(line[nextind(line, at):end])
        end
    end
    out
end

nums(text::AbstractString) = [parse(Int, word) for word in split(text)]

# Purpose: the command line over the environment over a default.
function option(flag::AbstractString, env_name::AbstractString, fallback::AbstractString)
    at = findfirst(==(flag), ARGS)
    at !== nothing && at < length(ARGS) && return ARGS[at+1]
    value = get(ENV, env_name, "")
    isempty(value) ? fallback : value
end

# ============================================================ game loop

function main()
    server = rstrip(option("--server", "CARDNIM_SERVER", "http://localhost:8000"), '/')
    game   = option("--game", "CARDNIM_GAME", "")
    name   = option("--name", "CARDNIM_NAME", "Julia bot")
    seat   = option("--seat", "CARDNIM_SEAT", "")

    if isempty(game)
        println(stderr, "usage: julia client.jl --game ID [--server http://host:8000] [--name NAME] [--seat 1|2]")
        exit(1)
    end

    base = "$server/api/games/$game"
    query = "?format=text&name=" * encode(name) * (isempty(seat) ? "" : "&seat=$seat")

    status, body = request("POST", base * "/join" * query)
    me = parse_state(body)
    if status != 200 || !haskey(me, "token")
        println(stderr, "[$name] join failed ($status): $(get(me, "error", body))")
        exit(1)
    end
    token = me["token"]
    my_seat = parse(Int, get(me, "seat", "0"))
    println("[$name] joined $game as seat $my_seat")
    flush(stdout)

    failures = 0
    while true
        # getstate answers only when it is our turn or the game is over, so this
        # loop does not poll and waiting costs nothing on the clock.
        status, body = request("GET", "$base/getstate?format=text&timeout=60&token=$token")
        if status == 0
            failures += 1
            if failures >= 30
                println(stderr, "[$name] server gone, giving up")
                exit(1)
            end
            sleep(1)
            continue
        end
        failures = 0
        if status != 200
            println(stderr, "[$name] getstate failed ($status)")
            exit(1)
        end

        s = parse_state(body)
        if get(s, "status", "") == "finished"
            winner = parse(Int, get(s, "winner", "0"))
            verdict = winner == my_seat ? "WIN" : (winner == 0 ? "no winner" : "LOSS")
            println("[$name] game over: $verdict. $(get(s, "reason", ""))")
            exit(winner == my_seat ? 0 : 2)
        end
        get(s, "your_turn", "") == "1" || continue

        stones = parse(Int, s["stones"])
        mine = nums(get(s, "your_cards", ""))
        card = choose_card(stones, mine, nums(get(s, "opp_cards", "")))
        println("[$name] $stones stones, playing $card")
        flush(stdout)

        status, _ = request("POST", "$base/move?format=text&token=$token&card=$card")
        status == 200 && continue

        println(stderr, "[$name] move $card rejected ($status)")
        back = safest_card(stones, mine)
        if back != card
            println(stderr, "[$name] playing $back instead")
            request("POST", "$base/move?format=text&token=$token&card=$back")
        else
            sleep(1)    # not our turn any more or a hiccup: do not hammer it
        end
    end
end

main()
