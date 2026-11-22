import { $, esc, safeUrl, debounce, icon, watchImages } from "./ui.js";
import { Site } from "./api.js";
import { searches } from "./store.js";

export function initSearch(go) {
  const form = $("#find"), input = $("#q"), box = $("#suggest"), toggle = $("#find-toggle");
  let items = [], sel = -1, ctl = null, open = false;

  const setOpen = on => {
    open = on; form.classList.toggle("open", on); toggle.setAttribute("aria-expanded", String(on)); input.tabIndex = on ? 0 : -1;
    if (on) input.focus(); else { hide(); input.blur(); }
  };
  const hide = () => { box.hidden = true; input.setAttribute("aria-expanded", "false"); input.removeAttribute("aria-activedescendant"); sel = -1; };
  const paintSel = () => { [...box.querySelectorAll("[role=option]")].forEach((n, i) => n.setAttribute("aria-selected", String(i === sel))); const n = box.querySelectorAll("[role=option]")[sel]; if (n) { input.setAttribute("aria-activedescendant", n.id); n.scrollIntoView({ block: "nearest" }); } else input.removeAttribute("aria-activedescendant"); };
  const show = html => { box.innerHTML = html; box.hidden = false; input.setAttribute("aria-expanded", "true"); sel = -1; watchImages(box); };

  const recent = () => {
    const l = searches.list(); if (!l.length) return hide();
    items = l.map(q => ({ href: `/search?q=${encodeURIComponent(q)}`, q }));
    show(`<div class="sg-h"><span>Recent searches</span><button type="button" data-clear>Clear</button></div>` +
      l.map((q, i) => `<div class="sg-t" role="option" id="sg${i}" data-i="${i}">${icon("clock-history")}<span>${esc(q)}</span></div>`).join(""));
  };
  const run = debounce(async q => {
    if (ctl) ctl.abort(); ctl = new AbortController();
    show(`<div class="sg-empty"><span class="spin"></span> Searching</div>`);
    try {
      const r = await Site.search(q, {}, ctl.signal);
      items = r.items.slice(0, 6).map(i => ({ href: `/title/${i.id}`, it: i }));
      if (!items.length) return show(`<div class="sg-empty">No titles match "${esc(q)}". Press Enter to search again or request it.</div>`);
      items.push({ href: `/search?q=${encodeURIComponent(q)}`, all: true });
      show(items.map((x, i) => x.all ? `<div class="sg-t" role="option" id="sg${i}" data-i="${i}">${icon("search")}<span>See all results for "${esc(q)}"</span></div>` :
        `<div class="sg" role="option" id="sg${i}" data-i="${i}">${safeUrl(x.it.poster) ? `<img src="${esc(safeUrl(x.it.poster))}" alt="" loading="lazy" referrerpolicy="no-referrer" data-img>` : '<div class="ph"></div>'}<div><div class="t">${esc(x.it.title)}</div><div class="s">${esc([x.it.year, x.it.kind === "series" ? "Series" : "Movie"].filter(Boolean).join(" · "))}</div></div></div>`).join(""));
    } catch (e) { if (e.name !== "AbortError") show(`<div class="sg-empty">${esc(e.message)}</div>`); }
  }, 300);

  toggle.addEventListener("click", () => setOpen(!open || !input.value ? !open : true));
  input.addEventListener("focus", () => { if (!input.value) recent(); });
  input.addEventListener("input", () => { const q = input.value.trim(); if (q.length < 2) { run.cancel(); if (ctl) ctl.abort(); q ? hide() : recent(); } else run(q); });
  input.addEventListener("keydown", e => {
    const n = box.querySelectorAll("[role=option]").length;
    if (e.key === "ArrowDown" && n) { e.preventDefault(); sel = (sel + 1) % n; paintSel(); }
    else if (e.key === "ArrowUp" && n) { e.preventDefault(); sel = (sel - 1 + n) % n; paintSel(); }
    else if (e.key === "Escape") { setOpen(false); toggle.focus(); }
    else if (e.key === "Enter" && sel >= 0 && items[sel]) { e.preventDefault(); const x = items[sel]; setOpen(false); input.value = ""; go(x.href); }
  });
  form.addEventListener("submit", e => { e.preventDefault(); const q = input.value.trim(); if (!q) return; searches.add(q); setOpen(false); go(`/search?q=${encodeURIComponent(q)}`); });
  box.addEventListener("pointerdown", e => e.preventDefault());       // keep focus in the input
  box.addEventListener("click", e => {
    if (e.target.closest("[data-clear]")) { searches.clear(); return hide(); }
    const o = e.target.closest("[role=option]"); if (!o) return; const x = items[+o.dataset.i]; if (!x) return;
    if (x.q) searches.add(x.q); setOpen(false); input.value = ""; go(x.href);
  });
  document.addEventListener("pointerdown", e => { if (open && !form.contains(e.target)) { if (!input.value) setOpen(false); else hide(); } });
  document.addEventListener("keydown", e => {
    if (e.key === "/" && !e.ctrlKey && !e.metaKey && !/^(input|textarea|select)$/i.test(e.target.tagName) && !e.target.isContentEditable && !document.querySelector(".ajp")) { e.preventDefault(); setOpen(true); }
  });
  return { setQuery: q => { input.value = q || ""; }, close: () => setOpen(false) };
}
