import { esc, safeUrl, icon, minutes } from "./ui.js";
import { Site } from "./api.js";
import { watchlist } from "./store.js";
import { playTitle } from "./play.js";
import { motionOn, reduced } from "./motion.js";

/** Featured hero: cross-fading backdrops with a slow zoom, staged text entrance, dots + arrows, pauses when hidden or in use. */
export function createHero(root, slides, ctx) {
  const reg = new AbortController(), sig = { signal: reg.signal };
  let i = 0, timer = 0, zoom = null, tl = null, dead = false, paused = false;
  const wide = slides.map(s => safeUrl(s.backdrop) || safeUrl(s.poster));
  root.innerHTML = `<section class="hero" aria-roledescription="carousel" aria-label="Featured titles">
    <div class="hero-bg">${slides.map((s, k) => `<div class="layer${k === 0 ? " on" : ""}" aria-hidden="true">${wide[k] ? `<img src="${esc(wide[k])}" alt="" ${k === 0 ? 'fetchpriority="high"' : 'loading="lazy"'} decoding="async" referrerpolicy="no-referrer">` : ""}</div>`).join("")}</div>
    <div class="hero-in container-fluid aj-gutter"><div class="hero-copy" aria-live="polite" id="hero-copy"></div></div>
    <div class="hero-dots">${slides.length > 1 ? `<div class="hero-arrows"><button class="icon-btn" type="button" data-hero="prev" aria-label="Previous featured title"><i class="bi bi-chevron-left"></i></button><button class="icon-btn" type="button" data-hero="next" aria-label="Next featured title"><i class="bi bi-chevron-right"></i></button></div>` : ""}
      ${slides.length > 1 ? slides.map((_, k) => `<button class="hero-dot${k === 0 ? " on" : ""}" type="button" data-dot="${k}" aria-label="Show featured title ${k + 1} of ${slides.length}"></button>`).join("") : ""}</div></section>`;
  const copy = root.querySelector("#hero-copy"), layers = [...root.querySelectorAll(".layer")], dots = [...root.querySelectorAll(".hero-dot")];

  const paint = (s, detail) => {
    const meta = [s.year, detail && detail.runtime ? minutes(detail.runtime) : "", s.kind === "series" ? "Series" : "Movie"].filter(Boolean);
    const saved = watchlist.has(s.id);
    copy.innerHTML = `<div class="meta" data-intro>${s.rating ? `<span class="rating">${icon("star-fill")}${esc(s.rating)}</span>` : ""}${meta.map(m => `<span class="sep">${esc(m)}</span>`).join("")}${(s.genres || []).slice(0, 3).map(g => `<span class="sep">${esc(g)}</span>`).join("")}</div>
      <h1 data-intro>${esc(s.title)}</h1>${s.overview ? `<p class="overview" data-intro>${esc(s.overview)}</p>` : ""}
      <div class="actions" data-intro><button class="aj-btn primary" type="button" data-play>${icon("play-fill")}Play now</button>
      <a class="aj-btn" href="/title/${esc(s.id)}" data-link>${icon("info-circle")}More information</a>
      <button class="aj-btn ghost${saved ? " on" : ""}" type="button" data-list aria-pressed="${saved}" aria-label="${saved ? "Remove from" : "Add to"} watchlist">${icon(saved ? "bookmark-check-fill" : "bookmark-plus")}</button></div>`;
  };
  const detailFor = s => Site.title(s.id).catch(() => null);

  async function show(n, first = false) {
    if (dead) return; n = (n + slides.length) % slides.length;
    const s = slides[n], prev = i; i = n; clearTimeout(timer);
    dots.forEach((d, k) => { d.classList.toggle("on", k === n); d.toggleAttribute("aria-current", k === n); });
    const detail = await detailFor(s); if (dead || i !== n) return;
    const swap = () => {
      layers.forEach((l, k) => l.classList.toggle("on", k === n));
      paint(s, detail);
      if (motionOn()) {
        tl && tl.kill(); zoom && zoom.kill();
        const img = layers[n].querySelector("img");
        tl = gsap.timeline({ defaults: { ease: "power3.out" } });
        if (first || prev !== n) tl.fromTo(layers[n], { opacity: first ? 0 : 0 }, { opacity: 1, duration: .9, ease: "power1.inOut" }, 0);
        if (prev !== n && !first) tl.to(layers[prev], { opacity: 0, duration: .9, ease: "power1.inOut" }, 0);
        tl.fromTo(copy.querySelectorAll("[data-intro]"), { opacity: 0, y: 20 }, { opacity: 1, y: 0, duration: .65, stagger: .08, clearProps: "transform" }, .15);
        if (img) zoom = gsap.fromTo(img, { scale: 1 }, { scale: 1.08, duration: 16, ease: "none" });
      } else layers.forEach((l, k) => { l.style.opacity = k === n ? 1 : 0; });
    };
    if (motionOn() && !first && copy.firstElementChild) gsap.to(copy, { opacity: 0, duration: .18, onComplete: () => { gsap.set(copy, { opacity: 1 }); swap(); } }); else swap();
    schedule();
  }
  function schedule() { clearTimeout(timer); if (slides.length < 2 || reduced() || paused || document.hidden) return; timer = setTimeout(() => show(i + 1), 9000); }

  root.addEventListener("click", e => {
    const b = e.target.closest("[data-hero],[data-dot],[data-play],[data-list]"); if (!b) return; const s = slides[i];
    if (b.dataset.hero) show(i + (b.dataset.hero === "next" ? 1 : -1));
    else if (b.dataset.dot) show(+b.dataset.dot);
    else if (b.hasAttribute("data-play")) { b.disabled = true; playTitle(s, ctx.go).finally(() => { b.disabled = false; }); }
    else if (b.hasAttribute("data-list")) { const on = watchlist.toggle(s); b.classList.toggle("on", on); b.setAttribute("aria-pressed", String(on)); b.innerHTML = icon(on ? "bookmark-check-fill" : "bookmark-plus"); }
  }, sig);
  root.addEventListener("pointerenter", () => { paused = true; clearTimeout(timer); }, sig);
  root.addEventListener("pointerleave", () => { paused = false; schedule(); }, sig);
  root.addEventListener("focusin", () => { paused = true; clearTimeout(timer); }, sig);
  root.addEventListener("focusout", () => { paused = false; schedule(); }, sig);
  document.addEventListener("visibilitychange", schedule, sig);
  show(0, true);
  return () => { dead = true; clearTimeout(timer); tl && tl.kill(); zoom && zoom.kill(); reg.abort(); };
}
