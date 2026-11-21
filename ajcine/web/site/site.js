/* AJ CINE AI public site. Vanilla JS, no inline code (strict CSP). Developed by AJ APPLICATIONS · Developer: AJWEYA */
"use strict";
const $ = (s, r = document) => r.querySelector(s);
const main = $("#main");
let CFG = { streaming: false, bot: null };

const esc = v => String(v ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const safeUrl = u => (typeof u === "string" && (u.startsWith("https://") || u.startsWith("/posters/"))) ? u : null;
const STORE = { list: "ajcine.watchlist.v1", history: "ajcine.history.v1", theme: "ajcine.theme.v1", profile: "ajcine.profile.v1", profiles: "ajcine.profiles.v1" };
const storageKey = key => (key === STORE.list || key === STORE.history) ? `${key}:${encodeURIComponent(activeProfile())}` : key;
const readStore = key => { try { const v = JSON.parse(localStorage.getItem(storageKey(key)) || "[]"); return Array.isArray(v) ? v : []; } catch { return []; } };
const writeStore = (key, value) => { try { localStorage.setItem(storageKey(key), JSON.stringify(value)); } catch {} };
const itemFrom = d => ({ id: String(d.id || ""), title: String(d.title || "Untitled"), year: d.year || null, kind: d.kind || "movie", poster: safeUrl(d.poster), overview: String(d.overview || "").slice(0, 700), saved_at: Date.now() });
function saveHistory(item) { if (!item || !item.id) return; const a = readStore(STORE.history).filter(x => x.id !== item.id); a.unshift(itemFrom(item)); writeStore(STORE.history, a.slice(0, 80)); }
function isSaved(id) { return readStore(STORE.list).some(x => x.id === String(id)); }
function toggleSaved(item) { const a = readStore(STORE.list); const id = String(item.id); const exists = a.some(x => x.id === id); writeStore(STORE.list, exists ? a.filter(x => x.id !== id) : [itemFrom(item), ...a].slice(0, 200)); return !exists; }
function activeProfile() { return localStorage.getItem(STORE.profile) || "Main"; }

async function api(path, body) {
  const opt = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const r = await fetch(path, opt);
  let data = null;
  try { data = await r.json(); } catch (e) { /* not JSON */ }
  if (!r.ok) {
    const err = new Error((data && data.detail) || (r.status === 429 ? "Too many requests. Please wait a moment." : "Something went wrong."));
    err.status = r.status; throw err;
  }
  return data;
}

/* ---------- small views ---------- */
function art(item) {
  const src = safeUrl(item.poster);
  return `<div class="art">${src ? `<img src="${esc(src)}" alt="" loading="lazy" referrerpolicy="no-referrer" data-poster>` : ""}${src ? "" : `<div class="noposter">${esc(item.title)}</div>`}</div>`;
}
const card = it => `<article class="card" data-card-id="${esc(it.id)}"><a class="card-link" href="/title/${esc(it.id)}" data-link data-card-open>${art(it)}<h3>${esc(it.title)}</h3><div class="sub">${esc([it.year, it.kind === "series" ? "Series" : "Movie"].filter(Boolean).join(" · "))}</div></a><button class="save-toggle ${isSaved(it.id) ? "saved" : ""}" type="button" data-save="${esc(it.id)}" aria-label="${isSaved(it.id) ? "Remove from" : "Add to"} My List" title="${isSaved(it.id) ? "Remove from" : "Add to"} My List"><i class="bi ${isSaved(it.id) ? "bi-bookmark-check-fill" : "bi-bookmark-plus"}"></i></button></article>`;
const rail = (title, items, more) => items && items.length ? `<section><div class="row-h"><h2>${esc(title)}</h2>${more ? `<a href="${esc(more)}" data-link>See all</a>` : ""}</div><div class="rail">${items.map(card).join("")}</div></section>` : "";
const state = (h, p, keys = "") => { main.innerHTML = `<div class="state"><h2>${esc(h)}</h2>${p ? `<p>${esc(p)}</p>` : ""}${keys}</div>`; };
const fail = e => e.status === 404 ? state("Nothing here", e.message || "This page is not available.", `<div class="keys"><a class="key go" href="/" data-link>Go to home</a></div>`) : state("Could not load this page", e.message, `<div class="keys"><button class="key go" data-retry>Try again</button></div>`);

// A broken or blocked poster image falls back to a typographic tile (error events do not bubble, so capture).
document.addEventListener("error", e => {
  const t = e.target;
  if (t && t.matches && t.matches("img[data-poster]")) {
    const holder = t.parentElement, name = holder.closest(".card, .detail, .hero")?.querySelector("h3, h1")?.textContent || "";
    holder.innerHTML = `<div class="noposter">${esc(name)}</div>`;
  }
}, true);

/* ---------- routing ---------- */
const routes = [
  [/^\/$/, home], [/^\/search$/, search], [/^\/browse$/, browse],
  [/^\/watchlist$/, watchlistPage], [/^\/history$/, historyPage], [/^\/settings$/, settingsPage], [/^\/profiles$/, profilesPage],
  [/^\/title\/((?:tt\d{7,10})|(?:loc_[0-9a-f]{10}))$/, titlePage], [/^\/watch\/([A-Za-z0-9_-]{8,64})$/, watchPage],
];
let navId = 0, retry = null;
async function route(push) {
  const id = ++navId, url = new URL(location.href);
  for (const [re, fn] of routes) {
    const m = url.pathname.match(re);
    if (!m) continue;
    document.querySelectorAll("[data-nav]").forEach(a => { const n = a.dataset.nav; a.classList.toggle("on", (n === "home" && url.pathname === "/") || (n === "browse" && url.pathname === "/browse" && !url.searchParams.get("kind")) || (n === "movie" && url.searchParams.get("kind") === "movie") || (n === "series" && url.searchParams.get("kind") === "series")); });
    document.querySelectorAll(".profile-avatar").forEach(a => { const n = activeProfile(); a.textContent = (n[0] || "A").toUpperCase(); });
    retry = () => route(false);
    main.innerHTML = '<div class="state">Loading…</div>';
    try { await fn(m.slice(1), url.searchParams, () => id === navId); } catch (e) { if (id === navId) fail(e); }
    if (push) window.scrollTo(0, 0);
    main.focus({ preventScroll: true });
    return;
  }
  state("Page not found", "Check the address or go back to the home page.", `<div class="keys"><a class="key go" href="/" data-link>Go to home</a></div>`);
}
function go(href) { history.pushState(null, "", href); route(true); }
document.addEventListener("click", e => {
  const save = e.target.closest("[data-save]");
  if (save) { e.preventDefault(); e.stopPropagation(); const id = save.dataset.save; const cardEl = save.closest("[data-card-id]"); const link = cardEl && $(".card-link", cardEl); const artImg = cardEl && $("img", cardEl); const title = cardEl && $("h3", cardEl)?.textContent; const sub = cardEl && $(".sub", cardEl)?.textContent || ""; const item = { id, title, year: sub.split(" · ")[0], kind: sub.includes("Series") ? "series" : "movie", poster: artImg?.getAttribute("src") || null }; const saved = toggleSaved(item); save.classList.toggle("saved", saved); save.innerHTML = `<i class="bi ${saved ? "bi-bookmark-check-fill" : "bi-bookmark-plus"}"></i>`; save.setAttribute("aria-label", `${saved ? "Remove from" : "Add to"} My List`); return; }
  const a = e.target.closest("a[data-link]");
  if (a && a.hasAttribute("data-card-open")) { const cardEl = a.closest("[data-card-id]"); if (cardEl) { const sub = $(".sub", cardEl)?.textContent || ""; saveHistory({ id: cardEl.dataset.cardId, title: $("h3", cardEl)?.textContent, year: sub.split(" · ")[0], kind: sub.includes("Series") ? "series" : "movie", poster: $("img", cardEl)?.getAttribute("src") || null }); } }
  if (a && !e.metaKey && !e.ctrlKey && !e.shiftKey && e.button === 0) { e.preventDefault(); go(a.getAttribute("href")); }
  if (e.target.closest("[data-retry]") && retry) retry();
});
window.addEventListener("popstate", () => route(false));
document.addEventListener("keydown", e => { if (e.key === "/" && !["INPUT","TEXTAREA","SELECT"].includes(document.activeElement.tagName)) { e.preventDefault(); $("#q")?.focus(); } });
$("#find").addEventListener("submit", e => { e.preventDefault(); const q = $("#q").value.trim(); if (q.length >= 2) go("/search?q=" + encodeURIComponent(q)); });

/* ---------- home ---------- */
async function home(_, __, live) {
  const d = await api("/api/public/home");
  if (!live()) return;
  const all = [...d.recent, ...d.movies, ...d.series];
  if (!all.length) return state("The library is empty", "Titles show up here as soon as files are indexed and identified.");
  const h = d.recent.find(i => safeUrl(i.backdrop) || safeUrl(i.poster)) || d.recent[0];
  const bg = safeUrl(h.backdrop) || safeUrl(h.poster);
  document.title = "AJ CINE AI";
  main.innerHTML = `<section class="hero">${bg ? `<img class="bg" src="${esc(bg)}" alt="" referrerpolicy="no-referrer" data-poster>` : ""}<div class="in">
    <div class="meta">${h.rating ? `<span class="rate">★ ${esc(h.rating)}</span>` : ""}<span>${esc([h.year, h.kind === "series" ? "Series" : "Movie"].filter(Boolean).join(" · "))}</span>${h.languages.slice(0, 4).map(l => `<span class="chip">${esc(l.name)}</span>`).join("")}</div>
    <h1>${esc(h.title)}</h1>${h.overview ? `<p>${esc(h.overview)}</p>` : ""}
    <a class="key go" href="/title/${esc(h.id)}" data-link>${CFG.streaming ? "Watch or download" : "Get in Telegram"}</a></div></section>
    ${rail("Recently viewed", readStore(STORE.history).slice(0, 12), "/history")}${rail("My List", readStore(STORE.list).slice(0, 12), "/watchlist")}${rail("Recently added", d.recent, "/browse")}${rail("Movies", d.movies, "/browse?kind=movie")}${rail("Series", d.series, "/browse?kind=series")}
    ${rail("Popular this month", d.popular)}
    ${d.language_rows.map(r => rail(r.name, r.items, "/browse?language=" + encodeURIComponent(r.code))).join("")}`;
}

/* ---------- search / browse ---------- */
async function search(_, q, live) {
  const term = (q.get("q") || "").trim(); $("#q").value = term;
  if (term.length < 2) return state("Search the library", "Type at least two letters of a movie or series name.");
  const page = +q.get("page") || 1;
  const d = await api(`/api/public/search?q=${encodeURIComponent(term)}&page=${page}`);
  if (!live()) return;
  document.title = `${term} · AJ CINE AI`;
  if (!d.items.length) {
    const sg = d.suggestions.map(s => `<a class="key" href="/title/${esc(s.id)}" data-link>${esc(s.title)}${s.year ? ` <small>${esc(s.year)}</small>` : ""}</a>`).join("");
    return state(`No results for “${term}”`, sg ? "Did you mean one of these?" : "Check the spelling, or try a shorter name.", sg ? `<div class="keys">${sg}</div>` : "");
  }
  main.innerHTML = `<div class="row-h"><h2>${d.total} result${d.total === 1 ? "" : "s"} for “${esc(d.query)}”</h2></div>${d.interpreted ? `<p class="muted">Showing results for “${esc(d.interpreted)}”.</p>` : ""}<div class="grid">${d.items.map(card).join("")}</div>${pager(d.page, d.pages, p => `/search?q=${encodeURIComponent(term)}&page=${p}`)}`;
}
const pager = (page, pages, href) => pages > 1 ? `<nav class="pager" aria-label="Pages">${page > 1 ? `<a class="key back" href="${esc(href(page - 1))}" data-link>Previous</a>` : ""}<span class="muted">Page ${page} of ${pages}</span>${page < pages ? `<a class="key back" href="${esc(href(page + 1))}" data-link>Next</a>` : ""}</nav>` : "";

async function browse(_, q, live) {
  const f = { kind: q.get("kind") || "", language: q.get("language") || "", genre: q.get("genre") || "", sort: q.get("sort") || "new", page: +q.get("page") || 1 };
  const qs = new URLSearchParams(Object.entries(f).filter(([, v]) => v));
  const [d, h] = await Promise.all([api("/api/public/browse?" + qs), api("/api/public/home")]);
  if (!live()) return;
  document.title = "Browse · AJ CINE AI";
  const opt = (v, label, cur) => `<option value="${esc(v)}" ${v === cur ? "selected" : ""}>${esc(label)}</option>`;
  main.innerHTML = `<form class="filters" id="flt">
    <label class="sr" for="fk">Type</label><select id="fk" name="kind">${opt("", "Movies and series", f.kind)}${opt("movie", "Movies", f.kind)}${opt("series", "Series", f.kind)}</select>
    <label class="sr" for="fl">Language</label><select id="fl" name="language">${opt("", "Any language", f.language)}${h.languages.map(l => opt(l.code, l.name, f.language)).join("")}</select>
    <label class="sr" for="fg">Genre</label><select id="fg" name="genre">${opt("", "Any genre", f.genre)}${d.genres.map(g => opt(g, g, f.genre)).join("")}</select>
    <label class="sr" for="fs">Sort</label><select id="fs" name="sort">${opt("new", "Recently added", f.sort)}${opt("rating", "Highest rated", f.sort)}${opt("title", "Title A to Z", f.sort)}</select></form>
    ${d.items.length ? `<div class="grid">${d.items.map(card).join("")}</div>` : `<div class="state"><h2>No titles match</h2><p>Try another language or genre.</p></div>`}
    ${pager(d.page, d.pages, p => "/browse?" + new URLSearchParams({ ...Object.fromEntries(qs), page: p }))}`;
  $("#flt").addEventListener("change", () => {
    const n = new URLSearchParams(new FormData($("#flt"))); for (const [k, v] of [...n]) if (!v) n.delete(k);
    go("/browse" + (n.toString() ? "?" + n : ""));
  });
}

/* ---------- title page: a message with inline buttons, like the Telegram bot ---------- */
async function titlePage([id], _q, live) {
  const t = await api("/api/public/title/" + id);
  if (!live()) return;
  document.title = `${t.title} · AJ CINE AI`;
  saveHistory(t);
  CFG.streaming = t.streaming;
  const src = safeUrl(t.poster);
  main.innerHTML = `<div class="detail"><div class="art">${src ? `<img src="${esc(src)}" alt="" referrerpolicy="no-referrer" data-poster>` : `<div class="noposter">${esc(t.title)}</div>`}</div>
  <div><h1>${esc(t.title)}</h1>
  <div class="meta">${t.rating ? `<span class="rate">★ ${esc(t.rating)}</span>` : ""}${t.year ? `<span>${esc(t.year)}</span>` : ""}<span>${t.kind === "series" ? "Series" : "Movie"}</span>${t.runtime ? `<span>${esc(t.runtime)} min</span>` : ""}${(t.genres || []).map(g => `<span class="chip">${esc(g)}</span>`).join("")}${t.verified ? "" : `<span class="chip">Not matched to IMDb</span>`}</div>
  ${t.overview ? `<p class="overview">${esc(t.overview)}</p>` : ""}
  <div class="detail-actions"><button class="key back" id="detail-save" type="button"><i class="bi ${isSaved(t.id) ? "bi-bookmark-check-fill" : "bi-bookmark-plus"}"></i> ${isSaved(t.id) ? "In My List" : "Add to My List"}</button><a class="key back" href="/history" data-link><i class="bi bi-clock-history"></i> Watch history</a></div>
  <div class="bubble" id="bubble" aria-live="polite"></div></div></div>`;
  const saveBtn = $("#detail-save");
  if (saveBtn) saveBtn.addEventListener("click", () => { const saved = toggleSaved(t); saveBtn.innerHTML = `<i class="bi ${saved ? "bi-bookmark-check-fill" : "bi-bookmark-plus"}"></i> ${saved ? "In My List" : "Add to My List"}`; });
  keyboard(t, $("#bubble"));
}

function keyboard(t, box) {
  const sel = { season: null, episode: null, pack: null, language: null, quality: null };
  const body = () => ({ title_id: t.id, language: sel.language, quality: sel.quality, season: sel.season, episode: sel.episode, pack: sel.pack });
  const pad = n => String(n).padStart(2, "0");
  const trail = () => [sel.pack === "series" ? "Complete series" : null, sel.season !== null ? (sel.pack === "season" ? `Season ${sel.season} pack` : `Season ${sel.season}`) : null,
    sel.episode !== null ? `Episode ${sel.episode}` : null, sel.language ? sel.langName : null, sel.quality ? (sel.quality === "xx" ? "Unknown quality" : sel.quality) : null].filter(Boolean).join(" › ");
  const say = (text) => `<p class="say"><b>${esc(text)}</b>${trail() ? `<span class="trail">${esc(trail())}</span>` : ""}</p>`;
  const set = html => { box.innerHTML = html; };
  const back = () => `<button class="key back" data-act="back">‹ Back</button>`;
  let stack = [];
  const push = fn => { stack.push(fn); fn(); };
  const pop = () => { stack.pop(); const fn = stack[stack.length - 1]; fn ? fn() : start(); };
  let handlers = {};
  const bind = map => { handlers = map; };
  box.addEventListener("click", e => {         // one listener; each screen swaps in its own handlers
    const b = e.target.closest("[data-act]"); if (!b || b.disabled) return;
    if (b.dataset.act === "back") return pop();
    const h = handlers[b.dataset.act]; if (h) h(b);
  });
  const wait = msg => { box.innerHTML = `${say(msg)}<div class="keys"><button class="key" disabled>Working…</button></div>`; };

  function start() {
    stack = []; Object.assign(sel, { season: null, episode: null, pack: null, language: null, quality: null });
    t.kind === "series" ? seasons() : languages();
  }

  function seasons() {
    const ks = t.seasons.map(s => `<button class="key" data-act="season" data-s="${s.season}">${s.season === 0 ? "Specials" : "Season " + s.season} <small>${s.episodes} ep</small></button>`).join("");
    const full = t.complete_pack ? `<button class="key" data-act="series">Complete series pack</button>` : "";
    if (!t.seasons.length && !t.complete_pack) { box.innerHTML = say("Nothing is available yet") + `<p class="note">Files for this series are being indexed.</p>`; return; }
    set(`${say("Choose a season")}<div class="keys two">${ks}</div>${full ? `<div class="keys">${full}</div>` : ""}`);
    bind({
      season: b => { sel.season = +b.dataset.s; push(episodes); },
      series: () => { sel.pack = "series"; push(languages); },
    });
  }

  async function episodes() {
    wait("Loading episodes…");
    let d; try { d = await api(`/api/public/title/${t.id}/episodes?season=${sel.season}`); } catch (e) { return fail2(e); }
    const ks = d.episodes.map(e => `<button class="key" data-act="ep" data-e="${e.episode}" title="${esc(e.title || "")}">${pad(e.episode)}</button>`).join("");
    set(`${say("Choose an episode")}<div class="keys many">${ks}</div>${d.has_pack ? `<div class="keys"><button class="key" data-act="pack">Whole season (pack)</button></div>` : ""}<div class="keys">${back()}</div>`);
    bind({
      ep: b => { sel.episode = +b.dataset.e; push(languages); },
      pack: () => { sel.pack = "season"; push(languages); },
    });
  }

  async function languages() {
    sel.language = null; sel.quality = null;
    wait("Loading languages…");
    let d; try { d = await api("/api/public/options", { ...body(), language: null, quality: null }); } catch (e) { return fail2(e); }
    if (!d.languages.length) { set(`${say("Not available")}<p class="note bad">No files for this choice right now.</p><div class="keys">${back()}</div>`); return bind({}); }
    const ks = d.languages.map(l => `<button class="key" data-act="lang" data-c="${esc(l.code)}" data-n="${esc(l.name)}">${esc(l.name)} <small>${l.files}</small></button>`).join("");
    set(`${say("Choose a language")}<div class="keys two">${ks}</div>${t.kind === "series" ? `<div class="keys">${back()}</div>` : ""}`);
    bind({ lang: b => { sel.language = b.dataset.c; sel.langName = b.dataset.n; push(() => qualities(d.can_stream)); } });
  }

  async function qualities(can) {
    sel.quality = null;
    wait("Loading qualities…");
    let d; try { d = await api("/api/public/options", { ...body(), quality: null }); } catch (e) { return fail2(e); }
    const ks = d.qualities.map(q => `<button class="key" data-act="q" data-q="${esc(q.raw)}">${esc(q.quality)} <small>${esc(q.size_text)}</small></button>`).join("");
    set(`${say("Choose a quality")}<div class="keys two">${ks}</div><div class="keys">${back()}</div>`);
    bind({ q: b => { sel.quality = b.dataset.q; push(() => actions(d.can_stream)); } });
  }

  function actions(can) {
    const w = can && !sel.pack;
    set(`${say("Ready")}<p class="ready">Pick how you want it.</p><div class="keys">${w ? `<button class="key go" data-act="watch">▶ Watch online</button>` : ""}<button class="key ${w ? "" : "go"}" data-act="tg">Get in Telegram</button>${back()}</div>${sel.pack ? `<p class="note">Packs are delivered through Telegram.</p>` : ""}<p class="note" id="msg"></p>`);
    const run = async b => {
      const msg = $("#msg", box); msg.className = "note"; b.disabled = true;
      try {
        if (b.dataset.act === "watch") { msg.textContent = "Opening the player…"; const r = await api("/api/public/stream-link", body()); go(r.watch); return; }
        msg.textContent = "Creating your link…";
        const r = await api("/api/public/download-link", body());
        msg.innerHTML = `Telegram will open. Press <b>Start</b> to receive ${r.files} file${r.files === 1 ? "" : "s"} (${esc(r.size_text)}). The link works for the first Telegram account that uses it and expires in ${r.expires_minutes} minutes. <a class="chip" href="${esc(r.url)}" target="_blank" rel="noopener noreferrer">Open Telegram</a>`;
        window.open(r.url, "_blank", "noopener,noreferrer");
      } catch (err) { msg.className = "note bad"; msg.textContent = err.message; }
      b.disabled = false;
    };
    bind({ watch: run, tg: run });
  }
  const fail2 = e => { box.innerHTML = `${say("Something went wrong")}<p class="note bad">${esc(e.message)}</p><div class="keys"><button class="key go" data-act="again">Start again</button></div>`; bind({ again: start }); };
  start();
}

/* ---------- player ---------- */
const HINTS = {
  maybe: "This file may not play in every browser: MKV files and some video or audio formats depend on your browser. If the picture or sound fails, use Chrome or Edge, or get the file in Telegram.",
  poor: "This format usually does not play in a browser. Get the file in Telegram instead.",
};
async function watchPage([token], _q, live) {
  const w = await api("/api/public/watch/" + token);
  if (!live()) return;
  const name = w.title ? `${w.title}${w.year ? ` (${w.year})` : ""}` : "Video";
  document.title = `${name} · AJ CINE AI PLAYER`;
  const ep = w.episode ? `S${String(w.episode.season).padStart(2, "0")}E${String(w.episode.episode).padStart(2, "0")}${w.episode.title ? " · " + w.episode.title : ""}` : "";
  const poster = safeUrl(w.backdrop) || safeUrl(w.poster);
  if (w.title_id) saveHistory({ id: w.title_id, title: name, year: w.year, kind: w.kind || "movie", poster });
  main.innerHTML = `<div class="watch">
    <div class="player-shell" id="player-shell" tabindex="0" aria-label="AJ CINE AI video player">
      <video class="player-video" id="vid" playsinline preload="metadata" ${poster ? `poster="${esc(poster)}"` : ""} src="${esc(w.stream)}"></video>
      <div class="player-topline"><span class="player-brand">AJ CINE <span>AI</span> PLAYER</span><span class="muted small">${esc(w.quality || "")}</span></div>
      <div class="player-overlay"><button class="big-play" id="big-play" type="button" aria-label="Play"><i class="bi bi-play-fill"></i></button></div>
      <div class="player-controls" id="player-controls">
        <div class="progress-row"><span class="time-label" id="current-time">0:00</span><input class="seekbar" id="seekbar" type="range" min="0" max="1000" value="0" step="1" aria-label="Seek"><span class="time-label" id="duration">0:00</span></div>
        <div class="control-row">
          <button class="player-control" id="play-toggle" data-action="play" type="button" aria-label="Play"><i class="bi bi-play-fill"></i></button>
          <button class="player-control" id="back-ten" type="button" aria-label="Back 10 seconds"><i class="bi bi-arrow-counterclockwise"></i></button>
          <button class="player-control" id="forward-ten" type="button" aria-label="Forward 10 seconds"><i class="bi bi-arrow-clockwise"></i></button>
          <div class="volume-wrap"><button class="player-control" id="mute-toggle" type="button" aria-label="Mute"><i class="bi bi-volume-up"></i></button><input class="volume-slider" id="volume" type="range" min="0" max="1" step="0.05" value="1" aria-label="Volume"></div>
          <span class="player-spacer"></span>
          <select class="speed-select" id="speed" aria-label="Playback speed"><option value="0.5">0.5×</option><option value="0.75">0.75×</option><option value="1" selected>1×</option><option value="1.25">1.25×</option><option value="1.5">1.5×</option><option value="1.75">1.75×</option><option value="2">2×</option></select>
          <button class="player-control" id="pip" data-action="pip" type="button" aria-label="Picture in picture"><i class="bi bi-pip"></i></button>
          <button class="player-control" id="fullscreen" type="button" aria-label="Fullscreen"><i class="bi bi-fullscreen"></i></button>
        </div>
      </div>
      <div class="player-error hidden" id="player-error" role="alert"><div><strong>Playback unavailable</strong><span id="player-error-text">This file could not be played in this browser.</span></div></div>
    </div>
    <h1>${esc(name)}</h1>
    <div class="meta watch-meta">${ep ? `<span>${esc(ep)}</span>` : ""}${w.quality ? `<span class="chip">${esc(w.quality)}</span>` : ""}${w.language ? `<span class="chip">${esc(w.language)}</span>` : ""}<span>${esc(w.size_text)}</span>${w.container ? `<span class="chip">${esc(w.container.toUpperCase())}</span>` : ""}</div>
    ${HINTS[w.hint] ? `<div class="banner">${esc(HINTS[w.hint])}</div>` : ""}
    <div class="acts">${w.title_id ? `<a class="key back" href="/title/${esc(w.title_id)}" data-link><i class="bi bi-arrow-left"></i> Other versions</a>` : ""}<button class="key go" id="tg"><i class="bi bi-telegram"></i> Get in Telegram</button></div><p class="note" id="msg"></p>
    <p class="player-shortcuts">Keyboard: Space play/pause · ←/→ seek 5s · ↑/↓ volume · M mute · F fullscreen</p>
  </div>`;
  const v = $("#vid"), shell = $("#player-shell"), play = $("#play-toggle"), big = $("#big-play"), seek = $("#seekbar"), current = $("#current-time"), duration = $("#duration"), mute = $("#mute-toggle"), volume = $("#volume"), controls = $("#player-controls");
  let hideTimer = null;
  const fmt = n => { if (!Number.isFinite(n) || n < 0) return "0:00"; const h = Math.floor(n / 3600), m = Math.floor(n % 3600 / 60), sec = Math.floor(n % 60).toString().padStart(2, "0"); return h ? `${h}:${String(m).padStart(2,"0")}:${sec}` : `${m}:${sec}`; };
  const updatePlay = () => { const icon = v.paused ? "bi-play-fill" : "bi-pause-fill"; play.innerHTML = `<i class="bi ${icon}"></i>`; play.setAttribute("aria-label", v.paused ? "Play" : "Pause"); big.innerHTML = `<i class="bi ${icon}"></i>`; big.setAttribute("aria-label", v.paused ? "Play" : "Pause"); shell.classList.toggle("is-paused", v.paused); };
  const updateVolume = () => { volume.value = v.muted ? 0 : v.volume; mute.innerHTML = `<i class="bi ${v.muted || v.volume === 0 ? "bi-volume-mute" : v.volume < .5 ? "bi-volume-down" : "bi-volume-up"}"></i>`; };
  const updateTime = () => { current.textContent = fmt(v.currentTime); duration.textContent = fmt(v.duration); const p = Number.isFinite(v.duration) && v.duration ? v.currentTime / v.duration * 100 : 0; seek.value = Math.round(p * 10); seek.style.setProperty("--progress", `${p}%`); };
  const togglePlay = () => { if (v.paused) v.play().catch(() => showPlayerError("Your browser could not start this media format.")); else v.pause(); };
  const showControls = () => { shell.classList.remove("controls-hidden"); clearTimeout(hideTimer); if (!v.paused) hideTimer = setTimeout(() => shell.classList.add("controls-hidden"), 3200); };
  const showPlayerError = message => { $("#player-error-text").textContent = message; $("#player-error").classList.remove("hidden"); };
  play.addEventListener("click", togglePlay); big.addEventListener("click", togglePlay); v.addEventListener("click", togglePlay);
  v.addEventListener("play", () => { updatePlay(); showControls(); }); v.addEventListener("pause", () => { updatePlay(); showControls(); });
  v.addEventListener("timeupdate", updateTime); v.addEventListener("durationchange", updateTime); v.addEventListener("loadedmetadata", updateTime);
  v.addEventListener("volumechange", updateVolume); v.addEventListener("error", () => showPlayerError(v.error && v.error.code === 4 ? "Your browser does not support this file's video or audio format. Use Telegram to receive the original file." : "Playback stopped. Check your connection or open the file in Telegram."));
  seek.addEventListener("input", () => { if (Number.isFinite(v.duration)) { v.currentTime = Number(seek.value) / 1000 * v.duration; updateTime(); } });
  $("#back-ten").addEventListener("click", () => { v.currentTime = Math.max(0, v.currentTime - 10); }); $("#forward-ten").addEventListener("click", () => { v.currentTime = Math.min(v.duration || Infinity, v.currentTime + 10); });
  volume.addEventListener("input", () => { v.volume = Number(volume.value); v.muted = v.volume === 0; }); mute.addEventListener("click", () => { v.muted = !v.muted; updateVolume(); });
  $("#speed").addEventListener("change", e => { v.playbackRate = Number(e.target.value); });
  $("#fullscreen").addEventListener("click", async () => { try { if (document.fullscreenElement) await document.exitFullscreen(); else await shell.requestFullscreen(); } catch {} });
  $("#pip").addEventListener("click", async () => { try { if (document.pictureInPictureElement) await document.exitPictureInPicture(); else if (document.pictureInPictureEnabled && v.requestPictureInPicture) await v.requestPictureInPicture(); } catch {} });
  shell.addEventListener("mousemove", showControls); shell.addEventListener("touchstart", showControls, { passive: true });
  shell.addEventListener("keydown", e => { if (["INPUT", "SELECT", "BUTTON"].includes(e.target.tagName)) return; if (e.code === "Space") { e.preventDefault(); togglePlay(); } else if (e.key === "ArrowLeft") v.currentTime = Math.max(0, v.currentTime - 5); else if (e.key === "ArrowRight") v.currentTime = Math.min(v.duration || Infinity, v.currentTime + 5); else if (e.key === "ArrowUp") { e.preventDefault(); v.volume = Math.min(1, v.volume + .05); v.muted = false; } else if (e.key === "ArrowDown") { e.preventDefault(); v.volume = Math.max(0, v.volume - .05); } else if (e.key.toLowerCase() === "m") v.muted = !v.muted; else if (e.key.toLowerCase() === "f") $("#fullscreen").click(); showControls(); });
  document.addEventListener("fullscreenchange", () => { $("#fullscreen").innerHTML = `<i class="bi ${document.fullscreenElement ? "bi-fullscreen-exit" : "bi-fullscreen"}"></i>`; });
  if (!(document.pictureInPictureEnabled && v.requestPictureInPicture)) $("#pip").classList.add("hidden");
  updatePlay(); updateVolume(); updateTime();
  $("#tg").addEventListener("click", async ev => { const b = ev.currentTarget, msg = $("#msg"); b.disabled = true; msg.className = "note"; try { const r = await api("/api/public/download-link", w.select); msg.innerHTML = `Telegram will open. Press <b>Start</b> to receive the file. <a class="chip" href="${esc(r.url)}" target="_blank" rel="noopener noreferrer">Open Telegram</a>`; window.open(r.url, "_blank", "noopener,noreferrer"); } catch (err) { msg.className = "note bad"; msg.textContent = err.message; } b.disabled = false; });
}

function localCards(items) { return items.length ? `<div class="grid">${items.map(card).join("")}</div>` : `<div class="library-empty"><i class="bi bi-collection"></i><h2>Your library is waiting</h2><p>Titles you save or watch will appear here on this browser.</p><a class="key go" href="/browse" data-link>Explore the library</a></div>`; }
function watchlistPage() { document.title = "My List · AJ CINE AI"; const items = readStore(STORE.list); main.innerHTML = `<div class="page-heading"><p class="eyebrow">YOUR LIBRARY</p><h1>My List</h1><p>Titles you have bookmarked on this browser.</p></div>${localCards(items)}`; }
function historyPage() { document.title = "Recently Watched · AJ CINE AI"; const items = readStore(STORE.history); main.innerHTML = `<div class="page-heading"><p class="eyebrow">KEEP WATCHING</p><h1>Recently Watched</h1><p>Your recently opened titles, saved locally in this browser.</p></div>${items.length ? `<div class="row-h"><span class="section-kicker">${items.length} titles</span><button class="key back" id="clear-history" type="button">Clear history</button></div>${localCards(items)}` : localCards([])}`; const b = $("#clear-history"); if (b) b.addEventListener("click", () => { writeStore(STORE.history, []); historyPage(); }); }
function settingsPage() { document.title = "Settings · AJ CINE AI"; const theme = localStorage.getItem(STORE.theme) || "dark"; main.innerHTML = `<div class="page-heading"><p class="eyebrow">PREFERENCES</p><h1>Settings</h1><p>Manage the experience on this browser.</p></div><div class="settings-layout"><section class="settings-group"><h2>Appearance</h2><p>Choose how AJ CINE AI looks on this device.</p><div class="setting-row"><div><strong>Light appearance</strong><small>Switch from the default dark cinematic theme.</small></div><input class="switch" id="light-theme" type="checkbox" ${theme === "light" ? "checked" : ""} aria-label="Enable light appearance"></div></section><section class="settings-group"><h2>Local data</h2><p>Your watchlist, recent history and profiles are stored in this browser only. They are not synced to an account.</p><div class="setting-row"><div><strong>Clear watchlist</strong><small>Remove all bookmarked titles from this browser.</small></div><button class="key back" id="clear-list">Clear list</button></div><div class="setting-row"><div><strong>Clear viewing history</strong><small>Remove all recently opened titles from this browser.</small></div><button class="key back" id="clear-history-settings">Clear history</button></div></section><section class="settings-group"><h2>Profile</h2><p>Current profile: <strong>${esc(activeProfile())}</strong></p><a class="key" href="/profiles" data-link>Manage profiles</a></section></div>`; $("#light-theme").addEventListener("change", e => { const v = e.target.checked ? "light" : "dark"; localStorage.setItem(STORE.theme, v); applyTheme(); }); $("#clear-list").addEventListener("click", () => { writeStore(STORE.list, []); toastLocal("Watchlist cleared"); }); $("#clear-history-settings").addEventListener("click", () => { writeStore(STORE.history, []); toastLocal("Viewing history cleared"); }); }
function profilesPage() { document.title = "Profiles · AJ CINE AI"; const names = readStore(STORE.profiles); if (!names.length) names.push("Main"); if (!names.includes(activeProfile())) names.push(activeProfile()); writeStore(STORE.profiles, [...new Set(names)]); main.innerHTML = `<div class="page-heading"><p class="eyebrow">WHO'S WATCHING?</p><h1>Profiles</h1><p>Profiles are local labels for this browser. They do not create separate server accounts.</p></div><div class="profile-grid">${names.map((n,i) => `<button class="profile-card" type="button" data-profile="${esc(n)}"><span class="profile-avatar">${esc((n[0] || "A").toUpperCase())}</span><strong>${esc(n)}</strong><small>${n === activeProfile() ? "Current profile" : "Select profile"}</small></button>`).join("")}</div><div class="detail-actions profile-actions"><button class="key" id="add-profile" type="button"><i class="bi bi-plus-lg"></i> Add profile</button>${names.length > 1 ? `<button class="key back" id="remove-profile" type="button">Remove current profile</button>` : ""}</div>`; main.querySelectorAll("[data-profile]").forEach(b => b.addEventListener("click", () => { localStorage.setItem(STORE.profile, b.dataset.profile); route(false); })); $("#add-profile").addEventListener("click", () => { const name = prompt("Profile name (up to 24 characters)"); if (!name || !name.trim()) return; const n = name.trim().slice(0,24); if (names.some(x => x.toLowerCase() === n.toLowerCase())) return toastLocal("That profile already exists"); names.push(n); writeStore(STORE.profiles, names); localStorage.setItem(STORE.profile,n); profilesPage(); }); const rm = $("#remove-profile"); if (rm) rm.addEventListener("click", () => { if (activeProfile() === "Main") return toastLocal("The Main profile cannot be removed"); const next = names.filter(x => x !== activeProfile()); writeStore(STORE.profiles,next); localStorage.setItem(STORE.profile,"Main"); profilesPage(); }); }
function toastLocal(message) { let t = $("#local-toast"); if (!t) { t = document.createElement("div"); t.id = "local-toast"; t.className = "local-toast"; document.body.append(t); } t.textContent = message; t.classList.add("show"); setTimeout(() => t.classList.remove("show"), 2200); }
function applyTheme() { document.body.dataset.theme = localStorage.getItem(STORE.theme) || "dark"; }

(async function boot() {
  applyTheme();
  try { CFG = await api("/api/public/config"); } catch (e) { /* the page route shows the error */ }
  route(false);
})();
