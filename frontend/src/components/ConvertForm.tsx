import { useState } from "preact/hooks";

import {
  ApiError,
  createBatch,
  createJob,
  probe,
  type Batch,
  type FormatInfo,
  type Job,
  type PlaylistInfo,
  type ProbeResult,
  type VideoInfo,
} from "../api";
import { formatDuration } from "../format";
import { useFormatChoice } from "./FormatPicker";

type Busy = "probing" | "starting" | null;

export function ConvertForm({
  formats,
  onJobCreated,
  onBatchCreated,
}: {
  formats: FormatInfo[];
  onJobCreated: (job: Job) => void;
  onBatchCreated: (batch: Batch, jobs: Job[]) => void;
}) {
  const [url, setUrl] = useState("");
  const [found, setFound] = useState<{ url: string; info: ProbeResult } | null>(null);
  const [busy, setBusy] = useState<Busy>(null);
  const [error, setError] = useState<string | null>(null);

  function fail(cause: unknown) {
    setError(cause instanceof ApiError ? cause.message : "Something went wrong. Try again.");
  }

  async function find(event: Event) {
    event.preventDefault();
    const target = url.trim();
    if (!target || busy) return;
    setBusy("probing");
    setError(null);
    setFound(null);
    try {
      setFound({ url: target, info: await probe(target) });
    } catch (cause) {
      fail(cause);
    } finally {
      setBusy(null);
    }
  }

  /** Run `start`, then reset the form; shared by the video and playlist cards. */
  async function start(action: () => Promise<void>) {
    if (busy) return;
    setBusy("starting");
    setError(null);
    try {
      await action();
      setFound(null);
      setUrl("");
    } catch (cause) {
      fail(cause);
    } finally {
      setBusy(null);
    }
  }

  return (
    <section class="card">
      <form class="url-form" onSubmit={find}>
        <label class="visually-hidden" for="url">
          YouTube video or playlist link
        </label>
        <input
          id="url"
          type="url"
          inputMode="url"
          placeholder="Paste a YouTube video or playlist link"
          autocomplete="off"
          required
          value={url}
          onInput={(event) => setUrl(event.currentTarget.value)}
        />
        <button type="submit" class="button" disabled={busy !== null || !url.trim()}>
          {busy === "probing" ? "Looking…" : "Find"}
        </button>
      </form>

      {error && (
        <p class="error" role="alert">
          {error}
        </p>
      )}

      {found?.info.kind === "video" && (
        <VideoCard
          key={found.url}
          video={found.info}
          formats={formats}
          starting={busy === "starting"}
          onConvert={(format, quality) =>
            start(async () => onJobCreated(await createJob(found.url, format, quality)))
          }
        />
      )}

      {found?.info.kind === "playlist" && (
        <PlaylistCard
          key={found.url}
          playlist={found.info}
          formats={formats}
          starting={busy === "starting"}
          onConvert={(format, quality, entries) =>
            start(async () => {
              const created = await createBatch({
                url: found.url,
                title: found.info.title,
                format,
                quality,
                entries,
              });
              onBatchCreated(created.batch, created.jobs);
            })
          }
        />
      )}
    </section>
  );
}

function VideoCard({
  video,
  formats,
  starting,
  onConvert,
}: {
  video: VideoInfo;
  formats: FormatInfo[];
  starting: boolean;
  onConvert: (format: string, quality: string | null) => void;
}) {
  const { format, quality, picker } = useFormatChoice(formats, video.max_height);
  if (!format) return null;

  return (
    <div class="preview">
      {video.thumbnail && (
        <img class="thumb" src={video.thumbnail} alt="" referrerpolicy="no-referrer" />
      )}
      <div class="preview-body">
        <h2 class="title">{video.title ?? "Untitled video"}</h2>
        <p class="meta">
          {[video.uploader, formatDuration(video.duration)].filter(Boolean).join(" · ")}
        </p>
        {picker}
        <button
          type="button"
          class="button primary"
          disabled={starting}
          onClick={() => onConvert(format.id, quality)}
        >
          {starting ? "Starting…" : `Convert to ${format.label}`}
        </button>
      </div>
    </div>
  );
}

function PlaylistCard({
  playlist,
  formats,
  starting,
  onConvert,
}: {
  playlist: PlaylistInfo;
  formats: FormatInfo[];
  starting: boolean;
  onConvert: (
    format: string,
    quality: string | null,
    entries: { video_id: string; title: string | null }[],
  ) => void;
}) {
  // The videos' resolutions aren't known until each is converted, so every
  // video quality is offered; a video is never upscaled past what it has.
  const { format, quality, picker } = useFormatChoice(formats, null);
  const [chosen, setChosen] = useState(() => new Set(playlist.entries.map((e) => e.video_id)));
  if (!format) return null;

  const allChosen = chosen.size === playlist.entries.length;

  function toggle(videoId: string) {
    setChosen((current) => {
      const next = new Set(current);
      if (!next.delete(videoId)) next.add(videoId);
      return next;
    });
  }

  const entries = playlist.entries
    .filter((entry) => chosen.has(entry.video_id))
    .map((entry) => ({ video_id: entry.video_id, title: entry.title }));

  return (
    <div class="preview playlist">
      <div class="preview-body">
        <h2 class="title">{playlist.title ?? "Untitled playlist"}</h2>
        <p class="meta">
          {[playlist.uploader, `Playlist · ${playlist.total} videos`].filter(Boolean).join(" · ")}
        </p>
        {playlist.truncated && (
          <p class="meta">
            Only the first {playlist.entries.length} videos are listed; that is this server's limit
            per conversion.
          </p>
        )}

        <div class="entries-head">
          <span class="meta">
            {chosen.size} of {playlist.entries.length} selected
          </span>
          <button
            type="button"
            class="button quiet"
            onClick={() =>
              setChosen(allChosen ? new Set() : new Set(playlist.entries.map((e) => e.video_id)))
            }
          >
            {allChosen ? "Select none" : "Select all"}
          </button>
        </div>
        <ul class="entries">
          {playlist.entries.map((entry, index) => (
            <li key={entry.video_id}>
              <label class="entry">
                <input
                  type="checkbox"
                  checked={chosen.has(entry.video_id)}
                  onChange={() => toggle(entry.video_id)}
                />
                <span class="entry-title">
                  {index + 1}. {entry.title ?? entry.video_id}
                </span>
                <span class="meta">{formatDuration(entry.duration)}</span>
              </label>
            </li>
          ))}
        </ul>

        {picker}
        <button
          type="button"
          class="button primary"
          disabled={starting || entries.length === 0}
          onClick={() => onConvert(format.id, quality, entries)}
        >
          {starting
            ? "Starting…"
            : `Convert ${entries.length} ${entries.length === 1 ? "video" : "videos"} to ${format.label}`}
        </button>
      </div>
    </div>
  );
}
