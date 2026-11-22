import { $, esc, safeUrl, icon, toast, epCode, errorView } from "./ui.js";
import { Site } from "./api.js";
import { prefs, progress, watchlist } from "./store.js";
import { playSelection } from "./play.js";

const HINTS = {
  maybe: "This file may not play in every browser. If it stalls, get it in Telegram.",
  poor: "This file format is unlikely to play in a browser. Get it in Telegram for the best result.",
};
let carry = null;          

export async function watchPage(ctx) {
  const token = ctx.params[0], root = ctx.root;
  const w = await Site.watch(token); if (!ctx.live()) return;
  const name = w.title ? `${w.title}${w.year ? ` (${w.year})` : ""}` : "Video";
  ctx.title(name);
  const ep = w.episode, epText = ep ? `${epCode(ep.season, ep.episode)}${ep.title ? " · " + ep.title : ""}` : "";
  const key = progress.key(w.title_id || "file", ep && ep.season, ep && ep.episode);
  const art = safeUrl(w.backdrop) || safeUrl(w.poster);
  root.innerHTML = `<div class="w-page" id="wpage"><div class="w-wrap"><div id="player"></div></div>
    <div class="container-fluid aj-gutter w-info"><div><h1>${esc(name)}</h1>
      <div class="meta">${epText ? `<span>${esc(epText)}</span>` : ""}${w.quality ? `<span class="chip">${esc(w.quality)}</span>` : ""}${w.language ? `<span class="chip">${esc(w.language)}</span>` : ""}${w.size_text ? `<span>${esc(w.size_text)}</span>` : ""}${w.container ? `<span class="chip">${esc(w.container.toUpperCase())}</span>` : ""}</div>
      ${HINTS[w.hint] ? `<div class="banner mt-3">${esc(HINTS[w.hint])}</div>` : ""}<div class="banner bad mt-3 hidden" id="werr" role="alert"></div>
      <div class="actions mt-3">${w.title_id ? `<a class="aj-btn" href="/title/${esc(w.title_id)}" data-link>${icon("collection-play")}Other versions</a>` : ""}<button class="aj-btn" type="button" id="tg">${icon("telegram")}Get in Telegram</button>${w.title_id ? `<button class="aj-btn ghost" type="button" id="wl"></button>` : ""}</div><p class="note" id="wmsg" aria-live="polite"></p></div>
      <aside>${w.next ? `<div class="bubble"><p class="say"><b>Next episode</b><span class="trail">${esc(epCode(w.next.season, w.next.episode))}${w.next.title ? " · " + esc(w.next.title) : ""}</span></p><div class="keys"><button class="key go" type="button" id="nextbtn">${icon("skip-end-fill")}Play next</button></div></div>` : ""}</aside></div></div>`;

  const paintWl = () => { const b = $("#wl"); if (!b) return; const on = watchlist.has(w.title_id); b.classList.toggle("on", on); b.innerHTML = `${icon(on ? "bookmark-check-fill" : "bookmark-plus")}${on ? "In watchlist" : "Add to watchlist"}`; };
  const info = { id: w.title_id, title: w.title, year: w.year, kind: w.kind, poster: w.poster, backdrop: w.backdrop };
  paintWl();
  $("#wl") && $("#wl").addEventListener("click", () => { watchlist.toggle(info); paintWl(); });
  const switchTo = (sel, t, playing) => { carry = { key: null, t, playing, at: Date.now() }; playSelection(sel, ctx.go); };
  const tg = async () => {
    const m = $("#wmsg"); try { const r = await Site.downloadLink(w.select); m.innerHTML = `Telegram will open. Press <b>Start</b> to receive the file. <a class="chip" href="${esc(r.url)}" target="_blank" rel="noopener noreferrer">Open Telegram</a>`; window.open(r.url, "_blank", "noopener,noreferrer"); }
    catch (e) { m.className = "note bad"; m.textContent = e.message; }
  };
  $("#tg").addEventListener("click", tg);
  $("#nextbtn") && $("#nextbtn").addEventListener("click", () => switchTo(w.next.select, 0, true));

  const meta = { ...info, genres: [], season: ep ? ep.season : null, episode: ep ? ep.episode : null, select: w.select };
  if (w.title_id) Site.title(w.title_id).then(t => { meta.genres = (t.genres || []).slice(0, 4); meta.rating = t.rating; }).catch(() => { });
  const handoff = carry && Date.now() - carry.at < 10000 ? carry : null; carry = null;
  const { mountPlayer } = await import("./player/aj-player.js"); if (!ctx.live()) return;
  const host = $("#player"), page = $("#wpage");
  host.addEventListener("ajp:theater", e => page.classList.toggle("theater", !!e.detail));
  let player;
  try {
    player = await mountPlayer(host, {
      src: w.stream, poster: art, title: name, subtitle: epText || [w.quality, w.language].filter(Boolean).join(" · "), key, meta,
      versions: (w.qualities || []).map(q => ({ label: q.quality, sub: q.size_text, current: q.current, onSelect: t => switchTo(q.select, t, !player.video.paused) })),
      next: w.next ? { label: `${epCode(w.next.season, w.next.episode)}${w.next.title ? " · " + w.next.title : ""}`, onSelect: () => switchTo(w.next.select, 0, true) } : null,
      prev: w.prev ? { label: `${epCode(w.prev.season, w.prev.episode)}`, onSelect: () => switchTo(w.prev.select, 0, true) } : null,
      segments: w.segments || [], thumbnails: w.thumbnails || null, tracks: w.subtitles || [],
      refreshSource: async () => { const r = await Site.streamLink(w.select); const i = await Site.watch(r.watch.split("/").pop()); return i.stream; },
      onBack: () => (history.length > 1 ? history.back() : ctx.go(w.title_id ? `/title/${w.title_id}` : "/")),
      onFallback: tg, autoplay: !!(handoff && handoff.playing), startAt: handoff && handoff.t > 0 ? handoff.t : undefined,
      prefs: prefs.all(), onPref: (k, v) => prefs.set(k, v),
    });
  } catch (e) { host.innerHTML = ""; $("#werr").classList.remove("hidden"); $("#werr").textContent = "The player could not start: " + e.message; return; }
  page.classList.toggle("theater", !!prefs.get("theater"));
  ctx.onLeave(() => player.destroy());
}
