/* Per-device library: watchlist, watch history / progress, recent searches and preferences. Stored in this browser only. */
const P = "aj:";
const read = (k, d) => { try { const v = localStorage.getItem(P + k); return v === null ? d : JSON.parse(v); } catch { return d; } };
const write = (k, v) => { try { localStorage.setItem(P + k, JSON.stringify(v)); return true; } catch { return false; } };
const bus = new EventTarget();
const fire = k => bus.dispatchEvent(new CustomEvent("change", { detail: k }));
export const onChange = fn => { bus.addEventListener("change", fn); return () => bus.removeEventListener("change", fn); };
const slim = it => ({ id: it.id, title: it.title, year: it.year, kind: it.kind, poster: it.poster || null, backdrop: it.backdrop || null, rating: it.rating || null, genres: (it.genres || []).slice(0, 4) });

export const watchlist = {
  list: () => read("watchlist", []),
  has: id => watchlist.list().some(x => x.id === id),
  toggle(it) {
    const l = watchlist.list(), i = l.findIndex(x => x.id === it.id);
    if (i >= 0) l.splice(i, 1); else l.unshift({ ...slim(it), added: Date.now() });
    write("watchlist", l.slice(0, 300)); fire("watchlist"); return i < 0;
  },
  remove(id) { write("watchlist", watchlist.list().filter(x => x.id !== id)); fire("watchlist"); },
  clear() { write("watchlist", []); fire("watchlist"); },
};

/* history: one entry per title (latest episode/version watched); progress: position per exact item */
export const progress = {
  key: (id, s, e) => `${id}:${s ?? ""}:${e ?? ""}`,
  all: () => read("progress", {}),
  get: key => progress.all()[key] || null,
  save(key, pos, dur, meta) {
    const all = progress.all();
    all[key] = { pos: Math.floor(pos), dur: Math.floor(dur), t: Date.now(), ...(meta ? { meta } : all[key]?.meta ? { meta: all[key].meta } : {}) };
    const keys = Object.keys(all); if (keys.length > 400) keys.sort((a, b) => all[a].t - all[b].t).slice(0, keys.length - 400).forEach(k => delete all[k]);
    write("progress", all);
  },
  finish(key) { const all = progress.all(); if (all[key]) { all[key].done = true; all[key].pos = 0; write("progress", all); fire("progress"); } },
  forget(key) { const all = progress.all(); delete all[key]; write("progress", all); fire("progress"); },
  clear() { write("progress", {}); fire("progress"); },
  /** Items still in progress (between 30 s and 95 %), most recent first, one per title. */
  continueList() {
    const seen = new Set();
    return Object.entries(progress.all()).filter(([, v]) => v.meta && !v.done && v.pos > 30 && v.dur && v.pos / v.dur < .95)
      .sort((a, b) => b[1].t - a[1].t).filter(([, v]) => !seen.has(v.meta.id) && seen.add(v.meta.id)).map(([key, v]) => ({ key, ...v }));
  },
  history() {
    return Object.entries(progress.all()).filter(([, v]) => v.meta).sort((a, b) => b[1].t - a[1].t).map(([key, v]) => ({ key, ...v }));
  },
  forTitle(id) { return progress.history().filter(h => h.meta.id === id); },
};

export const searches = {
  list: () => read("searches", []),
  add(q) { q = q.trim(); if (q.length < 2) return; write("searches", [q, ...searches.list().filter(x => x.toLowerCase() !== q.toLowerCase())].slice(0, 8)); },
  remove(q) { write("searches", searches.list().filter(x => x !== q)); },
  clear() { write("searches", []); },
};

const DEFAULTS = { autonext: true, speed: 1, volume: 1, muted: false, subSize: 100, subColor: "#ffffff", subBg: "dark", motion: "auto", theater: false };
export const prefs = {
  all: () => ({ ...DEFAULTS, ...read("prefs", {}) }),
  get: k => prefs.all()[k],
  set(k, v) { const a = read("prefs", {}); a[k] = v; write("prefs", a); fire("prefs"); },
  reset() { write("prefs", {}); fire("prefs"); },
};
export function clearAll() { try { Object.keys(localStorage).filter(k => k.startsWith(P)).forEach(k => localStorage.removeItem(k)); } catch { /* storage unavailable */ } fire("all"); }
