import { need } from "../deps.js";

/** Playback engine adapter. Direct media files (what Telegram streaming serves) use the browser's own engine; DASH/HLS manifests
 *  use Shaka Player when the browser needs it. Quality/audio lists only ever describe what the source really offers. */
const isManifest = u => /\.(mpd|m3u8)(\?|#|$)/i.test(u || "");

export async function createEngine(video, { src, manifest }) {
  const url = manifest || src, target = new EventTarget();
  const emit = (n, d) => target.dispatchEvent(new CustomEvent(n, { detail: d }));
  let shaka = null, kind = "native";
  const nativeHls = /\.m3u8/i.test(url || "") && video.canPlayType("application/vnd.apple.mpegurl");

  if (isManifest(url) && !nativeHls && await need("shaka") && window.shaka) {
    shaka = window.shaka; shaka.polyfill.installAll();
    if (shaka.Player.isBrowserSupported()) kind = "shaka";
  }
  let player = null;
  if (kind === "shaka") {
    player = new shaka.Player(); await player.attach(video);
    player.addEventListener("error", e => emit("error", { code: e.detail.code, fatal: e.detail.severity === shaka.util.Error.Severity.CRITICAL }));
    player.addEventListener("trackschanged", () => emit("tracks"));
    player.addEventListener("variantchanged", () => emit("tracks"));
    player.addEventListener("adaptation", () => emit("tracks"));
    player.configure({ abr: { enabled: true } });
  }
  return {
    kind, events: target,
    async load(startAt = 0) {
      if (kind === "shaka") await player.load(url, startAt || null);
      else { video.src = url; if (startAt) video.addEventListener("loadedmetadata", () => { video.currentTime = startAt; }, { once: true }); video.load(); }
    },
    /** Selectable heights offered by the stream itself (adaptive only). */
    qualities() {
      if (kind !== "shaka") return [];
      const seen = new Map(); player.getVariantTracks().forEach(t => { if (t.height && (!seen.has(t.height) || t.bandwidth > seen.get(t.height).bandwidth)) seen.set(t.height, t); });
      const cfgAuto = player.getConfiguration().abr.enabled, active = player.getVariantTracks().find(t => t.active);
      return [...seen.values()].sort((a, b) => b.height - a.height).map(t => ({ id: String(t.id), label: `${t.height}p`, active: !cfgAuto && active && active.height === t.height }));
    },
    isAuto: () => kind === "shaka" && player.getConfiguration().abr.enabled,
    setQuality(id) {
      if (kind !== "shaka") return;
      if (id === "auto") return player.configure({ abr: { enabled: true } });
      const t = player.getVariantTracks().find(x => String(x.id) === id); if (!t) return;
      player.configure({ abr: { enabled: false } }); player.selectVariantTrack(t, true);
    },
    audioTracks() {
      if (kind === "shaka") { const langs = player.getAudioLanguagesAndRoles(); const cur = player.getVariantTracks().find(t => t.active); return langs.map(l => ({ id: l.language + "|" + l.role, label: l.language + (l.role ? ` (${l.role})` : ""), active: !!cur && cur.language === l.language })); }
      const out = []; if (video.audioTracks) for (let i = 0; i < video.audioTracks.length; i++) { const t = video.audioTracks[i]; out.push({ id: String(i), label: t.label || t.language || `Track ${i + 1}`, active: t.enabled }); } return out;
    },
    setAudio(id) {
      if (kind === "shaka") { const [lang, role] = id.split("|"); player.selectAudioLanguage(lang, role || undefined); return; }
      for (let i = 0; i < video.audioTracks.length; i++) video.audioTracks[i].enabled = String(i) === id;
    },
    async destroy() { try { if (player) await player.destroy(); } catch { /* already destroyed */ } },
  };
}
