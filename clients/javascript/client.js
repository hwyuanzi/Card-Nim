#!/usr/bin/env node
// Card Nim client in JavaScript.  Node 12+, no packages: only the built-in
// http module.
//
//     node client.js --server http://localhost:8000 --game K7PX --name "JS bot" [--seat 1]
//
// CARDNIM_SERVER, CARDNIM_GAME, CARDNIM_NAME and CARDNIM_SEAT are used as
// defaults, which is how the server starts this client for a seat.
//
// The state comes back as "key value" lines (?format=text), so there is no
// JSON to parse:
//
//     status playing | waiting | finished
//     you 2                    your seat
//     your_turn 1              1 or 0
//     stones 37
//     your_cards 1 2 4 5
//     opp_cards 1 3 6
//     winner 0                 0, 1 or 2
//     reason ...
//
// Put your strategy in chooseCard(). Everything else is plumbing.

"use strict";
const http = require("http");
const { URL } = require("url");

// ============================================================ your strategy

// Purpose: pick the card to play.
// Inputs:  stones on the table, your cards, the opponent's cards (both sorted).
// Output:  one card from your hand.  A card larger than stones loses at once.
function chooseCard(stones, myCards, oppCards) {
  if (myCards.includes(stones)) return stones;            // take the win
  const fit = myCards.filter((c) => c <= stones);         // cards that still fit
  if (fit.length === 0) return myCards[0] || 1;           // nothing fits: any card loses
  const safe = fit.filter((c) => !oppCards.includes(stones - c));  // do not leave an exact match
  return Math.min(...(safe.length ? safe : fit));
}

// Purpose: the smallest card that fits, played when the server refuses our
// choice, so a bug in the strategy cannot burn the clock.
function safestCard(stones, myCards) {
  const fit = myCards.filter((c) => c <= stones);
  return fit.length ? Math.min(...fit) : (myCards[0] || 1);
}

// ============================================================ plumbing

// Purpose: one HTTP request.  Outputs: {status, text}; status 0 means the
// server could not be reached.
function request(method, url) {
  return new Promise((resolve) => {
    const u = new URL(url);
    const req = http.request(
      { method, hostname: u.hostname, port: u.port || 80, path: u.pathname + u.search,
        headers: { "Content-Length": "0" } },
      (res) => {
        let body = "";
        res.setEncoding("utf8");
        res.on("data", (d) => { body += d; });
        res.on("end", () => resolve({ status: res.statusCode, text: body }));
      });
    req.on("error", () => resolve({ status: 0, text: "" }));
    req.end();
  });
}

// Purpose: turn "key value value" lines into an object of strings.
function parseState(text) {
  const out = {};
  for (const raw of text.split("\n")) {
    const line = raw.replace(/\r$/, "");
    if (!line) continue;
    const sp = line.indexOf(" ");
    if (sp < 0) out[line] = "";
    else out[line.slice(0, sp)] = line.slice(sp + 1);
  }
  return out;
}

const nums = (s) => (s || "").split(" ").filter((x) => x !== "").map(Number);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Purpose: the command line over the environment over a default.
function options() {
  const argv = process.argv.slice(2);
  const opt = (flag, envName, fallback) => {
    const i = argv.indexOf(flag);
    if (i >= 0 && i + 1 < argv.length) return argv[i + 1];
    return process.env[envName] || fallback;
  };
  return { server: opt("--server", "CARDNIM_SERVER", "http://localhost:8000"),
           game: opt("--game", "CARDNIM_GAME", ""),
           name: opt("--name", "CARDNIM_NAME", "JS bot"),
           seat: opt("--seat", "CARDNIM_SEAT", "") };
}

// ============================================================ game loop

async function main() {
  const o = options();
  if (!o.game) {
    console.error("usage: node client.js --game ID [--server http://host:8000] [--name NAME] [--seat 1|2]");
    process.exit(1);
  }
  const base = o.server.replace(/\/+$/, "") + "/api/games/" + o.game;
  const q = "?format=text&name=" + encodeURIComponent(o.name) + (o.seat ? "&seat=" + o.seat : "");

  const joined = await request("POST", base + "/join" + q);
  const me = parseState(joined.text);
  if (joined.status !== 200 || !me.token) {
    console.error(`[${o.name}] join failed (${joined.status}): ${me.error || joined.text}`);
    process.exit(1);
  }
  const token = me.token;
  const mySeat = Number(me.seat);
  console.log(`[${o.name}] joined ${o.game} as seat ${mySeat}`);

  let failures = 0;
  for (;;) {
    // getstate answers only when it is our turn or the game is over, so this
    // loop does not poll and waiting costs nothing on the clock.
    const res = await request("GET", `${base}/getstate?format=text&timeout=60&token=${token}`);
    if (res.status === 0) {
      if (++failures >= 30) { console.error(`[${o.name}] server gone, giving up`); process.exit(1); }
      await sleep(1000);
      continue;
    }
    failures = 0;
    if (res.status !== 200) { console.error(`[${o.name}] getstate failed (${res.status})`); process.exit(1); }
    const s = parseState(res.text);
    if (s.status === "finished") {
      const w = Number(s.winner);
      console.log(`[${o.name}] game over: ${w === mySeat ? "WIN" : w ? "LOSS" : "no winner"}. ${s.reason}`);
      process.exit(w === mySeat ? 0 : 2);
    }
    if (s.your_turn !== "1") continue;

    const stones = Number(s.stones);
    const mine = nums(s.your_cards);
    const card = chooseCard(stones, mine, nums(s.opp_cards));
    console.log(`[${o.name}] ${stones} stones, playing ${card}`);

    const played = await request("POST", `${base}/move?format=text&token=${token}&card=${card}`);
    if (played.status !== 200) {
      console.error(`[${o.name}] move ${card} rejected (${played.status})`);
      const back = safestCard(stones, mine);
      if (back !== card) {
        console.error(`[${o.name}] playing ${back} instead`);
        await request("POST", `${base}/move?format=text&token=${token}&card=${back}`);
      } else {
        await sleep(1000);   // not our turn any more or a hiccup: do not hammer it
      }
    }
  }
}

main();
