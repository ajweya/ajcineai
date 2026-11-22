import { esc } from "../ui.js";

const SPEEDS = [0.5, 0.75, 1, 1.25, 1.5, 1.75, 2];
/** Settings drawer with a panel stack: root > speed | quality | audio | subtitles | subtitle style | preferences.
 *  Rows appear only for things the current source really offers. `m` is the model supplied by the player. */
export function createSettings(host, m) {
  const ac = new AbortController(), sig = { signal: ac.signal };
  let panel = null;
  host.setAttribute("role", "menu"); host.hidden = true;
  const row = (k, l, v, chev = true) => `<button type="button" class="ajp-row" role="menuitem" data-m="${k}"><span>${esc(l)}</span><span class="ajp-val">${esc(v || "")}${chev ? '<i class="bi bi-chevron-right" aria-hidden="true"></i>' : ""}</span></button>`;
  const opt = (k, l, on, sub = "") => `<button type="button" class="ajp-row" role="menuitemradio" aria-checked="${on}" data-m="${k}"><span>${esc(l)}${sub ? `<small>${esc(sub)}</small>` : ""}</span>${on ? '<i class="bi bi-check2 ajp-ck" aria-hidden="true"></i>' : ""}</button>`;
  const sw = (k, l, on) => `<button type="button" class="ajp-row" role="menuitemcheckbox" aria-checked="${on}" data-m="${k}"><span>${esc(l)}</span><span class="ajp-sw${on ? " on" : ""}" aria-hidden="true"></span></button>`;
  const head = t => `<button type="button" class="ajp-row ajp-head" data-m="root"><i class="bi bi-chevron-left" aria-hidden="true"></i><span>${esc(t)}</span></button>`;

  function render(p) {
    panel = p; let h = "";
    const q = m.qualities(), subs = m.subtitles(), aud = m.audio(), p2 = m.prefs();
    if (p === "root") {
      h += row("speed", "Playback speed", m.speed() === 1 ? "Normal" : m.speed() + "×");
      if (q.items.length) h += row("quality", "Quality", q.label);
      if (aud.length > 1) h += row("audio", "Audio", (aud.find(a => a.active) || {}).label);
      if (subs.length) h += row("subs", "Subtitles", (subs.find(s => s.active) || { label: "Off" }).label);
      if (subs.length) h += row("style", "Subtitle style", "");
      h += row("prefs", "Player preferences", "");
      h += row("keys", "Keyboard shortcuts", "", false);
    } else if (p === "speed") h = head("Playback speed") + SPEEDS.map(s => opt("sp:" + s, s === 1 ? "Normal" : s + "×", m.speed() === s)).join("");
    else if (p === "quality") h = head("Quality") + q.items.map(i => opt("q:" + i.id, i.label, i.active, i.sub)).join("");
    else if (p === "audio") h = head("Audio") + aud.map(a => opt("a:" + a.id, a.label, a.active)).join("");
    else if (p === "subs") h = head("Subtitles") + opt("s:off", "Off", !subs.some(s => s.active)) + subs.map(s => opt("s:" + s.id, s.label, s.active)).join("");
    else if (p === "style") h = head("Subtitle style") + [["size:80", "Small", p2.subSize === 80], ["size:100", "Medium", p2.subSize === 100], ["size:130", "Large", p2.subSize === 130], ["size:170", "Extra large", p2.subSize === 170]].map(([k, l, on]) => opt(k, l, on)).join("") +
      '<div class="ajp-sep-row"></div>' + [["color:#ffffff", "White text", p2.subColor === "#ffffff"], ["color:#ffe066", "Yellow text", p2.subColor === "#ffe066"], ["color:#9ee6ff", "Cyan text", p2.subColor === "#9ee6ff"]].map(([k, l, on]) => opt(k, l, on)).join("") +
      '<div class="ajp-sep-row"></div>' + [["bg:dark", "Dark box", p2.subBg === "dark"], ["bg:solid", "Solid black", p2.subBg === "solid"], ["bg:none", "No background", p2.subBg === "none"]].map(([k, l, on]) => opt(k, l, on)).join("");
    else if (p === "prefs") h = head("Player preferences") + sw("pref:autonext", "Play next episode automatically", p2.autonext) + sw("pref:theater", "Theater mode", p2.theater) + sw("pref:loop", "Loop this video", m.loop());
    host.innerHTML = h;
  }
  host.addEventListener("click", e => {
    const b = e.target.closest("[data-m]"); if (!b) return; e.stopPropagation();
    const [k, ...r] = b.dataset.m.split(":"), a = r.join(":");
    if (["root", "speed", "quality", "audio", "subs", "style", "prefs"].includes(k)) return render(k);
    if (k === "keys") { close(); return m.help(); }
    if (k === "sp") m.setSpeed(+a); else if (k === "q") { m.setQuality(a); if (a !== "auto" && m.closeOnQuality) return close(); }
    else if (k === "a") m.setAudio(a); else if (k === "s") m.setSubtitle(a === "off" ? null : a);
    else if (k === "size") m.setPref("subSize", +a); else if (k === "color") m.setPref("subColor", a); else if (k === "bg") m.setPref("subBg", a);
    else if (k === "pref") m.setPref(a, a === "loop" ? !m.loop() : !m.prefs()[a]);
    render(panel);
  }, sig);
  const open = (p = "root") => { render(p); host.hidden = false; m.onToggle(true); const f = host.querySelector(".ajp-row"); if (f) f.focus({ preventScroll: true }); };
  const close = () => { if (host.hidden) return; host.hidden = true; panel = null; m.onToggle(false); };
  document.addEventListener("pointerdown", e => { if (!host.hidden && !e.target.closest(".ajp-menu, [data-k=settings]")) close(); }, sig);
  host.addEventListener("keydown", e => {
    const rows = [...host.querySelectorAll(".ajp-row")], i = rows.indexOf(document.activeElement);
    if (e.key === "ArrowDown") { e.preventDefault(); rows[(i + 1) % rows.length].focus(); } else if (e.key === "ArrowUp") { e.preventDefault(); rows[(i - 1 + rows.length) % rows.length].focus(); }
  }, sig);
  return { open, close, toggle: () => (host.hidden ? open() : close()), isOpen: () => !host.hidden, refresh: () => { if (!host.hidden && panel) render(panel); }, destroy: () => ac.abort() };
}
