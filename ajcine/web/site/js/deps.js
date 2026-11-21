/* Every external dependency of the public site, in one place. Versions are pinned and integrity-checked (SRI);
 * each has a byte-identical bundled copy under /assets/vendor/ that is used when the CDN is blocked or unreachable.
 * Shaka Player is the only dependency without a bundled copy: it is fetched only when a DASH/HLS manifest is played. */
const CDN = "https://cdn.jsdelivr.net/npm";
export const DEPS = {
  bootstrapCss: { type: "css", url: `${CDN}/bootstrap@5.3.3/dist/css/bootstrap.min.css`, sri: "sha384-QWTKZyjpPEjISv5WaRU9OFeRpok6YctnYmDr5pNlyT2bRjXh0JMhjY6hW+ALEwIH", local: "/assets/vendor/bootstrap.min.css" },
  icons: { type: "css", url: `${CDN}/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css`, sri: "sha384-XGjxtQfXaH2tnPFa9x+ruJTuLE3Aa6LhHSWRr1XeTyhezb4abCG4ccI5AkVDxqC+", local: "/assets/vendor/bootstrap-icons/bootstrap-icons.min.css" },
  swiperCss: { type: "css", url: `${CDN}/swiper@11.1.14/swiper-bundle.min.css`, sri: "sha384-IFb872aCXZQb6BKRUHmGn0YHKnFl4ylZjkgVRRyMTEQ5bjo/H6xFUiLJm6opOO66", local: "/assets/vendor/swiper-bundle.min.css" },
  bootstrapJs: { type: "js", url: `${CDN}/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js`, sri: "sha384-YvpcrYf0tY3lHB60NNkmXc5s9fDVZLESaAA55NDzOxhy9GkcIdslK1eN7N6jIeHz", local: "/assets/vendor/bootstrap.bundle.min.js", ready: () => window.bootstrap },
  gsap: { type: "js", url: `${CDN}/gsap@3.12.5/dist/gsap.min.js`, sri: "sha384-g4NTh/Iv5PPU4xPyhEWqPcwtNXOvdaDI8LLnyYfyNZOjKJeYQyjzQ9X5275eBjpt", local: "/assets/vendor/gsap.min.js", ready: () => window.gsap },
  scrollTrigger: { type: "js", url: `${CDN}/gsap@3.12.5/dist/ScrollTrigger.min.js`, sri: "sha384-Z3REaz79l2IaAZqJsSABtTbhjgOUYyV3p90XNnAPCSHg3EMTz1fouunq9WZRtj3d", local: "/assets/vendor/ScrollTrigger.min.js", ready: () => window.ScrollTrigger },
  swiper: { type: "js", url: `${CDN}/swiper@11.1.14/swiper-bundle.min.js`, sri: "sha384-JiF4eFN9V3I2Fry7TzFM3XKdq0P3abiVxpOeHjGz4cwvas+uNg6pFWVmuD6MKXqG", local: "/assets/vendor/swiper-bundle.min.js", ready: () => window.Swiper },
  shaka: { type: "js", url: `${CDN}/shaka-player@4.10.9/dist/shaka-player.compiled.js`, sri: "sha384-+BGVRXAlaphDEZu+pb6rdPW1/4y+vOrPkb0IyHvQaFE5TvBRStqyJgfT27vquXqI", local: null, ready: () => window.shaka },
};

const once = {};
function add(tag, attrs, before = null) {
  return new Promise(res => {
    const el = document.createElement(tag);
    Object.assign(el, attrs);
    el.onload = () => res(true); el.onerror = () => { el.remove(); res(false); };
    if (tag === "link") document.head.insertBefore(el, before); else document.body.appendChild(el);
  });
}
/* Whether a stylesheet really applied is checked by what it does (browsers report a `sheet` even for a failed load). */
const probes = {
  bootstrapCss: () => getComputedStyle(document.documentElement).getPropertyValue("--bs-blue").trim() !== "",
  icons: () => { const i = document.createElement("i"); i.className = "bi bi-play-fill"; i.style.cssText = "position:absolute;visibility:hidden"; document.body.appendChild(i); const c = getComputedStyle(i, "::before").content; i.remove(); return !!c && c !== "none" && c !== "normal"; },
  swiperCss: () => { const w = document.createElement("div"); w.className = "swiper-wrapper"; w.style.cssText = "position:absolute;visibility:hidden"; document.body.appendChild(w); const ok = getComputedStyle(w).display === "flex"; w.remove(); return ok; },
};
async function css(d) {
  const link = document.querySelector(`link[data-dep="${d.name}"]`), ok = probes[d.name] || (() => !!(link && link.sheet));
  if (ok()) return true;
  if (link && !link.sheet) await Promise.race([new Promise(r => { link.addEventListener("load", r, { once: true }); link.addEventListener("error", r, { once: true }); }), new Promise(r => setTimeout(r, 4000))]);
  if (ok()) return true;
  const spot = link ? link.nextSibling : null;
  if (link) link.remove();
  if (!d.local || !(await add("link", { rel: "stylesheet", href: d.local }, spot))) return false;
  return ok();
}
async function js(d) {
  if (d.ready && d.ready()) return true;
  if (await add("script", { src: d.url, integrity: d.sri, crossOrigin: "anonymous", async: false }) && (!d.ready || d.ready())) return true;
  return d.local ? add("script", { src: d.local, async: false }) : false;
}
/** Resolve once the named dependency is usable. Resolves false (never throws) when it could not be loaded. */
export function need(name) {
  const d = DEPS[name]; if (!d) return Promise.resolve(false);
  d.name = name;
  return (once[name] ||= (d.type === "css" ? css(d) : js(d)));
}
export const needAll = names => Promise.all(names.map(need));
