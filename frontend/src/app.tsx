import { useEffect, useRef, useState } from "preact/hooks";

import {
  ApiError,
  clearFinishedJobs,
  deleteBatch,
  deleteJob,
  isActive,
  listBatches,
  listFormats,
  listJobs,
  updateBatchQuality,
  updateJobQuality,
  watchJob,
  type Batch,
  type FormatInfo,
  type Job,
} from "./api";
import { BatchGroup } from "./components/BatchGroup";
import { ConvertForm } from "./components/ConvertForm";
import { JobItem } from "./components/JobItem";

// How often the list is reloaded: quickly while conversions are waiting or
// running, slowly otherwise (to notice conversions that expired).
const ACTIVE_REFRESH_MS = 2_500;
const IDLE_REFRESH_MS = 60_000;
// Running conversions are followed live, each over its own connection.
// Browsers allow only a handful of connections per site, so the number is
// capped; the rest of a large playlist is covered by the regular reload.
const MAX_LIVE = 3;

export function App() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [batches, setBatches] = useState<Batch[]>([]);
  const [formats, setFormats] = useState<FormatInfo[]>([]);
  const [loadError, setLoadError] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  // Jobs currently followed live, by id, with the function that stops each.
  const watchers = useRef(new Map<string, () => void>());
  const jobsRef = useRef<Job[]>([]);
  jobsRef.current = jobs;

  function upsert(job: Job) {
    setJobs((current) =>
      current.some((existing) => existing.id === job.id)
        ? current.map((existing) => (existing.id === job.id ? job : existing))
        : [job, ...current],
    );
  }

  function follow(job: Job) {
    if (job.status !== "running" || watchers.current.has(job.id)) return;
    if (watchers.current.size >= MAX_LIVE) return;
    const stop = watchJob(job.id, (update) => {
      upsert(update);
      if (!isActive(update)) watchers.current.delete(update.id);
    });
    watchers.current.set(job.id, stop);
  }

  async function reload() {
    const [loadedJobs, loadedBatches] = await Promise.all([listJobs(), listBatches()]);
    setBatches(loadedBatches);
    // A job followed live is at least as fresh as this snapshot; keep it.
    setJobs((current) =>
      loadedJobs.map((job) =>
        watchers.current.has(job.id) ? (current.find((c) => c.id === job.id) ?? job) : job,
      ),
    );
    loadedJobs.forEach(follow);
  }

  useEffect(() => {
    listFormats()
      .then(setFormats)
      .then(reload)
      .catch(() => setLoadError(true));

    let lastReload = Date.now();
    const timer = setInterval(() => {
      if (document.hidden) return;
      const wait = jobsRef.current.some(isActive) ? ACTIVE_REFRESH_MS : IDLE_REFRESH_MS;
      if (Date.now() - lastReload < wait) return;
      lastReload = Date.now();
      reload().catch(() => {});
    }, 1_000);

    return () => {
      clearInterval(timer);
      watchers.current.forEach((stop) => stop());
      watchers.current.clear();
    };
  }, []);

  function onJobCreated(job: Job) {
    upsert(job);
    follow(job);
  }

  function onBatchCreated(batch: Batch, created: Job[]) {
    setBatches((current) => [batch, ...current]);
    // Newest first, like the rest of the list.
    setJobs((current) => [...[...created].reverse(), ...current]);
    created.forEach(follow);
  }

  function forget(removed: Job[]) {
    const ids = new Set(removed.map((job) => job.id));
    ids.forEach((id) => {
      watchers.current.get(id)?.();
      watchers.current.delete(id);
    });
    setJobs((current) => current.filter((job) => !ids.has(job.id)));
  }

  async function removeJob(job: Job) {
    setActionError(null);
    try {
      await deleteJob(job.id);
      forget([job]);
    } catch {
      // It may already be gone (expired or removed in another tab); reload to see.
      setActionError("Couldn't remove that conversion. Reload the page and try again.");
    }
  }

  async function removeBatch(batchId: string, batchJobs: Job[]) {
    if (!confirm(`Remove this playlist's ${batchJobs.length} conversion(s) and their files?`)) return;
    setActionError(null);
    try {
      await deleteBatch(batchId);
      forget(batchJobs);
    } catch {
      setActionError("Couldn't remove that playlist. Reload the page and try again.");
    }
  }

  async function changeJobQuality(job: Job, quality: string) {
    setActionError(null);
    try {
      upsert(await updateJobQuality(job.id, quality));
    } catch (cause) {
      // Most likely it started in the meantime; show the server's reason.
      setActionError(cause instanceof ApiError ? cause.message : "Couldn't change the quality.");
      reload().catch(() => {});
    }
  }

  async function changeBatchQuality(batchId: string, quality: string) {
    setActionError(null);
    try {
      const updated = await updateBatchQuality(batchId, quality);
      setBatches((current) => current.map((b) => (b.id === batchId ? updated.batch : b)));
      const fresh = new Map(updated.jobs.map((job) => [job.id, job]));
      // Jobs followed live are already newer than this answer; keep those.
      setJobs((current) =>
        current.map((job) => (watchers.current.has(job.id) ? job : (fresh.get(job.id) ?? job))),
      );
    } catch (cause) {
      setActionError(cause instanceof ApiError ? cause.message : "Couldn't change the quality.");
      reload().catch(() => {});
    }
  }

  async function clearFinished() {
    const finished = jobs.filter((job) => !isActive(job));
    if (!confirm(`Remove ${finished.length} finished conversion(s) and their files?`)) return;
    setActionError(null);
    try {
      await clearFinishedJobs();
      forget(finished);
    } catch {
      setActionError("Couldn't clear the finished conversions. Try again.");
    }
  }

  // Single conversions stay as they are; a playlist's conversions are shown
  // together, at the place of its newest one.
  const groups: ({ job: Job } | { batchId: string; jobs: Job[] })[] = [];
  const seenBatches = new Set<string>();
  for (const job of jobs) {
    if (job.batch_id == null) {
      groups.push({ job });
    } else if (!seenBatches.has(job.batch_id)) {
      seenBatches.add(job.batch_id);
      groups.push({ batchId: job.batch_id, jobs: jobs.filter((j) => j.batch_id === job.batch_id) });
    }
  }

  const running = jobs.filter((job) => job.status === "running").length;
  const waiting = jobs.filter((job) => job.status === "queued").length;
  const done = jobs.filter((job) => job.status === "done").length;
  const counts = [
    running > 0 ? `${running} running` : "",
    waiting > 0 ? `${waiting} waiting` : "",
    done > 0 ? `${done} done` : "",
  ]
    .filter(Boolean)
    .join(", ");

  return (
    <main class="page">
      <header class="masthead">
        <h1>Media Converter</h1>
        <p class="lead">YouTube videos and playlists to audio and video files.</p>
      </header>

      <div class="workbench">
        <ConvertForm formats={formats} onJobCreated={onJobCreated} onBatchCreated={onBatchCreated} />

        <section class="pane queue" aria-labelledby="queue-title">
          <div class="pane-head">
            <h2 class="pane-title" id="queue-title">
              Queue{counts && <span class="pane-count"> · {counts}</span>}
            </h2>
            {jobs.some((job) => !isActive(job)) && (
              <button type="button" class="button quiet" onClick={clearFinished}>
                Clear finished
              </button>
            )}
          </div>
          {loadError && (
            <p class="error" role="alert">
              Can't reach the server. Check that it's running, then reload.
            </p>
          )}
          {actionError && (
            <p class="error" role="alert">
              {actionError}
            </p>
          )}
          {jobs.length === 0 && !loadError && (
            <p class="empty">
              Conversions appear here. Paste a link on the left to start the first one.
            </p>
          )}
          <ul class="jobs">
            {groups.map((group) =>
              "job" in group ? (
                <JobItem
                  key={group.job.id}
                  job={group.job}
                  formats={formats}
                  onRemove={removeJob}
                  onQualityChange={changeJobQuality}
                />
              ) : (
                <BatchGroup
                  key={group.batchId}
                  batch={batches.find((batch) => batch.id === group.batchId)}
                  jobs={group.jobs}
                  formats={formats}
                  onRemoveJob={removeJob}
                  onRemoveBatch={removeBatch}
                  onJobQualityChange={changeJobQuality}
                  onBatchQualityChange={changeBatchQuality}
                />
              ),
            )}
          </ul>
        </section>
      </div>
    </main>
  );
}
