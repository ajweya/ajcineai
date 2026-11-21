import { $, $$, esc, card, watchImages, stateView, icon, toast, epCode, clock } from "./ui.js";
import { watchlist, progress, prefs, searches, clearAll, onChange } from "./store.js";
import { playSelection } from "./play.js";
import { staggerIn } from "./motion.js";

const TABS = [["continue", "Continue watching"], ["watchlist", "Watchlist"], ["history", "History"]];

/** Library lives on this device only (there are no accounts); the page says so. */
export async function libraryPage(ctx) {
  const root = ctx.root, tab = TABS.some(t => t[0] === ctx.query.get("tab")) ? ctx.query.get("tab") : "continue";
  ctx.title("My library");
  root.innerHTML = `<div class="page"><div class="container-fluid aj-gutter"><h1 class="page-title">My library</h1><p class="page-sub">Your watchlist and progress are saved in this browser on this device.</p>
    <div class="tab-row" role="tablist">${TABS.map(([k, l]) => `<a href="/library?tab=${k}" data-link role="tab" aria-selected="${k === tab}" class="${k === tab ? "on" : ""}">${l}</a>`).join("")}</div><div id="lib"></div></div></div>`;
  const host = $("#lib");
  const empty = (i, t, b, cta = '<a class="aj-btn primary" href="/" data-link>Browse titles</a>') => `<div class="state">${icon(i)}<h2>${t}</h2><p>${b}</p><div class="actions">${cta}</div></div>`;
  const paint = () => {
    if (tab === "watchlist") {
      const l = watchlist.list();
      host.innerHTML = l.length ? `<div class="grid">${l.map(i => card(i, { remove: true })).join("")}</div><div class="actions mt-4"><button class="aj-btn ghost sm" type="button" data-clear="watchlist">Clear watchlist</button></div>` : empty("bookmark", "Your watchlist is empty", "Use Add to watchlist on any title to keep it here.");
    } else if (tab === "continue") {
      const l = progress.continueList();
      host.innerHTML = l.length ? `<div class="grid wide">${l.map(h => card(h.meta, { land: true, progress: h.pos / h.dur, sub: h.meta.season != null ? epCode(h.meta.season, h.meta.episode) : "", href: `/title/${esc(h.meta.id)}` }).replace('data-link', `data-link data-continue="${esc(h.key)}"`).replace('</a>', `<button class="rm" type="button" data-forget="${esc(h.key)}" aria-label="Remove ${esc(h.meta.title)} from continue watching">${icon("x-lg")}</button></a>`)).join("")}</div>` : empty("play-circle", "Nothing in progress", "Titles you start watching show up here so you can pick up where you stopped.");
    } else {
      const l = progress.history();
      host.innerHTML = l.length ? `<div class="ep-list">${l.map(h => `<div class="ep"><div class="ep-n">${icon(h.done ? "check2-circle" : "clock-history")}</div><div><div class="ep-t"><a href="/title/${esc(h.meta.id)}" data-link>${esc(h.meta.title)}</a></div><div class="ep-s">${esc([h.meta.season != null ? epCode(h.meta.season, h.meta.episode) : "", h.done ? "Watched" : `${clock(h.pos)} of ${clock(h.dur)}`, new Date(h.t).toLocaleDateString()].filter(Boolean).join(" · "))}</div></div><div class="ep-acts"><button class="aj-btn sm" type="button" data-forget="${esc(h.key)}">Remove</button></div></div>`).join("")}</div><div class="actions mt-4"><button class="aj-btn ghost sm" type="button" data-clear="history">Clear history</button></div>` : empty("clock-history", "No history yet", "What you watch will be listed here.");
    }
    watchImages(host); staggerIn($$(".pcard,.ep", host), ctx.sc);
  };
  host.addEventListener("click", e => {
    const f = e.target.closest("[data-forget]"), c = e.target.closest("[data-clear]"), rm = e.target.closest("[data-remove]"), co = e.target.closest("[data-continue]");
    if (f) { e.preventDefault(); e.stopPropagation(); progress.forget(f.dataset.forget); }
    else if (rm) { e.preventDefault(); e.stopPropagation(); watchlist.remove(rm.dataset.remove); }
    else if (c) { c.dataset.clear === "watchlist" ? watchlist.clear() : progress.clear(); toast("Cleared"); }
    else if (co && !e.metaKey && !e.ctrlKey) { e.preventDefault(); const r = progress.get(co.dataset.continue); r && r.meta.select ? playSelection(r.meta.select, ctx.go) : ctx.go(co.getAttribute("href")); }
  }, true);
  ctx.onLeave(onChange(paint)); paint();
}

export async function settingsPage(ctx) {
  const root = ctx.root; ctx.title("Preferences");
  const sw = (k, t, d) => `<div class="pref"><div><div class="pref-t">${t}</div><div class="pref-d">${d}</div></div><div class="form-check form-switch m-0"><input class="form-check-input" type="checkbox" role="switch" id="p-${k}" data-pref="${k}" ${prefs.get(k) ? "checked" : ""} aria-label="${t}"></div></div>`;
  const sel = (k, t, d, opts) => `<div class="pref"><div><div class="pref-t">${t}</div><div class="pref-d">${d}</div></div><select class="form-select w-auto" data-pref="${k}" aria-label="${t}">${opts.map(([v, l]) => `<option value="${v}"${String(prefs.get(k)) === String(v) ? " selected" : ""}>${l}</option>`).join("")}</select></div>`;
  root.innerHTML = `<div class="page"><div class="container-fluid aj-gutter"><div class="settings"><h1 class="page-title">Preferences</h1><p class="page-sub">Saved in this browser on this device. There is no account to sign in to.</p>
    <h2 class="h5 mt-5">Playback</h2>
    ${sw("autonext", "Play the next episode automatically", "Starts the next episode after a short countdown when one ends.")}
    ${sel("speed", "Default speed", "Used when a video starts.", [[0.5, "0.5×"], [0.75, "0.75×"], [1, "Normal"], [1.25, "1.25×"], [1.5, "1.5×"], [2, "2×"]])}
    ${sw("theater", "Theater mode", "Use the widest player layout on large screens.")}
    <h2 class="h5 mt-5">Subtitles</h2>
    ${sel("subSize", "Size", "Applies when the video has subtitle tracks.", [[80, "Small"], [100, "Medium"], [130, "Large"], [170, "Extra large"]])}
    ${sel("subColor", "Text colour", "", [["#ffffff", "White"], ["#ffe066", "Yellow"], ["#9ee6ff", "Cyan"]])}
    ${sel("subBg", "Background", "", [["dark", "Dark box"], ["none", "None"], ["solid", "Solid black"]])}
    <h2 class="h5 mt-5">Display</h2>
    ${sel("motion", "Animations", "Automatic follows your device's reduced-motion setting.", [["auto", "Automatic"], ["on", "Always on"], ["off", "Off"]])}
    <h2 class="h5 mt-5">Data on this device</h2>
    <div class="pref"><div><div class="pref-t">Clear everything</div><div class="pref-d">Removes your watchlist, history, progress, searches and preferences from this browser.</div></div><button class="aj-btn ghost sm" type="button" id="wipe">Clear data</button></div></div></div></div>`;
  root.addEventListener("change", e => {
    const k = e.target.dataset.pref; if (!k) return;
    const v = e.target.type === "checkbox" ? e.target.checked : (isNaN(+e.target.value) || e.target.value.startsWith("#") ? e.target.value : +e.target.value);
    prefs.set(k, v); toast("Saved");
  });
  $("#wipe").addEventListener("click", () => { if (confirm("Remove all data saved by AJ CINE AI in this browser?")) { clearAll(); toast("Cleared"); ctx.go("/settings", true); } });
}
