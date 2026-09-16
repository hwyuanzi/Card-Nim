/* The finish: a few seconds of fireworks on a canvas laid over the table.

   Shared by the board (a game's winner) and the bracket (the champion), so
   the celebration is the same one in both places rather than two copies that
   drift apart.  Skipped when the viewer prefers reduced motion. */

window.cardnimFireworks = (function () {
  "use strict";

  /* Purpose: a few seconds of fireworks on the canvas that covers the table.
     Skipped when the viewer prefers reduced motion. */
  function fireworks(canvas, durationMs) {
    if (!canvas || !canvas.getContext) return;
    if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth, h = canvas.clientHeight;
    if (!w || !h) return;
    canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
    const ctx = canvas.getContext("2d");
    ctx.scale(dpr, dpr);
    const colors = ["#ffd166", "#34c759", "#4cc2ff", "#ff9500", "#ff6b9d", "#ffffff", "#d6b25e"];
    const sparks = [];
    const start = performance.now();
    let nextBurst = start;
    if (canvas._raf) cancelAnimationFrame(canvas._raf);
    function burst(x, y) {
      const color = colors[Math.floor(Math.random() * colors.length)];
      const n = 70 + Math.floor(Math.random() * 40);
      for (let i = 0; i < n; i++) {
        const a = (Math.PI * 2 * i) / n + Math.random() * 0.2;
        const sp = 1.6 + Math.random() * 3.2;
        sparks.push({ x, y, vx: Math.cos(a) * sp, vy: Math.sin(a) * sp, life: 1, decay: 0.009 + Math.random() * 0.010, color, r: 2.2 + Math.random() * 2.2 });
      }
    }
    function frame(now) {
      const t = now - start;
      if (t < durationMs && now >= nextBurst) {
        burst(w * (0.15 + Math.random() * 0.7), h * (0.12 + Math.random() * 0.45));
        nextBurst = now + 200 + Math.random() * 260;
      }
      ctx.clearRect(0, 0, w, h);
      for (let i = sparks.length - 1; i >= 0; i--) {
        const p = sparks[i];
        p.x += p.vx; p.y += p.vy; p.vy += 0.045; p.vx *= 0.985; p.vy *= 0.985; p.life -= p.decay;
        if (p.life <= 0) { sparks.splice(i, 1); continue; }
        ctx.globalAlpha = Math.max(0, p.life);
        ctx.fillStyle = p.color;
        ctx.beginPath(); ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2); ctx.fill();
      }
      ctx.globalAlpha = 1;
      if (t < durationMs || sparks.length) canvas._raf = requestAnimationFrame(frame);
      else { ctx.clearRect(0, 0, w, h); canvas._raf = null; }
    }
    canvas._raf = requestAnimationFrame(frame);
  }

  return fireworks;
})();
