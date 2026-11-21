
const memo = new Map();
export class ApiError extends Error { constructor(m, status) { super(m); this.status = status; } }

export async function api(path, body, opts = {}) {
  let r;
  try {
    r = await fetch(path, { method: body ? "POST" : "GET", headers: body ? { "Content-Type": "application/json" } : undefined, body: body ? JSON.stringify(body) : undefined, signal: opts.signal, credentials: "same-origin" });
  } catch (e) { if (e.name === "AbortError") throw e; throw new ApiError("Could not reach the server. Check your connection.", 0); }
  let j = null; try { j = await r.json(); } catch { /* non-JSON error body */ }
  if (!r.ok) throw new ApiError((j && (j.detail?.message || (typeof j.detail === "string" ? j.detail : "") || j.message)) || `Request failed (${r.status})`, r.status);
  return j;
}
/** Short-lived memo for read-only calls shared by several views. */
export function cached(key, ttlMs, fn) {
  const hit = memo.get(key);
  if (hit && Date.now() - hit.t < ttlMs) return hit.p;
  const p = fn().catch(e => { memo.delete(key); throw e; });
  memo.set(key, { t: Date.now(), p }); return p;
}
const qs = o => { const p = new URLSearchParams(); Object.entries(o).forEach(([k, v]) => { if (v !== undefined && v !== null && v !== "") p.set(k, v); }); const s = p.toString(); return s ? "?" + s : ""; };

export const Site = {
  config: () => cached("config", 60000, () => api("/api/public/config")),
  home: () => cached("home", 30000, () => api("/api/public/home")),
  browse: (o, sig) => api("/api/public/browse" + qs(o), null, { signal: sig }),
  browseCached: o => cached("browse:" + qs(o), 60000, () => api("/api/public/browse" + qs(o))),
  search: (q, o = {}, sig) => api("/api/public/search" + qs({ q, ...o }), null, { signal: sig }),
  title: id => cached("title:" + id, 60000, () => api(`/api/public/title/${encodeURIComponent(id)}`)),
  episodes: (id, season) => api(`/api/public/title/${encodeURIComponent(id)}/episodes?season=${season}`),
  options: sel => api("/api/public/options", sel),
  streamLink: sel => api("/api/public/stream-link", sel),
  downloadLink: sel => api("/api/public/download-link", sel),
  watch: token => api("/api/public/watch/" + encodeURIComponent(token)),
  ad: () => api("/api/public/ads"),
  request: b => api("/api/public/requests", b),
};
/** Anime is defined by what the library really holds: Animation titles in Japanese. */
export const ANIME = { genre: "Animation", language: "ja" };
