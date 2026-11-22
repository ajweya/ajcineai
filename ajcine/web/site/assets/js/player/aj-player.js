/* AJ CINE AI PLAYER. Orchestrates the engine adapter, controls, timeline, settings, subtitles, shortcuts and playback state.
 * mountPlayer(root, options) -> { video, destroy() }
 * options: src, manifest?, poster, title, subtitle, key (resume key), meta (history record), versions [{label,sub,current,onSelect}],
 *   next/prev {label, onSelect}, segments [{type:'intro'|'outro',start,end}], thumbnails, tracks [{src,lang,label}],
 *   refreshSource():Promise<string> (fresh signed link), onBack(), onFallback(), autoplay, prefs, onPref(k,v). */
import { esc, clock } from "../ui.js";
import { buildMarkup, setIcon } from "./controls.js";
import { createTimeline } from "./timeline.js";
import { createSettings } from "./settings.js";
import { createSubtitles } from "./subtitles.js";
import { createEngine } from "./engine.js";
import { createPlaybackState } from "./playback-state.js";
import { bindShortcuts, SHORTCUTS } from "./shortcuts.js";
import { progress } from "../store.js";

const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const bad = [401, 403, 404, 410];

export async function mountPlayer(root, o) {
  const ac = new AbortController(), sig = { signal: ac.signal };
  const prefs = { ...o.prefs };
  root.classList.add("ajp"); root.tabIndex = 0; root.setAttribute("role", "group"); root.setAttribute("aria-label", `AJ CINE AI PLAYER: ${o.title || "video"}`); root.dataset.state = "paused";
  root.innerHTML = buildMarkup(o);
  const $ = s => root.querySelector(s), k = n => root.querySelector(`[data-k="${n}"]`);
  const v = $(".ajp-video"), menuEl = $(".ajp-menu"), toastEl = $(".ajp-toast");
  let destroyed = false, hideT = 0, toastT = 0, upT = 0, retries = 0, tapT = 0, lastTap = 0, engine = null, refreshing = false;
  v.volume = clamp(prefs.volume ?? 1, 0, 1); v.muted = !!prefs.muted; v.defaultPlaybackRate = v.playbackRate = prefs.speed || 1;

  const say = (text, action) => {
    clearTimeout(toastT); toastEl.innerHTML = esc(text) + (action ? ` <button type="button">${esc(action.label)}</button>` : ""); toastEl.classList.add("on");
    const b = toastEl.querySelector("button"); if (b) b.onclick = () => { action.run(); toastEl.classList.remove("on"); };
    toastT = setTimeout(() => toastEl.classList.remove("on"), action ? 6000 : 1300);
  };
  const flash = name => { const f = $(".ajp-flash"); f.firstElementChild.className = `bi bi-${name}`; f.classList.remove("on"); void f.offsetWidth; f.classList.add("on"); };
  const dur = () => (isFinite(v.duration) ? v.duration : 0);
  const seekBy = s => { v.currentTime = clamp(v.currentTime + s, 0, dur() || v.currentTime + s); };
  const toggle = () => { if (v.paused || v.ended) { v.play().catch(() => { }); flash("play-fill"); } else { v.pause(); flash("pause-fill"); } };
  const isFull = () => (document.fullscreenElement || document.webkitFullscreenElement) === root;
  const toggleFull = () => {
    if (isFull()) return (document.exitFullscreen || document.webkitExitFullscreen).call(document);
    const r = root.requestFullscreen || root.webkitRequestFullscreen;
    if (r) r.call(root).catch(() => { }); else if (v.webkitEnterFullscreen) v.webkitEnterFullscreen();
  };
  const setVol = d => { v.volume = clamp(v.volume + d, 0, 1); v.muted = v.volume === 0; say(`Volume ${Math.round(v.volume * 100)}%`); };
  const toggleMute = () => { v.muted = !v.muted; if (!v.muted && v.volume === 0) v.volume = .5; say(v.muted ? "Muted" : "Sound on"); };
  const setSpeed = r => { v.playbackRate = r; v.defaultPlaybackRate = r; o.onPref && o.onPref("speed", r); say(`Speed ${r}×`); };
  const setPref = (key, val) => {
    if (key === "loop") { v.loop = !!val; return; }
    prefs[key] = val; o.onPref && o.onPref(key, val);
    if (key === "theater") root.dispatchEvent(new CustomEvent("ajp:theater", { detail: val, bubbles: true }));
    if (/^sub/.test(key)) subs.style(prefs);
  };
  const doPip = async () => { try { if (document.pictureInPictureElement) await document.exitPictureInPicture(); else await v.requestPictureInPicture(); } catch { say("Picture in picture is not available here"); } };
  if (!(document.pictureInPictureEnabled && v.requestPictureInPicture)) k("pip").remove();
  if (!(root.requestFullscreen || root.webkitRequestFullscreen || v.webkitEnterFullscreen)) k("full").remove();

  /* ---- parts ---- */
  const tl = createTimeline($(".ajp-seek"), v, { thumbnails: o.thumbnails, segments: o.segments });
  const subs = createSubtitles(v, { tracks: o.tracks, prefs });
  const state = createPlaybackState(v, { key: o.key, meta: o.meta });
  const model = {
    speed: () => v.playbackRate, loop: () => v.loop, prefs: () => prefs, help: () => showHelp(true),
    qualities: () => {
      if (engine && engine.kind === "shaka" && engine.qualities().length) { const q = engine.qualities(), auto = engine.isAuto(); return { label: auto ? "Auto" : (q.find(x => x.active) || {}).label || "Auto", items: [{ id: "auto", label: "Auto", active: auto }, ...q] }; }
      const vs = o.versions || []; const cur = vs.find(x => x.current);
      return { label: cur ? cur.label : "", items: vs.length > 1 ? vs.map((x, i) => ({ id: "v" + i, label: x.label, sub: x.sub, active: !!x.current })) : [] };
    },
    audio: () => (engine ? engine.audioTracks() : []), subtitles: () => subs.list(),
    setSpeed, setPref, setSubtitle: id => subs.set(id), setAudio: id => engine && engine.setAudio(id),
    setQuality: id => { if (id.startsWith("v")) { const x = o.versions[+id.slice(1)]; if (x && !x.current) { menu.close(); x.onSelect(v.currentTime); } } else engine && engine.setQuality(id); },
    closeOnQuality: true, onToggle: open => { k("settings").setAttribute("aria-expanded", String(open)); open ? (root.classList.add("show"), clearTimeout(hideT)) : wake(); },
  };
  const menu = createSettings(menuEl, model);
  subs.events.addEventListener("change", () => { const has = subs.list().length > 0; k("cc").classList.toggle("ajp-hide", !has); k("cc").classList.toggle("on", subs.list().some(s => s.active)); setIcon(k("cc"), subs.list().some(s => s.active) ? "badge-cc-fill" : "badge-cc"); menu.refresh(); });
  subs.events.dispatchEvent(new Event("change"));

  /* ---- activity / auto-hide ---- */
  function wake() {
    root.classList.add("show"); clearTimeout(hideT);
    if (!v.paused && !menu.isOpen()) hideT = setTimeout(() => { if (tl.isDragging() || menu.isOpen()) return wake(); root.classList.remove("show"); }, 2800);
  }
  const on = (el, ev, fn, opt) => el.addEventListener(ev, fn, { ...sig, ...(opt || {}) });

  /* ---- video events ---- */
  const paintTime = () => { $('[data-t="cur"]').textContent = clock(v.currentTime); $('[data-t="dur"]').textContent = clock(dur()); checkSegments(); };
  on(v, "play", () => { root.dataset.state = "playing"; setIcon(k("play"), "pause-fill"); setIcon($(".ajp-bigplay"), "pause-fill"); hideUp(); wake(); });
  on(v, "pause", () => { root.dataset.state = v.ended ? "ended" : "paused"; setIcon(k("play"), v.ended ? "arrow-repeat" : "play-fill"); setIcon($(".ajp-bigplay"), "play-fill"); root.classList.add("show"); clearTimeout(hideT); });
  on(v, "playing", () => { root.classList.remove("wait"); $(".ajp-err").hidden = true; retries = 0; });
  for (const ev of ["waiting", "seeking"]) on(v, ev, () => root.classList.add("wait"));
  for (const ev of ["canplay", "seeked", "playing"]) on(v, ev, () => root.classList.remove("wait"));
  for (const ev of ["timeupdate", "durationchange", "loadedmetadata"]) on(v, ev, paintTime);
  on(v, "volumechange", () => {
    const lv = v.muted || v.volume === 0 ? "volume-mute-fill" : v.volume < .5 ? "volume-down-fill" : "volume-up-fill"; setIcon(k("mute"), lv);
    $(".ajp-range").value = v.muted ? 0 : v.volume; o.onPref && (o.onPref("volume", v.volume), o.onPref("muted", v.muted));
  });
  on(v, "ratechange", () => menu.refresh());
  on(v, "ended", () => { root.dataset.state = "ended"; root.classList.add("show"); if (o.next) showUp(); });
  on(document, "fullscreenchange", fsChange); on(document, "webkitfullscreenchange", fsChange);
  function fsChange() { const f = isFull(); root.classList.toggle("full", f); setIcon(k("full"), f ? "fullscreen-exit" : "fullscreen"); }
  on(v, "enterpictureinpicture", () => root.classList.add("pip")); on(v, "leavepictureinpicture", () => root.classList.remove("pip"));
  on(window, "online", () => { if (!$(".ajp-err").hidden) recover(!!o.refreshSource); });

  /* ---- skip intro / outro ---- */
  const skipBtn = $(".ajp-skipseg"); let seg = null;
  function checkSegments() {
    const t = v.currentTime, s = (o.segments || []).find(x => t >= x.start && t < x.end - 1);
    if (s === seg) return; seg = s || null;
    if (!seg) { skipBtn.hidden = true; return; }
    skipBtn.hidden = false; skipBtn.textContent = seg.type === "outro" ? "Skip credits" : "Skip intro";
  }
  on(skipBtn, "click", () => { if (seg) { if (seg.type === "outro" && o.next) { showUp(true); } else v.currentTime = seg.end; } });

  /* ---- up next ---- */
  function hideUp() { clearInterval(upT); $(".ajp-up").hidden = true; }
  function showUp(manual = false) {
    const up = $(".ajp-up"); let n = 8; up.hidden = false;
    const draw = () => { up.innerHTML = `<div class="ajp-upc"><div class="ajp-upk">Up next</div><div class="ajp-upt">${esc(o.next.label || "Next episode")}</div><div class="ajp-upb"><button type="button" class="ajp-btn pri" data-up="go">Play now</button><button type="button" class="ajp-btn" data-up="no">Cancel</button></div>
      <label class="ajp-auto"><input type="checkbox" data-up="auto" ${prefs.autonext ? "checked" : ""}> Play next episode automatically</label>${prefs.autonext && !manual ? `<div class="ajp-upn">Starting in ${n} s</div>` : ""}</div>`; const g = up.querySelector("[data-up=go]"); g && g.focus({ preventScroll: true }); };
    draw();
    up.onclick = e => { const a = e.target.dataset && e.target.dataset.up; if (a === "go") { hideUp(); o.next.onSelect(); } else if (a === "no") hideUp(); else if (a === "auto") { setPref("autonext", e.target.checked); if (!e.target.checked) clearInterval(upT); } };
    clearInterval(upT);
    if (prefs.autonext && !manual) upT = setInterval(() => { n -= 1; if (n <= 0) { hideUp(); o.next.onSelect(); } else draw(); }, 1000);
  }

  /* ---- errors and recovery ---- */
  function showError(html, actions) {
    const e = $(".ajp-err"); root.classList.remove("wait"); e.hidden = false; e.innerHTML = `<i class="bi bi-exclamation-circle" aria-hidden="true"></i><p>${html}</p><div class="ajp-errb">${actions}</div>`;
    const f = e.querySelector("button"); f && f.focus({ preventScroll: true });
    e.onclick = ev => { const a = ev.target.dataset && ev.target.dataset.err; if (a === "retry") recover(!!o.refreshSource); else if (a === "tg" && o.onFallback) o.onFallback(); };
  }
  async function diagnose() {
    try { const r = await fetch(v.currentSrc || o.src, { method: "HEAD", credentials: "same-origin" }); return r.status; } catch { return 0; }
  }
  async function recover(fresh = false) {
    if (destroyed || refreshing) return; refreshing = true; const t = v.currentTime; $(".ajp-err").hidden = true; root.classList.add("wait");
    try {
      let src = o.src;
      if (fresh && o.refreshSource) src = await o.refreshSource();
      o.src = src; await engine.destroy(); engine = await createEngine(v, { src }); wireEngine(); await engine.load(t); v.play().catch(() => { });
    } catch (e) { showError("Playback could not be restored. " + esc(e.message || ""), `<button type="button" class="ajp-btn pri" data-err="retry">Try again</button>${o.onFallback ? '<button type="button" class="ajp-btn" data-err="tg">Get it in Telegram</button>' : ""}`); }
    finally { refreshing = false; }
  }
  const fb = o.onFallback ? '<button type="button" class="ajp-btn" data-err="tg">Get it in Telegram</button>' : "";
  on(v, "error", async () => {
    if (destroyed || refreshing) return; const code = v.error && v.error.code; root.classList.remove("wait");
    const status = await diagnose();
    if (bad.includes(status)) {
      if (status !== 404 && retries < 1 && o.refreshSource) { retries += 1; return recover(true); }
      return showError(status === 404 ? "This video is no longer available." : "This playback link has expired.", `${o.refreshSource ? '<button type="button" class="ajp-btn pri" data-err="retry">Get a new link</button>' : ""}${fb}`);
    }
    if (status === 0) return showError("You appear to be offline. Playback will resume when you reconnect.", `<button type="button" class="ajp-btn pri" data-err="retry">Try again</button>`);
    if (code === 4 || code === 3) return showError("Your browser cannot play this file format.", fb || "");
    showError("Playback stopped. Check your connection and try again.", `<button type="button" class="ajp-btn pri" data-err="retry">Try again</button>${fb}`);
  });
  function wireEngine() {
    engine.events.addEventListener("tracks", () => menu.refresh());
    engine.events.addEventListener("error", e => { if (e.detail.fatal) showError("Playback failed.", `<button type="button" class="ajp-btn pri" data-err="retry">Try again</button>${fb}`); });
  }
  /* ---- help overlay ---- */
  function showHelp(show) {
    const h = $(".ajp-help"); h.hidden = !show; if (!show) return;
    h.innerHTML = `<div class="ajp-helpc" role="dialog" aria-label="Keyboard shortcuts"><div class="ajp-helph"><strong>Keyboard shortcuts</strong><button type="button" class="ajp-b" data-help="close" aria-label="Close"><i class="bi bi-x-lg"></i></button></div><dl>${SHORTCUTS.map(([a, b]) => `<dt>${esc(a)}</dt><dd>${esc(b)}</dd>`).join("")}</dl></div>`;
    h.onclick = e => { if (e.target === h || e.target.closest("[data-help=close]")) showHelp(false); };
    const c = h.querySelector("[data-help=close]"); c && c.focus();
  }

  /* ---- bar buttons, volume, gestures ---- */
  const actions = { play: toggle, back10: () => { seekBy(-10); say("−10 s"); }, fwd10: () => { seekBy(10); say("+10 s"); }, mute: toggleMute, full: toggleFull, pip: doPip,
    settings: () => menu.toggle(), cc: () => { const r = subs.toggle(); if (r !== null) say(r ? "Subtitles on" : "Subtitles off"); }, theater: () => setPref("theater", !prefs.theater),
    next: () => o.next && o.next.onSelect(), prev: () => o.prev && o.prev.onSelect(), back: () => o.onBack && o.onBack() };
  on(root, "click", e => { const b = e.target.closest("[data-k]"); if (b && root.contains(b) && actions[b.dataset.k]) { e.stopPropagation(); actions[b.dataset.k](); } });
  on($(".ajp-range"), "input", e => { v.volume = +e.target.value; v.muted = +e.target.value === 0; });
  on(v, "click", e => { if (e.pointerType === "touch") return; clearTimeout(tapT); tapT = setTimeout(toggle, 220); });
  on(v, "dblclick", e => { clearTimeout(tapT); if (e.pointerType !== "touch") toggleFull(); });
  const fx = side => { const el = $(".ajp-seekfx-" + side); el.classList.remove("on"); void el.offsetWidth; el.classList.add("on"); };
  on(root, "pointerup", e => {
    if (e.pointerType !== "touch" || e.target.closest(".ajp-controls,.ajp-menu,.ajp-up,.ajp-help,.ajp-err,.ajp-bigplay,.ajp-skipseg,.ajp-top,.ajp-toast")) return;
    const now = Date.now(), r = root.getBoundingClientRect(), x = (e.clientX - r.left) / r.width;
    if (now - lastTap < 300) { clearTimeout(tapT); lastTap = 0; if (x < .35) { seekBy(-10); fx("l"); } else if (x > .65) { seekBy(10); fx("r"); } else toggleFull(); return; }
    lastTap = now; tapT = setTimeout(() => { if (root.classList.contains("show") && !v.paused) root.classList.remove("show"); else wake(); }, 300);
  });
  on(root, "mousemove", wake); on(root, "pointerdown", wake); on(root, "focusin", wake);
  on(root, "mouseleave", () => { if (!v.paused && !menu.isOpen()) { clearTimeout(hideT); hideT = setTimeout(() => root.classList.remove("show"), 700); } });

  const unbind = bindShortcuts(root, {
    toggle, seek: s => { seekBy(s); say(`${s > 0 ? "+" : "−"}${Math.abs(s)} s`); }, volume: setVol, mute: toggleMute, fullscreen: toggleFull,
    captions: () => actions.cc(), theater: () => actions.theater(), pip: () => k("pip") && doPip(), next: () => actions.next(), prev: () => actions.prev(),
    jump: f => { v.currentTime = dur() * f; say(`${Math.round(f * 100)}%`); }, help: () => showHelp($(".ajp-help").hidden), activity: wake,
    escape: () => { if (!$(".ajp-help").hidden) showHelp(false); else if (menu.isOpen()) { menu.close(); k("settings").focus(); } },
  });

  if ("mediaSession" in navigator) {
    try {
      navigator.mediaSession.metadata = new MediaMetadata({ title: o.title || "Video", artist: o.subtitle || "AJ CINE AI", artwork: o.poster ? [{ src: o.poster }] : [] });
      const ms = (a, f) => { try { navigator.mediaSession.setActionHandler(a, f); } catch { /* unsupported action */ } };
      ms("play", () => v.play()); ms("pause", () => v.pause()); ms("seekbackward", () => seekBy(-10)); ms("seekforward", () => seekBy(10)); ms("seekto", d => { v.currentTime = d.seekTime; });
      ms("nexttrack", o.next ? () => o.next.onSelect() : null); ms("previoustrack", o.prev ? () => o.prev.onSelect() : null);
    } catch { /* media session is optional */ }
  }

  /* ---- start ---- */
  engine = await createEngine(v, { src: o.src, manifest: o.manifest }); wireEngine();
  const resumeAt = o.startAt ?? state.resumeAt();
  await engine.load(resumeAt);
  if (resumeAt > 30) say(`Resumed at ${clock(resumeAt)}`, { label: "Start over", run: () => { v.currentTime = 0; } });
  if (o.autoplay) v.play().catch(() => { });
  root.classList.add("show"); paintTime();
  root.dispatchEvent(new CustomEvent("ajp:theater", { detail: !!prefs.theater, bubbles: true }));
  return {
    video: v,
    destroy() {
      if (destroyed) return; destroyed = true; state.destroy(); tl.destroy(); subs.destroy(); menu.destroy(); unbind(); ac.abort();
      clearTimeout(hideT); clearTimeout(toastT); clearTimeout(tapT); clearInterval(upT);
      try { v.pause(); } catch { /* ignore */ }
      if (engine) engine.destroy().finally(() => { try { v.removeAttribute("src"); v.load(); } catch { /* ignore */ } });
      if (document.pictureInPictureElement === v) document.exitPictureInPicture().catch(() => { });
      if (isFull()) (document.exitFullscreen || document.webkitExitFullscreen).call(document);
      if ("mediaSession" in navigator) { try { navigator.mediaSession.metadata = null; } catch { /* ignore */ } }
    },
  };
}
