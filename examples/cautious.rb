#!/usr/bin/env ruby
# Card Nim client in Ruby.  No gems: net/http ships with Ruby.
#
#     ruby client.rb --server http://localhost:8000 --game K7PX --name "Ruby bot" [--seat 1]
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

require "net/http"
require "uri"

# ============================================================ your strategy

# Purpose: pick the card to play.
# Inputs:  stones on the table, your cards, the opponent's cards (both sorted).
# Output:  one card from your hand.  A card larger than stones loses at once.
def choose_card(stones, my_cards, opp_cards)
  # Team Cautious: take the win, otherwise always play the SMALLEST card and
  # hoard the big ones.  The opposite of hungry.py.
  return stones if my_cards.include?(stones)
  fit = my_cards.select { |c| c <= stones }
  fit.empty? ? (my_cards.first || 1) : fit.min
end

# Purpose: the smallest card that fits, played when the server refuses our
# choice, so a bug in the strategy cannot burn the clock.
def safest_card(stones, my_cards)
  fit = my_cards.select { |c| c <= stones }
  fit.empty? ? (my_cards.first || 1) : fit.min
end

# ============================================================ plumbing

# Purpose: one HTTP request.  Outputs: [status, body]; status 0 means the
# server could not be reached.
def request(method, url)
  uri = URI(url)
  http = Net::HTTP.new(uri.host, uri.port)
  http.read_timeout = 90                     # getstate may hold the line for 60 s
  req = (method == "POST" ? Net::HTTP::Post : Net::HTTP::Get).new(uri.request_uri)
  req.body = "" if method == "POST"
  res = http.request(req)
  [res.code.to_i, res.body.to_s]
rescue StandardError
  [0, ""]
end

# Purpose: turn "key value value" lines into a hash of strings.
def parse_state(text)
  text.split("\n").each_with_object({}) do |raw, out|
    line = raw.chomp("\r")
    next if line.empty?
    key, _, rest = line.partition(" ")
    out[key] = rest
  end
end

def nums(s)
  (s || "").split(" ").map(&:to_i)
end

# Purpose: the command line over the environment over a default.
def option(flag, env_name, fallback)
  i = ARGV.index(flag)
  return ARGV[i + 1] if i && ARGV[i + 1]
  value = ENV[env_name]
  value.nil? || value.empty? ? fallback : value
end

# ============================================================ game loop

server = option("--server", "CARDNIM_SERVER", "http://localhost:8000").sub(%r{/+\z}, "")
game   = option("--game", "CARDNIM_GAME", "")
name   = option("--name", "CARDNIM_NAME", "Ruby bot")
seat   = option("--seat", "CARDNIM_SEAT", "")

if game.empty?
  warn "usage: ruby client.rb --game ID [--server http://host:8000] [--name NAME] [--seat 1|2]"
  exit 1
end

base = "#{server}/api/games/#{game}"
query = "?format=text&name=#{URI.encode_www_form_component(name)}#{seat.empty? ? '' : "&seat=#{seat}"}"

status, body = request("POST", base + "/join" + query)
me = parse_state(body)
if status != 200 || me["token"].nil?
  warn "[#{name}] join failed (#{status}): #{me['error'] || body}"
  exit 1
end
token = me["token"]
my_seat = me["seat"].to_i
puts "[#{name}] joined #{game} as seat #{my_seat}"

failures = 0
loop do
  # getstate answers only when it is our turn or the game is over, so this
  # loop does not poll and waiting costs nothing on the clock.
  status, body = request("GET", "#{base}/getstate?format=text&timeout=60&token=#{token}")
  if status.zero?
    failures += 1
    if failures >= 30
      warn "[#{name}] server gone, giving up"
      exit 1
    end
    sleep 1
    next
  end
  failures = 0
  if status != 200
    warn "[#{name}] getstate failed (#{status})"
    exit 1
  end

  s = parse_state(body)
  if s["status"] == "finished"
    w = s["winner"].to_i
    result = w == my_seat ? "WIN" : (w.zero? ? "no winner" : "LOSS")
    puts "[#{name}] game over: #{result}. #{s['reason']}"
    exit(w == my_seat ? 0 : 2)
  end
  next unless s["your_turn"] == "1"

  stones = s["stones"].to_i
  mine = nums(s["your_cards"])
  card = choose_card(stones, mine, nums(s["opp_cards"]))
  puts "[#{name}] #{stones} stones, playing #{card}"

  status, = request("POST", "#{base}/move?format=text&token=#{token}&card=#{card}")
  next if status == 200

  warn "[#{name}] move #{card} rejected (#{status})"
  back = safest_card(stones, mine)
  if back != card
    warn "[#{name}] playing #{back} instead"
    request("POST", "#{base}/move?format=text&token=#{token}&card=#{back}")
  else
    sleep 1    # not our turn any more or a hiccup: do not hammer it
  end
end
