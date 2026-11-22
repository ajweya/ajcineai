/* GSAP lifecycle. Every view animates inside one gsap.context, which the router reverts on navigation, so timelines and
 * ScrollTriggers never leak or duplicate. Without GSAP (blocked) or with reduced motion, content is simply shown. */
import { prefs } from "./store.js";

export const hasGsap = () => !!window.gsap;
export const reduced = () => prefs.get("motion") === "off" || (prefs.get("motion") === "auto" && matchMedia("(prefers-reduced-motion: reduce)").matches);
export const motionOn = () => hasGsap() && !reduced();
export function syncMotionClass() {
  document.documentElement.classList.toggle("no-motion", reduced());
  document.documentElement.classList.toggle("no-gsap", !hasGsap());
}
let registered = false;
function register() { if (!registered && window.ScrollTrigger) { gsap.registerPlugin(ScrollTrigger); registered = true; } }

export function scope(root) {
  if (!hasGsap()) return { add: fn => fn?.(), revert() { }, ctx: null };
  register();
  const ctx = gsap.context(() => { }, root);
  return { add: fn => ctx.add(fn), revert: () => ctx.revert(), ctx };
}
/** Staggered fade-up for freshly rendered nodes. */
export function staggerIn(nodes, sc, delay = 0) {
  nodes = [...nodes]; if (!nodes.length) return;
  if (!motionOn()) { nodes.forEach(n => { n.style.opacity = 1; n.style.transform = "none"; }); return; }
  sc.add(() => gsap.fromTo(nodes, { opacity: 0, y: 14 }, { opacity: 1, y: 0, duration: .5, ease: "power2.out", stagger: .045, delay, clearProps: "transform" }));
}
/** Reveal each `.reveal` section as it scrolls into view (batched, one observer for all). */
export function revealOnScroll(root, sc, sel = ".reveal") {
  const nodes = [...root.querySelectorAll(sel)];
  if (!nodes.length) return;
  if (!motionOn() || !window.ScrollTrigger) { nodes.forEach(n => { n.style.opacity = 1; n.style.transform = "none"; }); return; }
  sc.add(() => ScrollTrigger.batch(nodes, {
    start: "top 92%", once: true,
    onEnter: b => gsap.to(b, { opacity: 1, y: 0, duration: .6, ease: "power2.out", stagger: .08, clearProps: "transform" }),
  }));
  const t = setTimeout(() => { try { ScrollTrigger.refresh(); } catch { /* page already left */ } }, 900);   // positions shift as artwork and shelves settle
  sc.add(() => () => clearTimeout(t));
}
export function heroIntro(root, sc) {
  const items = [...root.querySelectorAll("[data-intro]")];
  if (!items.length) return null;
  if (!motionOn()) { items.forEach(n => { n.style.opacity = 1; n.style.transform = "none"; }); return null; }
  let tl; sc.add(() => { tl = gsap.timeline({ defaults: { ease: "power3.out" } }).fromTo(items, { opacity: 0, y: 22 }, { opacity: 1, y: 0, duration: .7, stagger: .09, clearProps: "transform" }); });
  return tl;
}
export function pageIn(el) { el.classList.remove("enter"); void el.offsetWidth; if (!reduced()) el.classList.add("enter"); }