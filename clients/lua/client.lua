#!/usr/bin/env lua
-- Card Nim client in Lua.
--
--     lua client.lua --server http://localhost:8000 --game K7PX --name "Lua bot" [--seat 1]
--
-- Lua's standard library has no sockets and no HTTP, so this shells out to
-- curl through io.popen, the way the shell client does.  (LuaSocket would
-- remove that dependency, but it is not part of a stock Lua.)
--
-- CARDNIM_SERVER, CARDNIM_GAME, CARDNIM_NAME and CARDNIM_SEAT are used as
-- defaults, which is how the server starts this client for a seat.
--
-- Put your strategy in choose_card(). Everything else is plumbing.

-- ============================================================ your strategy

-- Purpose: pick the card to play.
-- Inputs:  stones on the table, your cards, the opponent's cards (both sorted).
-- Output:  one card from your hand.  A card larger than stones loses at once.
local function choose_card(stones, mine, theirs)
  local held, opp = {}, {}
  for _, c in ipairs(mine) do held[c] = true end
  for _, c in ipairs(theirs) do opp[c] = true end
  if held[stones] then return stones end               -- take the win
  local fit = {}
  for _, c in ipairs(mine) do if c <= stones then fit[#fit + 1] = c end end
  table.sort(fit)
  if #fit == 0 then
    return mine[1] or 1                                -- nothing fits: any card loses
  end
  for _, c in ipairs(fit) do
    if not opp[stones - c] then return c end           -- do not leave an exact match
  end
  return fit[1]
end

-- Purpose: the smallest card that fits, played when the server refuses our
-- choice, so a bug in the strategy cannot burn the clock.
local function safest_card(stones, mine)
  local fit = {}
  for _, c in ipairs(mine) do if c <= stones then fit[#fit + 1] = c end end
  table.sort(fit)
  return fit[1] or mine[1] or 1
end

-- ============================================================ plumbing

-- Purpose: percent-encode a name for a query parameter.
local function encode(s)
  return (s:gsub("[^A-Za-z0-9%-_.~]", function(c)
    return string.format("%%%02X", string.byte(c))
  end))
end

-- Purpose: one request through curl.  Outputs: status, body (status 0 means
-- the server could not be reached).  The URL is single-quoted for the shell
-- and any quote in it escaped, so nothing in a name reaches sh unquoted.
local function req(method, url)
  local safe = "'" .. url:gsub("'", "'\\''") .. "'"
  local cmd = string.format("curl -s -m 90 -w '\\n%%{http_code}' -X %s %s 2>/dev/null", method, safe)
  local pipe = io.popen(cmd, "r")
  if not pipe then return 0, "" end
  local out = pipe:read("*a") or ""
  pipe:close()
  local body, code = out:match("^(.*)\n(%d+)%s*$")
  if not code then return 0, "" end
  return tonumber(code), body
end

-- Purpose: turn "key value value" lines into a table of strings.
local function parse_state(text)
  local out = {}
  for raw in (text or ""):gmatch("[^\n]+") do
    -- a for-loop variable is const in Lua 5.5, so trim into a new local
    local line = raw:gsub("\r$", "")
    if #line > 0 then
      local k, v = line:match("^(%S+) ?(.*)$")
      if k then out[k] = v end
    end
  end
  return out
end

local function nums(s)
  local out = {}
  for word in (s or ""):gmatch("%S+") do
    local n = tonumber(word)
    if n then out[#out + 1] = n end
  end
  return out
end

-- Purpose: the command line over the environment over a default.
local function opt(flag, env, default)
  for i = 1, #arg - 1 do
    if arg[i] == flag then return arg[i + 1] end
  end
  local v = os.getenv(env)
  if v and #v > 0 then return v end
  return default
end

-- ============================================================ game loop

local server = opt("--server", "CARDNIM_SERVER", "http://localhost:8000"):gsub("/+$", "")
local game   = opt("--game", "CARDNIM_GAME", "")
local name   = opt("--name", "CARDNIM_NAME", "Lua bot")
local seat   = opt("--seat", "CARDNIM_SEAT", "")

if game == "" then
  io.stderr:write("usage: lua client.lua --game ID [--server http://host:8000] [--name NAME] [--seat 1|2]\n")
  os.exit(1)
end

local base = server .. "/api/games/" .. game
local query = "?format=text&name=" .. encode(name) .. (seat ~= "" and ("&seat=" .. seat) or "")

local status, body = req("POST", base .. "/join" .. query)
local me = parse_state(body)
if status ~= 200 or not me.token then
  io.stderr:write(string.format("[%s] join failed (%s): %s\n", name, tostring(status), body))
  os.exit(1)
end
local token = me.token
local my_seat = tonumber(me.seat) or 0
print(string.format("[%s] joined %s as seat %d", name, game, my_seat))
io.stdout:flush()

local failures = 0
while true do
  -- getstate answers only when it is our turn or the game is over, so this
  -- loop does not poll and waiting costs nothing on the clock.
  status, body = req("GET", base .. "/getstate?format=text&timeout=60&token=" .. token)
  if status == 0 then
    failures = failures + 1
    if failures >= 30 then
      io.stderr:write(string.format("[%s] server gone\n", name))
      os.exit(1)
    end
    os.execute("sleep 1")
  else
    failures = 0
    if status ~= 200 then
      io.stderr:write(string.format("[%s] getstate failed (%d)\n", name, status))
      os.exit(1)
    end
    local s = parse_state(body)
    if s.status == "finished" then
      local w = tonumber(s.winner) or 0
      local verdict = (w == my_seat) and "WIN" or (w ~= 0 and "LOSS" or "no winner")
      print(string.format("[%s] game over: %s. %s", name, verdict, s.reason or ""))
      os.exit(w == my_seat and 0 or 2)
    end
    if s.your_turn == "1" then
      local stones = tonumber(s.stones) or 0
      local mine = nums(s.your_cards)
      local card = choose_card(stones, mine, nums(s.opp_cards))
      print(string.format("[%s] %d stones, playing %d", name, stones, card))
      io.stdout:flush()
      status = req("POST", base .. "/move?format=text&token=" .. token .. "&card=" .. card)
      if status ~= 200 then
        io.stderr:write(string.format("[%s] move %d rejected (%s)\n", name, card, tostring(status)))
        local back = safest_card(stones, mine)
        if back ~= card then
          req("POST", base .. "/move?format=text&token=" .. token .. "&card=" .. back)
        else
          os.execute("sleep 1")
        end
      end
    end
  end
end
