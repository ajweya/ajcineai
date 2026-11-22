/* AJ Tooltip: one shared tooltip for any element with [data-tip] (native title attributes on controls are converted). */
export function initTooltips() {
  const tip = document.createElement("div"); tip.className = "aj-tip"; tip.setAttribute("role", "tooltip"); tip.id = "aj-tip"; document.body.appendChild(tip);
  let cur = null, t = 0;
  const adopt = el => { if (el.title && el.matches("button,a,[role=button]")) { el.dataset.tip = el.title; if (!el.getAttribute("aria-label")) el.setAttribute("aria-label", el.title); el.removeAttribute("title"); } };
  const show = el => {
    adopt(el); const text = el.dataset.tip; if (!text || matchMedia("(hover: none)").matches) return;
    cur = el; tip.textContent = text; tip.classList.add("on");
    const r = el.getBoundingClientRect(), w = tip.offsetWidth, h = tip.offsetHeight;
    const below = r.top < h + 16; let x = r.left + r.width / 2 - w / 2; x = Math.max(8, Math.min(innerWidth - w - 8, x));
    tip.style.left = x + "px"; tip.style.top = (below ? r.bottom + 8 : r.top - h - 8) + "px";
  };
  const hide = () => { clearTimeout(t); cur = null; tip.classList.remove("on"); };
  const find = e => e.target.closest && e.target.closest("[data-tip],[title]");
  document.addEventListener("pointerover", e => { const el = find(e); if (!el || el === cur) return; if (el.hasAttribute("title")) adopt(el); if (!el.dataset.tip) return; clearTimeout(t); t = setTimeout(() => show(el), 380); });
  document.addEventListener("pointerout", e => { const el = find(e); if (el && !el.contains(e.relatedTarget)) hide(); });
  document.addEventListener("focusin", e => { const el = find(e); if (el && e.target.matches(":focus-visible")) show(el); });
  document.addEventListener("focusout", hide); document.addEventListener("pointerdown", hide); addEventListener("scroll", hide, { passive: true });
}
