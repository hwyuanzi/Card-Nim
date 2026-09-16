#!/usr/bin/env -S deno run --allow-net --allow-env
// Card Nim client in TypeScript, run by Deno.  No packages: fetch is built in.
//
//     deno run --allow-net --allow-env client.ts --game K7PX --name "TS bot" [--seat 1]
//
// CARDNIM_SERVER, CARDNIM_GAME, CARDNIM_NAME and CARDNIM_SEAT are used as
// defaults, which is how the server starts this client for a seat.
//
// The state comes back as "key value" lines (?format=text), so there is no
// JSON to parse.  Put your strategy in chooseCard(); the rest is plumbing.

type State = Record<string, string>;

// ============================================================ your strategy

// Purpose: pick the card to play.
// Inputs:  stones on the table, your cards, the opponent's cards (both sorted).
// Output:  one card from your hand.  A card larger than stones loses at once.
function chooseCard(stones: number, myCards: number[], oppCards: number[]): number {
  if (myCards.includes(stones)) return stones;                   // take the win
  const fit = myCards.filter((c) => c <= stones);                // cards that still fit
  if (fit.length === 0) return safestCard(stones, myCards);
  const safe = fit.filter((c) => !oppCards.includes(stones - c)); // do not leave an exact match
  return Math.min(...(safe.length ? safe : fit));
}

// Purpose: the smallest card that fits, played when the server refuses our
// choice, so a bug in the strategy cannot burn the clock.
function safestCard(stones: number, myCards: number[]): number {
  const fit = myCards.filter((c) => c <= stones);
  return fit.length ? Math.min(...fit) : (myCards[0] ?? 1);
}

// ============================================================ plumbing

// Purpose: one request.  Outputs: [status, body]; status 0 means unreachable.
async function req(method: string, url: string): Promise<[number, string]> {
  try {
    const res = await fetch(url, { method });
    return [res.status, await res.text()];
  } catch {
    return [0, ""];
  }
}

// Purpose: turn "key value value" lines into an object of strings.
function parseState(text: string): State {
  const out: State = {};
  for (const raw of text.split("\n")) {
    const line = raw.replace(/\r$/, "");
    if (!line) continue;
    const sp = line.indexOf(" ");
    if (sp < 0) out[line] = "";
    else out[line.slice(0, sp)] = line.slice(sp + 1);
  }
  return out;
}

const nums = (s: string | undefined): number[] =>
  (s ?? "").split(" ").filter((x) => x !== "").map(Number);
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

// Purpose: the command line over the environment over a default.
function opt(flag: string, envName: string, fallback: string): string {
  const i = Deno.args.indexOf(flag);
  if (i >= 0 && i + 1 < Deno.args.length) return Deno.args[i + 1];
  return Deno.env.get(envName) || fallback;
}

// ============================================================ game loop

const server = opt("--server", "CARDNIM_SERVER", "http://localhost:8000").replace(/\/+$/, "");
const game = opt("--game", "CARDNIM_GAME", "");
const name = opt("--name", "CARDNIM_NAME", "TS bot");
const seat = opt("--seat", "CARDNIM_SEAT", "");

if (!game) {
  console.error("usage: deno run --allow-net --allow-env client.ts --game ID [--name NAME] [--seat 1|2]");
  Deno.exit(1);
}

const base = `${server}/api/games/${game}`;
const query = `?format=text&name=${encodeURIComponent(name)}${seat ? `&seat=${seat}` : ""}`;

const [joinStatus, joinBody] = await req("POST", `${base}/join${query}`);
const me = parseState(joinBody);
if (joinStatus !== 200 || !me.token) {
  console.error(`[${name}] join failed (${joinStatus}): ${joinBody}`);
  Deno.exit(1);
}
const token = me.token;
const mySeat = Number(me.seat);
console.log(`[${name}] joined ${game} as seat ${mySeat}`);

let failures = 0;
while (true) {
  // getstate answers only when it is our turn or the game is over, so this
  // loop does not poll and waiting costs nothing on the clock.
  const [status, body] = await req("GET", `${base}/getstate?format=text&timeout=60&token=${token}`);
  if (status === 0) {
    if (++failures >= 30) { console.error(`[${name}] server gone`); Deno.exit(1); }
    await sleep(1000);
    continue;
  }
  failures = 0;
  if (status !== 200) { console.error(`[${name}] getstate failed (${status})`); Deno.exit(1); }

  const s = parseState(body);
  if (s.status === "finished") {
    const w = Number(s.winner);
    console.log(`[${name}] game over: ${w === mySeat ? "WIN" : w ? "LOSS" : "no winner"}. ${s.reason}`);
    Deno.exit(w === mySeat ? 0 : 2);
  }
  if (s.your_turn !== "1") continue;

  const stones = Number(s.stones);
  const mine = nums(s.your_cards);
  const card = chooseCard(stones, mine, nums(s.opp_cards));
  console.log(`[${name}] ${stones} stones, playing ${card}`);

  const [moveStatus] = await req("POST", `${base}/move?format=text&token=${token}&card=${card}`);
  if (moveStatus !== 200) {
    const back = safestCard(stones, mine);
    console.error(`[${name}] move ${card} rejected (${moveStatus})`);
    if (back !== card) await req("POST", `${base}/move?format=text&token=${token}&card=${back}`);
    else await sleep(1000);
  }
}
