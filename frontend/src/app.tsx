import { useEffect, useRef, useState } from "preact/hooks";

import {
  clearFinishedJobs,
  deleteJob,
  isActive,
  listFormats,
  listJobs,
  watchJob,
  type FormatInfo,
  type Job,
} from "./api";
import { ConvertForm } from "./components/ConvertForm";
import { JobItem } from "./components/JobItem";

const REFRESH_MS = 60_000;

export function App() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [formats, setFormats] = useState<FormatInfo[]>([]);
  const [loadError, setLoadError] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  // Jobs currently being followed, by id, with the function that stops each.
  const watchers = useRef(new Map<string, () => void>());

  function upsert(job: Job) {
    setJobs((current) =>
      current.some((existing) => existing.id === job.id)
        ? current.map((existing) => (existing.id === job.id ? job : existing))
        : [job, ...current],
    );
  }

  function follow(job: Job) {
    if (!isActive(job) || watchers.current.has(job.id)) return;
    const stop = watchJob(job.id, (update) => {
      upsert(update);
      if (!isActive(update)) watchers.current.delete(update.id);
    });
    watchers.current.set(job.id, stop);
  }

  useEffect(() => {
    Promise.all([listFormats(), listJobs()])
      .then(([loadedFormats, loadedJobs]) => {
        setFormats(loadedFormats);
        setJobs(loadedJobs);
        loadedJobs.forEach(follow);
      })
      .catch(() => setLoadError(true));
    // Pick up changes made elsewhere, such as conversions that expired.
    const refresh = setInterval(() => {
      if (document.hidden) return;
      listJobs()
        .then((loaded) => {
          setJobs(loaded);
          loaded.forEach(follow);
        })
        .catch(() => {});
    }, REFRESH_MS);
    return () => {
      clearInterval(refresh);
      watchers.current.forEach((stop) => stop());
      watchers.current.clear();
    };
  }, []);

  function forget(job: Job) {
    watchers.current.get(job.id)?.();
    watchers.current.delete(job.id);
    setJobs((current) => current.filter((existing) => existing.id !== job.id));
  }

  async function remove(job: Job) {
    setActionError(null);
    try {
      await deleteJob(job.id);
      forget(job);
    } catch {
      // It may already be gone (expired or removed in another tab); reload to see.
      setActionError("Couldn't remove that conversion. Reload the page and try again.");
    }
  }

  async function clearFinished() {
    const finished = jobs.filter((job) => !isActive(job));
    if (!confirm(`Remove ${finished.length} finished conversion(s) and their files?`)) return;
    setActionError(null);
    try {
      await clearFinishedJobs();
      finished.forEach(forget);
    } catch {
      setActionError("Couldn't clear the finished conversions. Try again.");
    }
  }

  function onJobCreated(job: Job) {
    upsert(job);
    follow(job);
  }

  return (
    <main class="page">
      <header>
        <h1>Media Converter</h1>
        <p class="lead">Convert YouTube videos to audio and video files.</p>
      </header>

      <ConvertForm formats={formats} onJobCreated={onJobCreated} />

      {loadError && (
        <p class="error" role="alert">
          Can't reach the server. Check that it's running, then reload.
        </p>
      )}

      {jobs.length > 0 && (
        <section>
          <div class="section-head">
            <h2 class="section-title">Conversions</h2>
            {jobs.some((job) => !isActive(job)) && (
              <button type="button" class="button quiet" onClick={clearFinished}>
                Clear finished
              </button>
            )}
          </div>
          {actionError && (
            <p class="error" role="alert">
              {actionError}
            </p>
          )}
          <ul class="jobs">
            {jobs.map((job) => (
              <JobItem key={job.id} job={job} formats={formats} onRemove={remove} />
            ))}
          </ul>
        </section>
      )}
    </main>
  );
}
