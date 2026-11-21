/* AJ CINE AI admin console — Developed by AJ APPLICATIONS · Developer: AJWEYA
   Every value from the API is HTML-escaped before it reaches the DOM. */
"use strict";
const $ = (s, r = document) => r.querySelector(s);
const esc = v => String(v ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const LANGS = { en: "English", hi: "Hindi", ml: "Malayalam", ta: "Tamil", te: "Telugu", kn: "Kannada", bn: "Bengali", mr: "Marathi", pa: "Punjabi", gu: "Gujarati", ur: "Urdu", ko: "Korean", ja: "Japanese", zh: "Chinese", es: "Spanish", fr: "French", de: "German", ru: "Russian", ar: "Arabic", th: "Thai", tr: "Turkish", it: "Italian", pt: "Portuguese", fa: "Persian", id: "Indonesian", xx: "Unknown" };
const langs = a => (a || []).map(c => LANGS[c] || c).join(", ") || "—";
const fmtBytes = n => { if (!n) return "—"; const u = ["B", "KB", "MB", "GB", "TB"]; let i = 0; while (n >= 1024 && i < 4) { n /= 1024; i++; } return (i < 2 ? n.toFixed(0) : n.toFixed(2)) + " " + u[i]; };
const fmtDate = s => s ? new Date(s).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : "—";
const r2 = n => typeof n === "number" ? n.toFixed(2) : "—";
const fmtUp = s => `${Math.floor(s / 86400)}d ${Math.floor(s % 86400 / 3600)}h ${Math.floor(s % 3600 / 60)}m`;

let ME = null, timer = null;

async function api(path, { method = "GET", body, form } = {}) {
  const opt = { method, headers: { "X-Requested-With": "ajcine" }, credentials: "same-origin" };
  if (body !== undefined) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
  if (form) opt.body = form;
  const r = await fetch(path, opt);
  if (r.status === 401 && path !== "/api/login") { showLogin(); throw new Error("Signed out"); }
  let data = null; try { data = await r.json(); } catch { /* no body */ }
  if (!r.ok) { const d = data && data.detail; throw new Error(typeof d === "string" ? d : Array.isArray(d) ? d.map(x => x.msg).join("; ") : "Request failed (" + r.status + ")"); }
  return data;
}
function toast(msg, kind = "") { const t = document.createElement("div"); t.className = "toast " + kind; t.textContent = msg; $("#toasts").append(t); setTimeout(() => t.remove(), 4500); }
async function act(fn, ok) { try { const r = await fn(); if (ok) toast(ok, "ok"); return r; } catch (e) { toast(e.message, "bad"); return undefined; } }

/* ---------- dialog ---------- */
function dialog(title, bodyHtml, { submit = "Save", onSubmit, danger = false } = {}) {
  const d = $("#dlg"), f = $("#dlg-form");
  f.innerHTML = `<h3>${esc(title)}</h3><div class="gap">${bodyHtml}</div><div class="row end" id="dlg-actions"><button class="btn" value="cancel" type="button" id="dlg-cancel">Close</button>${onSubmit ? `<button class="btn ${danger ? "danger" : "primary"}" type="submit">${esc(submit)}</button>` : ""}</div>`;
  $("#dlg-cancel").onclick = () => d.close();
  f.onsubmit = async e => { e.preventDefault(); if (!onSubmit) return; const data = Object.fromEntries(new FormData(f).entries()); const r = await onSubmit(data); if (r !== false) d.close(); };
  d.showModal();
}

/* ---------- navigation ---------- */
const PAGES = [
  { id: "overview", label: "Overview", perm: "view", render: pgOverview },
  { id: "media", label: "Media", perm: "view", render: pgMedia },
  { id: "review", label: "Review queue", perm: "review", render: pgReview, badge: "review_queue" },
  { id: "ai", label: "AI", perm: "view", render: pgAI },
  { id: "tmdb", label: "TMDB", perm: "view", render: pgTmdb },
  { id: "analytics", label: "Analytics", perm: "view", render: pgAnalytics },
  { id: "requests", label: "Requests", perm: "view", render: pgRequests },
  { id: "groups", label: "Groups", perm: "broadcast", render: pgGroups },
  { id: "broadcast", label: "Broadcast", perm: "broadcast", render: pgBroadcast },
  { id: "ads", label: "Ads", perm: "ads", render: pgAds },
  { id: "indexing", label: "Indexing", perm: "view", render: pgIndexing },
  { id: "users", label: "Users", perm: "users", render: pgUsers },
  { id: "system", label: "System", perm: "view", render: pgSystem },
  { id: "team", label: "Administrators", perm: "admins", render: pgTeam },
];
const RANK = { viewer: 0, moderator: 1, admin: 2, owner: 3 };
const NEED = { view: "viewer", review: "moderator", media_edit: "moderator", indexing: "admin", sources: "admin", ai: "admin", tmdb: "admin", users: "admin", settings: "admin", broadcast: "admin", ads: "admin", cache: "admin", backups: "owner", admins: "owner" };
const can = p => ME && RANK[ME.role] >= RANK[NEED[p] || "owner"];

function route() { return (location.hash.replace("#/", "").split("/")[0]) || "overview"; }
async function render() {
  clearInterval(timer);
  const id = route(), page = PAGES.find(p => p.id === id && can(p.perm)) || PAGES[0];
  document.querySelectorAll("#nav a").forEach(a => a.classList.toggle("on", a.dataset.id === page.id));
  $("#page-title").textContent = page.label;
  document.title = page.label + " · AJ CINE AI";
  $("#side").classList.remove("open");
  const v = $("#view"); v.innerHTML = '<div class="empty">Loading…</div>';
  try { await page.render(v, location.hash.split("/").slice(2)); } catch (e) { if (e.message !== "Signed out") v.innerHTML = `<div class="card error">${esc(e.message)}</div>`; }
  refreshBadges();
}
async function refreshBadges() {
  try {
    const s = await api("/api/stats");
    const a = document.querySelector('#nav a[data-id="review"] .count'); if (a) a.textContent = s.review_queue || "";
    if (a) a.classList.toggle("hidden", !s.review_queue);
    const ts = $("#top-status"); const bad = s.jobs_failed > 0;
    ts.className = "pill " + (bad ? "bad" : "ok"); ts.textContent = bad ? `${s.jobs_failed} failed job(s)` : "All systems normal";
  } catch { /* ignore */ }
}
function buildNav() {
  $("#nav").innerHTML = PAGES.filter(p => can(p.perm)).map(p => `<a href="#/${p.id}" data-id="${p.id}"><span>${esc(p.label)}</span>${p.badge ? '<span class="count hidden"></span>' : ""}</a>`).join("");
}
function showLogin() { ME = null; $("#shell").classList.add("hidden"); $("#login").classList.remove("hidden"); }
async function boot() {
  try { ME = await api("/api/me"); } catch { return showLogin(); }
  $("#login").classList.add("hidden"); $("#shell").classList.remove("hidden");
  $("#who").textContent = `${ME.username} · ${ME.role}`; $("#ver").textContent = "v" + ME.version;
  buildNav(); render();
}
window.addEventListener("hashchange", () => ME && render());
$("#menu").onclick = () => $("#side").classList.toggle("open");
$("#logout").onclick = async () => { await act(() => api("/api/logout", { method: "POST" })); showLogin(); };
$("#login-form").onsubmit = async e => {
  e.preventDefault(); const f = new FormData(e.target); $("#login-error").textContent = "";
  try { await api("/api/login", { method: "POST", body: { username: f.get("username"), password: f.get("password") } }); e.target.reset(); boot(); }
  catch (err) { $("#login-error").textContent = err.message; }
};

/* ---------- helpers ---------- */
const stat = (n, l) => `<div class="card stat"><div class="n">${esc(n)}</div><div class="l">${esc(l)}</div></div>`;
const pill = (t, k = "") => `<span class="pill ${k}">${esc(t)}</span>`;
const table = (heads, rows, empty = "Nothing here yet.") => rows.length ? `<div class="table-wrap"><table><thead><tr>${heads.map(h => `<th>${esc(h)}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table></div>` : `<div class="card empty">${esc(empty)}</div>`;
const poster = (t, cls = "thumb") => t.poster ? `<img class="${cls}" src="${t.poster.startsWith("https://") ? esc(t.poster) : "/posters/" + encodeURIComponent(t.poster)}" referrerpolicy="no-referrer" alt="" loading="lazy">` : `<div class="${cls} ph">No poster</div>`;
const statusPill = s => pill(s, { identified: "ok", done: "ok", running: "info", queued: "info", pending_review: "warn", paused: "warn", failed: "bad", unidentified: "bad", rejected: "bad", cancelled: "" }[s] || "");

/* ---------- overview ---------- */
async function pgOverview(v) {
  const draw = async () => {
    const s = await api("/api/stats"), ai = s.ai;
    v.innerHTML = `
    <div class="grid g4">${stat(s.files, "Indexed files")}${stat(s.movies, "Movies")}${stat(s.series, "TV series")}${stat(s.seasons, "Seasons")}${stat(s.episodes, "Episodes")}${stat(s.users, "Users")}${stat(s.searches, "Searches")}${stat(s.deliveries, "Deliveries")}</div>
    <div class="section grid g2">
      <div class="card"><h3>Catalog health</h3><dl class="kv">
        <dt>Identified</dt><dd>${s.files_identified}</dd><dt>In review queue</dt><dd>${s.review_queue} ${s.review_queue ? `<a href="#/review">open</a>` : ""}</dd>
        <dt>No title match (also queued)</dt><dd>${s.files_unidentified}</dd><dt>Source missing</dt><dd>${s.files_missing}</dd></dl></div>
      <div class="card"><h3>Services</h3><dl class="kv">
        <dt>Indexing</dt><dd>${s.jobs_running ? pill(s.jobs_running + " running", "info") : pill("idle")} ${s.jobs_failed ? pill(s.jobs_failed + " failed", "bad") : ""}</dd>
        <dt>AI</dt><dd>${ai.effective_enabled ? pill("enabled", "ok") : pill("off", "warn")} <span class="muted small">${esc((ai.models[0] || {}).id || "no model")}</span></dd>
        <dt>TMDB</dt><dd>${pill(s.tmdb.state, s.tmdb.state === "ready" ? "ok" : "bad")}</dd>
        <dt>Cache</dt><dd>${pill(s.cache_backend, s.cache_backend === "redis" ? "ok" : "warn")}</dd></dl></div>
    </div>`;
  };
  await draw(); timer = setInterval(() => draw().catch(() => { }), 15000);
}

/* ---------- media ---------- */
let mediaFilter = { kind: "", q: "", page: 1 };
async function pgMedia(v, args) {
  if (args[0]) return mediaDetail(v, decodeURIComponent(args[0]));
  const qs = new URLSearchParams({ page: mediaFilter.page, ...(mediaFilter.kind && { kind: mediaFilter.kind }), ...(mediaFilter.q && { q: mediaFilter.q }) });
  const r = await api("/api/media?" + qs);
  const rows = r.items.map(t => `<tr class="click" data-go="${esc(t._id)}"><td>${poster(t)}</td><td class="mono">${esc(t._id)}</td><td class="name"><strong>${esc(t.title)}</strong></td><td>${pill(t.kind === "series" ? "Series" : "Movie")}</td><td>${esc(t.year || "—")}</td><td>${t.file_count || 0}</td><td>${esc(langs(t.languages))}</td><td>${esc((t.qualities || []).join(" · ") || "—")}</td><td>${(t.rating || t.imdb_rating) ? "★ " + esc(t.rating || t.imdb_rating) : "—"}</td></tr>`);
  v.innerHTML = `<div class="row"><input class="grow" id="mq" placeholder="Search titles" value="${esc(mediaFilter.q)}"><select id="mk"><option value="">All types</option><option value="movie">Movies</option><option value="series">Series</option></select></div>
  <div class="section">${table(["", "Collection ID", "Collection", "Type", "Year", "Files", "Languages", "Qualities", "Rating"], rows, "No titles match. Titles appear after files are indexed and identified.")}</div>
  <div class="row end section"><span class="muted small">${r.total} titles</span><button class="btn small" id="pv" ${r.page <= 1 ? "disabled" : ""}>Previous</button><button class="btn small" id="nx" ${r.page * r.per_page >= r.total ? "disabled" : ""}>Next</button></div>`;
  $("#mk").value = mediaFilter.kind;
  let t; $("#mq").oninput = e => { clearTimeout(t); t = setTimeout(() => { mediaFilter.q = e.target.value; mediaFilter.page = 1; render(); }, 350); };
  $("#mk").onchange = e => { mediaFilter.kind = e.target.value; mediaFilter.page = 1; render(); };
  $("#pv").onclick = () => { mediaFilter.page--; render(); }; $("#nx").onclick = () => { mediaFilter.page++; render(); };
  v.querySelectorAll("[data-go]").forEach(tr => tr.onclick = () => location.hash = "#/media/" + encodeURIComponent(tr.dataset.go));
}
async function mediaDetail(v, id) {
  const d = await api("/api/media/" + encodeURIComponent(id)), t = d.title, edit = can("media_edit");
  const fileRows = d.files.map(f => `<tr><td class="name" title="${esc(f.file_name)}">${esc(f.file_name || f._id)}</td><td>${f.season != null ? "S" + String(f.season).padStart(2, "0") + (f.episode != null ? "E" + String(f.episode).padStart(2, "0") : " pack") : "—"}</td><td>${esc(f.quality || "—")}</td><td>${esc(langs(f.languages))}${f.subtitles && f.subtitles.length ? `<div class="muted small">Subs: ${esc(langs(f.subtitles))}</div>` : ""}</td><td>${esc([f.video_codec, f.audio_codec].filter(Boolean).join(" ") || "—")}</td><td>${fmtBytes(f.size)}</td><td>${f.missing ? pill("source missing", "bad") : statusPill(f.status)}</td>${edit ? `<td><button class="btn small" data-edit-file="${esc(f._id)}">Edit</button></td>` : "<td></td>"}</tr>`);
  const miss = Object.entries(d.missing || {}).map(([s, e]) => `Season ${s}: ${e.map(n => "E" + String(n).padStart(2, "0")).join(", ")}`);
  v.innerHTML = `<p><a href="#/media">← All media</a></p>
  <div class="card detail"><div>${poster(t, "poster")}${edit ? `<div class="gap"><button class="btn small" id="pu">Set poster URL</button><button class="btn small" id="pr">Fetch from TMDB</button>${t.poster ? '<button class="btn small danger" id="pd">Remove</button>' : ""}</div>` : ""}</div>
  <div><div class="row"><h2 class="grow">${esc(t.title)} ${t.year ? `<span class="muted">(${esc(t.year)})</span>` : ""}</h2>${edit ? '<button class="btn" id="te">Edit details</button>' : ""}</div>
  <dl class="kv section"><dt>Type</dt><dd>${esc(t.kind)}</dd><dt>ID</dt><dd class="mono">${esc(t._id)} ${t._id.startsWith("tt") ? `<a href="https://www.imdb.com/title/${esc(t._id)}/" target="_blank" rel="noopener noreferrer">IMDb ↗</a>` : pill("local title")}</dd>
  <dt>Original title</dt><dd>${esc(t.original_title || "—")}</dd><dt>Alternative titles</dt><dd>${esc((t.alt_titles || []).slice(0, 8).join(" · ") || "—")}</dd>
  <dt>Genres</dt><dd>${esc((t.genres || []).join(", ") || "—")}</dd><dt>Rating</dt><dd>${(t.rating || t.imdb_rating) ? "★ " + esc(t.rating || t.imdb_rating) : "—"}</dd><dt>Runtime</dt><dd>${t.runtime ? esc(t.runtime) + " min" : "—"}</dd>
  <dt>Languages</dt><dd>${esc(langs(t.languages))}</dd><dt>Qualities</dt><dd>${esc((t.qualities || []).join(" · ") || "—")}</dd><dt>Manually corrected</dt><dd>${t.manual ? "Yes" : "No"}</dd>
  <dt>Overview</dt><dd>${esc(t.overview || "—")}</dd></dl>
  ${edit ? `<div class="row section"><button class="btn" id="ti">Change IMDb ID</button><button class="btn" id="tm">Merge into another title</button></div>` : ""}</div></div>
  ${miss.length ? `<div class="card section"><h3>Missing episodes</h3>${miss.map(m => `<div>${esc(m)}</div>`).join("")}</div>` : ""}
  <div class="section"><h3>Files in this collection (${d.files.length}) <span class="muted small">per-file details</span></h3>${table(["File", "Season/Episode", "Resolution", "Audio", "Codec", "Size", "Status", ""], fileRows, "No files.")}</div>`;
  if (!edit) return;
  $("#te").onclick = () => dialog("Edit details", `<label>Title<input name="title" value="${esc(t.title)}" maxlength="200" required></label><label>Year<input name="year" type="number" min="1888" max="2100" value="${esc(t.year || "")}"></label><label>Original title<input name="original_title" value="${esc(t.original_title || "")}" maxlength="200"></label><label>Genres (comma separated)<input name="genres" value="${esc((t.genres || []).join(", "))}"></label><label>Type<select name="kind"><option value="movie" ${t.kind === "movie" ? "selected" : ""}>Movie</option><option value="series" ${t.kind === "series" ? "selected" : ""}>TV series</option></select></label><label>Overview<textarea name="overview" maxlength="2000">${esc(t.overview || "")}</textarea></label>`, {
    onSubmit: async f => { const ok = await act(() => api("/api/media/" + encodeURIComponent(id), { method: "PATCH", body: { title: f.title, year: f.year ? +f.year : null, original_title: f.original_title || null, genres: f.genres.split(",").map(s => s.trim()).filter(Boolean), kind: f.kind, overview: f.overview || null } }), "Saved"); if (ok) render(); else return false; }
  });
  $("#ti").onclick = () => imdbPicker("Change IMDb ID", async imdb => { const r = await act(() => api(`/api/media/${encodeURIComponent(id)}/imdb`, { method: "POST", body: { imdb_id: imdb } }), "Re-linked"); if (r) location.hash = "#/media/" + r.title_id; });
  $("#tm").onclick = () => dialog("Merge into another title", `<p class="muted small">All files of this title move to the target and this record is removed.</p><label>Target ID (tt… or loc_…)<input name="target" required pattern="(tt\\d{7,10}|loc_[0-9a-f]{10})"></label>`, { submit: "Merge", danger: true, onSubmit: async f => { const r = await act(() => api("/api/media/merge", { method: "POST", body: { source: id, target: f.target.trim() } }), "Merged"); if (r) location.hash = "#/media/" + f.target.trim(); else return false; } });
  $("#pu").onclick = () => dialog("Poster URL", `<label>Direct image link (https)<input name="url" type="url" required maxlength="500" placeholder="https://image.tmdb.org/t/p/w500/..."></label><p class="muted small">The image is not copied to this server; only the link is stored.</p>`, { onSubmit: async f => { const r = await act(() => api(`/api/media/${encodeURIComponent(id)}/poster`, { method: "PUT", body: { url: f.url } }), "Poster updated"); if (r) render(); else return false; } });
  $("#pr").onclick = async () => { const r = await act(() => api(`/api/media/${encodeURIComponent(id)}/poster/refresh`, { method: "POST" })); if (r) { toast(r.result === "set" ? "Poster fetched from TMDB" : "TMDB has no confident match", r.result === "set" ? "ok" : ""); render(); } };
  const pd = $("#pd"); if (pd) pd.onclick = async () => { if (await act(() => api(`/api/media/${encodeURIComponent(id)}/poster`, { method: "DELETE" }), "Poster removed")) render(); };
  v.querySelectorAll("[data-edit-file]").forEach(b => b.onclick = () => { const f = d.files.find(x => x._id === b.dataset.editFile); fileEditor(f, render); });
}
function fileEditor(f, done) {
  dialog("Edit file", `<p class="mono muted small">${esc(f.file_name)}</p><div class="row"><label class="grow">Season<input name="season" type="number" min="0" max="99" value="${esc(f.season ?? "")}"></label><label class="grow">Episode<input name="episode" type="number" min="0" max="9999" value="${esc(f.episode ?? "")}"></label></div><label>Episode title<input name="episode_title" maxlength="200" value="${esc(f.episode_title || "")}"></label><div class="row"><label class="grow">Quality (e.g. 1080p, 2160p, SD)<input name="quality" value="${esc(f.quality || "")}"></label><label class="grow">Languages (names or codes, comma separated)<input name="languages" value="${esc((f.languages || []).join(", "))}"></label></div>`, {
    onSubmit: async x => { const body = { season: x.season === "" ? null : +x.season, episode: x.episode === "" ? null : +x.episode, episode_title: x.episode_title || null, quality: x.quality || null, languages: x.languages.split(",").map(s => s.trim()).filter(Boolean) }; if (!body.languages.length) delete body.languages; const ok = await act(() => api("/api/files/" + encodeURIComponent(f._id), { method: "PATCH", body }), "File updated — saved as a permanent correction"); if (ok) done(); else return false; }
  });
}
function imdbPicker(title, onPick) {
  dialog(title, `<div class="row"><input id="ipq" class="grow" placeholder="Search by title"><input id="ipy" type="number" placeholder="Year" min="1888" max="2100" class="small-input"><button class="btn" type="button" id="ips">Search</button></div><div id="ipr"></div><label>…or enter an IMDb ID<input id="ipid" placeholder="tt1375666"></label><button class="btn" type="button" id="ipuse">Use this ID</button>`, {});
  const run = async () => { const q = $("#ipq").value.trim(); if (!q) return; const y = $("#ipy").value; const r = await act(() => api("/api/titles/search?q=" + encodeURIComponent(q) + (y ? "&year=" + y : ""))); if (!r) return; $("#ipr").innerHTML = r.length ? table(["Title", "Year", "Type", "Rating", ""], r.map(c => `<tr><td>${esc(c.title)}<div class="mono muted">${esc(c.imdb_id)}</div></td><td>${esc(c.year || "—")}</td><td>${esc(c.kind)}</td><td>${c.rating ? "★ " + esc(c.rating) : "—"}</td><td><button class="btn small" type="button" data-pick="${esc(c.imdb_id)}">Select</button></td></tr>`)) : '<p class="muted">No matches.</p>'; $("#ipr").querySelectorAll("[data-pick]").forEach(b => b.onclick = () => { $("#dlg").close(); onPick(b.dataset.pick); }); };
  $("#ips").onclick = run; $("#ipq").onkeydown = e => { if (e.key === "Enter") { e.preventDefault(); run(); } };
  $("#ipuse").onclick = () => { const id = $("#ipid").value.trim(); if (/^tt\d{7,10}$/.test(id)) { $("#dlg").close(); onPick(id); } else toast("Enter a valid IMDb ID", "bad"); };
}

/* ---------- review ---------- */
async function pgReview(v, args) {
  if (args[0]) return reviewItem(v, decodeURIComponent(args[0]));
  const tab = sessionStorage.getItem("rtab") || "pending";
  v.innerHTML = `<div class="tabs"><button data-t="pending" class="${tab === "pending" ? "on" : ""}">Pending</button><button data-t="history" class="${tab === "history" ? "on" : ""}">Correction history</button></div><div id="rbody"></div>`;
  v.querySelectorAll("[data-t]").forEach(b => b.onclick = () => { sessionStorage.setItem("rtab", b.dataset.t); render(); });
  const body = $("#rbody");
  if (tab === "history") {
    const h = await api("/api/corrections?limit=150");
    body.innerHTML = table(["When", "By", "Action", "Detail", ""], h.map(c => `<tr><td>${fmtDate(c.created_at)}</td><td>${esc((c.actor || {}).username || (c.actor || {}).id || "—")}</td><td>${pill(c.type)}</td><td class="name">${esc([c.title, c.year, c.imdb_id, c.title_key].filter(Boolean).join(" · "))}</td><td>${c.active === false ? pill("undone") : `<button class="btn small" data-undo="${esc(c._id)}">Undo</button>`}</td></tr>`), "No corrections yet.");
    body.querySelectorAll("[data-undo]").forEach(b => b.onclick = async () => { if (await act(() => api(`/api/corrections/${b.dataset.undo}/undo`, { method: "POST" }), "Correction deactivated for future indexing")) render(); });
    return;
  }
  const r = await api("/api/review?page=" + (+sessionStorage.getItem("rpage") || 1));
  body.innerHTML = table(["File", "Parsed", "Best candidate", "Reason", ""], r.items.map(i => { const p = i.parsed || {}, c = i.top; return `<tr class="click" data-go="${esc(i.file_id)}"><td class="name" title="${esc(i.file_name)}">${esc(i.file_name)}</td><td>${esc(p.title || "—")} ${p.year ? `(${esc(p.year)})` : ""}</td><td>${c ? `${esc(c.title)} (${esc(c.year || "?")}) ${pill((c.score || 0).toFixed(2), c.score >= .8 ? "ok" : "warn")}` : "—"}</td><td class="muted small">${esc(i.reason || "")}${i.ai_used ? " " + pill("AI") : ""}</td><td><button class="btn small">Review</button></td></tr>`; }), "The review queue is empty. Everything indexed so far is verified.")
    + `<p class="muted small">${r.total} pending</p>`;
  body.querySelectorAll("[data-go]").forEach(tr => tr.onclick = () => location.hash = "#/review/" + encodeURIComponent(tr.dataset.go));
}
async function reviewItem(v, fid) {
  const d = await api("/api/review/" + encodeURIComponent(fid)), f = d.file, p = d.parsed || {}, ai = d.ai;
  v.innerHTML = `<p><a href="#/review">← Review queue</a></p>
  <div class="card"><h3>File</h3><p class="mono">${esc(f.file_name)}</p><dl class="kv"><dt>Size</dt><dd>${fmtBytes(f.size)}</dd><dt>Caption</dt><dd>${esc(f.caption || "—")}</dd><dt>Status</dt><dd>${statusPill(f.status)}</dd><dt>Why it is here</dt><dd>${esc(d.reason || "—")}</dd></dl></div>
  <div class="grid g2 section"><div class="card"><h3>Parsed from filename</h3><dl class="kv"><dt>Title</dt><dd>${esc(p.title || "—")}</dd><dt>Year</dt><dd>${esc(p.year || "—")}</dd><dt>Type</dt><dd>${esc(p.content_type || "—")}</dd><dt>Season / Episode</dt><dd>${esc(p.season ?? "—")} / ${esc(p.episode ?? "—")}</dd><dt>Languages</dt><dd>${esc(langs(p.languages))}</dd><dt>Quality</dt><dd>${esc(p.quality || "—")}</dd></dl></div>
  <div class="card"><h3>AI suggestion</h3>${ai ? `<dl class="kv"><dt>Title</dt><dd>${esc(ai.title)}</dd><dt>Year</dt><dd>${esc(ai.year || "—")}</dd><dt>Type</dt><dd>${esc(ai.content_type || "—")}</dd><dt>Languages</dt><dd>${esc(langs(ai.languages))}</dd><dt>Confidence</dt><dd>${esc((ai.confidence ?? 0).toFixed(2))}</dd><dt>Model</dt><dd class="mono">${esc(ai.model || "—")}</dd></dl><p class="muted small">AI text only guides the IMDb search. It is never approved on its own.</p>` : '<p class="muted">No AI result for this file.</p>'}</div></div>
  <div class="section"><h3>Candidates</h3><div id="cands"></div></div>
  <div class="row section"><button class="btn" id="rs">Search titles manually</button><button class="btn" id="rl">Create local title</button><button class="btn" id="re">Edit file fields</button><button class="btn danger" id="rx">Reject</button></div>`;
  const scopeSel = '<label>Apply to<select name="scope"><option value="group">All files with the same parsed title</option><option value="file">Only this file</option></select></label>';
  const list = c => table(["Title", "Year", "Type", "Rating", "Match", ""], c.map(x => `<tr><td>${esc(x.title)}<div class="mono muted">${esc(x.imdb_id)}</div></td><td>${esc(x.year || "—")}</td><td>${esc(x.kind)}</td><td>${x.rating ? "★ " + esc(x.rating) : "—"}</td><td>${pill((x.score || 0).toFixed(2), x.score >= .8 ? "ok" : "warn")}<div class="muted small">title ${r2((x.reasons || {}).title)} · year ${r2((x.reasons || {}).year)}</div></td><td><button class="btn small primary" data-ap="${esc(x.imdb_id)}">Approve</button></td></tr>`), "No candidates.");
  const bind = root => root.querySelectorAll("[data-ap]").forEach(b => b.onclick = () => dialog("Approve match", `<p>Link to <strong class="mono">${esc(b.dataset.ap)}</strong>.</p>${scopeSel}`, { submit: "Approve", onSubmit: async f2 => { const r = await act(() => api(`/api/review/${encodeURIComponent(fid)}/approve`, { method: "POST", body: { imdb_id: b.dataset.ap, scope: f2.scope } }), "Approved"); if (r) location.hash = "#/review"; else return false; } }));
  $("#cands").innerHTML = list(d.candidates || []); bind($("#cands"));
  $("#rs").onclick = () => imdbPicker("Search titles", imdb => dialog("Approve match", `<p>Link to <strong class="mono">${esc(imdb)}</strong>.</p>${scopeSel}`, { submit: "Approve", onSubmit: async f2 => { const r = await act(() => api(`/api/review/${encodeURIComponent(fid)}/approve`, { method: "POST", body: { imdb_id: imdb, scope: f2.scope } }), "Approved"); if (r) location.hash = "#/review"; else return false; } }));
  $("#rl").onclick = () => dialog("Create local title", `<p class="muted small">Use only when TMDB has no record. It is clearly marked as a local title and has no TMDB data.</p><label>Title<input name="title" required maxlength="200" value="${esc(p.title || "")}"></label><div class="row"><label class="grow">Year<input name="year" type="number" min="1888" max="2100" value="${esc(p.year || "")}"></label><label class="grow">Type<select name="kind"><option value="movie">Movie</option><option value="series">TV series</option></select></label></div>${scopeSel}`, { onSubmit: async x => { const r = await act(() => api(`/api/review/${encodeURIComponent(fid)}/local`, { method: "POST", body: { title: x.title, year: x.year ? +x.year : null, kind: x.kind, scope: x.scope } }), "Created"); if (r) location.hash = "#/review"; else return false; } });
  $("#re").onclick = () => fileEditor({ ...f, season: p.season, episode: p.episode, quality: p.quality, languages: p.languages }, render);
  $("#rx").onclick = () => dialog("Reject", `<p>Remove this file from the review queue. It will not be searchable or deliverable.</p>${scopeSel}`, { submit: "Reject", danger: true, onSubmit: async x => { const r = await act(() => api(`/api/review/${encodeURIComponent(fid)}/reject`, { method: "POST", body: { scope: x.scope } }), "Rejected"); if (r) location.hash = "#/review"; else return false; } });
}

/* ---------- AI ---------- */
async function pgAI(v) {
  const [s, usage, q] = await Promise.all([api("/api/ai/status"), api("/api/ai/usage"), api("/api/ai/queue")]), ed = can("ai");
  const t = s.today || {};
  v.innerHTML = `<div class="grid g4">${stat(t.requests || 0, "Requests today")}${stat(t.success || 0, "Successful")}${stat(t.errors || 0, "Errors")}${stat(t.cache_hits || 0, "Cache hits")}${stat(t.rate_limited || 0, "Rate limited (429)")}${stat(q.pending, "Awaiting review")}</div>
  <div class="grid g2 section"><div class="card"><h3>OpenRouter (free models only)</h3><dl class="kv"><dt>API key</dt><dd>${s.key_configured ? pill("configured", "ok") : pill("missing", "bad")}</dd><dt>AI</dt><dd>${s.enabled ? pill("enabled", "ok") : pill("disabled", "warn")}</dd><dt>Catalogue</dt><dd>${s.catalogue_reachable ? pill("reachable", "ok") : pill("unreachable — :free suffix only", "warn")}</dd><dt>Daily limit</dt><dd>${s.daily_limit_reached ? pill("reached", "bad") : pill("ok", "ok")}</dd></dl>
  ${ed ? `<div class="row section"><button class="btn" id="aitog">${s.enabled ? "Disable AI" : "Enable AI"}</button><button class="btn" id="aitest">Test connection</button></div><p class="muted small">Disabling AI never affects ordinary search or indexing.</p>` : ""}</div>
  <div class="card"><h3>Models</h3>${table(["Model", "Role", "Status"], s.models.map(m => `<tr><td class="mono">${esc(m.id)}</td><td>${esc(m.role)}</td><td>${m.verified_free ? pill("verified free", "ok") : pill("NOT FREE — blocked", "bad")} ${m.circuit_open ? pill("cooling down", "warn") : ""}</td></tr>`), "No model configured.")}
  ${ed ? '<div class="row section"><button class="btn" id="aimodel">Change models</button></div>' : ""}</div></div>
  <div class="section"><h3>Usage by day and model</h3>${table(["Day", "Model", "Requests", "OK", "Errors", "429", "Fallbacks", "Tokens"], usage.map(u => `<tr><td>${esc(u.day)}</td><td class="mono">${esc(u.model)}</td><td>${u.requests || 0}</td><td>${u.success || 0}</td><td>${u.errors || 0}</td><td>${u.rate_limited || 0}</td><td>${u.fallbacks || 0}</td><td>${u.tokens || 0}</td></tr>`), "No AI requests yet.")}</div>`;
  if (!ed) return;
  $("#aitog").onclick = async () => { if (await act(() => api("/api/ai/config", { method: "POST", body: { enabled: !s.enabled } }), "Updated")) render(); };
  $("#aitest").onclick = async () => { const r = await act(() => api("/api/ai/test", { method: "POST" })); if (r) toast(r.ok ? "AI reachable via " + r.model : "Test failed: " + r.error, r.ok ? "ok" : "bad"); };
  $("#aimodel").onclick = async () => {
    const m = await act(() => api("/api/ai/models")); const list = (m && m.models || []).map(x => `<option value="${esc(x)}">`).join("");
    dialog("Free models", `<datalist id="fm">${list}</datalist><label>Primary model<input name="model" list="fm" value="${esc((s.models.find(x => x.role === "primary") || {}).id || "")}" placeholder="provider/model:free"></label><label>Fallbacks (comma separated, in order)<input name="fb" list="fm" value="${esc(s.models.filter(x => x.role === "fallback").map(x => x.id).join(", "))}"></label><p class="muted small">Only models verified as free are accepted. ${m ? m.models.length + " free models available." : ""}</p>`, { onSubmit: async f => { const ok = await act(() => api("/api/ai/config", { method: "POST", body: { model: f.model.trim(), fallbacks: f.fb.split(",").map(x => x.trim()).filter(Boolean) } }), "Models saved"); if (ok) render(); else return false; } });
  };
}

/* ---------- TMDB ---------- */
async function pgTmdb(v) {
  const s = await api("/api/tmdb/status"), ed = can("tmdb"); let h = null;
  if (ed && s.configured) { try { h = await api("/api/tmdb/health"); } catch { } }
  v.innerHTML = `<div class="grid g2"><div class="card"><h3>Title lookup</h3><dl class="kv"><dt>Configured</dt><dd>${s.configured ? pill("yes", "ok") : pill("no", "bad")}</dd><dt>Sign-in</dt><dd>${esc(s.auth || "—")}</dd><dt>Live check</dt><dd>${h ? (h.ok ? pill("working", "ok") : pill("failing", "bad")) : "—"}</dd><dt>Latency</dt><dd>${h && h.latency_ms != null ? h.latency_ms + " ms" : "—"}</dd>${h && h.error ? `<dt>Problem</dt><dd class="error">${esc(h.error)}</dd>` : ""}<dt>Cached lookups</dt><dd>${esc(s.cached_lookups)}</dd></dl>
  ${ed ? `<div class="row section"><button class="btn" id="tcc">Clear lookup cache</button></div>` : ""}</div>
  <div class="card"><h3>How it works</h3><p class="muted small">Titles are identified through the TMDB API, so there is no local movie database to download or update. Each match carries its IMDb id, which stays the catalog id. Results are cached for up to 30 days. Set <span class="mono">TMDB_API_KEY</span> or <span class="mono">TMDB_READ_TOKEN</span> in the environment.</p><p class="muted small">This product uses the TMDB API but is not endorsed or certified by TMDB.</p></div></div>`;
  if (ed) $("#tcc").onclick = async () => { const r = await act(() => api("/api/tmdb/clear-cache", { method: "POST" })); if (r) { toast(`Cleared ${r.cleared} cached lookups`, "ok"); render(); } };
}

/* ---------- indexing ---------- */
async function pgIndexing(v) {
  const draw = async () => {
    const [src, jobs] = await Promise.all([api("/api/sources"), api("/api/jobs")]), ed = can("sources"), idx = can("indexing");
    v.innerHTML = `<div class="row"><h3 class="grow">Source channels &amp; groups</h3>${ed ? '<button class="btn primary" id="sadd">Add source</button>' : ""}</div>
    ${table(["Source", "Status", "Files", "Indexed", "Duplicates", "Failed", "Last message", ""], src.map(s => { const st = s.stats || {}; return `<tr><td><strong>${esc(s.title)}</strong><div class="mono muted">${esc(s._id)}${s.username ? " · @" + esc(s.username) : ""}</div></td><td>${s.enabled ? pill("enabled", "ok") : pill("disabled")} ${s.live !== false ? pill("live", "info") : ""}</td><td>—</td><td>${st.indexed || 0}</td><td>${st.duplicate || 0}</td><td>${st.failed || 0}</td><td>${s.last_message_id || 0}</td><td class="row">${idx ? `<button class="btn small" data-h="${s._id}">Index history</button><button class="btn small" data-r="${s._id}">Re-index</button>` : ""}${ed ? `<button class="btn small" data-t="${s._id}">Test</button><button class="btn small" data-e="${s._id}" data-v="${s.enabled}">${s.enabled ? "Disable" : "Enable"}</button><button class="btn small" data-l="${s._id}" data-v="${s.live !== false}">${s.live !== false ? "Live off" : "Live on"}</button><button class="btn small danger" data-x="${s._id}">Remove</button>` : ""}</td></tr>`; }), "No sources yet. Add a channel the bot is an admin of.")}
    <div class="section"><h3>Jobs</h3>${table(["Job", "Source", "Type", "Status", "Progress", "Indexed", "Dup", "Failed", "Updated", ""], jobs.map(j => { const pct = j.end_id && j.cursor ? Math.min(100, Math.round(j.cursor * 100 / j.end_id)) : 0; const a = idx ? (["running", "queued"].includes(j.status) ? `<button class="btn small" data-ja="pause" data-id="${j._id}">Pause</button><button class="btn small danger" data-ja="cancel" data-id="${j._id}">Cancel</button>` : ["paused", "failed"].includes(j.status) ? `<button class="btn small" data-ja="resume" data-id="${j._id}">Resume</button>` : "") : ""; return `<tr><td class="mono">${esc(j._id)}</td><td class="mono">${esc(j.chat_id)}</td><td>${esc(j.kind)}</td><td>${statusPill(j.status)}${j.error ? `<div class="error small">${esc(j.error)}</div>` : ""}</td><td><progress max="100" value="${pct}"></progress> ${pct}%</td><td>${j.indexed || 0}</td><td>${j.duplicates || 0}</td><td>${j.failed || 0}</td><td>${fmtDate(j.updated_at)}</td><td class="row">${a}</td></tr>`; }), "No jobs yet.")}</div>`;
    const jb = (sel, fn) => v.querySelectorAll(sel).forEach(b => b.onclick = () => fn(b));
    const startJob = (id, kind) => act(async () => { await api("/api/jobs", { method: "POST", body: { chat_id: +id, kind } }); draw(); }, "Job started");
    jb("[data-h]", b => startJob(b.dataset.h, "history")); jb("[data-r]", b => startJob(b.dataset.r, "reindex"));
    jb("[data-ja]", b => act(async () => { await api(`/api/jobs/${b.dataset.id}/${b.dataset.ja}`, { method: "POST" }); draw(); }));
    jb("[data-t]", async b => { const r = await act(() => api(`/api/sources/${b.dataset.t}/test`, { method: "POST" })); if (r) toast(r.ok ? "Access OK" : "No access: " + r.detail, r.ok ? "ok" : "bad"); });
    jb("[data-e]", b => act(async () => { await api("/api/sources/" + b.dataset.e, { method: "PATCH", body: { enabled: b.dataset.v !== "true" } }); draw(); }));
    jb("[data-l]", b => act(async () => { await api("/api/sources/" + b.dataset.l, { method: "PATCH", body: { live: b.dataset.v !== "true" } }); draw(); }));
    jb("[data-x]", b => dialog("Remove source", "<p>The source stops being indexed. Files already indexed stay in the catalog.</p>", { submit: "Remove", danger: true, onSubmit: async () => { await act(() => api("/api/sources/" + b.dataset.x, { method: "DELETE" }), "Removed"); draw(); } }));
    const add = $("#sadd"); if (add) add.onclick = () => dialog("Add source", '<label>@username, t.me link or numeric ID (-100…)<input name="chat" required></label><p class="muted small">The bot (or the configured user session) must be a member/admin of the source. Only add sources you are authorized to distribute from.</p>', { submit: "Add", onSubmit: async f => { const r = await act(() => api("/api/sources", { method: "POST", body: { chat: f.chat.trim() } }), "Source added"); if (r) draw(); else return false; } });
  };
  await draw(); timer = setInterval(() => draw().catch(() => { }), 8000);
}

/* ---------- users ---------- */
let userQ = "";
async function pgUsers(v) {
  const r = await api("/api/users?q=" + encodeURIComponent(userQ));
  v.innerHTML = `<div class="row"><input class="grow" id="uq" placeholder="Search by @username or numeric ID" value="${esc(userQ)}"><span class="muted small">${r.total} users</span></div><div class="section">${table(["User", "Joined", "Last seen", "Searches", "Deliveries", "Status", ""], r.items.map(u => `<tr><td><strong>${esc(u.first_name || "—")}</strong> ${u.username ? "@" + esc(u.username) : ""}<div class="mono muted">${esc(u.id)}</div></td><td>${fmtDate(u.joined_at)}</td><td>${fmtDate(u.last_seen)}</td><td>${u.search_count || 0}</td><td>${u.delivery_count || 0}</td><td>${u.banned ? pill("banned", "bad") : pill("active", "ok")}</td><td class="row"><button class="btn small" data-u="${esc(u.id)}">Activity</button><button class="btn small ${u.banned ? "" : "danger"}" data-b="${esc(u.id)}" data-banned="${u.banned ? 1 : 0}">${u.banned ? "Unban" : "Ban"}</button></td></tr>`), "No users yet.")}</div>`;
  let t; $("#uq").oninput = e => { clearTimeout(t); t = setTimeout(() => { userQ = e.target.value.trim(); render(); }, 400); };
  v.querySelectorAll("[data-b]").forEach(b => b.onclick = () => b.dataset.banned === "1" ? act(async () => { await api(`/api/users/${b.dataset.b}/unban`, { method: "POST" }); render(); }, "Unbanned") : dialog("Ban user", '<label>Reason (optional)<input name="reason" maxlength="200"></label>', { submit: "Ban", danger: true, onSubmit: async f => { await act(() => api(`/api/users/${b.dataset.b}/ban`, { method: "POST", body: { reason: f.reason || null } }), "Banned"); render(); } }));
  v.querySelectorAll("[data-u]").forEach(b => b.onclick = async () => {
    const d = await act(() => api("/api/users/" + b.dataset.u)); if (!d) return; const u = d.user, perms = u.permissions || {};
    dialog("User " + u.id, `<dl class="kv"><dt>Name</dt><dd>${esc(u.first_name || "—")} ${u.username ? "@" + esc(u.username) : ""}</dd><dt>Search access</dt><dd>${perms.search !== false ? "Allowed" : "Blocked"} <button type="button" class="btn small" data-p="search" data-a="${perms.search !== false ? 0 : 1}">${perms.search !== false ? "Block" : "Allow"}</button></dd><dt>Download access</dt><dd>${perms.download !== false ? "Allowed" : "Blocked"} <button type="button" class="btn small" data-p="download" data-a="${perms.download !== false ? 0 : 1}">${perms.download !== false ? "Block" : "Allow"}</button></dd></dl><h3>Recent searches</h3><pre class="log">${esc(d.searches.map(s => fmtDate(s.ts) + "  " + s.query + "  (" + s.results + ")").join("\n") || "none")}</pre><h3>Recent deliveries</h3><pre class="log">${esc(d.deliveries.map(s => fmtDate(s.ts) + "  " + s.title_id + "  " + (s.quality || "") + "  " + s.status).join("\n") || "none")}</pre>`, {});
    $("#dlg-form").querySelectorAll("[data-p]").forEach(pb => pb.onclick = async () => { await act(() => api(`/api/users/${u.id}/permission`, { method: "POST", body: { permission: pb.dataset.p, allowed: pb.dataset.a === "1" } }), "Updated"); $("#dlg").close(); });
  });
}

/* ---------- system ---------- */
async function pgSystem(v) {
  const [s, logs, site, dbs] = await Promise.all([api("/api/system"), api("/api/logs?limit=150"), api("/api/site-config"), api("/api/databases")]);
  let back = [], dups = [];
  if (can("backups")) back = await api("/api/backups");
  dups = await api("/api/duplicates");
  v.innerHTML = `<div class="grid g2"><div class="card"><h3>Runtime</h3><dl class="kv"><dt>Version</dt><dd>${esc(s.version)}</dd><dt>Uptime</dt><dd>${fmtUp(s.uptime_s)}</dd><dt>Telegram bot</dt><dd>${s.telegram ? pill("connected", "ok") : pill("offline", "bad")}</dd><dt>User session</dt><dd>${s.user_session ? pill("active", "ok") : pill("not configured")}</dd><dt>Cache</dt><dd>${esc(s.cache_backend)} ${s.cache_ok ? pill("ok", "ok") : pill("down", "bad")}</dd><dt>Auto-approve ≥</dt><dd>${esc(s.settings.match_auto_threshold)}</dd><dt>Review ≥</dt><dd>${esc(s.settings.match_review_threshold)}</dd><dt>Token TTL</dt><dd>${esc(s.settings.token_ttl_seconds)} s</dd><dt>Rate limit</dt><dd>${esc(s.settings.rate_limit_per_minute)} / min</dd></dl>${can("cache") ? '<div class="row section"><button class="btn" id="cc">Clear cache</button></div>' : ""}<p class="muted small">Thresholds and credentials are environment settings; change them in your deployment configuration.</p></div>
  <div class="card"><h3>Application</h3><dl class="kv"><dt>Product</dt><dd>AJ CINE AI</dd><dt>Company</dt><dd>AJ APPLICATIONS</dd><dt>Developer</dt><dd>AJWEYA</dd></dl><p class="muted small">Developed by AJ APPLICATIONS · Developer: AJWEYA. Third-party components retain their own licenses; see README.</p></div></div>
  <div class="section"><div class="card"><h3>Public website and streaming</h3><dl class="kv"><dt>Website</dt><dd>${site.public ? pill("on", "ok") : pill("off", "bad")}</dd><dt>Streaming</dt><dd>${site.streaming ? pill("on", "ok") : pill("off")}</dd><dt>Streams now</dt><dd>${esc(site.active_streams)} (limit ${esc(site.limits.max_concurrent)}, ${esc(site.limits.max_per_ip)} per visitor)</dd><dt>Telegram bot</dt><dd>${site.bot_username ? "@" + esc(site.bot_username) : pill("not connected", "bad")}</dd><dt>Public URL</dt><dd>${site.base_url ? esc(site.base_url) : pill("not set")}</dd></dl>${can("settings") ? `<div class="row section"><button class="btn" id="sp">${site.public ? "Turn website off" : "Turn website on"}</button><button class="btn" id="ss">${site.streaming ? "Turn streaming off" : "Turn streaming on"}</button></div>` : ""}<p class="muted small">Streaming sends video from Telegram through this server, so it uses your bandwidth. Turn it on only for content you are authorized to distribute. Switches take effect immediately; defaults come from PUBLIC_SITE_ENABLED and STREAMING_ENABLED. Set PUBLIC_BASE_URL to show a Watch online button in Telegram.</p></div></div>
  <div class="section"><h3>Databases</h3>${table(["Role", "Database", "Host", "Status", "Collections (documents)"], dbs.map(d => `<tr><td>${esc(d.role)}</td><td class="mono">${esc(d.database)}</td><td class="mono">${esc(d.host)}</td><td>${d.ok === true ? pill("ok", "ok") : d.ok === false ? pill("down", "bad") : pill("unknown")}</td><td class="small">${esc(Object.entries(d.documents || {}).map(([c, n]) => c + " (" + n + ")").join(", ") || d.collections.join(", "))}</td></tr>`), "No database information.")}<p class="muted small">Roles can live in one database or in separate databases/servers (MONGO_CATALOG_URI, MONGO_FILES_URI, MONGO_USERS_URI, MONGO_ACTIVITY_URI). Connection strings are never shown.</p></div>
  ${can("backups") ? `<div class="section"><div class="row"><h3 class="grow">Backups</h3><button class="btn primary" id="bk">Create backup</button></div>${table(["File", "Size", "Created", ""], back.map(b => `<tr><td class="mono">${esc(b.file)}</td><td>${fmtBytes(b.bytes)}</td><td>${fmtDate(b.modified * 1000)}</td><td><a class="btn small" href="/api/backups/${encodeURIComponent(b.file)}">Download</a></td></tr>`), "No backups yet.")}<p class="muted small">Restore with <span class="mono">python -m ajcine.tools.backup restore FILE</span>. Backups contain password hashes: store them securely.</p></div>` : ""}
  <div class="section"><h3>Duplicate files (${dups.length} groups)</h3>${table(["Count", "Title", "Season/Ep", "Language", "Quality", "Files"], dups.slice(0, 30).map(d => `<tr><td>${d.count}</td><td class="mono">${esc(d.key.t)}</td><td>${esc(d.key.s ?? "—")}/${esc(d.key.e ?? "—")}</td><td>${esc(LANGS[d.key.l] || d.key.l)}</td><td>${esc(d.key.q || "—")}</td><td class="small">${esc(d.files.map(f => f.name).join(" | "))}</td></tr>`), "No duplicate versions detected.")}</div>
  <div class="section"><div class="row"><h3 class="grow">Logs</h3><select id="ll"><option value="">All</option><option>ERROR</option><option>WARNING</option><option>INFO</option></select></div><pre class="log" id="lg">${esc(logs.map(l => `${l.ts} ${l.level.padEnd(7)} ${l.message}`).join("\n") || "No log entries yet.")}</pre></div>`;
  const cc = $("#cc"); if (cc) cc.onclick = async () => { const r = await act(() => api("/api/cache/clear", { method: "POST" })); if (r) toast(`Cleared ${r.cleared} entries`, "ok"); };
  const sp = $("#sp"); if (sp) sp.onclick = async () => { if (await act(() => api("/api/site-config", { method: "PUT", body: { public: !site.public } }), "Saved")) render(); };
  const ss = $("#ss"); if (ss) ss.onclick = async () => { if (await act(() => api("/api/site-config", { method: "PUT", body: { streaming: !site.streaming } }), "Saved")) render(); };
  const bk = $("#bk"); if (bk) bk.onclick = async () => { bk.disabled = true; if (await act(() => api("/api/backups", { method: "POST" }), "Backup created")) render(); else bk.disabled = false; };
  $("#ll").onchange = async e => { const l = await act(() => api("/api/logs?limit=150" + (e.target.value ? "&level=" + e.target.value : ""))); if (l) $("#lg").textContent = l.map(x => `${x.ts} ${x.level.padEnd(7)} ${x.message}`).join("\n") || "No entries."; };
}

/* ---------- title requests ---------- */
let reqStatus = "open";
async function pgRequests(v) {
  const rows = await api("/api/requests?status=" + reqStatus), edit = can("review");
  v.innerHTML = `<div class="row"><select id="rs"><option value="open">Open</option><option value="done">Done</option><option value="declined">Declined</option></select><span class="muted small grow">Visitors ask for titles from the public website. Most wanted first.</span></div>
  <div class="section">${table(["Votes", "Title", "Year", "Type", "Requested", "In library now", ""], rows.map(r => `<tr><td>${esc(r.votes)}</td><td class="name"><strong>${esc(r.title)}</strong></td><td>${esc(r.year || "—")}</td><td>${esc(r.kind || "any")}</td><td>${fmtDate(r.created_at)}</td><td>${r.in_library ? `<a href="#/media/${encodeURIComponent(r.in_library.id)}">${esc(r.in_library.title)}</a>` : "—"}</td><td>${edit ? (reqStatus === "open" ? `<button class="btn small" data-rq="done" data-id="${esc(r._id)}">Mark done</button> <button class="btn small danger" data-rq="declined" data-id="${esc(r._id)}">Decline</button>` : `<button class="btn small" data-rq="open" data-id="${esc(r._id)}">Reopen</button>`) : ""}</td></tr>`), "No requests here.")}</div>`;
  $("#rs").value = reqStatus; $("#rs").onchange = e => { reqStatus = e.target.value; render(); };
  v.querySelectorAll("[data-rq]").forEach(b => b.onclick = async () => { if (await act(() => api("/api/requests/" + b.dataset.id, { method: "PUT", body: { status: b.dataset.rq } }), "Saved")) render(); });
}

/* ---------- administrators ---------- */
async function pgTeam(v) {
  const a = await api("/api/admins"), web = a.filter(x => x.username), tg = a.filter(x => !x.username && x.active !== false);
  v.innerHTML = `<div class="row"><h3 class="grow">Dashboard accounts</h3><button class="btn primary" id="wa">Add account</button></div>${table(["Username", "Role", "Status", "Last sign-in", ""], web.map(x => `<tr><td>${esc(x.username)}</td><td>${pill(x.role)}</td><td>${x.active === false ? pill("disabled", "bad") : pill("active", "ok")}</td><td>${fmtDate(x.last_login)}</td><td>${x.active !== false && x.username !== ME.username && x.role !== "owner" ? `<button class="btn small danger" data-d="${esc(x.username)}">Disable</button>` : ""}</td></tr>`))}
  <div class="section"><div class="row"><h3 class="grow">Telegram administrators</h3><button class="btn primary" id="ta">Add administrator</button></div>${table(["Telegram ID", "Role", ""], tg.map(x => `<tr><td class="mono">${esc(x.telegram_id)}</td><td>${pill(x.role)}</td><td><button class="btn small danger" data-t="${esc(x.telegram_id)}">Revoke</button></td></tr>`), "Only the owners from OWNER_IDS can use the Telegram admin panel.")}<p class="muted small">Owners listed in OWNER_IDS always have full access in Telegram.</p></div>`;
  const roles = '<option value="viewer">Viewer — read only</option><option value="moderator">Moderator — review &amp; edit media</option><option value="admin">Admin — indexing, AI, TMDB, users</option>';
  $("#wa").onclick = () => dialog("Add dashboard account", `<label>Username<input name="username" required pattern="[A-Za-z0-9_.\\-]{3,32}"></label><label>Password (min 10 characters)<input name="password" type="password" minlength="10" required autocomplete="new-password"></label><label>Role<select name="role">${roles}</select></label>`, { onSubmit: async f => { const r = await act(() => api("/api/admins", { method: "POST", body: f }), "Account created"); if (r) render(); else return false; } });
  $("#ta").onclick = () => dialog("Add Telegram administrator", `<label>Telegram user ID<input name="telegram_id" type="number" required></label><label>Role<select name="role">${roles}</select></label>`, { onSubmit: async f => { const r = await act(() => api("/api/telegram-admins", { method: "POST", body: { telegram_id: +f.telegram_id, role: f.role } }), "Added"); if (r) render(); else return false; } });
  v.querySelectorAll("[data-d]").forEach(b => b.onclick = () => dialog("Disable account", `<p>Disable <strong>${esc(b.dataset.d)}</strong> and end their sessions?</p>`, { submit: "Disable", danger: true, onSubmit: async () => { await act(() => api("/api/admins/" + encodeURIComponent(b.dataset.d), { method: "DELETE" }), "Disabled"); render(); } }));
  v.querySelectorAll("[data-t]").forEach(b => b.onclick = async () => { await act(() => api("/api/telegram-admins/" + b.dataset.t, { method: "DELETE" }), "Revoked"); render(); });
}

boot();


/* ---------- broadcast ---------- */
const bcPill = st => pill(st, st === "done" ? "ok" : st === "failed" || st === "cancelled" ? "bad" : "");
async function pgBroadcast(v) {
  const d = await api("/api/broadcasts");
  timer = setInterval(() => { if (d.items.some(b => b.status === "running" || b.status === "queued")) render(); }, 5000);
  v.innerHTML = `<div class="grid g4">${stat(d.audience.users, "Users reachable")}${stat(d.audience.groups, "Groups")}</div>
  <div class="row"><span class="muted small grow">Messages are paced to stay within Telegram limits. Users who blocked the bot and groups that removed it are skipped automatically.</span><button class="btn primary" id="bn">New broadcast</button></div>
  <div class="section">${table(["Status", "Audience", "Message", "Sent", "Failed", "Unreachable", "Created", ""], d.items.map(b => `<tr><td>${bcPill(b.status)}</td><td>${esc(b.audience)}</td><td class="name">${esc(b.text.slice(0, 80))}${b.image_url ? " (image)" : ""}</td><td>${b.stats.sent}${b.total ? " / " + b.total : ""}</td><td>${b.stats.failed}</td><td>${b.stats.unreachable}</td><td>${fmtDate(b.created_at)}</td><td>${b.status === "draft" ? `<button class="btn small" data-t="${esc(b._id)}">Test</button> <button class="btn small primary" data-s="${esc(b._id)}">Start</button> ` : ""}${b.status === "running" || b.status === "queued" ? `<button class="btn small danger" data-c="${esc(b._id)}">Stop</button>` : `<button class="btn small danger" data-d="${esc(b._id)}">Delete</button>`}</td></tr>`), "No broadcasts yet.")}</div>`;
  $("#bn").onclick = () => dialog("New broadcast", `<label>Message<textarea name="text" required maxlength="3900"></textarea></label><label>Image link (https, optional)<input name="image_url" type="url" maxlength="500"></label><label>Button label (optional)<input name="button_text" maxlength="24"></label><label>Button link (https)<input name="button_url" type="url" maxlength="500"></label><label>Send to<select name="audience"><option value="users">Users</option><option value="groups">Groups</option><option value="all">Users and groups</option></select></label><p class="muted small">It is saved as a draft first. Send yourself a test, then start it. Images are linked, not stored here.</p>`, { submit: "Save draft", onSubmit: async f => { const r = await act(() => api("/api/broadcasts", { method: "POST", body: { text: f.text, image_url: f.image_url || null, button_text: f.button_text || null, button_url: f.button_url || null, audience: f.audience } }), "Draft saved"); if (r) render(); else return false; } });
  v.querySelectorAll("[data-t]").forEach(b => b.onclick = () => dialog("Send a test", `<label>Your Telegram user id<input name="tg" type="number" min="1" required></label><p class="muted small">Press Start in the bot first so it can message you.</p>`, { submit: "Send test", onSubmit: async f => { const r = await act(() => api(`/api/broadcasts/${b.dataset.t}/test`, { method: "POST", body: { telegram_id: Number(f.tg) } }), "Test sent"); if (!r) return false; } }));
  v.querySelectorAll("[data-s]").forEach(b => b.onclick = () => dialog("Start this broadcast?", `<p>It will be sent to real users now and cannot be recalled. You can stop it while it runs.</p>`, { submit: "Start", onSubmit: async () => { const r = await act(() => api(`/api/broadcasts/${b.dataset.s}/start`, { method: "POST" }), "Started"); if (r) render(); else return false; } }));
  v.querySelectorAll("[data-c]").forEach(b => b.onclick = async () => { if (await act(() => api(`/api/broadcasts/${b.dataset.c}/cancel`, { method: "POST" }), "Stopped")) render(); });
  v.querySelectorAll("[data-d]").forEach(b => b.onclick = async () => { if (await act(() => api(`/api/broadcasts/${b.dataset.d}`, { method: "DELETE" }), "Deleted")) render(); });
}

/* ---------- ads ---------- */
const AD_PLACES = [["bot_results", "Under search results in the bot"], ["bot_delivery", "After a file is delivered"], ["site_banner", "Website banner"]];
function adForm(a = {}) {
  const on = new Set(a.placements || []);
  return `<label>Title<input name="title" required maxlength="80" value="${esc(a.title || "")}"></label><label>Text (optional)<textarea name="text" maxlength="300">${esc(a.text || "")}</textarea></label><label>Link (https)<input name="link_url" type="url" required maxlength="500" value="${esc(a.link_url || "")}"></label><label>Image link (https; required for the website banner)<input name="image_url" type="url" maxlength="500" value="${esc(a.image_url || "")}"></label><label>Button label<input name="button_text" maxlength="24" value="${esc(a.button_text || "Learn more")}"></label><fieldset class="gap"><legend>Where it appears</legend>${AD_PLACES.map(([k, l]) => `<label class="check"><input type="checkbox" name="p_${k}" ${on.has(k) ? "checked" : ""}> ${esc(l)}</label>`).join("")}</fieldset><label>Weight (1 to 10, higher shows more often)<input name="weight" type="number" min="1" max="10" value="${esc(a.weight || 1)}"></label><label>Start date (optional)<input name="starts_at" type="date" value="${esc((a.starts_at || "").slice(0, 10))}"></label><label>End date (optional)<input name="ends_at" type="date" value="${esc((a.ends_at || "").slice(0, 10))}"></label>`;
}
const adBody = f => ({ title: f.title, text: f.text || "", link_url: f.link_url, image_url: f.image_url || null, button_text: f.button_text || "Learn more", placements: AD_PLACES.map(x => x[0]).filter(k => f["p_" + k]), weight: Number(f.weight || 1), starts_at: f.starts_at || null, ends_at: f.ends_at || null });
async function pgAds(v) {
  const d = await api("/api/ads");
  v.innerHTML = `<div class="row"><span class="muted small grow">Every ad is labelled Sponsored. Images are linked from their own address and are not stored here. Ad images load from the advertiser's host, which can see visitors' addresses.</span><button class="btn" id="as">${d.enabled ? "Turn ads off" : "Turn ads on"}</button><button class="btn primary" id="an">New ad</button></div>
  <div class="section">${table(["Status", "Title", "Where", "Weight", "Shown", "Clicks", "Click rate", ""], d.items.map(a => `<tr><td>${a.active ? pill("on", "ok") : pill("off")}</td><td class="name"><strong>${esc(a.title)}</strong><div class="muted small">${esc(a.link_url)}</div></td><td>${esc(a.placements.join(", "))}</td><td>${a.weight}</td><td>${a.impressions}</td><td>${a.clicks}</td><td>${a.ctr === null ? "—" : a.ctr + "%"}</td><td><button class="btn small" data-e="${esc(a._id)}">Edit</button> <button class="btn small" data-a="${esc(a._id)}" data-on="${a.active ? 0 : 1}">${a.active ? "Turn off" : "Turn on"}</button> <button class="btn small danger" data-d="${esc(a._id)}">Delete</button></td></tr>`), "No ads yet.")}</div>`;
  $("#as").onclick = async () => { if (await act(() => api("/api/ads-switch", { method: "PUT", body: { enabled: !d.enabled } }), "Saved")) render(); };
  $("#an").onclick = () => dialog("New ad", adForm(), { onSubmit: async f => { const r = await act(() => api("/api/ads", { method: "POST", body: adBody(f) }), "Ad saved"); if (r) render(); else return false; } });
  v.querySelectorAll("[data-e]").forEach(b => b.onclick = () => { const a = d.items.find(x => x._id === b.dataset.e); dialog("Edit ad", adForm(a), { onSubmit: async f => { const r = await act(() => api("/api/ads/" + a._id, { method: "PUT", body: { ...adBody(f), active: a.active } }), "Saved"); if (r) render(); else return false; } }); });
  v.querySelectorAll("[data-a]").forEach(b => b.onclick = async () => { if (await act(() => api(`/api/ads/${b.dataset.a}/active`, { method: "PUT", body: { active: b.dataset.on === "1" } }), "Saved")) render(); });
  v.querySelectorAll("[data-d]").forEach(b => b.onclick = async () => { if (await act(() => api("/api/ads/" + b.dataset.d, { method: "DELETE" }), "Deleted")) render(); });
}


/* ---------- analytics ---------- */
let anDays = 30;
function lineChart(labels, series) {
  const W = 720, H = 180, P = 28, n = labels.length, max = Math.max(1, ...series.flatMap(s => s.values));
  const x = i => P + (n === 1 ? 0 : i * (W - P - 8) / (n - 1)), y = v => H - 22 - v * (H - 40) / max;
  const lines = series.map((s, k) => `<polyline class="ln l${k}" fill="none" points="${s.values.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ")}"/>`).join("");
  const ticks = [0, Math.floor((n - 1) / 2), n - 1].map(i => `<text class="tk" x="${x(i).toFixed(1)}" y="${H - 6}" text-anchor="${i === 0 ? "start" : i === n - 1 ? "end" : "middle"}">${esc(labels[i].slice(5))}</text>`).join("");
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(series.map(s => s.name).join(", "))} per day"><line class="ax" x1="${P}" y1="${y(0)}" x2="${W - 8}" y2="${y(0)}"/><text class="tk" x="2" y="${y(max) + 4}">${max}</text>${lines}${ticks}</svg>
  <div class="legend">${series.map((s, k) => `<span><i class="sw l${k}"></i>${esc(s.name)} (${s.values.reduce((a, b) => a + b, 0)})</span>`).join("")}</div>`;
}
async function pgAnalytics(v) {
  const d = await api("/api/analytics?days=" + anDays);
  const list = (rows, empty, f) => rows.length ? `<table class="tbl"><tbody>${rows.map(f).join("")}</tbody></table>` : `<p class="muted small">${empty}</p>`;
  v.innerHTML = `<div class="row"><span class="muted small grow">Real counts from the search history and delivery log.</span><select id="ad-days" aria-label="Period">${[7, 30, 90].map(n => `<option value="${n}" ${n === anDays ? "selected" : ""}>Last ${n} days</option>`).join("")}</select></div>
  <div class="grid g4">${stat(d.searches.reduce((a, b) => a + b, 0), "Searches")}${stat(d.deliveries.reduce((a, b) => a + b, 0), "Files delivered")}${stat(d.streams.reduce((a, b) => a + b, 0), "Streams started")}${stat(d.active_users, "Active searchers")}</div>
  <div class="card section"><h3>Activity per day</h3>${lineChart(d.labels, [{ name: "Searches", values: d.searches }, { name: "Deliveries", values: d.deliveries }, { name: "Streams", values: d.streams }])}</div>
  <div class="grid g2 section"><div class="card"><h3>Most searched</h3>${list(d.top_searches, "No searches in this period.", r => `<tr><td>${esc(r.query)}</td><td class="num">${r.count}</td></tr>`)}</div>
  <div class="card"><h3>Searched, nothing found</h3><p class="muted small">Good candidates to index or request.</p>${list(d.no_results, "Every search found something.", r => `<tr><td>${esc(r.query)}</td><td class="num">${r.count}</td></tr>`)}</div></div>
  <div class="grid g2 section"><div class="card"><h3>Most opened titles</h3><p class="muted small">Files delivered plus streams.</p>${list(d.top_titles, "No deliveries in this period.", r => `<tr><td>${esc(r.title)}${r.year ? " (" + esc(r.year) + ")" : ""}</td><td class="num">${r.count}</td></tr>`)}</div>
  <div class="card"><h3>Bot users</h3><dl class="kv"><dt>Total</dt><dd>${d.users.total}</dd><dt>Blocked the bot</dt><dd>${d.users.blocked}</dd><dt>Banned</dt><dd>${d.users.banned}</dd></dl></div></div>`;
  $("#ad-days").onchange = e => { anDays = Number(e.target.value); render(); };
}

/* ---------- groups ---------- */
async function pgGroups(v) {
  let chats = [];
  try { chats = await api("/api/chats"); } catch (e) { v.innerHTML = `<p class="muted">Groups are visible to administrators only.</p>`; return; }
  const active = chats.filter(c => c.active).length;
  v.innerHTML = `<div class="grid g4">${stat(active, "Active groups")}${stat(chats.length - active, "Removed or unreachable")}</div>
  <div class="section">${table(["Group", "Type", "Status", "Last seen", "Added"], chats.map(c => `<tr><td class="name"><strong>${esc(c.title || "Untitled")}</strong>${c.username ? `<div class="muted small">@${esc(c.username)}</div>` : ""}<div class="mono muted">${esc(c._id)}</div></td><td>${esc((c.type || "").toLowerCase())}</td><td>${c.active ? pill("active", "ok") : pill("left", "warn")}</td><td>${fmtDate(c.last_seen)}</td><td>${fmtDate(c.joined_at)}</td></tr>`), "The bot is not in any group yet. Add it to a group and it will appear here.")}</div>
  <p class="muted small">Groups are used as a Broadcast audience. A group is marked left when the bot is removed or cannot post there.</p>`;
}
