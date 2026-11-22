/* AJ CINE AI public site: router and app shell. Views live in their own modules and are loaded on demand. */
import { $, $$, errorView, stateView } from "./ui.js";
import { initNavigation, setActiveTab } from "./navigation.js";
import { initSearch } from "./search.js";
import { scope, syncMotionClass, pageIn } from "./motion.js";
import { onChange } from "./store.js";
import { Site, ApiError } from "./api.js";
import { initSelects } from "./components/select.js";
import { initTooltips } from "./components/tooltip.js";
import { initMenus } from "./components/menu.js";

const main = $("#main");
const ROUTES = [
  [/^\/$/, () => import("./discovery.js").then(m => m.home), "home"],
  [/^\/(movies|series|anime|browse)$/, () => import("./discovery.js").then(m => m.listing), null],
  [/^\/search$/, () => import("./discovery.js").then(m => m.searchPage), null],
  [/^\/title\/([^/]+)$/, () => import("./details.js").then(m => m.titlePage), null],
  [/^\/watch\/([^/]+)$/, () => import("./watch.js").then(m => m.watchPage), null],
  [/^\/library$/, () => import("./library.js").then(m => m.libraryPage), "library"],
  [/^\/settings$/, () => import("./library.js").then(m => m.settingsPage), "library"],
];
let leave = [], run = 0, search;

export const go = (href, replace = false) => {
  const u = new URL(href, location.origin); if (u.origin !== location.origin) return (location.href = href);
  if (u.pathname + u.search === location.pathname + location.search && !replace) return route(false);
  history[replace ? "replaceState" : "pushState"](null, "", u.pathname + u.search); route(true);
};
const cleanup = () => { leave.splice(0).forEach(fn => { try { fn(); } catch (e) { console.error(e); } }); };

async function route(fresh = true) {
  const id = ++run, live = () => id === run;
  cleanup();
  const path = location.pathname.replace(/\/+$/, "") || "/", query = new URLSearchParams(location.search);
  let hit = null; for (const r of ROUTES) { const m = path.match(r[0]); if (m) { hit = { r, m }; break; } }
  const tab = hit ? (hit.r[2] || path.slice(1)) : "";
  setActiveTab(tab === "browse" || tab === "search" ? "" : tab);
  document.documentElement.classList.toggle("is-watch", /^\/watch\//.test(path));
  if (search) search.setQuery(path === "/search" ? query.get("q") : "");
  if (fresh) window.scrollTo(0, 0);
  document.title = "AJ CINE AI";
  if (!hit) { main.innerHTML = stateView("film", "Page not found", "That page does not exist. Check the address or head back home.", '<a class="aj-btn primary" href="/" data-link>Go home</a>'); return pageIn(main); }
  const sc = scope(main);
  const ctx = {
    root: main, params: hit.m.slice(1), path, query, live, sc, go,
    onLeave: fn => { if (live()) leave.push(fn); else try { fn(); } catch { /* view already replaced */ } },
    title: t => { if (live()) document.title = t ? `${t} · AJ CINE AI` : "AJ CINE AI"; },
  };
  ctx.onLeave(() => sc.revert());
  try {
    const view = await hit.r[1]();
    if (!live()) return;
    await view(ctx);
    if (live()) { pageIn(main); main.focus({ preventScroll: true }); }
  } catch (err) {
    if (!live() || err.name === "AbortError") return;
    const gone = err instanceof ApiError && (err.status === 404 || err.status === 410);
    if (!gone) console.error(err);
    main.innerHTML = gone ? stateView("film", err.status === 410 ? "This link has expired" : "Not available", err.message, '<a class="aj-btn primary" href="/" data-link>Go home</a>') : errorView(err.message);
    pageIn(main);
  }
}

export async function start() {
  syncMotionClass();
  onChange(e => { if (e.detail === "prefs" || e.detail === "all") syncMotionClass(); });
  initNavigation(); initMenus(); initSelects(); initTooltips();
  search = initSearch(go);
  document.addEventListener("click", e => {
    const a = e.target.closest("a[data-link]");
    if (a && !e.defaultPrevented && e.button === 0 && !e.metaKey && !e.ctrlKey && !e.shiftKey && !a.target) { e.preventDefault(); go(a.getAttribute("href")); return; }
    if (e.target.closest("[data-retry]")) route(false);
  });
  addEventListener("popstate", () => route(false));
  try { const cfg = await Site.config(); $$(".brand-text").forEach(n => n.setAttribute("title", cfg.company || "")); } catch { /* the page itself reports errors */ }
  route(true);
}
