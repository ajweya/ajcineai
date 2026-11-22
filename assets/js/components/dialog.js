/* AJ Dialog: accessible modal replacing alert()/confirm(). Traps focus, restores it on close, closes on Escape/backdrop. */
const esc = v => String(v ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
let n = 0;
export function dialog({ title, body = "", confirm = "OK", cancel = "Cancel", danger = false, icon = "" }) {
  return new Promise(resolve => {
    const id = "ajd" + ++n, back = document.activeElement, el = document.createElement("div");
    el.className = "aj-dialog"; el.innerHTML = `<div class="aj-dialog-scrim" data-x></div><div class="aj-dialog-panel" role="dialog" aria-modal="true" aria-labelledby="${id}-t" aria-describedby="${id}-b">
      ${icon ? `<div class="aj-dialog-ic${danger ? " bad" : ""}"><i class="bi bi-${icon}" aria-hidden="true"></i></div>` : ""}<h2 id="${id}-t">${esc(title)}</h2>${body ? `<p id="${id}-b">${esc(body)}</p>` : ""}
      <div class="aj-dialog-acts">${cancel ? `<button type="button" class="aj-btn ghost" data-x>${esc(cancel)}</button>` : ""}<button type="button" class="aj-btn ${danger ? "danger" : "primary"}" data-ok>${esc(confirm)}</button></div></div>`;
    document.body.appendChild(el); document.documentElement.classList.add("aj-lock");
    requestAnimationFrame(() => el.classList.add("on"));
    const focusables = () => [...el.querySelectorAll("button")];
    (el.querySelector("[data-ok]")).focus();
    const done = v => {
      el.classList.remove("on"); document.documentElement.classList.remove("aj-lock"); document.removeEventListener("keydown", key, true);
      setTimeout(() => el.remove(), 200); back && back.focus && back.focus({ preventScroll: true }); resolve(v);
    };
    const key = e => {
      if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); done(false); }
      else if (e.key === "Tab") { const f = focusables(), i = f.indexOf(document.activeElement); e.preventDefault(); f[(i + (e.shiftKey ? -1 : 1) + f.length) % f.length].focus(); }
    };
    document.addEventListener("keydown", key, true);
    el.addEventListener("click", e => { if (e.target.closest("[data-ok]")) done(true); else if (e.target.closest("[data-x]")) done(false); });
  });
}
export const confirmDialog = o => dialog({ icon: "exclamation-triangle", ...o });
