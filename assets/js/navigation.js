import { $, $$ } from "./ui.js";

/** Navbar: solid once the page has scrolled (IntersectionObserver, no scroll handler), active tab, mobile menu closing. */
export function initNavigation() {
  const nav = $("#nav"), sentinel = document.createElement("div");
  sentinel.style.cssText = "position:absolute;top:0;left:0;width:1px;height:40px;pointer-events:none";
  document.body.prepend(sentinel);
  new IntersectionObserver(([e]) => nav.classList.toggle("solid", !e.isIntersecting)).observe(sentinel);
}
export function setActiveTab(tab) { $$("[data-tab]").forEach(a => { const on = a.dataset.tab === tab; a.classList.toggle("on", on); on ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current"); }); }
