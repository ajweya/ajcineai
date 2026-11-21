import { $, $$, paintBars, esc, safeUrl, icon, card, watchImages, toast, epCode, minutes, errorView } from "./ui.js";
import { Site } from "./api.js";
import { watchlist, progress } from "./store.js";
import { shelf, shelfHtml } from "./carousel.js";
import { playTitle, playSelection } from "./play.js";
import { heroIntro, revealOnScroll, staggerIn } from "./motion.js";

const langName = l => l.name || l.code;

/** The Telegram-style key flow: language, then quality, then stream or download. Only versions that really exist are offered. */
function versionPicker(box, base, ctx, streaming) {
  const sel = { ...base }; let seq = 0;
  const bubble = (say, trail, keys, note = "") => `<div class="bubble"><p class="say">${say}${trail ? `<span class="trail">${esc(trail)}</span>` : ""}</p>${keys}${note ? `<p class="note">${note}</p>` : ""}</div>`;
  const trailOf = () => [sel.language_name, sel.quality_label].filter(Boolean).join(" · ");
  async function step() {
    const my = ++seq; box.innerHTML = bubble("Loading versions", "", `<div class="keys"><div class="skel btn-s"></div></div>`);
    let o; try { o = await Site.options({ title_id: sel.title_id, language: sel.language, season: sel.season, episode: sel.episode, pack: sel.pack }); } catch (e) { return box.innerHTML = `<div class="banner bad">${esc(e.message)}</div>`; }
    if (my !== seq || !ctx.live()) return;
    if (!sel.language) {
      if (!o.languages.length) return box.innerHTML = bubble("Nothing to choose from yet.", "", "");
      if (o.languages.length === 1) { sel.language = o.languages[0].code; sel.language_name = langName(o.languages[0]); return step(); }
      return box.innerHTML = bubble("<b>Choose a language</b>", "", `<div class="keys">${o.languages.map(l => `<button class="key" type="button" data-lang="${esc(l.code)}" data-name="${esc(langName(l))}">${esc(langName(l))}</button>`).join("")}</div>`);
    }
    if (!sel.quality) {
      if (!o.qualities.length) return box.innerHTML = bubble("No versions found for that language.", "", `<div class="keys"><button class="key back" type="button" data-back>Back</button></div>`);
      if (o.qualities.length === 1 && !sel.autoSkipped) { sel.autoSkipped = true; sel.quality = o.qualities[0].raw; sel.quality_label = o.qualities[0].quality; sel.size_text = o.qualities[0].size_text; sel.can_stream = o.can_stream; return step(); }
      return box.innerHTML = bubble("<b>Choose a quality</b>", trailOf(), `<div class="keys many">${o.qualities.map(q => `<button class="key" type="button" data-q="${esc(q.raw)}" data-label="${esc(q.quality)}" data-size="${esc(q.size_text)}">${esc(q.quality)}<small>${esc(q.size_text)}</small></button>`).join("")}</div><div class="keys"><button class="key back" type="button" data-back>Back</button></div>`, "");
    }
    sel.can_stream = o.can_stream !== undefined ? o.can_stream : sel.can_stream;
    box.innerHTML = bubble("<b>Ready</b>", `${trailOf()}${sel.size_text ? " · " + sel.size_text : ""}`,
      `<div class="keys${sel.can_stream && streaming ? " two" : ""}">${sel.can_stream && streaming ? `<button class="key go" type="button" data-act="stream">${icon("play-fill")}Play in browser</button>` : ""}<button class="key${sel.can_stream && streaming ? "" : " go"}" type="button" data-act="tg">${icon("telegram")}Get in Telegram</button></div><div class="keys"><button class="key back" type="button" data-back>Start over</button></div>`, '<span id="vmsg" aria-live="polite"></span>');
  }
  const cur = () => ({ title_id: sel.title_id, language: sel.language, quality: sel.quality, season: sel.season, episode: sel.episode });
  box.addEventListener("click", async e => {
    const b = e.target.closest("button"); if (!b) return;
    if (b.dataset.lang) { sel.language = b.dataset.lang; sel.language_name = b.dataset.name; sel.quality = null; sel.autoSkipped = false; step(); }
    else if (b.dataset.q) { sel.quality = b.dataset.q; sel.quality_label = b.dataset.label; sel.size_text = b.dataset.size; step(); }
    else if (b.hasAttribute("data-back")) { if (sel.quality) { sel.quality = null; sel.autoSkipped = true; } else { sel.language = null; } step(); }
    else if (b.dataset.act) {
      b.disabled = true; const msg = $("#vmsg", box);
      try {
        if (b.dataset.act === "stream") await playSelection(cur(), ctx.go);
        else { const r = await Site.downloadLink(cur()); msg.innerHTML = `Telegram will open. Press <b>Start</b> to receive the file. <a class="chip" href="${esc(r.url)}" target="_blank" rel="noopener noreferrer">Open Telegram</a>`; window.open(r.url, "_blank", "noopener,noreferrer"); }
      } catch (err) { if (msg) { msg.className = "bad"; msg.textContent = err.message; } }
      b.disabled = false;
    }
  });
  step();
  return { reset(patch) { Object.assign(sel, { language: null, quality: null, autoSkipped: false }, patch); step(); } };
}

export async function titlePage(ctx) {
  const root = ctx.root, id = ctx.params[0];
  const t = await Site.title(id); if (!ctx.live()) return;
  ctx.title(t.title + (t.year ? ` (${t.year})` : ""));
  const bg = safeUrl(t.backdrop) || safeUrl(t.poster), poster = safeUrl(t.poster), streaming = !!t.streaming;
  const last = () => progress.forTitle(t.id)[0];
  const meta = [t.year, t.runtime ? minutes(t.runtime) : "", t.kind === "series" ? `${(t.seasons || []).filter(s => s.season > 0).length || (t.seasons || []).length} season${(t.seasons || []).length === 1 ? "" : "s"}` : "Movie"].filter(Boolean);
  root.innerHTML = `<div class="d-hero"><div class="hero-bg">${bg ? `<div class="layer on"><img src="${esc(bg)}" alt="" decoding="async" referrerpolicy="no-referrer"></div>` : ""}</div>
    <div class="container-fluid aj-gutter"><div class="d-grid"><div class="d-poster" data-intro>${poster ? `<img src="${esc(poster)}" alt="Poster of ${esc(t.title)}" referrerpolicy="no-referrer" data-img data-alt="${esc(t.title)}">` : ""}${poster ? "" : `<div class="noposter">${esc(t.title)}</div>`}</div>
    <div><div class="meta" data-intro>${t.rating ? `<span class="rating">${icon("star-fill")}${esc(t.rating)}</span>` : ""}${meta.map(m => `<span class="sep">${esc(m)}</span>`).join("")}${t.verified ? "" : '<span class="sep">Unverified match</span>'}</div>
    <h1 class="d-title" data-intro>${esc(t.title)}</h1>${t.original_title && t.original_title !== t.title ? `<p class="d-orig" data-intro>${esc(t.original_title)}</p>` : ""}
    <div class="pillset" data-intro>${(t.genres || []).map(g => `<a class="chip" href="/browse?genre=${encodeURIComponent(g)}${t.kind ? "&kind=" + t.kind : ""}" data-link>${esc(g)}</a>`).join("")}</div>
    ${t.overview ? `<p class="d-overview" data-intro>${esc(t.overview)}</p>` : '<div class="spacer"></div>'}
    <div class="actions" data-intro><button class="aj-btn primary" type="button" id="play">${icon("play-fill")}<span id="playlabel">Play now</span></button>
    <button class="aj-btn" type="button" id="versions">${icon("collection-play")}Versions &amp; download</button>
    <button class="aj-btn ghost" type="button" id="list" aria-pressed="false"></button></div>
    <dl class="facts" data-intro>${t.languages?.length ? `<div><dt>Languages</dt><dd>${esc(t.languages.map(l => l.name).join(", "))}</dd></div>` : ""}${t.qualities?.length ? `<div><dt>Qualities</dt><dd>${esc(t.qualities.join(", "))}</dd></div>` : ""}${t.file_count ? `<div><dt>Files</dt><dd>${esc(t.file_count)}</dd></div>` : ""}</dl></div></div></div></div>
    <div class="container-fluid aj-gutter" id="body"></div>`;
  watchImages(root);
  const body = $("#body"), listBtn = $("#list"), playLabel = $("#playlabel");

  const paintList = () => { const on = watchlist.has(t.id); listBtn.classList.toggle("on", on); listBtn.setAttribute("aria-pressed", String(on)); listBtn.innerHTML = `${icon(on ? "bookmark-check-fill" : "bookmark-plus")}${on ? "In watchlist" : "Add to watchlist"}`; };
  const paintPlay = () => { const l = last(); playLabel.textContent = l && !l.done ? (l.meta.season != null ? `Continue ${epCode(l.meta.season, l.meta.episode)}` : "Continue watching") : "Play now"; };
  paintList(); paintPlay();
  listBtn.addEventListener("click", () => { const on = watchlist.toggle(t); paintList(); toast(on ? "Added to your watchlist" : "Removed from your watchlist"); });
  $("#play").addEventListener("click", e => { e.currentTarget.disabled = true; playTitle(t, ctx.go).finally(() => { e.currentTarget.disabled = false; }); });

  /* versions & download */
  body.insertAdjacentHTML("beforeend", `<section class="section" id="vsec" aria-labelledby="vh"><div class="section-head"><h2 id="vh">Versions and download</h2></div><div id="vbox"></div>
    ${t.complete_pack ? '<p class="note">A complete-series pack is available. Get it in Telegram from the Versions list under "Whole series".</p>' : ""}</section>`);
  const picker = versionPicker($("#vbox"), { title_id: t.id }, ctx, streaming);
  $("#versions").addEventListener("click", () => { $("#vsec").scrollIntoView({ behavior: "smooth", block: "start" }); });

  /* seasons and episodes */
  if (t.kind === "series" && (t.seasons || []).length) {
    const seasons = [...t.seasons].sort((a, b) => a.season - b.season), sname = n => n === 0 ? "Specials" : `Season ${n}`;
    const saved = +ctx.query.get("season"); let cur = seasons.find(s => s.season === saved)?.season ?? (last() && last().meta.season != null ? last().meta.season : (seasons.find(s => s.season > 0) || seasons[0]).season);
    body.insertAdjacentHTML("afterbegin", `<section class="section" aria-labelledby="eh"><div class="season-bar"><h2 id="eh">Episodes</h2><select class="form-select w-auto" id="season" aria-label="Season">${seasons.map(s => `<option value="${s.season}"${s.season === cur ? " selected" : ""}>${sname(s.season)}</option>`).join("")}</select><span class="muted small" id="epinfo"></span></div><div class="ep-list" id="eps" aria-live="polite"></div></section>`);
    const eps = $("#eps");
    async function loadEps(n) {
      eps.innerHTML = Array.from({ length: 4 }, () => `<div class="ep"><div></div><div><div class="skel line m"></div><div class="skel line s"></div></div></div>`).join("");
      try {
        const r = await Site.episodes(t.id, n); if (!ctx.live() || +$("#season").value !== n) return;
        $("#epinfo").textContent = `${r.episodes.length} episode${r.episodes.length === 1 ? "" : "s"}${r.has_pack ? " · season pack available" : ""}`;
        if (!r.episodes.length) return eps.innerHTML = `<div class="state"><p>No episodes of ${sname(n).toLowerCase()} are in the library yet.</p></div>`;
        eps.innerHTML = r.episodes.map(e => { const p = progress.get(progress.key(t.id, n, e.episode)); const frac = p && p.dur ? p.pos / p.dur : 0;
          return `<div class="ep"><div class="ep-n">${e.episode}</div><div><div class="ep-t">${esc(e.title || `Episode ${e.episode}`)}</div><div class="ep-s">${esc(epCode(n, e.episode))} · ${e.files} version${e.files === 1 ? "" : "s"}${p && p.done ? " · Watched" : ""}</div>${frac > .02 && !p.done ? `<div class="ep-bar" aria-label="Watched ${Math.round(frac * 100)}%"><i data-w="${Math.round(frac * 100)}"></i></div>` : ""}</div>
          <div class="ep-acts"><button class="aj-btn sm primary" type="button" data-ep="${e.episode}" data-s="${n}">${icon("play-fill")}Play</button><button class="aj-btn sm" type="button" data-vep="${e.episode}" data-s="${n}">Versions</button></div></div>`; }).join("");
        paintBars(eps); staggerIn($$(".ep", eps), ctx.sc);
      } catch (err) { eps.innerHTML = `<div class="banner bad">${esc(err.message)}</div>`; }
    }
    $("#season").addEventListener("change", e => { cur = +e.target.value; const u = new URL(location.href); u.searchParams.set("season", cur); history.replaceState(null, "", u.pathname + u.search); loadEps(cur); });
    eps.addEventListener("click", e => {
      const p = e.target.closest("[data-ep]"), v = e.target.closest("[data-vep]");
      if (p) { p.disabled = true; playTitle(t, ctx.go, { season: +p.dataset.s, episode: +p.dataset.ep }).finally(() => { p.disabled = false; }); }
      if (v) { picker.reset({ season: +v.dataset.s, episode: +v.dataset.vep }); $("#vh").textContent = `Versions: ${epCode(+v.dataset.s, +v.dataset.vep)}`; $("#vsec").scrollIntoView({ behavior: "smooth", block: "start" }); }
    });
    await loadEps(cur);
    if (t.complete_pack) { /* the pack is selectable only through Telegram; see note */ }
  }

  /* similar titles: same kind, first genre, excluding this one */
  const g = (t.genres || [])[0];
  if (g) {
    Site.browseCached({ kind: t.kind, genre: g, sort: "rating" }).then(r => {
      if (!ctx.live()) return; const items = r.items.filter(x => x.id !== t.id).slice(0, 18); if (!items.length) return;
      const wrap = document.createElement("div"); wrap.innerHTML = shelfHtml("similar", t.kind === "series" ? "Similar series" : "Similar movies", items.map(i => `<div class="swiper-slide">${card(i)}</div>`).join(""));
      const node = wrap.firstElementChild; body.appendChild(node); ctx.onLeave(shelf(node.querySelector(".shelf"))); watchImages(body); revealOnScroll(body, ctx.sc);
    }).catch(() => { });
  }
  heroIntro(root, ctx.sc); revealOnScroll(root, ctx.sc);
}
