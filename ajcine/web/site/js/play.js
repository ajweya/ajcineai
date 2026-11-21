import { Site } from "./api.js";
import { progress } from "./store.js";
import { toast } from "./ui.js";

const busy = new Set();
/** Start playback of a title: resumes the last-watched episode/version when there is one, otherwise the first episode (series)
 *  or the best browser-friendly version (movie). Opens the title page instead when streaming is off. */
export async function playTitle(it, go, { season, episode } = {}) {
  if (busy.has(it.id)) return; busy.add(it.id);
  try {
    const cfg = await Site.config();
    if (!cfg.streaming) { go(`/title/${it.id}`); return toast("Streaming is off. Choose a version to get it in Telegram."); }
    let sel = { title_id: it.id };
    const last = progress.forTitle(it.id).find(h => !h.done) || progress.forTitle(it.id)[0];
    if (season !== undefined) sel = { title_id: it.id, season, episode };
    else if (last && last.meta.select) sel = last.meta.select;
    else if (it.kind === "series") {
      const t = await Site.title(it.id);
      const s = (t.seasons || []).filter(x => x.season > 0).sort((a, b) => a.season - b.season)[0] || (t.seasons || [])[0];
      if (!s) { go(`/title/${it.id}`); return; }
      const eps = (await Site.episodes(it.id, s.season)).episodes;
      if (!eps.length) { go(`/title/${it.id}`); return; }
      sel = { title_id: it.id, season: s.season, episode: eps[0].episode };
    }
    const r = await Site.streamLink(sel);
    go(r.watch);
  } catch (e) { toast(e.message || "Could not start playback."); }
  finally { busy.delete(it.id); }
}
export async function playSelection(sel, go) {
  try { go((await Site.streamLink(sel)).watch); } catch (e) { toast(e.message || "That version is not available any more."); }
}
