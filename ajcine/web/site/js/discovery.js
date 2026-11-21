import { $, $$, esc, card, skeletons, gridSkeletons, watchImages, stateView, errorView, icon, toast, safeUrl } from "./ui.js";
import { Site, ANIME } from "./api.js";
import { progress, watchlist } from "./store.js";
import { shelf, shelfHtml } from "./carousel.js";
import { createHero } from "./hero.js";
import { staggerIn, revealOnScroll, heroIntro } from "./motion.js";
import { playSelection } from "./play.js";

const isAnime = it => (it.genres || []).includes(ANIME.genre) && (it.languages || []).some(l => l.code === ANIME.language);
const slidesOf = items => items.map(it => `<div class="swiper-slide">${card(it)}</div>`).join("");
const heroPicks = (...lists) => { const seen = new Set(), out = []; for (const l of lists) for (const it of l || []) if (!seen.has(it.id) && (it.backdrop || it.poster)) { seen.add(it.id); out.push(it); } return out.sort((a, b) => !!b.backdrop - !!a.backdrop).slice(0, 5); };
const keep = (r, d = []) => (r.status === "fulfilled" ? r.value : d);

/** Mount a shelf (cards + Swiper) in `host` and register its cleanup. */
function mountShelf(ctx, host, id, title, items, opts = {}) {
  if (!items.length) return null;
  const wrap = document.createElement("div");
  wrap.innerHTML = shelfHtml(id, title, opts.html || slidesOf(items), opts);
  const node = wrap.firstElementChild; host.appendChild(node);
  ctx.onLeave(shelf(node.querySelector(".shelf"), opts)); watchImages(node);
  return node;
}
/** Genre shelves that fetch their titles only when scrolled near. */
function lazyGenres(ctx, host, genres, base) {
  const io = new IntersectionObserver(es => es.forEach(async e => {
    if (!e.isIntersecting) return; io.unobserve(e.target); const g = e.target.dataset.genre;
    try {
      const r = await Site.browseCached({ ...base, genre: g, sort: "rating" }); if (!ctx.live()) return;
      const items = r.items; const n = mountShelf(ctx, e.target, "g-" + g, esc(g), items, { more: `<a class="more" href="/browse?${new URLSearchParams({ ...(base.kind ? { kind: base.kind } : {}), genre: g })}" data-link>See all</a>` });
      if (n) { e.target.replaceWith(...e.target.childNodes); }
    } catch { e.target.remove(); }
  }), { rootMargin: "600px 0px" });
  ctx.onLeave(() => io.disconnect());
  genres.forEach(g => { const d = document.createElement("div"); d.dataset.genre = g; d.style.minHeight = "1px"; host.appendChild(d); io.observe(d); });
}
async function adBanner(ctx, host) {
  try {
    const { ad } = await Site.ad(); if (!ad || !ctx.live()) return;
    const img = safeUrl(ad.image), href = safeUrl(new URL(ad.href, location.origin).href);
    const el = document.createElement("aside"); el.className = "ad"; el.setAttribute("aria-label", "Sponsored");
    el.innerHTML = `<a href="${esc(href)}" rel="sponsored noopener nofollow" target="_blank">${img ? `<img src="${esc(img)}" alt="" loading="lazy" referrerpolicy="no-referrer">` : ""}<div class="adtext"><span class="adtag">Sponsored</span><strong>${esc(ad.title)}</strong>${ad.text ? `<span>${esc(ad.text)}</span>` : ""}<span class="adbtn">${esc(ad.button)}</span></div></a>`;
    host.appendChild(el);
  } catch { /* no banner is fine */ }
}
const continueSlides = list => list.map(h => {
  const m = h.meta, ep = m.season != null ? `S${String(m.season).padStart(2, "0")}E${String(m.episode).padStart(2, "0")}` : "";
  return `<div class="swiper-slide">${card({ ...m, id: m.id }, { land: true, progress: h.pos / h.dur, sub: ep, href: "#", remove: true }).replace('href="#"', `href="/title/${esc(m.id)}" data-continue="${esc(h.key)}"`)}</div>`;
}).join("");
function wireContinue(ctx, host) {
  const h = e => {
    const rm = e.target.closest("[data-remove]");
    const a = e.target.closest("[data-continue]");
    if (rm && a) { e.preventDefault(); e.stopPropagation(); const key = a.dataset.continue; progress.forget(key); a.closest(".swiper-slide").remove(); if (!host.querySelector("[data-continue]")) host.remove(); return; }
    if (a && !e.metaKey && !e.ctrlKey) { e.preventDefault(); e.stopPropagation(); const rec = progress.get(a.dataset.continue); if (rec && rec.meta.select) playSelection(rec.meta.select, ctx.go); else ctx.go(a.getAttribute("href")); }
  };
  host.addEventListener("click", h, true);
}

/* ============================================================ home */
export async function home(ctx) {
  const root = ctx.root; ctx.title("");
  root.innerHTML = `<div class="page-home"><div class="skel hero-s"></div><div class="container-fluid aj-gutter"><div class="section"><div class="shelf"><div class="swiper"><div class="swiper-wrapper">${skeletons(8)}</div></div></div></div></div></div>`;
  const [homeR, topR, animeR, genreR] = await Promise.allSettled([
    Site.home(), Site.browseCached({ sort: "rating" }), Site.browseCached({ ...ANIME, sort: "new" }), Site.browseCached({ sort: "new" })]);
  if (!ctx.live()) return;
  if (homeR.status === "rejected") throw homeR.reason;
  const h = homeR.value, top = keep(topR, { items: [] }).items.filter(i => i.rating), anime = keep(animeR, { items: [] }).items;
  if (!h.recent.length && !h.movies.length && !h.series.length) {
    root.innerHTML = stateView("film", "The library is empty", "Titles appear here as soon as files are indexed.", '<a class="aj-btn primary" href="/search" data-link>Search</a>'); return;
  }
  root.innerHTML = `<div class="page-home"><div id="hero"></div><div class="container-fluid aj-gutter" id="shelves"></div></div>`;
  const shelves = $("#shelves", root);
  const picks = heroPicks(h.trending, h.popular, h.recent, h.movies, h.series);
  if (picks.length) ctx.onLeave(createHero($("#hero", root), picks, ctx)); else $("#hero", root).innerHTML = `<div class="nav-gap"></div>`;

  const cont = progress.continueList();
  if (cont.length) { const n = mountShelf(ctx, shelves, "continue", "Continue watching", cont, { wide: true, html: continueSlides(cont) }); if (n) wireContinue(ctx, n); }
  mountShelf(ctx, shelves, "trending", "Trending this week", h.trending || []);
  await adBanner(ctx, shelves);
  mountShelf(ctx, shelves, "popm", "Popular movies", (h.popular || []).filter(i => i.kind === "movie"));
  mountShelf(ctx, shelves, "recent", "Recently added", h.recent);
  mountShelf(ctx, shelves, "top", "Top rated", top.slice(0, 18));
  mountShelf(ctx, shelves, "movies", "Latest movies", h.movies, { more: '<a class="more" href="/movies" data-link>See all</a>' });
  mountShelf(ctx, shelves, "pops", "Popular TV series", (h.popular || []).filter(i => i.kind === "series").concat(h.series).filter((x, i, a) => a.findIndex(y => y.id === x.id) === i), { more: '<a class="more" href="/series" data-link>See all</a>' });
  mountShelf(ctx, shelves, "anime", "Anime", anime, { more: '<a class="more" href="/anime" data-link>See all</a>' });
  for (const r of h.language_rows || []) mountShelf(ctx, shelves, "l-" + r.code, `In ${esc(r.name)}`, r.items);

  /* recommendations come only from what this device has watched or saved: the genres you return to, minus what you have seen */
  const seenIds = new Set([...progress.history().map(x => x.meta.id), ...watchlist.list().map(x => x.id)]);
  const tally = {}; [...progress.history().map(x => x.meta), ...watchlist.list()].forEach(m => (m.genres || []).forEach(g => { tally[g] = (tally[g] || 0) + 1; }));
  const fav = Object.entries(tally).sort((a, b) => b[1] - a[1]).slice(0, 2).map(x => x[0]);
  if (fav.length) {
    const rs = await Promise.allSettled(fav.map(g => Site.browseCached({ genre: g, sort: "rating" })));
    const rec = rs.flatMap(r => keep(r, { items: [] }).items).filter((x, i, a) => !seenIds.has(x.id) && a.findIndex(y => y.id === x.id) === i).slice(0, 18);
    if (ctx.live()) mountShelf(ctx, shelves, "rec", "Recommended for you", rec);
  }
  const genres = (keep(genreR, { genres: [] }).genres || []).slice(0, 6);
  lazyGenres(ctx, shelves, genres, {});
  heroIntro(root, ctx.sc); revealOnScroll(root, ctx.sc);
}

/* ============================================================ movies / series / anime / browse */
const COPY = {
  movies: ["Movies", "Everything in the library that is a feature film."],
  series: ["Series", "Shows with their seasons and episodes."],
  anime: ["Anime", "Japanese animation in the library."],
  browse: ["Browse", "Everything in the library."],
};
export async function listing(ctx) {
  const root = ctx.root, kindPage = ctx.path.slice(1), q = ctx.query;
  const fixed = kindPage === "movies" ? { kind: "movie" } : kindPage === "series" ? { kind: "series" } : kindPage === "anime" ? { ...ANIME } : (q.get("kind") ? { kind: q.get("kind") } : {});
  const [title, sub] = COPY[kindPage] || COPY.browse; ctx.title(title);
  const f = { genre: q.get("genre") || "", year: q.get("year") || "", language: q.get("language") || "", sort: q.get("sort") || "new" };
  root.innerHTML = `<div class="page-home"><div id="hero"></div><div class="container-fluid aj-gutter" id="shelves"></div>
    <div class="container-fluid aj-gutter"><section class="section" aria-labelledby="all-h"><div class="section-head"><h2 id="all-h" class="page-title mb-0">${esc(title === "Browse" ? "All titles" : "All " + title.toLowerCase())}</h2><span class="muted small" id="count" aria-live="polite"></span></div>
    <div class="filters" id="filters" role="group" aria-label="Filters"></div><div class="grid" id="grid">${gridSkeletons(12)}</div><div class="load-more"><button class="aj-btn ghost hidden" id="more" type="button">Load more</button></div><div id="sentinel"></div></section></div></div>`;
  const shelves = $("#shelves"), grid = $("#grid"), more = $("#more");
  const base = { ...fixed };

  /* featured + shelves (only for the three main pages, not for filtered browsing) */
  if (kindPage !== "browse") {
    const [hr, rr, nr] = await Promise.allSettled([Site.home(), Site.browseCached({ ...base, sort: "rating" }), Site.browseCached({ ...base, sort: "new" })]);
    if (!ctx.live()) return;
    const h = keep(hr, { popular: [], trending: [], recent: [] });
    const kindOk = it => !fixed.kind || it.kind === fixed.kind;
    const animeOnly = fixed.genre ? isAnime : () => true;
    const fresh = keep(nr, { items: [] }).items, topI = keep(rr, { items: [] }).items;
    const picks = heroPicks((h.trending || []).filter(kindOk).filter(animeOnly), fresh, topI);
    if (picks.length) ctx.onLeave(createHero($("#hero"), picks, ctx)); else $("#hero").innerHTML = `<div class="nav-gap lg"></div><div class="container-fluid aj-gutter"><h1 class="page-title">${esc(title)}</h1><p class="page-sub">${esc(sub)}</p></div>`;
    mountShelf(ctx, shelves, "tr", kindPage === "anime" ? "Trending anime" : "Trending now", (h.trending || []).filter(kindOk).filter(animeOnly));
    mountShelf(ctx, shelves, "po", kindPage === "anime" ? "Popular anime" : `Popular ${title.toLowerCase()}`, (h.popular || []).filter(kindOk).filter(animeOnly));
    mountShelf(ctx, shelves, "ne", kindPage === "series" ? "Recently updated" : kindPage === "anime" ? "Recently added anime" : "Recently added", fresh.slice(0, 18));
    mountShelf(ctx, shelves, "tp", "Top rated", topI.filter(i => i.rating).slice(0, 18));
  } else $("#hero").innerHTML = `<div class="nav-gap lg"></div><div class="container-fluid aj-gutter"><h1 class="page-title">${esc(title)}</h1><p class="page-sub">${esc(sub)}</p></div>`;

  /* filter bar */
  let meta = null, page = 1, pages = 1, loading = false, gen = 0;
  const sync = () => { const p = new URLSearchParams(); if (!fixed.genre && f.genre) p.set("genre", f.genre); if (f.year) p.set("year", f.year); if (f.language && !fixed.language) p.set("language", f.language); if (f.sort !== "new") p.set("sort", f.sort); if (q.get("kind") && kindPage === "browse") p.set("kind", q.get("kind")); history.replaceState(null, "", location.pathname + (p.toString() ? "?" + p : "")); };
  async function loadPage(reset) {
    if (loading) return; loading = true; const my = ++gen; if (reset) { page = 1; grid.innerHTML = gridSkeletons(12); more.classList.add("hidden"); }
    try {
      const r = await Site.browse({ ...base, genre: fixed.genre || f.genre, year: f.year, language: fixed.language || f.language, sort: f.sort, page });
      if (!ctx.live() || my !== gen) return;
      if (!meta) { meta = r; buildFilters(r); }
      pages = r.pages; if (reset) grid.innerHTML = "";
      $("#count").textContent = `${r.total} ${r.total === 1 ? "title" : "titles"}`;
      if (!r.items.length && reset) {
        grid.innerHTML = ""; grid.insertAdjacentHTML("afterend", `<div class="state" id="none">${icon("film")}<h2>Nothing here yet</h2><p>${kindPage === "anime" ? "Anime appears when Japanese animation titles are indexed in the library." : "No titles match these filters."}</p>${f.genre || f.year || f.language ? '<div class="actions"><button class="aj-btn" id="reset" type="button">Clear filters</button></div>' : ""}</div>`);
      } else { $("#none")?.remove(); const t = document.createElement("template"); t.innerHTML = r.items.map(i => card(i)).join(""); const nodes = [...t.content.children]; grid.append(...nodes); watchImages(grid); staggerIn(nodes, ctx.sc); }
      more.classList.toggle("hidden", page >= pages);
    } catch (e) { if (ctx.live()) { grid.innerHTML = ""; grid.insertAdjacentHTML("afterend", `<div class="state" id="none"><h2>Could not load titles</h2><p>${esc(e.message)}</p><div class="actions"><button class="aj-btn primary" id="again" type="button">Try again</button></div></div>`); } }
    finally { loading = false; }
  }
  function buildFilters(r) {
    const sel = (id, label, opts, val) => `<select class="form-select" id="${id}" aria-label="${label}"><option value="">${label}</option>${opts.map(o => `<option value="${esc(o[0])}"${String(o[0]) === String(val) ? " selected" : ""}>${esc(o[1])}</option>`).join("")}</select>`;
    Site.home().then(h => {
      if (!ctx.live()) return;
      $("#filters").innerHTML = (fixed.genre ? "" : sel("f-genre", "All genres", r.genres.map(g => [g, g]), f.genre)) + sel("f-year", "Any year", r.years.map(y => [y, y]), f.year) +
        (fixed.language ? "" : sel("f-lang", "Any language", h.languages.map(l => [l.code, l.name]), f.language)) +
        `<select class="form-select" id="f-sort" aria-label="Sort by"><option value="new"${f.sort === "new" ? " selected" : ""}>Recently added</option><option value="rating"${f.sort === "rating" ? " selected" : ""}>Top rated</option><option value="title"${f.sort === "title" ? " selected" : ""}>Title A to Z</option></select>`;
    }).catch(() => { });
  }
  const onFilter = e => {
    const m = { "f-genre": "genre", "f-year": "year", "f-lang": "language", "f-sort": "sort" }[e.target.id]; if (!m) return;
    f[m] = e.target.value || (m === "sort" ? "new" : ""); sync(); $("#none")?.remove(); loadPage(true);
  };
  const onClick = e => { if (e.target.id === "reset") { f.genre = f.year = f.language = ""; sync(); $$("#filters select").forEach(s => { if (s.id !== "f-sort") s.value = ""; }); $("#none")?.remove(); loadPage(true); } else if (e.target.id === "again") { $("#none")?.remove(); loadPage(true); } };
  $("#filters").addEventListener("change", onFilter); root.addEventListener("click", onClick); more.addEventListener("click", () => { if (page < pages) { page += 1; loadPage(false); } });
  const io = new IntersectionObserver(es => { if (es[0].isIntersecting && !loading && meta && page < pages) { page += 1; loadPage(false); } }, { rootMargin: "700px" });
  io.observe($("#sentinel")); ctx.onLeave(() => io.disconnect());
  await loadPage(true);
  if (!ctx.live()) return;
  if (kindPage !== "browse" && !fixed.genre && meta) lazyGenres(ctx, shelves, meta.genres.slice(0, 5), base);
  heroIntro(root, ctx.sc); revealOnScroll(root, ctx.sc);
}

/* ============================================================ search */
export async function searchPage(ctx) {
  const root = ctx.root, q = (ctx.query.get("q") || "").trim(); let kind = ctx.query.get("kind") || "";
  ctx.title(q ? `Search: ${q}` : "Search");
  if (!q) { root.innerHTML = stateView("search", "Search the library", "Type a title, a year or a language in the search box above."); return; }
  root.innerHTML = `<div class="page"><div class="container-fluid aj-gutter"><h1 class="page-title">Results for "${esc(q)}"</h1><div class="pillset" id="kinds" role="group" aria-label="Type">
    ${[["", "All"], ["movie", "Movies"], ["series", "Series"]].map(([k, l]) => `<button class="pill${kind === k ? " on" : ""}" type="button" data-kind="${k}" aria-pressed="${kind === k}">${l}</button>`).join("")}</div>
    <p class="muted mt-3" id="interp" aria-live="polite"></p><div class="grid mt-3" id="grid">${gridSkeletons(12)}</div><div class="load-more"><button class="aj-btn ghost hidden" id="more" type="button">Load more</button></div><div id="after"></div></div></div>`;
  const grid = $("#grid"), more = $("#more"); let page = 1, pages = 1, gen = 0, ctl = null;
  async function load(reset) {
    if (ctl) ctl.abort(); ctl = new AbortController(); const my = ++gen; if (reset) { page = 1; grid.innerHTML = gridSkeletons(12); $("#after").innerHTML = ""; }
    try {
      const r = await Site.search(q, { kind, page }, ctl.signal); if (!ctx.live() || my !== gen) return;
      pages = r.pages; if (reset) grid.innerHTML = "";
      $("#interp").textContent = r.interpreted && r.interpreted.toLowerCase() !== q.toLowerCase() ? `Showing results for "${r.interpreted}"` : (r.total ? `${r.total} ${r.total === 1 ? "title" : "titles"}` : "");
      const t = document.createElement("template"); t.innerHTML = r.items.map(i => card(i)).join(""); const nodes = [...t.content.children]; grid.append(...nodes); watchImages(grid); staggerIn(nodes, ctx.sc);
      more.classList.toggle("hidden", page >= pages);
      if (reset && !r.items.length) { grid.innerHTML = ""; $("#after").innerHTML = requestBox(q, kind); }
    } catch (e) { if (e.name !== "AbortError" && ctx.live()) { grid.innerHTML = ""; $("#after").innerHTML = errorView(e.message); } }
  }
  const requestBox = (title, k) => `<div class="state">${icon("search")}<h2>No results for "${esc(title)}"</h2><p>Check the spelling, or ask for it to be added to the library.</p>
    <form class="ask" id="req"><div class="d-grid gap-2"><input class="aj-input" name="title" value="${esc(title)}" minlength="2" maxlength="120" required aria-label="Title"><button class="aj-btn primary" type="submit">Request this title</button></div><p class="note" id="reqmsg" role="status"></p></form></div>`;
  root.addEventListener("click", e => {
    const b = e.target.closest("[data-kind]"); if (b) { kind = b.dataset.kind; $$("#kinds .pill").forEach(p => { const on = p === b; p.classList.toggle("on", on); p.setAttribute("aria-pressed", String(on)); }); const u = new URL(location.href); kind ? u.searchParams.set("kind", kind) : u.searchParams.delete("kind"); history.replaceState(null, "", u.pathname + u.search); load(true); }
  });
  root.addEventListener("submit", async e => {
    if (e.target.id !== "req") return; e.preventDefault(); const msg = $("#reqmsg"); const btn = e.target.querySelector("button"); btn.disabled = true;
    try { await Site.request({ title: new FormData(e.target).get("title").trim(), kind: kind || null }); msg.className = "note"; msg.textContent = "Request sent. It will show up here once it is added."; }
    catch (err) { msg.className = "note bad"; msg.textContent = err.message; btn.disabled = false; }
  });
  more.addEventListener("click", () => { if (page < pages) { page += 1; load(false); } });
  ctx.onLeave(() => ctl && ctl.abort());
  await load(true);
}
