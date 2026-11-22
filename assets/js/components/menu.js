/* AJ Menu: dropdown menus ([data-menu] trigger + [data-menu-panel]) with keyboard support, and the mobile navigation sheet. */
export function initMenus() {
  let open = null;
  const close = (refocus = false) => { if (!open) return; const { btn, panel } = open; panel.hidden = true; btn.setAttribute("aria-expanded", "false"); btn.closest(".aj-menu-wrap")?.classList.remove("open"); if (refocus) btn.focus(); open = null; };
  const items = p => [...p.querySelectorAll("a,button")];
  document.addEventListener("click", e => {
    const btn = e.target.closest("[data-menu]");
    if (btn) { const panel = document.getElementById(btn.getAttribute("aria-controls")); const was = open && open.panel === panel; close(); if (!was) { panel.hidden = false; btn.setAttribute("aria-expanded", "true"); btn.closest(".aj-menu-wrap")?.classList.add("open"); open = { btn, panel }; } return; }
    if (open && (!open.panel.contains(e.target) || e.target.closest("a,button"))) close();
  });
  document.addEventListener("keydown", e => {
    if (!open) return; const l = items(open.panel), i = l.indexOf(document.activeElement);
    if (e.key === "Escape") { e.preventDefault(); close(true); }
    else if (e.key === "ArrowDown") { e.preventDefault(); l[(i + 1) % l.length].focus(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); l[(i - 1 + l.length) % l.length].focus(); }
  });
  document.querySelectorAll("[data-menu]").forEach(b => b.addEventListener("keydown", e => { if (e.key === "ArrowDown") { e.preventDefault(); if (!open) b.click(); items(open.panel)[0]?.focus(); } }));

  /* mobile sheet */
  const sheet = document.getElementById("sheet"), opener = document.getElementById("sheet-open");
  if (!sheet || !opener) return;
  const setSheet = on => {
    sheet.classList.toggle("on", on); sheet.setAttribute("aria-hidden", String(!on)); opener.setAttribute("aria-expanded", String(on));
    document.documentElement.classList.toggle("aj-lock", on); sheet.inert = !on;
    if (on) sheet.querySelector("a,button")?.focus(); else opener.focus({ preventScroll: true });
  };
  sheet.inert = true;
  opener.addEventListener("click", () => setSheet(!sheet.classList.contains("on")));
  sheet.addEventListener("click", e => { if (e.target.closest("[data-sheet-close]") || e.target.closest("a[data-link]")) setSheet(false); });
  document.addEventListener("keydown", e => { if (e.key === "Escape" && sheet.classList.contains("on")) setSheet(false); });
}
