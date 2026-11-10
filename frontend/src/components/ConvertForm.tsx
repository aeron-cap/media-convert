import { useState } from "preact/hooks";

import {
  ApiError,
  createJob,
  probe,
  type FormatInfo,
  type Job,
  type VideoInfo,
} from "../api";
import { formatDuration, formatQuality } from "../format";

type Busy = "probing" | "starting" | null;

const KINDS = [
  { kind: "audio", label: "Audio" },
  { kind: "video", label: "Video" },
] as const;

/** The qualities of `format` that make sense for this particular video. */
function qualitiesFor(format: FormatInfo, video: VideoInfo): string[] {
  if (format.kind !== "video" || video.max_height == null) return format.qualities;
  const maxHeight = video.max_height;
  return format.qualities.filter((q) => q === "best" || Number(q) <= maxHeight);
}

function qualityLabel(format: FormatInfo, quality: string, video: VideoInfo): string {
  if (format.kind === "video" && quality === "best") {
    return video.max_height ? `Best available (${video.max_height}p)` : "Best available";
  }
  return formatQuality(format.kind, quality);
}

export function ConvertForm({
  formats,
  onJobCreated,
}: {
  formats: FormatInfo[];
  onJobCreated: (job: Job) => void;
}) {
  const [url, setUrl] = useState("");
  const [video, setVideo] = useState<{ url: string; info: VideoInfo } | null>(null);
  const [formatId, setFormatId] = useState("mp3");
  // The quality picked for each format, so switching formats keeps choices.
  const [picked, setPicked] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<Busy>(null);
  const [error, setError] = useState<string | null>(null);

  const format = formats.find((f) => f.id === formatId) ?? formats[0];
  const qualities = format && video ? qualitiesFor(format, video.info) : [];
  const quality =
    format && qualities.length > 0
      ? qualities.includes(picked[format.id])
        ? picked[format.id]
        : qualities.includes(format.default_quality ?? "")
          ? format.default_quality
          : qualities[0]
      : null;

  function fail(cause: unknown) {
    setError(cause instanceof ApiError ? cause.message : "Something went wrong. Try again.");
  }

  async function findVideo(event: Event) {
    event.preventDefault();
    const target = url.trim();
    if (!target || busy) return;
    setBusy("probing");
    setError(null);
    setVideo(null);
    try {
      setVideo({ url: target, info: await probe(target) });
    } catch (cause) {
      fail(cause);
    } finally {
      setBusy(null);
    }
  }

  async function convert() {
    if (!video || !format || busy) return;
    setBusy("starting");
    setError(null);
    try {
      onJobCreated(await createJob(video.url, format.id, quality));
      setVideo(null);
      setUrl("");
    } catch (cause) {
      fail(cause);
    } finally {
      setBusy(null);
    }
  }

  return (
    <section class="card">
      <form class="url-form" onSubmit={findVideo}>
        <label class="visually-hidden" for="url">
          YouTube link
        </label>
        <input
          id="url"
          type="url"
          inputMode="url"
          placeholder="Paste a YouTube link"
          autocomplete="off"
          required
          value={url}
          onInput={(event) => setUrl(event.currentTarget.value)}
        />
        <button type="submit" class="button" disabled={busy !== null || !url.trim()}>
          {busy === "probing" ? "Looking…" : "Find video"}
        </button>
      </form>

      {error && (
        <p class="error" role="alert">
          {error}
        </p>
      )}

      {video && format && (
        <div class="preview">
          {video.info.thumbnail && (
            <img class="thumb" src={video.info.thumbnail} alt="" referrerpolicy="no-referrer" />
          )}
          <div class="preview-body">
            <h2 class="title">{video.info.title ?? "Untitled video"}</h2>
            <p class="meta">
              {[video.info.uploader, formatDuration(video.info.duration)].filter(Boolean).join(" · ")}
            </p>

            <div class="options">
              {KINDS.map(({ kind, label }) => (
                <fieldset class="formats" key={kind}>
                  <legend>{label}</legend>
                  {formats
                    .filter((option) => option.kind === kind)
                    .map((option) => (
                      <label class="format" key={option.id}>
                        <input
                          type="radio"
                          name="format"
                          value={option.id}
                          checked={format.id === option.id}
                          onChange={() => setFormatId(option.id)}
                        />
                        <span>{option.label}</span>
                      </label>
                    ))}
                </fieldset>
              ))}

              {quality !== null && (
                <div class="quality">
                  <label for="quality">Quality</label>
                  <select
                    id="quality"
                    value={quality}
                    onChange={(event) =>
                      setPicked({ ...picked, [format.id]: event.currentTarget.value })
                    }
                  >
                    {qualities.map((q) => (
                      <option value={q} key={q}>
                        {qualityLabel(format, q, video.info)}
                      </option>
                    ))}
                  </select>
                </div>
              )}

              {format.note && <p class="meta">{format.note}</p>}
            </div>

            <button type="button" class="button primary" disabled={busy !== null} onClick={convert}>
              {busy === "starting" ? "Starting…" : `Convert to ${format.label}`}
            </button>
          </div>
        </div>
      )}
    </section>
  );
}
