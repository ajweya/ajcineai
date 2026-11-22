export const $ = (s, r = document) => r.querySelector(s);
export const $$ = (s, r = document) => [...r.querySelectorAll(s)];
export const esc = v => String(v ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
export const safeUrl = u => { if (!u || typeof u !== "string") return ""; try { const x = new URL(u, location.origin); return /^https?:$/.test(x.protocol) ? x.href : ""; } catch { return ""; } };
export const pad = n => String(n).padStart(2, "0");
export const epCode = (s, e) => `S${pad(s)}E${pad(e)}`;
export const minutes = m => !m ? "" : m >= 60 ? `${Math.floor(m / 60)}h ${m % 60 ? m % 60 + "m" : ""}`.trim() : `${m}m`;
export const clock = s => { s = Math.max(0, Math.floor(s || 0)); const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60); return (h ? `${h}:${pad(m)}` : m) + ":" + pad(s % 60); };
export const debounce = (fn, ms) => { let t; const d = (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; d.cancel = () => clearTimeout(t); return d; };
export const icon = (n, cls = "") => `<i class="bi bi-${n} ${cls}" aria-hidden="true"></i>`;

export function toast(text, action) {
  const zone = $("#toasts"); const t = document.createElement("div"); t.className = "toast-msg";
  t.innerHTML = `<span>${esc(text)}</span>${action ? `<button type="button">${esc(action.label)}</button>` : ""}`;
  if (action) t.querySelector("button").onclick = () => { action.run(); t.remove(); };
  zone.appendChild(t); setTimeout(() => t.remove(), action ? 6000 : 2800);
}

/** Poster or landscape card. `opts`: land (use backdrop), progress (0..1), remove (show remove button), tag. */
export function card(it, opts = {}) {
  const img = safeUrl(opts.land ? (it.backdrop || it.poster) : it.poster);
  const sub = [it.year, it.kind === "series" ? "Series" : it.kind === "movie" ? "Movie" : "", opts.sub].filter(Boolean).join(" · ");
  const href = opts.href || `/title/${esc(it.id)}`;
  const q = (it.qualities || []).slice(-1)[0];
  return `<a class="pcard${opts.land ? " land" : ""}" href="${href}" data-link aria-label="${esc(it.title)}${it.year ? ` (${esc(it.year)})` : ""}">
    <div class="art">${img ? `<img src="${esc(img)}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer" data-img data-alt="${esc(it.title)}">` : ""}${img ? "" : `<div class="noposter">${esc(it.title)}</div>`}
      <div class="over">${icon("play-fill")}</div>${opts.tag ? `<span class="tag">${esc(opts.tag)}</span>` : ""}
      ${opts.progress ? `<div class="prog"><i data-w="${Math.round(opts.progress * 100)}"></i></div>` : ""}</div>
    <div class="cap"><div class="t">${esc(it.title)}</div><div class="s"><span>${esc(sub)}</span>${it.rating ? `<span class="rating">${icon("star-fill")}${esc(it.rating)}</span>` : ""}</div></div>
    ${opts.remove ? `<button class="rm" type="button" data-remove="${esc(it.id)}" aria-label="Remove ${esc(it.title)}">${icon("x-lg")}</button>` : ""}</a>`;
}
export const skeletons = (n, land = false) => Array.from({ length: n }, () => `<div class="swiper-slide"><div class="skel ${land ? "land" : "poster"}"></div><div class="skel line"></div><div class="skel line s"></div></div>`).join("");
export const gridSkeletons = n => Array.from({ length: n }, () => `<div><div class="skel poster"></div><div class="skel line"></div><div class="skel line s"></div></div>`).join("");

/** Fade artwork in when it has loaded (and drop it, showing the title card, if it fails). */
/** Widths come from data-w (inline style attributes are blocked by the CSP; CSSOM writes are not). */
export function paintBars(root) { $$("[data-w]", root).forEach(n => { n.style.width = n.dataset.w + "%"; n.removeAttribute("data-w"); }); }
export function watchImages(root) {
  paintBars(root);
  $$("img[data-img]", root).forEach(img => {
    img.removeAttribute("data-img");
    const ok = () => img.classList.add("loaded");
    const bad = () => { const art = img.closest(".art, .d-poster, .sg"); img.remove(); if (art && !art.querySelector(".noposter") && !art.classList.contains("sg")) art.insertAdjacentHTML("afterbegin", `<div class="noposter">${esc(img.dataset.alt || "")}</div>`); };
    if (img.complete && img.naturalWidth) ok(); else { img.addEventListener("load", ok, { once: true }); img.addEventListener("error", bad, { once: true }); }
  });
}
export const stateView = (iconName, title, body, actions = "") =>
  `<div class="state page"><div class="container-fluid aj-gutter">${icon(iconName)}<h2>${esc(title)}</h2><p>${esc(body)}</p>${actions ? `<div class="actions">${actions}</div>` : ""}</div></div>`;
export const errorView = (msg, retry = true) => stateView("exclamation-triangle", "Something went wrong", msg || "Please try again.",
  `${retry ? '<button class="aj-btn primary" data-retry>Try again</button>' : ""}<a class="aj-btn" href="/" data-link>Go home</a>`);
