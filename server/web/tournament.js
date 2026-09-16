/* Bracket page: the entrants, the draw and every match's live status.
   Long-polls GET /api/tournaments/{id}?since=V, so a move in any game of
   the tournament redraws the bracket within milliseconds.

   A person who entered from this browser is remembered in localStorage as
   cardnim_entrant_<ID> (their entrant id and secret).  That lets the page say
   "your match is ready" and sit them down in one click: the secret opens the
   seat reserved for them and becomes the seat token the board expects in
   sessionStorage.  Programs do the same over the API; see docs/API.md. */

(function () {
  "use strict";

  const rawId = (window.location.pathname.match(/\/tournament\/([A-Za-z0-9]+)/) || [])[1];
  const tid = rawId ? rawId.toUpperCase() : null;
  const $ = (id) => document.getElementById(id);
  const NUM_AVATARS = 16;
  const meKey = "cardnim_entrant_" + tid;

  let me = null;               // {id, token, name, kind} if this browser entered
  try {
    me = JSON.parse(localStorage.getItem(meKey) || "null");
    if (!me || typeof me.token !== "string") me = null;
  } catch (e) { me = null; }
  function saveMe() {
    try { if (me) localStorage.setItem(meKey, JSON.stringify(me)); else localStorage.removeItem(meKey); } catch (e) { /* memory only */ }
  }

  let t = null;                // the bracket, as last received
  let uploadInfo = { enabled: false, accept: "", languages: [] };  // /api/uploads
  /* The join QR belongs on the organiser's screen, not on the phone of
     someone who just scanned it. The server says whether this request came
     from the machine it runs on; ?host=1 forces it on for projecting from
     somewhere else, ?host=0 forces it off. */
  const hostOverride = new URLSearchParams(location.search).get("host");
  let isHost = hostOverride === "1";
  let publicUrl = window.location.origin;
  let confirmAbort = false;
  let joinAvatar = 1 + Math.floor(Math.random() * NUM_AVATARS);
  let busy = false;

  /* Does this server take strategy files? Only then is the Upload tab shown. */
  fetch("/api/uploads", { cache: "no-store" }).then((r) => r.ok ? r.json() : null)
    .then((u) => { if (u) { uploadInfo = u; renderJoin(); } })
    .catch(() => { /* leave the tab hidden */ });

  fetch("/api/health", { cache: "no-store" }).then((r) => r.json())
    .then((h) => {
      if (h.lobby_url) publicUrl = h.lobby_url.replace(/\/$/, "");
      if (hostOverride === null) isHost = Boolean(h.local);
      lastSignature = null;                 // the QR may have just appeared
      if (t) render();
    })
    .catch(() => { /* keep the page's own address */ });

  /* ------------------------------------------------------------ helpers */

  function esc(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }
  function avatarSrc(n) { return "/avatars/av" + String(n).padStart(2, "0") + ".png"; }
  function entrant(id) { return (t && t.entrants || []).find((e) => e.id === id) || null; }
  function title() { return t.label || "Tournament " + t.id; }
  function plural(n, word) { return n + " " + word + (n === 1 ? "" : "s"); }
  function headers(extra) {
    const h = Object.assign({}, extra || {});
    if (me) h["X-Token"] = me.token;
    return h;
  }

  async function api(method, path, body, extra) {
    let res;
    try {
      res = await fetch(path, {
        method,
        headers: headers(Object.assign({ "Content-Type": "application/json" }, extra || {})),
        body: body ? JSON.stringify(body) : undefined,
      });
    } catch (e) {
      throw new Error("cannot reach the server");
    }
    const payload = await res.json().catch(() => ({}));
    if (!res.ok) {
      const err = new Error(payload.error || ("server answered " + res.status));
      err.status = res.status;
      throw err;
    }
    return payload;
  }

  /* ------------------------------------------------------------ polling */

  let pollFailures = 0;
  async function poll() {
    if (!tid) return;
    const since = t ? t.version : -1;
    try {
      const res = await fetch(`/api/tournaments/${tid}?since=${since}&timeout=25`, { cache: "no-store", headers: headers() });
      if (res.status === 404) {
        $("detail").innerHTML = `<div class="detail-empty"><div>There is no tournament ${esc(tid)}. The server may have been restarted.<br><br><a class="btn" href="/">Back to the lobby</a></div></div>`;
        return;
      }
      if (!res.ok) throw new Error("server answered " + res.status);
      accept(await res.json());
      pollFailures = 0;
      if (t.status !== "finished") poll();      // a finished bracket never changes again
    } catch (err) {
      pollFailures += 1;
      if (pollFailures >= 2) $("title").textContent = "Lost contact with the server. Retrying…";
      setTimeout(poll, Math.min(1500 * pollFailures, 8000));
    }
  }

  let lastLiveKey = null;
  function accept(next) {
    if (!next || !Array.isArray(next.entrants)) throw new Error("unexpected answer from the server");
    // the server answers `you` for the token we sent; if it does not know it
    // (server restarted, entry withdrawn elsewhere) forget it
    if (me && !next.you) { me = null; saveMe(); }
    t = next;
    // a new match starting takes the pane back, so the room always sees the
    // game that is actually being played
    const live = liveMatch();
    const key = live ? `${live.round}-${live.index}` : null;
    if (key && key !== lastLiveKey) { pinned = null; lastLiveKey = key; }
    render();
  }

  async function refreshNow() {
    try {
      const res = await fetch(`/api/tournaments/${tid}`, { cache: "no-store", headers: headers() });
      if (res.ok) accept(await res.json());
    } catch (e) { /* the long-poll will catch up */ }
  }

  /* ------------------------------------------------------------ actions */

  async function join(name, avatar, kind) {
    const payload = await api("POST", `/api/tournaments/${tid}/join`, { name, avatar, kind });
    if (kind === "human" || kind === "api") {
      me = { id: payload.entrant.id, token: payload.token, name: payload.entrant.name, kind };
      saveMe();
    }
    accept(payload.tournament);
  }

  async function withdraw() {
    try { await api("POST", `/api/tournaments/${tid}/leave`); }
    catch (err) { if (err.status !== 401) throw err; }
    me = null; saveMe();
    await refreshNow();
  }

  async function removeBot(id) {
    accept(await api("POST", `/api/tournaments/${tid}/leave`, { entrant: id }));
  }

  async function start() { accept(await api("POST", `/api/tournaments/${tid}/start`)); }
  async function abort() { accept(await api("POST", `/api/tournaments/${tid}/abort`, { reason: "aborted from the bracket page" })); }

  /* Purpose: sit this browser's entrant down in their match and open the
     board.  The reserved seat opens for the entrant's secret, which the
     server then uses as the seat token; the board reads it from
     sessionStorage exactly as if the seat had been taken on the board. */
  async function sitDown(gameId, seat) {
    const payload = await api("POST", `/api/games/${gameId}/join`, { name: me.name, seat });
    try {
      const key = "cardnim_tokens_" + gameId;
      const held = JSON.parse(sessionStorage.getItem(key) || "{}");
      held[payload.seat] = payload.token;
      sessionStorage.setItem(key, JSON.stringify(held));
    } catch (e) { /* the board can still sit down by name */ }
    window.location.href = "/game/" + gameId;
  }

  /* ------------------------------------------------------------ the entry form */

  const joinForm = $("join-form");
  $("faces").addEventListener("click", (ev) => {
    const b = ev.target.closest(".face");
    if (!b) return;
    joinAvatar = Number(b.dataset.av);
    markFace();
  });
  $("join-file").addEventListener("change", () => { resetSteps(false); $("join-error").textContent = ""; });
  joinForm.addEventListener("submit", (ev) => {
    ev.preventDefault();
    if (busy) return;
    const err = $("join-error");
    err.textContent = "";
    const name = joinForm.elements.name.value.trim();
    const file = $("join-file").files[0];
    if (!name) { err.textContent = "Type a name first."; joinForm.elements.name.focus(); return; }
    if (!file) { err.textContent = "Choose your strategy file first."; $("join-file").focus(); return; }

    busy = true;
    const submit = joinForm.querySelector('button[type="submit"]');
    submit.disabled = true;
    joinForm.classList.add("working");
    uploadFlow(file, name)
      .then(() => {
        joinForm.elements.name.value = "";
        $("join-file").value = "";
        joinAvatar = 1 + Math.floor(Math.random() * NUM_AVATARS);
        markFace();
        setTimeout(() => { resetSteps(false); }, 4000);   // leave the ticks up a moment
      })
      .catch((e) => { err.textContent = e.message; })     // the failed step stays on screen
      .finally(() => {
        busy = false;
        submit.disabled = false;
        joinForm.classList.remove("working");
        renderJoin();
      });
  });

  /* The bracket's own QR: scan it to enter this tournament from a phone or
     another laptop, which is how a team joins without being handed a link.
     The SVG is only rebuilt when the address or the id actually changes, not
     on every poll. */
  function drawQr() {
    const card = $("qr-panel");
    if (!card || !t || !window.QR) return;
    if (!isHost || hostOverride === "0") {   // a visitor's screen: no QR at all
      card.hidden = true;
      return;
    }
    const url = publicUrl + "/tournament/" + t.id;
    const open = t.status === "open";
    card.hidden = false;
    card.querySelector(".group-title").textContent =
      open ? "Enter from another device" : "Follow from another device";
    // entries are closed once the bracket is drawn, so the command goes away
    $("qr-cmd").hidden = !open;
    $("qr-cmd-label").hidden = !open;
    if (open) {
      $("qr-cmd").textContent =
        `python3 clients/python/client.py --server ${publicUrl} --tournament ${t.id} --name "Your team"`;
    }
    const box = $("qr");
    if (box.dataset.url === url) return;          // already showing this one
    $("qr-url").textContent = url;
    try { box.innerHTML = QR.svg(url, { ecl: "M", module: 4, margin: 2 }); box.dataset.url = url; }
    catch (e) { box.textContent = ""; box.dataset.url = ""; }
  }

  /* Send one strategy file to the server, which writes it into the uploads
     folder with a generated manifest and can then build and run it.  Returns
     {kind, language, file, available, reason}. */
  async function postStrategy(file, team) {
    const q = `?filename=${encodeURIComponent(file.name)}&team=${encodeURIComponent(team)}`;
    const res = await fetch("/api/uploads" + q, { method: "POST", body: file });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `the server refused it (${res.status})`);
    return data;
  }

  /* ------------------------------------------------- the upload's own steps

     A rejected upload should say which guideline it missed, not just fail, so
     the checks run one at a time and each shows its own result.  The first two
     are done here in the browser, so an oversized or unrunnable file never
     leaves the device. */

  const UPLOAD_STEPS = [
    ["type", "File type"],
    ["size", "File size"],
    ["send", "Sent to the server"],
    ["run", "This machine can run it"],
    ["enter", "Entered the bracket"],
  ];
  let stepState = {};

  function renderSteps() {
    const box = $("upload-steps");
    if (!box) return;
    box.hidden = !Object.keys(stepState).length;
    if (box.hidden) return;
    box.innerHTML = UPLOAD_STEPS.map(([key, label]) => {
      const s = stepState[key] || { state: "todo" };
      const mark = s.state === "ok" ? "&#10003;" : s.state === "fail" ? "&#10007;"
        : s.state === "busy" ? '<span class="spin" aria-hidden="true"></span>' : "";
      return `<li class="step ${s.state}"><span class="mark">${mark}</span>` +
        `<span class="step-text"><span class="step-label">${esc(label)}</span>` +
        (s.note ? `<span class="step-note">${esc(s.note)}</span>` : "") + "</span></li>";
    }).join("");
  }
  const setStep = (key, state, note) => { stepState[key] = { state, note: note || "" }; renderSteps(); };
  function resetSteps(showing) {
    stepState = {};
    if (showing) UPLOAD_STEPS.forEach(([key]) => { stepState[key] = { state: "todo" }; });
    renderSteps();
  }
  const kb = (n) => `${(n / 1024).toFixed(1)} KB`;

  /* Purpose: run the checks in order, stopping at the first failure with that
     step marked.  Outputs: the upload's {kind, ...}; throws with a message for
     the error line. */
  async function uploadFlow(file, team) {
    resetSteps(true);

    setStep("type", "busy");
    const ext = (file.name.match(/\.[^.]+$/) || [""])[0].toLowerCase();
    const lang = (uploadInfo.languages || []).find((l) => l.extension === ext);
    if (!lang) {
      const accepted = (uploadInfo.languages || []).map((l) => l.extension).join(" ");
      setStep("type", "fail", `${ext || "no extension"} — accepted: ${accepted}`);
      throw new Error("That kind of file cannot be run here.");
    }
    setStep("type", "ok", `${ext} — ${lang.language}`);

    setStep("size", "busy");
    if (!file.size) { setStep("size", "fail", "the file is empty"); throw new Error("That file is empty."); }
    if (uploadInfo.max_bytes && file.size > uploadInfo.max_bytes) {
      setStep("size", "fail", `${kb(file.size)}, over the ${kb(uploadInfo.max_bytes)} limit`);
      throw new Error("That file is too big.");
    }
    setStep("size", "ok", `${kb(file.size)} of ${kb(uploadInfo.max_bytes || 0)}`);

    setStep("send", "busy");
    let up;
    try { up = await postStrategy(file, team); }
    catch (e) { setStep("send", "fail", e.message); throw e; }
    setStep("send", "ok", up.file);

    setStep("run", "busy");
    if (!up.available) {
      setStep("run", "fail", up.reason || "the toolchain is missing here");
      throw new Error(up.reason || "this machine cannot run that language");
    }
    setStep("run", "ok", up.language);

    setStep("enter", "busy");
    try { await join(team, joinAvatar, up.kind); }
    catch (e) { setStep("enter", "fail", e.message); throw e; }
    setStep("enter", "ok", `${team} is in`);
    return up;
  }

  /* The entry form is rebuilt as little as possible.  render() runs on every
     poll, and this form holds a name being typed, a chosen file and sixteen
     face buttons: tearing those down two seconds into someone's upload is
     exactly the kind of glitch that loses a submission.  The faces are built
     once; after that only their selected state changes. */
  let facesBuilt = false;

  function buildFaces() {
    if (facesBuilt) return;
    facesBuilt = true;
    $("faces").innerHTML = Array.from({ length: NUM_AVATARS }, (_, i) => i + 1).map((n) =>
      `<button type="button" class="face" data-av="${n}" aria-label="face ${n}" aria-pressed="false" style="background-image:url('${avatarSrc(n)}')"></button>`).join("");
    markFace();
  }

  function markFace() {
    $("faces").querySelectorAll(".face").forEach((b) => {
      const on = Number(b.dataset.av) === joinAvatar;
      b.classList.toggle("selected", on);
      b.setAttribute("aria-pressed", on ? "true" : "false");
    });
  }

  function renderJoin() {
    if (!t) return;
    const canEnter = t.status === "open" && uploadInfo.enabled;
    joinForm.hidden = !canEnter;
    if (!canEnter) return;
    if (busy) return;                 // never touch the form mid-upload
    buildFaces();
    $("join-file").accept = uploadInfo.accept || "";
    $("join-hint").textContent =
      `One self-contained file, up to ${Math.round((uploadInfo.max_bytes || 0) / 1024)} KB. `
      + `This machine runs it for you every round. Accepted: `
      + `${(uploadInfo.languages || []).map((l) => l.extension).join(" ")}.`;
    joinForm.querySelector('button[type="submit"]').textContent = "Upload and enter";
  }

  /* ------------------------------------------------------------ rendering */

  function youHtml() {
    const you = t.you;
    if (!me || !you) return "";
    const e = you.entrant;
    const url = publicUrl;
    const isApi = e.kind === "api";
    let lead = "", body = "", actions = "", cls = "";
    if (you.status === "open") {
      lead = "You are in.";
      body = t.entrants.length < 2 ? "Waiting for at least one more entrant, then someone presses Start." : "Waiting for the bracket to start.";
      actions = '<button class="btn small" type="button" id="withdraw">Withdraw</button>';
    } else if (you.status === "waiting") {
      const round = you.round_name || "Next round";
      lead = you.opponent ? `${round}: you play ${esc(you.opponent)}.` : `${round}: waiting for your opponent to be decided.`;
      body = you.opponent ? "Your game is being set up." : "The other match has to finish first.";
    } else if (you.status === "play") {
      cls = " play";
      lead = `${you.round_name || "Your match"} vs ${esc(you.opponent || "?")} is ready.`;
      body = you.claimed
        ? `You are seated at seat ${you.seat}.`
        : `You are seat ${you.seat}${you.seat === 1 ? " and move first" : ""}. Your clock starts once both players are seated.`;
      actions = you.claimed
        ? `<a class="btn blue small" href="/game/${esc(you.game)}"><svg class="icon"><use href="#i-play"/></svg>Open the board</a>`
        : `<button class="btn blue small" type="button" id="sit" data-game="${esc(you.game)}" data-seat="${you.seat}"><svg class="icon"><use href="#i-play"/></svg>Sit down at seat ${you.seat}</button>` +
          `<a class="btn small" href="/game/${esc(you.game)}"><svg class="icon"><use href="#i-eye"/></svg>Watch</a>`;
    } else if (you.status === "eliminated") {
      lead = `Out in the ${(you.round_name || "bracket").toLowerCase()}.`;
      body = you.lost_to ? `${esc(you.lost_to)} won that match.` : "";
    } else if (you.status === "champion") {
      cls = " play";
      lead = "You won the tournament.";
      body = `${plural(e.wins, "game")} won.`;
    } else {
      lead = "The tournament is over.";
      body = t.reason ? esc(t.reason) : "";
    }
    let cmd = "";
    if (isApi && you.status !== "eliminated" && you.status !== "champion" && you.status !== "finished") {
      cmd = `<div class="secondary" style="font-size:12px">Python sample client, plays every round by itself:</div>` +
        `<code class="cmd">python3 clients/python/client.py --server ${esc(url)} --tournament ${esc(t.id)} --name "${esc(e.name)}"</code>`;
      if (you.status === "play" && !you.claimed) {
        cmd += `<div class="secondary" style="font-size:12px">Any other client, this match only:</div>` +
          `<code class="cmd">--server ${esc(url)} --game ${esc(you.game)} --seat ${you.seat} --name "${esc(e.name)}"</code>`;
      }
    }
    return `<div class="head"><img src="${avatarSrc(e.avatar)}" alt=""><div><div class="name">${esc(e.name)} <span class="badge blue">you</span></div><div class="secondary">${isApi ? "program" : "playing in the browser"} · ${plural(e.wins, "win")}</div></div></div>
      <div class="lead">${lead}</div>${body ? `<div class="secondary">${body}</div>` : ""}${cmd}
      <div class="row-actions">${actions}<button class="forget" type="button" id="forget" title="Forget this entrant on this browser">Not you?</button></div>
      <span class="error" id="you-error"></span>`;
  }

  function entrantsHtml() {
    if (!t.entrants.length) return '<div class="list-empty">Nobody yet. Enter below.</div>';
    return t.entrants.map((e) => {
      const out = e.eliminated_in !== null;
      const champ = t.champion === e.id;
      const sub = champ ? "Champion" : out ? `Out in the ${t.rounds[e.eliminated_in - 1].name.toLowerCase()}` :
        t.status === "open" ? (e.kind === "api" ? "program" : e.bot ? "server bot" : "browser") :
        t.status === "running" ? "Still in" : "";
      const removable = t.status === "open" && (e.bot || (me && me.id === e.id));
      return `<div class="entrant${out ? " out" : ""}">
        <img src="${avatarSrc(e.avatar)}" alt="">
        <span class="text"><span class="title">${esc(e.name)}${e.bot ? ' <span class="badge">bot</span>' : ""}${e.kind === "api" ? ' <span class="badge">api</span>' : ""}${me && me.id === e.id ? ' <span class="badge blue">you</span>' : ""}${champ ? ' <span class="badge gold">champion</span>' : ""}</span>
        <span class="sub">${sub}${e.wins ? ` · ${plural(e.wins, "win")}` : ""}</span></span>
        <span class="side">${removable ? `<button class="iconbtn x" type="button" data-remove="${e.id}" title="Remove ${esc(e.name)}" aria-label="Remove ${esc(e.name)}">×</button>` : ""}</span>
      </div>`;
    }).join("");
  }

  /* One match drawn as the board's own table, shrunk.

     The markup below is the same structure game.js builds - .table holding a
     .player pod, the .pile, and the other .player - and the rules come from
     the shared table.css, so this is literally the board's table rather than
     something that resembles it.  .table-live only scales it. */

  /* Purpose: the pod for a SEAT, not a slot.
     The board always lays out seat 1's block, then the pile, then seat 2's,
     because .player.s1 hangs its pod off the top rail and .player.s2 off the
     bottom.  The coin toss means slot 0 is not always seat 1, so rendering by
     slot put an s2 block at the top, whose pod is pulled down into the pile. */
  function podForSeat(m, seat) {
    const eid = m.seats ? m.seats[seat - 1] : null;
    const i = eid ? m.slots.indexOf(eid) : -1;
    // the seat is passed down: a preview has no entrants yet, and the pod
    // still has to know which rail it belongs to
    return podHtml(m, i >= 0 ? i : seat - 1, seat);
  }

  function podHtml(m, i, seatHint) {
    const gs = m.game_state;
    const eid = m.slots[i];
    const e = eid ? entrant(eid) : null;
    const seat = (m.seats && eid ? (m.seats[0] === eid ? 1 : m.seats[1] === eid ? 2 : 0) : 0)
      || seatHint || 0;
    const won = Boolean(m.winner && m.winner === eid);
    const lost = Boolean(m.winner && eid && m.winner !== eid);
    const onTurn = Boolean(gs && gs.status === "playing" && seat && gs.turn === seat);
    const name = e ? esc(e.name) : m.bye ? "Bye" : "To be decided";
    const left = gs && gs.cards_left && seat ? gs.cards_left[seat - 1] : null;
    const secs = gs && gs.time_remaining && seat ? gs.time_remaining[seat - 1] : null;
    const pct = secs !== null && gs.time_limit ? Math.max(0, Math.min(100, (secs / gs.time_limit) * 100)) : 100;
    // game.js's fmtClock, character for character: tenths, and 2:00.0 never 1:60.0
    const fmtClock = (seconds) => {
      const tenths = Math.max(0, Math.round((Number(seconds) || 0) * 10));
      const m = Math.floor(tenths / 600);
      const rest = (tenths % 600) / 10;
      return m + ":" + (rest < 10 ? "0" : "") + rest.toFixed(1);
    };
    const clock = secs === null ? "–" : fmtClock(secs);
    const low = secs !== null && secs < 15 && gs && gs.status === "playing";

    // the hand: every card 1..k, the ones already played face down
    let hand = "";
    if (gs && gs.hands && seat) {
      const inHand = new Set(gs.hands[seat - 1]);
      const last = gs.last_move && gs.last_move.seat === seat ? gs.last_move.card : null;
      const parts = [];
      for (let c = 1; c <= gs.cards; c++) {
        const cls = ["card"];
        if (!inHand.has(c)) { cls.push("played"); if (c === last) cls.push("last"); }
        parts.push(`<button class="${cls.join(" ")}" disabled aria-hidden="true">${c}</button>`);
      }
      hand = `<div class="hand${gs.cards > 120 ? " very-dense" : gs.cards > 45 ? " dense" : ""}">${parts.join("")}</div>`;
    }

    return `<div class="player s${seat || 1}${onTurn ? " on-turn" : ""}${won ? " winner" : ""}${lost ? " loser" : ""}">
      <div class="player-head"><div class="pod">
        ${/* the board puts the picture in an <img> inside .avatar, which
              table.css sizes to 82% and renders pixelated; an empty seat
              shows its number. Same markup here or it would not match. */
          e && e.avatar ? `<div class="avatar"><img src="${avatarSrc(e.avatar)}" alt=""></div>`
                        : `<div class="avatar">${seat || ""}</div>`}
        <div class="pod-text">
          <div class="player-name">${seat ? `<span class="seat-tag s${seat}">${seat}</span> ` : ""}<span class="name">${name}</span>${e && e.bot ? ' <span class="badge">bot</span>' : ""}${me && e && e.id === me.id ? ' <span class="badge blue">you</span>' : ""}</div>
          <div class="clock-wrap">
            <div class="bar${low ? " red" : ""}"><div class="bar-fill clock-fill" style="width:${pct}%"></div></div>
            <div class="clock${low ? " low" : ""}">${clock}</div>
          </div>
          <div class="cards-left">${left !== null ? `${left} of ${gs.cards} cards` : ""}</div>
        </div>
      </div></div>
      ${hand}
      <div class="hand-note"></div>
    </div>`;
  }

  function pileHtml(m) {
    const gs = m.game_state;
    if (!gs) {
      return `<div class="pile"><div class="count-label">${m.bye ? "no opponent"
        : m.status === "pending" ? "waiting for the previous round" : "not started"}</div></div>`;
    }
    const total = gs.initial_stones;
    const rows = [];
    for (let r = 0; r * 25 < total; r++) {
      const groups = [];
      for (let g = 0; g < 5 && r * 25 + g * 5 < total; g++) {
        const n = Math.min(5, total - (r * 25 + g * 5));
        let pebbles = "";
        for (let k = 0; k < n; k++) {
          const idx = r * 25 + g * 5 + k;
          pebbles += `<span class="pebble${idx >= gs.stones ? " gone" : ""}"></span>`;
        }
        groups.push(`<span class="pgroup">${pebbles}</span>`);
      }
      rows.push(`<div class="prow">${groups.join("")}</div>`);
    }
    const pct = total ? Math.round((gs.stones / total) * 100) : 0;
    // the same sentence game.js writes, including the winner line at zero
    const winner = gs.winner ? (entrant((m.seats || [])[gs.winner - 1]) || {}).name : null;
    const label = gs.stones === 0 && winner
      ? `no stones left — <b>${esc(winner)}</b> took the last one`
      : `${gs.stones === 1 ? "stone" : "stones"} on the table, <b>${total - gs.stones}</b> removed`;
    return `<div class="pile">
      <div class="count-label">${label}</div>
      <div class="count">${gs.stones}</div>
      <div class="bar gold" role="progressbar" aria-label="stones remaining">
        <div class="bar-fill" style="width:${pct}%"></div>
        <span class="bar-label">${gs.stones} of ${total} stones left</span>
      </div>
      <div class="pebbles${total > 250 ? " many" : ""}">${rows.join("")}</div>
    </div>`;
  }

  /* A match in the bracket: the two sides, the result, and the buttons.
     Clicking it shows that match's table in the right-hand pane. */
  function matchHtml(m) {
    const gs = m.game_state;
    const mine = Boolean(me && m.slots.includes(me.id));
    const sides = [0, 1].map((i) => {
      const eid = m.slots[i];
      const e = eid ? entrant(eid) : null;
      const seat = m.seats && eid ? (m.seats[0] === eid ? 1 : m.seats[1] === eid ? 2 : 0) : 0;
      const won = Boolean(m.winner && m.winner === eid);
      const lost = Boolean(m.winner && eid && m.winner !== eid);
      const onTurn = Boolean(gs && gs.status === "playing" && !gs.paused && seat && gs.turn === seat);
      const claimed = Boolean(gs && seat && gs.occupied && gs.occupied[seat - 1]);
      let meta = "";
      if (e && gs && gs.status === "waiting") meta = claimed ? "seated" : "not seated yet";
      else if (e && gs && gs.status === "playing") meta = gs.paused ? "paused" : onTurn ? "to move" : "";
      else if (e && gs && gs.cards_left && seat) meta = `${gs.cards_left[seat - 1]} cards left`;
      else if (won) meta = m.walkover ? "walkover" : m.bye ? "bye" : "won";
      const name = e ? esc(e.name) : m.bye ? "Bye" : "To be decided";
      return `<div class="side${won ? " winner" : ""}${lost ? " loser" : ""}${onTurn ? " turn" : ""}">
        <span class="seat-tag ${seat ? "s" + seat : "none"}">${seat || "·"}</span>
        ${e ? `<img src="${avatarSrc(e.avatar)}" alt="">` : '<span class="ph"></span>'}
        <span class="text"><span class="name${e ? "" : " tbd"}">${name}${e && e.bot ? ' <span class="badge">bot</span>' : ""}${me && e && e.id === me.id ? ' <span class="badge blue">you</span>' : ""}</span>${meta ? `<span class="meta">${meta}</span>` : ""}</span>
        ${won ? '<svg class="mark"><use href="#i-check"/></svg>' : ""}
      </div>`;
    }).join("");

    let status = "", pill = "";
    if (m.status === "bye") status = "No opponent in this round";
    else if (m.status === "pending") status = "Waiting for the previous round";
    else if (m.status === "ready") status = anyLive() ? "Next up" : "Ready to start";
    else if (m.status === "waiting") { pill = "waiting"; status = "Waiting for the players to sit down"; }
    else if (m.status === "playing") {
      pill = gs.paused ? "waiting" : "playing";
      status = `${gs.stones} of ${gs.initial_stones} stones · ${plural(gs.moves, "move")}${gs.paused ? " · paused" : ""}`;
    } else if (m.status === "done") {
      pill = "finished";
      const w = entrant(m.winner);
      status = `${w ? esc(w.name) : "?"} won` + (m.walkover ? " by walkover" : gs && gs.reason ? ` · ${esc(gs.reason)}` : "");
    }

    const key = `${m.round}-${m.index}`;
    let actions = "";
    // matches wait for the organiser; only one can be running at a time
    if (m.status === "ready" && t.status === "running") {
      actions += `<button class="btn blue" type="button" data-play="${key}"${anyLive() ? " disabled title=\"Another match is still being played\"" : ""}><svg class="icon"><use href="#i-play"/></svg>Start match</button>`;
    }
    if (gs && m.status === "playing") {
      actions += gs.paused
        ? `<button class="btn blue" type="button" data-resume="${esc(m.game)}"><svg class="icon"><use href="#i-play"/></svg>Resume</button>`
        : `<button class="btn" type="button" data-pause="${esc(m.game)}"><svg class="icon"><use href="#i-pause"/></svg>Pause</button>`;
    }
    if (m.game) actions += `<a class="btn" href="/game/${esc(m.game)}"><svg class="icon"><use href="#${m.status === "done" ? "i-eye" : "i-play"}"/></svg>${m.status === "done" ? "Replay" : "Open"}</a>`;
    if (mine && t.you && t.you.status === "play" && t.you.game === m.game && !t.you.claimed) {
      actions += `<button class="btn blue" type="button" data-sit="${esc(m.game)}" data-seat="${t.you.seat}">Sit down</button>`;
    }
    const live = m.status === "playing";
    const picked = picking() === key;
    return `<div class="match${live ? " live" : ""}${mine && m.winner === null ? " mine" : ""}${picked ? " picked" : ""}" data-key="${key}">
      ${m.game ? `<button class="head-row" type="button" data-show="${key}">${sides}</button>` : sides}
      <div class="foot">${pill ? `<span class="status ${pill}"><span class="dot"></span>${status}</span>` : `<span>${status}</span>`}<span class="spacer"></span>${actions}</div>
    </div>`;
  }

  /* ------------------------------------------------- the table on the right

     One match at a time is played, so the right-hand pane follows it without
     being asked. Clicking another match pins that one instead; the pin is
     dropped as soon as a new match starts, so the pane goes back to showing
     whatever is live. */

  let pinned = null;          // "round-index" the user clicked, or null
  let championDismissed = false;
  let celebrated = false;      // the champion's fireworks run once
  let celebratingMatch = null; // key of the match whose result is on screen
  let celebratedMatches = {};  // keys already celebrated, so a poll cannot repeat one
  let celebrationTimer = null;

  const matchKey = (m) => (m ? `${m.round}-${m.index}` : null);

  /* A match that has just been decided gets its moment.  Only once: the
     bracket is polled every couple of seconds and the result would otherwise
     fire again on every one of them. */
  function celebrateMatch() {
    const m = shownMatch();
    const key = matchKey(m);
    if (!m || m.winner === null || !key) return;
    if (t.status === "finished") return;          // the champion card takes over
    if (celebratedMatches[key]) return;
    celebratedMatches[key] = true;
    celebratingMatch = key;
    const canvas = $("fireworks");
    if (canvas && window.cardnimFireworks) window.cardnimFireworks(canvas, MATCH_CELEBRATION);
    clearTimeout(celebrationTimer);
    celebrationTimer = setTimeout(() => {
      celebratingMatch = null;
      lastSignature = null;
      render();
    }, MATCH_CELEBRATION);
    lastSignature = null;
    render();
  }

  function celebrateChampion() {
    if (t.status !== "finished" || !t.champion || championDismissed) return;
    const close = $("champ-close");
    if (close) close.addEventListener("click", () => { championDismissed = true; lastSignature = null; render(); });
    if (celebrated) return;
    celebrated = true;
    const canvas = $("fireworks");
    if (canvas && window.cardnimFireworks) window.cardnimFireworks(canvas, 4200);
  }

  function anyLive() {
    return Boolean(liveMatch());
  }

  function liveMatch() {
    for (const r of t.rounds || []) {
      for (const m of r.matches) {
        if (m.game_state && m.game_state.status !== "finished") return m;
      }
    }
    return null;
  }
  function lastPlayed() {
    let out = null;
    for (const r of t.rounds || []) for (const m of r.matches) if (m.game_state) out = m;
    return out;
  }
  function nextReady() {
    for (const r of t.rounds || []) for (const m of r.matches) if (m.status === "ready") return m;
    return null;
  }
  function shownMatch() {
    if (pinned) {
      for (const r of t.rounds || []) {
        for (const m of r.matches) if (`${m.round}-${m.index}` === pinned) return m;
      }
    }
    return liveMatch() || nextReady() || lastPlayed();
  }
  function picking() {
    const m = shownMatch();
    return m ? `${m.round}-${m.index}` : null;
  }

  /* The champion card, laid over the table the way the board lays a game's
     winner over it: same .winner-card, same fireworks canvas, same CSS. */
  /* When a match ends, its winner is celebrated over the table exactly as a
     game's winner is on the board: the same card, the same fireworks.  It
     holds for MATCH_CELEBRATION ms and then the table goes back to showing
     the final position, so the room gets a moment on each result before the
     next match is started. */
  const MATCH_CELEBRATION = 3000;

  function matchCardHtml(m) {
    if (!m || m.winner === null || celebratingMatch !== matchKey(m)) return "";
    const w = entrant(m.winner);
    const seat = (m.seats || []).indexOf(m.winner) + 1;
    const gs = m.game_state || {};
    return `<div class="winner-card ${"s" + (seat || 1)}" role="dialog" aria-label="Result">
      <div class="big-avatar">${w && w.avatar ? `<img src="${avatarSrc(w.avatar)}" alt="">` : "–"}</div>
      <div class="label">Winner</div>
      <h2>${esc(w ? w.name : "?")}</h2>
      <div class="why">${esc(gs.reason || (m.walkover ? "walkover" : ""))}</div>
    </div>`;
  }

  function championCardHtml() {
    if (t.status !== "finished" || championDismissed) return "";
    const c = t.champion ? entrant(t.champion) : null;
    const seat = c && shownMatch() && shownMatch().winner === c.id
      ? ((shownMatch().seats || []).indexOf(c.id) + 1) : 0;
    return `<div class="winner-card ${c ? "s" + (seat || 1) : "none"}" role="dialog" aria-label="Champion">
      <div class="big-avatar">${c && c.avatar ? `<img src="${avatarSrc(c.avatar)}" alt="">` : "–"}</div>
      <div class="label">${c ? "Champion" : "No champion"}</div>
      <h2>${esc(c ? c.name : "Tournament aborted")}</h2>
      <div class="why">${c ? `${plural(c.wins, "game")} won · ${plural(t.entrants.length, "entrant")}`
                           : esc(t.reason || "")}</div>
      <div class="actions"><button class="btn small" type="button" id="champ-close">Close</button>
        <a class="btn gold small" href="/">Lobby</a></div>
    </div>`;
  }

  /* A match that has not been started has no game, so there is nothing to
     draw from.  Rather than leave the pane empty, stand the table up from the
     tournament's own settings: a full pile, two full hands and two full
     clocks.  It is the table as it will be the moment you press Start. */
  function previewMatch(m) {
    const full = Array.from({ length: t.cards }, (_, i) => i + 1);
    const slots = m ? m.slots : [null, null];
    return Object.assign({}, m || { round: 0, index: 0, bye: false, winner: null, status: "pending" }, {
      slots,
      seats: slots,                 // no coin toss yet: show them in bracket order
      game_state: {
        status: "waiting", stones: t.stones, initial_stones: t.stones, cards: t.cards,
        moves: 0, turn: null, paused: false, winner: null, reason: "",
        time_limit: t.time_limit,
        cards_left: [t.cards, t.cards],
        time_remaining: [t.time_limit, t.time_limit],
        occupied: [false, false],
        hands: [full, full],
        last_move: null,
      },
    });
  }

  function tablePaneHtml() {
    let m = shownMatch();
    let caption;
    if (!m || !m.game_state) {
      // Between games: both players are known, so the table can be stood up
      // ready to play - full pile, full hands, full clocks - and it waits.
      if (m && m.status === "ready") {
        const names = m.slots.map((id) => (entrant(id) || {}).name || "?");
        caption = anyLive()
          ? `<b>${esc(names[0])}</b> v <b>${esc(names[1])}</b> · after the match in progress`
          : `<b>${esc(names[0])}</b> v <b>${esc(names[1])}</b> · press Start match`;
        const p = previewMatch(m);
        return `<div class="table-live preview"><div class="table">
            ${podForSeat(p, 1)}${pileHtml(p)}${podForSeat(p, 2)}
          </div></div>
          <div class="table-caption">${caption}</div>`;
      }
      // Nothing to stand up yet: no bracket, or the pairing is not known.
      return `<div class="table-empty"><b>${t.status === "open" ? "No bracket yet" : "Nothing to play"}</b>${
        t.status === "open" ? "Draw the bracket, then start each match yourself."
          : "This match is waiting for the previous round."}</div>`;
    }
    const gs = m.game_state;
    caption = gs.status === "finished"
      ? `<b>${esc((entrant(m.winner) || {}).name || "?")}</b> won · ${esc(gs.reason || "")}`
      : gs.paused ? "<b>Paused</b> · the clocks are stopped"
      : gs.status === "waiting" ? "Waiting for both sides to sit down"
      : `${plural(gs.moves, "move")} played · <b>${gs.stones}</b> stones left`;
    return `<div class="table-live"><div class="table">
        <canvas class="fireworks" id="fireworks" aria-hidden="true"></canvas>
        ${championCardHtml() || matchCardHtml(m)}
        ${podForSeat(m, 1)}${pileHtml(m)}${podForSeat(m, 2)}
      </div></div>
      <div class="table-caption">${caption}</div>`;
  }

  function tableTitleHtml() {
    const m = shownMatch();
    if (!m) return "Table";
    const round = (t.rounds[m.round - 1] || {}).name || `Round ${m.round}`;
    const names = m.slots.map((id) => (entrant(id) || {}).name || "?");
    return `${esc(round)}${t.rounds[m.round - 1] && t.rounds[m.round - 1].matches.length > 1 ? " " + (m.index + 1) : ""} · ${esc(names[0])} v ${esc(names[1])}`;
  }

  function tableActionsHtml() {
    const m = shownMatch();
    if (m && !m.game && m.status === "ready" && t.status === "running") {
      return `<button class="btn blue" type="button" data-play="${m.round}-${m.index}"${anyLive() ? " disabled" : ""}><svg class="icon"><use href="#i-play"/></svg>Start match</button>`;
    }
    if (!m || !m.game) return "";
    const gs = m.game_state;
    let out = "";
    if (gs && gs.status === "playing") {
      out += gs.paused
        ? `<button class="btn blue" type="button" data-resume="${esc(m.game)}"><svg class="icon"><use href="#i-play"/></svg>Resume</button>`
        : `<button class="btn" type="button" data-pause="${esc(m.game)}"><svg class="icon"><use href="#i-pause"/></svg>Pause</button>`;
    }
    out += `<a class="btn" href="/game/${esc(m.game)}"><svg class="icon"><use href="#i-eye"/></svg>Open</a>`;
    return out;
  }

  function bracketHtml() {
    if (t.status === "open" || !t.rounds.length) {
      const n = t.entrants.length;
      let size = 1;
      while (size < Math.max(n, 2)) size *= 2;
      const byes = size - n;
      const rounds = Math.log2(size);
      return `<div class="bracket-empty">${n < 2 ? "The bracket is drawn when the tournament starts. It needs at least two entrants."
        : `${plural(n, "entrant")}: a bracket of ${size} with ${plural(rounds, "round")}${byes ? `, ${plural(byes, "bye")} in round one` : ""}. Press Start to draw it.`}</div>`;
    }
    return `<div class="bracket">${t.rounds.map((r) => `<div class="round">
      <div class="round-title">${esc(r.name)}</div>
      <div class="matches">${r.matches.map(matchHtml).join("")}</div>
    </div>`).join("")}</div>`;
  }

  /* The settings sit in the header beside the tournament's name, the way the
     board puts "100 stones  cards 1..25  2:00 / player" along its top. */
  function settingsHtml() {
    return `<span class="hs"><svg class="icon"><use href="#i-stones"/></svg><b>${t.stones}</b> stones</span>
      <span class="hs"><svg class="icon"><use href="#i-cards"/></svg>cards 1..<b>${t.cards}</b></span>
      <span class="hs"><svg class="icon"><use href="#i-clock"/></svg><b>${Math.round(t.time_limit)}</b>s each</span>`;
  }

  function actionsHtml() {
    if (t.status === "open") {
      return `<button class="btn blue" type="button" id="start"${t.entrants.length < 2 ? " disabled" : ""}><svg class="icon"><use href="#i-play"/></svg>Draw the bracket</button>`;
    }
    if (t.status === "running") return "";       // each match has its own buttons
    return `<a class="btn" href="/">Lobby</a>`;
  }

  let lastSignature = null;
  function render() {
    if (!t) return;
    document.title = `${title()} — Card Nim`;
    $("title").textContent = title();
    $("server-info").textContent = window.location.host;
    $("entrants-count").textContent = t.entrants.length ? `· ${t.entrants.length}` : "";
    const signature = [t.version, me && me.id, confirmAbort, publicUrl, isHost, pinned,
                       celebratingMatch].join("|");
    if (signature === lastSignature) return;       // nothing changed: leave the DOM alone
    lastSignature = signature;

    const you = $("you");
    const yh = youHtml();
    you.hidden = !yh;
    you.className = "you-card" + (t.you && (t.you.status === "play" || t.you.status === "champion") ? " play" : "");
    you.innerHTML = yh;
    $("settings").innerHTML = settingsHtml();
    $("entrants").innerHTML = entrantsHtml();
    renderJoin();
    drawQr();
    $("actions").innerHTML = actionsHtml();
    // Replacing the bracket's HTML throws away how far it was scrolled, so a
    // poll would yank it back to the left mid-drag. Keep the position across
    // the redraw, so a poll cannot yank it back while you are scrolling.
    const keptScroll = (() => {
      const box = $("detail").querySelector(".bracket");
      return box ? box.scrollLeft : null;
    })();
    $("detail").innerHTML = `<div><div class="group-title">Bracket</div>${bracketHtml()}</div>`;
    if (keptScroll !== null) {
      const box = $("detail").querySelector(".bracket");
      if (box) box.scrollLeft = keptScroll;
    }
    $("table-title").textContent = tableTitleHtml();
    $("table-actions").innerHTML = tableActionsHtml();
    $("livetable").innerHTML = tablePaneHtml();
    wire();
    celebrateChampion();
    celebrateMatch();
  }

  function wire() {
    const fail = (where) => (e) => { const el = $(where); if (el) el.textContent = e.message; };
    const on = (id, fn) => { const el = $(id); if (el) el.addEventListener("click", fn); };
    on("start", () => { $("start").disabled = true; start().catch((e) => { $("start").disabled = false; $("title").textContent = e.message; }); });
    on("abort-btn", () => { confirmAbort = true; render(); });
    on("abort-no", () => { confirmAbort = false; render(); });
    on("abort-yes", () => { $("abort-yes").disabled = true; abort().then(() => { confirmAbort = false; }).catch((e) => { $("title").textContent = e.message; }); });
    on("copy", async () => {
      const url = publicUrl + "/tournament/" + t.id;
      try { await navigator.clipboard.writeText(url); $("copied").textContent = "Copied"; }
      catch (e) { $("copied").textContent = url; }
      setTimeout(() => { const c = $("copied"); if (c) c.textContent = ""; }, 2500);
    });
    on("withdraw", () => { $("withdraw").disabled = true; withdraw().catch(fail("you-error")); });
    on("forget", () => { me = null; saveMe(); lastSignature = null; refreshNow(); });
    on("sit", () => { const b = $("sit"); b.disabled = true; sitDown(b.dataset.game, Number(b.dataset.seat)).catch((e) => { b.disabled = false; fail("you-error")(e); }); });
    $("entrants").querySelectorAll("[data-remove]").forEach((b) => b.addEventListener("click", () => {
      b.disabled = true;
      const id = Number(b.dataset.remove);
      (me && me.id === id ? withdraw() : removeBot(id)).catch((e) => { b.disabled = false; $("title").textContent = e.message; });
    }));
    $("detail").querySelectorAll("[data-sit]").forEach((b) => b.addEventListener("click", () => {
      b.disabled = true;
      sitDown(b.dataset.sit, Number(b.dataset.seat)).catch((e) => { b.disabled = false; $("title").textContent = e.message; });
    }));
    const startMatch = (b) => {
      const [round, index] = b.dataset.play.split("-").map(Number);
      b.disabled = true;
      api("POST", `/api/tournaments/${tid}/play`, { round, match: index })
        .then(accept)
        .catch((e) => { b.disabled = false; $("title").textContent = e.message; });
    };
    $("detail").querySelectorAll("[data-play]").forEach((b) => b.addEventListener("click", () => startMatch(b)));
    $("table-actions").querySelectorAll("[data-play]").forEach((b) => b.addEventListener("click", () => startMatch(b)));
    $("detail").querySelectorAll("[data-show]").forEach((b) => b.addEventListener("click", () => {
      pinned = b.dataset.show === picking() ? null : b.dataset.show;
      lastSignature = null;
      render();
    }));
    $("detail").querySelectorAll("[data-pause], [data-resume]").forEach((b) => b.addEventListener("click", () => {
      const id = b.dataset.pause || b.dataset.resume;
      b.disabled = true;
      api("POST", `/api/games/${id}/${b.dataset.pause ? "pause" : "resume"}`)
        .then(refreshNow)
        .catch((e) => { b.disabled = false; $("title").textContent = e.message; });
    }));
    $("table-actions").querySelectorAll("[data-pause], [data-resume]").forEach((b) => b.addEventListener("click", () => {
      const id = b.dataset.pause || b.dataset.resume;
      b.disabled = true;
      api("POST", `/api/games/${id}/${b.dataset.pause ? "pause" : "resume"}`)
        .then(refreshNow)
        .catch((e) => { b.disabled = false; $("title").textContent = e.message; });
    }));
  }

  if (!tid) {
    $("detail").innerHTML = '<div class="detail-empty">No tournament id in the address.</div>';
  } else {
    poll();
  }
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && t && t.status !== "finished") refreshNow();
  });
})();
