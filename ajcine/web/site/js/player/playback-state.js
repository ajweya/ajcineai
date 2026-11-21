import { progress } from "../store.js";

/** Remembers where the viewer is (per title/episode, in this browser) and restores it. Saves at most every 5 s. */
export function createPlaybackState(video, { key, meta }) {
  const ac = new AbortController(), sig = { signal: ac.signal };
  let last = 0, finished = false;
  const near = () => video.duration && video.currentTime > video.duration - Math.min(60, video.duration * .05);
  function save(force = false) {
    if (!key || !isFinite(video.duration) || !video.duration || finished) return;
    const now = Date.now(); if (!force && now - last < 5000) return; last = now;
    if (near()) { progress.finish(key); finished = true; return; }
    if (video.currentTime > 5) progress.save(key, video.currentTime, video.duration, meta);
  }
  video.addEventListener("timeupdate", () => save(false), sig);
  video.addEventListener("pause", () => save(true), sig);
  video.addEventListener("seeked", () => { if (finished && !near()) finished = false; }, sig);
  video.addEventListener("ended", () => { if (key) { progress.finish(key); finished = true; } }, sig);
  addEventListener("pagehide", () => save(true), sig);
  document.addEventListener("visibilitychange", () => { if (document.hidden) save(true); }, sig);
  return {
    /** Saved position in seconds if worth resuming, else 0. */
    resumeAt() { const p = key && progress.get(key); return p && !p.done && p.pos > 30 && p.dur && p.pos < p.dur * .95 ? p.pos : 0; },
    save, destroy() { save(true); ac.abort(); },
  };
}
