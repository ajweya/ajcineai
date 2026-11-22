import { clock } from "../ui.js";

const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
/** Seek bar: played + buffered ranges, hover time (with a thumbnail when sprite metadata exists), drag, keyboard.
 *  thumbnails: { src, width, height, interval, columns } describes a sprite sheet; segments: [{type,start,end}] marks intro/outro. */
export function createTimeline(root, video, { thumbnails, segments = [] } = {}) {
  const ac = new AbortController(), sig = { signal: ac.signal };
  root.innerHTML = `<div class="ajp-rail"><div class="ajp-buf"></div><div class="ajp-played"></div><div class="ajp-hover"></div><div class="ajp-thumb"></div></div>
    <div class="ajp-tip" aria-hidden="true"><div class="ajp-tipimg" hidden></div><span>0:00</span></div>`;
  root.setAttribute("role", "slider"); root.tabIndex = 0; root.setAttribute("aria-label", "Seek"); root.setAttribute("aria-valuemin", "0");
  const rail = root.querySelector(".ajp-rail"), buf = root.querySelector(".ajp-buf"), played = root.querySelector(".ajp-played"), hover = root.querySelector(".ajp-hover"), thumb = root.querySelector(".ajp-thumb");
  const tip = root.querySelector(".ajp-tip"), tipImg = root.querySelector(".ajp-tipimg"), tipTxt = tip.querySelector("span");
  let dragging = false, spriteOk = false;
  if (thumbnails && thumbnails.src) { const im = new Image(); im.onload = () => { spriteOk = true; tipImg.style.backgroundImage = `url("${thumbnails.src}")`; tipImg.style.width = thumbnails.width + "px"; tipImg.style.height = thumbnails.height + "px"; }; im.src = thumbnails.src; }

  const dur = () => (isFinite(video.duration) ? video.duration : 0);
  const ratio = x => { const r = root.getBoundingClientRect(); return clamp((x - r.left) / r.width, 0, 1); };
  function paint() {
    const d = dur(), t = video.currentTime, p = d ? t / d : 0;
    played.style.transform = `scaleX(${p})`; thumb.style.left = p * 100 + "%";
    let end = 0; for (let i = 0; i < video.buffered.length; i++) if (video.buffered.start(i) <= t + .5 && video.buffered.end(i) > end) end = video.buffered.end(i);
    buf.style.transform = `scaleX(${d ? Math.min(1, end / d) : 0})`;
    root.setAttribute("aria-valuenow", String(Math.round(p * 100))); root.setAttribute("aria-valuetext", `${clock(t)} of ${clock(d)}`);
  }
  function hoverAt(x) {
    const r = ratio(x), d = dur(), box = root.getBoundingClientRect(), t = r * d;
    hover.style.transform = `scaleX(${r})`; tipTxt.textContent = clock(t);
    const half = (spriteOk ? thumbnails.width : 44) / 2 + 6; tip.style.left = clamp(r * box.width, half, box.width - half) + "px";
    if (spriteOk && thumbnails.interval) {
      const n = Math.floor(t / thumbnails.interval), cols = thumbnails.columns || 10;
      tipImg.hidden = false; tipImg.style.backgroundPosition = `-${(n % cols) * thumbnails.width}px -${Math.floor(n / cols) * thumbnails.height}px`;
    }
  }
  const preview = x => { const r = ratio(x); played.style.transform = `scaleX(${r})`; thumb.style.left = r * 100 + "%"; };
  root.addEventListener("pointermove", e => { if (!dur()) return; hoverAt(e.clientX); if (dragging) preview(e.clientX); }, sig);
  root.addEventListener("pointerdown", e => { if (!dur()) return; dragging = true; root.setPointerCapture(e.pointerId); root.classList.add("drag"); hoverAt(e.clientX); preview(e.clientX); }, sig);
  const end = e => { if (!dragging) return; dragging = false; root.classList.remove("drag"); video.currentTime = ratio(e.clientX) * dur(); paint(); };
  root.addEventListener("pointerup", end, sig);
  root.addEventListener("pointercancel", () => { dragging = false; root.classList.remove("drag"); paint(); }, sig);
  root.addEventListener("keydown", e => {
    const d = dur(); if (!d) return; let t = null;
    if (e.key === "ArrowLeft") t = video.currentTime - 5; else if (e.key === "ArrowRight") t = video.currentTime + 5;
    else if (e.key === "PageDown") t = video.currentTime - d * .1; else if (e.key === "PageUp") t = video.currentTime + d * .1;
    else if (e.key === "Home") t = 0; else if (e.key === "End") t = d - 1;
    if (t !== null) { e.preventDefault(); e.stopPropagation(); video.currentTime = clamp(t, 0, d); paint(); }
  }, sig);
  for (const ev of ["timeupdate", "progress", "durationchange", "loadedmetadata", "seeked"]) video.addEventListener(ev, () => { if (!dragging) paint(); }, sig);
  function marks() {
    rail.querySelectorAll(".ajp-seg").forEach(n => n.remove()); const d = dur(); if (!d) return;
    segments.forEach(s => { const m = document.createElement("div"); m.className = "ajp-seg"; m.title = s.type === "outro" ? "Credits" : "Intro"; m.style.left = clamp(s.start / d, 0, 1) * 100 + "%"; m.style.width = clamp((s.end - s.start) / d, 0, 1) * 100 + "%"; rail.insertBefore(m, thumb); });
  }
  video.addEventListener("durationchange", marks, sig); marks();
  paint();
  return { paint, isDragging: () => dragging, destroy: () => ac.abort() };
}
