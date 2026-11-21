/** Subtitle tracks: those the browser exposes plus external WebVTT files the backend may supply. Styling uses CSS variables
 *  on the video element, which `video::cue` reads (see player.css). Nothing is offered when the source has no tracks. */
export function createSubtitles(video, { tracks = [], prefs }) {
  const ac = new AbortController(), sig = { signal: ac.signal }, target = new EventTarget();
  tracks.forEach((t, i) => {
    if (!/^(\/|https?:)/.test(t.src || "")) return;
    const el = document.createElement("track"); el.kind = "subtitles"; el.label = t.label || t.lang || `Subtitles ${i + 1}`; el.srclang = t.lang || "und"; el.src = t.src; el.dataset.ext = "1"; video.appendChild(el);
  });
  const list = () => [...(video.textTracks || [])].filter(t => t.kind === "subtitles" || t.kind === "captions");
  const info = () => list().map((t, i) => ({ id: String(i), label: t.label || t.language || `Track ${i + 1}`, active: t.mode === "showing" }));
  const notify = () => target.dispatchEvent(new Event("change"));
  video.textTracks && video.textTracks.addEventListener("addtrack", notify, sig);
  video.textTracks && video.textTracks.addEventListener("removetrack", notify, sig);
  function set(id) { list().forEach((t, i) => { t.mode = id !== null && String(i) === String(id) ? "showing" : "disabled"; }); notify(); }
  let lastId = null;
  function style(p) {
    const bg = { dark: "rgba(0,0,0,.72)", solid: "#000", none: "transparent" }[p.subBg] || "rgba(0,0,0,.72)";
    video.style.setProperty("--sub-size", `${Math.round(p.subSize || 100)}%`); video.style.setProperty("--sub-color", p.subColor || "#fff"); video.style.setProperty("--sub-bg", bg);
  }
  style(prefs);
  return {
    events: target, list: info, set, style,
    toggle() { const cur = info().find(t => t.active); if (cur) { lastId = cur.id; set(null); return false; } const i = info(); if (!i.length) return null; set(lastId ?? i[0].id); return true; },
    destroy() { ac.abort(); video.querySelectorAll("track[data-ext]").forEach(n => n.remove()); },
  };
}
