import { needAll } from "./deps.js";

const view = document.getElementById("main");
await needAll(["bootstrapCss", "icons", "swiperCss"]);
await needAll(["bootstrapJs", "gsap", "scrollTrigger", "swiper"]);   // all optional: the site works (without motion/carousels) if one is missing
try {
  const { start } = await import("./site.js");
  start();
} catch (err) {
  console.error(err);
  view.innerHTML = '<div class="state"><h2>The site could not start</h2><p>Reload the page. If this keeps happening, the administrator should check the server logs.</p></div>';
}
