import { esc, clock } from "../ui.js";

const btn = (k, icon, label, extra = "") => `<button type="button" class="ajp-b ${extra}" data-k="${k}" aria-label="${label}" title="${label}"><i class="bi bi-${icon}" aria-hidden="true"></i></button>`;
export function buildMarkup(o) {
  return `<video class="ajp-video" playsinline preload="metadata"${o.poster ? ` poster="${esc(o.poster)}"` : ""}></video>
  <div class="ajp-shade" aria-hidden="true"></div>
  <div class="ajp-top">${o.onBack ? `<button type="button" class="ajp-b" data-k="back" aria-label="Back"><i class="bi bi-arrow-left" aria-hidden="true"></i></button>` : ""}
    <div class="ajp-info"><div class="ajp-brand">AJ CINE AI PLAYER</div><div class="ajp-title">${esc(o.title || "")}</div>${o.subtitle ? `<div class="ajp-sub">${esc(o.subtitle)}</div>` : ""}</div></div>
  <button type="button" class="ajp-bigplay" data-k="play" aria-label="Play"><i class="bi bi-play-fill" aria-hidden="true"></i></button>
  <div class="ajp-spin" aria-hidden="true"></div>
  <div class="ajp-flash" aria-hidden="true"><i class="bi"></i></div>
  <div class="ajp-seekfx ajp-seekfx-l" aria-hidden="true"><i class="bi bi-chevron-double-left"></i>10 s</div><div class="ajp-seekfx ajp-seekfx-r" aria-hidden="true">10 s<i class="bi bi-chevron-double-right"></i></div>
  <div class="ajp-toast" role="status" aria-live="polite"></div>
  <button type="button" class="ajp-skipseg" hidden></button>
  <div class="ajp-controls">
    <div class="ajp-seek"></div>
    <div class="ajp-bar">
      ${btn("play", "play-fill", "Play (K)")}${o.prev ? btn("prev", "skip-start-fill", "Previous episode (P)") : ""}${o.next ? btn("next", "skip-end-fill", "Next episode (N)") : ""}
      <button type="button" class="ajp-b ajp-hide-sm" data-k="back10" aria-label="Back 10 seconds (J)" title="Back 10 seconds (J)"><i class="bi bi-arrow-counterclockwise" aria-hidden="true"></i><span class="ajp-n">10</span></button>
      <button type="button" class="ajp-b ajp-hide-sm" data-k="fwd10" aria-label="Forward 10 seconds (L)" title="Forward 10 seconds (L)"><i class="bi bi-arrow-clockwise" aria-hidden="true"></i><span class="ajp-n">10</span></button>
      <div class="ajp-vol">${btn("mute", "volume-up-fill", "Mute (M)")}<input class="ajp-range" type="range" min="0" max="1" step="0.02" value="1" aria-label="Volume"></div>
      <div class="ajp-time"><span data-t="cur">0:00</span><span class="ajp-tsep">/</span><span data-t="dur">0:00</span></div>
      <div class="ajp-grow"></div>
      ${btn("cc", "badge-cc", "Subtitles (C)", "ajp-hide")}${btn("settings", "gear-fill", "Settings")}${btn("pip", "pip", "Picture in picture (I)", "ajp-hide-sm")}${btn("theater", "aspect-ratio", "Theater mode (T)", "ajp-hide-sm")}${btn("full", "fullscreen", "Fullscreen (F)")}
    </div></div>
  <div class="ajp-menu" hidden></div><div class="ajp-up" hidden></div><div class="ajp-help" hidden></div><div class="ajp-err" role="alert" hidden></div>`;
}
export const setIcon = (b, name) => { const i = b.querySelector("i"); if (i) i.className = `bi bi-${name}`; };
export { clock };
