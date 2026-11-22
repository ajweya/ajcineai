/** Keyboard shortcuts for the player. Ignored while typing in a field, with modifier keys, or when focus is on a control that
 *  uses the same key (buttons keep Space/Enter, the seek slider keeps the arrows). */
export const SHORTCUTS = [
  ["Space / K", "Play or pause"], ["Left / Right", "Back / forward 5 seconds"], ["J / L", "Back / forward 10 seconds"], ["Up / Down", "Volume"],
  ["M", "Mute"], ["F", "Fullscreen"], ["C", "Subtitles on or off"], ["T", "Theater mode"], ["I", "Picture in picture"],
  ["N / P", "Next / previous episode"], ["0 to 9", "Jump to 0% to 90%"], ["Esc", "Close menus or exit fullscreen"], ["?", "Show this list"],
];
export function bindShortcuts(root, h) {
  const ac = new AbortController();
  document.addEventListener("keydown", e => {
    if (e.ctrlKey || e.metaKey || e.altKey || e.defaultPrevented) return;
    const t = e.target;
    if (t && t.closest && t.closest("input:not([type=range]), textarea, select, [contenteditable=true]")) return;
    if (e.key === "Escape") { h.escape(); return; }
    if (!(root.contains(t) || t === document.body || t === document.documentElement)) return;
    const onBtn = t && t.closest && t.closest("button, a, [role=menuitem], [role=menuitemradio], [role=menuitemcheckbox]");
    if (onBtn && (e.key === " " || e.key === "Enter")) return;
    if (t && t.getAttribute && t.getAttribute("role") === "slider" && /Arrow(Left|Right)|Home|End|Page/.test(e.key)) return;
    const k = e.key.length === 1 ? e.key.toLowerCase() : e.key; let used = true;
    if (k === " " || k === "k") h.toggle(); else if (k === "ArrowLeft") h.seek(-5); else if (k === "ArrowRight") h.seek(5);
    else if (k === "j") h.seek(-10); else if (k === "l") h.seek(10);
    else if (k === "ArrowUp") h.volume(.05); else if (k === "ArrowDown") h.volume(-.05);
    else if (k === "m") h.mute(); else if (k === "f") h.fullscreen(); else if (k === "c") h.captions(); else if (k === "t") h.theater(); else if (k === "i") h.pip();
    else if (k === "n") h.next(); else if (k === "p") h.prev(); else if (/^[0-9]$/.test(k)) h.jump(+k / 10); else if (k === "?") h.help();
    else used = false;
    if (used) { e.preventDefault(); h.activity(); }
  }, { signal: ac.signal });
  return () => ac.abort();
}
