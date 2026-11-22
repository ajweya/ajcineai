/* AJ Select: every native <select> on the page gets a custom button + listbox. The native element stays in the DOM
 * (hidden) as the source of truth, so existing `change` listeners and `.value` reads keep working.
 * Programmatic value changes can be reflected with `select.dispatchEvent(new Event("aj:sync"))`. */
const esc = v => String(v ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
let uid = 0, openOne = null;

function enhance(sel) {
  if (sel.dataset.aj || sel.closest(".ajp")) return;
  sel.dataset.aj = "1";
  const id = "ajs" + ++uid, wrap = document.createElement("div");
  wrap.className = "aj-select";
  const label = sel.getAttribute("aria-label") || "";
  wrap.innerHTML = `<button type="button" class="aj-select-btn" aria-haspopup="listbox" aria-expanded="false" aria-controls="${id}" ${label ? `aria-label="${esc(label)}"` : ""}><span class="aj-select-val"></span><i class="bi bi-chevron-down" aria-hidden="true"></i></button><div class="aj-select-list" role="listbox" id="${id}" tabindex="-1" hidden></div>`;
  sel.parentNode.insertBefore(wrap, sel); wrap.appendChild(sel);
  sel.classList.add("aj-native"); sel.tabIndex = -1; sel.setAttribute("aria-hidden", "true");
  const btn = wrap.firstElementChild, list = wrap.querySelector(".aj-select-list"), val = btn.firstElementChild;
  let active = -1;
  const opts = () => [...sel.options];
  const sync = () => { const o = sel.options[sel.selectedIndex]; val.textContent = o ? o.textContent : ""; wrap.classList.toggle("is-set", !!sel.value); };
  const paint = () => {
    list.innerHTML = opts().map((o, i) => `<div class="aj-option" role="option" id="${id}-${i}" data-i="${i}" aria-selected="${i === sel.selectedIndex}"${o.disabled ? ' aria-disabled="true"' : ""}><span>${esc(o.textContent)}</span><i class="bi bi-check2" aria-hidden="true"></i></div>`).join("");
  };
  const focusOpt = i => {
    const n = list.children.length; if (!n) return; active = (i + n) % n;
    [...list.children].forEach((c, k) => c.classList.toggle("is-active", k === active));
    list.setAttribute("aria-activedescendant", `${id}-${active}`); list.children[active].scrollIntoView({ block: "nearest" });
  };
  const open = () => {
    if (openOne && openOne !== close) openOne();
    paint(); list.hidden = false; wrap.classList.add("open"); btn.setAttribute("aria-expanded", "true");
    const r = wrap.getBoundingClientRect(); wrap.classList.toggle("up", innerHeight - r.bottom < 280 && r.top > 300);
    focusOpt(Math.max(0, sel.selectedIndex)); list.focus({ preventScroll: true }); openOne = close;
  };
  const close = (refocus = false) => {
    if (list.hidden) return; list.hidden = true; wrap.classList.remove("open"); btn.setAttribute("aria-expanded", "false");
    if (openOne === close) openOne = null; if (refocus) btn.focus({ preventScroll: true });
  };
  const choose = i => {
    const o = sel.options[i]; if (!o || o.disabled) return;
    const changed = sel.selectedIndex !== i; sel.selectedIndex = i; sync(); close(true);
    if (changed) sel.dispatchEvent(new Event("change", { bubbles: true }));
  };
  btn.addEventListener("click", () => (list.hidden ? open() : close()));
  btn.addEventListener("keydown", e => { if (["ArrowDown", "ArrowUp", "Enter", " "].includes(e.key)) { e.preventDefault(); open(); } });
  list.addEventListener("keydown", e => {
    if (e.key === "ArrowDown") { e.preventDefault(); focusOpt(active + 1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); focusOpt(active - 1); }
    else if (e.key === "Home") { e.preventDefault(); focusOpt(0); }
    else if (e.key === "End") { e.preventDefault(); focusOpt(list.children.length - 1); }
    else if (e.key === "Enter" || e.key === " ") { e.preventDefault(); choose(active); }
    else if (e.key === "Escape" || e.key === "Tab") { e.preventDefault(); close(true); }
    else if (e.key.length === 1) { const k = e.key.toLowerCase(), i = opts().findIndex((o, n) => n > active && o.textContent.trim().toLowerCase().startsWith(k)); const j = i >= 0 ? i : opts().findIndex(o => o.textContent.trim().toLowerCase().startsWith(k)); if (j >= 0) focusOpt(j); }
  });
  list.addEventListener("click", e => { const o = e.target.closest("[data-i]"); if (o) choose(+o.dataset.i); });
  list.addEventListener("pointermove", e => { const o = e.target.closest("[data-i]"); if (o && +o.dataset.i !== active) focusOpt(+o.dataset.i); });
  sel.addEventListener("change", sync); sel.addEventListener("aj:sync", sync);
  new MutationObserver(sync).observe(sel, { childList: true, subtree: true, attributes: true });
  sync();
}

export function initSelects(root = document.body) {
  root.querySelectorAll("select").forEach(enhance);
  new MutationObserver(ms => { for (const m of ms) for (const n of m.addedNodes) if (n.nodeType === 1) { if (n.tagName === "SELECT") enhance(n); else n.querySelectorAll?.("select").forEach(enhance); } })
    .observe(root, { childList: true, subtree: true });
  document.addEventListener("pointerdown", e => { if (openOne && !e.target.closest(".aj-select.open")) openOne(); });
}
