/* Swiper shelves with cleanup. Falls back to a native scroll-snap row if Swiper is unavailable. */
export function shelf(root, { wide = false } = {}) {
  const el = root.querySelector(".swiper");
  if (!el) return () => { };
  if (!window.Swiper) { root.classList.add("no-swiper"); return () => { }; }
  const sw = new Swiper(el, {
    slidesPerView: "auto", spaceBetween: 14, freeMode: { enabled: true, sticky: false }, watchSlidesProgress: false,
    navigation: { prevEl: root.querySelector(".shelf-nav.prev"), nextEl: root.querySelector(".shelf-nav.next") },
    a11y: { enabled: false }, keyboard: { enabled: false }, resistanceRatio: .6, grabCursor: false, touchStartPreventDefault: false,
  });
  return () => { try { sw.destroy(true, true); } catch { /* already gone */ } };
}
export const shelfHtml = (id, title, slides, { wide = false, more = "" } = {}) => `
  <section class="section reveal" aria-labelledby="h-${id}"><div class="section-head"><h2 id="h-${id}">${title}</h2>${more}</div>
  <div class="shelf${wide ? " wide" : ""}" data-shelf="${id}"><button class="shelf-nav prev" type="button" aria-label="Scroll left"><i class="bi bi-chevron-left"></i></button>
  <div class="swiper"><div class="swiper-wrapper">${slides}</div></div>
  <button class="shelf-nav next" type="button" aria-label="Scroll right"><i class="bi bi-chevron-right"></i></button></div></section>`;
