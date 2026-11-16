import type { FormatInfo, Source } from "./api";

/**
 * The qualities of `format` that make sense for one video. `maxHeight` is the
 * video's height when known; a video is never offered more than it has.
 * TikTok videos come in one resolution only, so their video formats have
 * nothing to choose but "best". Audio bitrates apply to every site.
 */
export function qualityChoices(
  format: FormatInfo,
  source: Source,
  maxHeight: number | null,
): string[] {
  if (format.kind !== "video") return format.qualities;
  if (source === "tiktok") return format.qualities.filter((q) => q === "best");
  if (maxHeight == null) return format.qualities;
  return format.qualities.filter((q) => q === "best" || Number(q) <= maxHeight);
}

export function formatDuration(seconds: number | null): string {
  if (seconds == null) return "";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  const pad = (n: number) => String(n).padStart(2, "0");
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

export function formatSize(bytes: number | null): string {
  if (bytes == null) return "";
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

/** "320 kbps", "720p" or "Best quality"; empty when the format has no choice. */
export function formatQuality(kind: "audio" | "video", quality: string | null): string {
  if (quality == null) return "";
  if (kind === "audio") return `${quality} kbps`;
  return quality === "best" ? "Best quality" : `${quality}p`;
}

/** "Expires in 5 h", "Expires in 12 min"; empty when the job never expires. */
export function formatExpiry(expiresAt: string | null, now: number = Date.now()): string {
  if (expiresAt == null) return "";
  const minutes = Math.round((new Date(expiresAt).getTime() - now) / 60000);
  if (minutes <= 1) return "Expires soon";
  if (minutes < 90) return `Expires in ${minutes} min`;
  return `Expires in ${Math.round(minutes / 60)} h`;
}
