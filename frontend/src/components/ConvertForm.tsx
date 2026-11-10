import { useState } from "preact/hooks";

import { ApiError, createJob, probe, type Format, type Job, type VideoInfo } from "../api";
import { formatDuration } from "../format";

const FORMATS: { value: Format; label: string; hint: string }[] = [
  { value: "mp3", label: "MP3", hint: "Audio" },
  { value: "mp4", label: "MP4", hint: "Video" },
];

type Busy = "probing" | "starting" | null;

export function ConvertForm({ onJobCreated }: { onJobCreated: (job: Job) => void }) {
  const [url, setUrl] = useState("");
  const [video, setVideo] = useState<{ url: string; info: VideoInfo } | null>(null);
  const [format, setFormat] = useState<Format>("mp3");
  const [busy, setBusy] = useState<Busy>(null);
  const [error, setError] = useState<string | null>(null);

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
    if (!video || busy) return;
    setBusy("starting");
    setError(null);
    try {
      onJobCreated(await createJob(video.url, format));
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

      {video && (
        <div class="preview">
          {video.info.thumbnail && (
            <img class="thumb" src={video.info.thumbnail} alt="" referrerpolicy="no-referrer" />
          )}
          <div class="preview-body">
            <h2 class="title">{video.info.title ?? "Untitled video"}</h2>
            <p class="meta">
              {[video.info.uploader, formatDuration(video.info.duration)].filter(Boolean).join(" · ")}
            </p>

            <fieldset class="formats">
              <legend class="visually-hidden">Output format</legend>
              {FORMATS.map((option) => (
                <label class="format" key={option.value}>
                  <input
                    type="radio"
                    name="format"
                    value={option.value}
                    checked={format === option.value}
                    onChange={() => setFormat(option.value)}
                  />
                  <span>
                    <strong>{option.label}</strong> {option.hint}
                  </span>
                </label>
              ))}
            </fieldset>

            <button type="button" class="button primary" disabled={busy !== null} onClick={convert}>
              {busy === "starting" ? "Starting…" : `Convert to ${format.toUpperCase()}`}
            </button>
          </div>
        </div>
      )}
    </section>
  );
}
