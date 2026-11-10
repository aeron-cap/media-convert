import { useEffect, useRef, useState } from "preact/hooks";

import { isActive, listJobs, watchJob, type Job } from "./api";
import { ConvertForm } from "./components/ConvertForm";
import { JobItem } from "./components/JobItem";

export function App() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loadError, setLoadError] = useState(false);
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
    listJobs()
      .then((loaded) => {
        setJobs(loaded);
        loaded.forEach(follow);
      })
      .catch(() => setLoadError(true));
    return () => {
      watchers.current.forEach((stop) => stop());
      watchers.current.clear();
    };
  }, []);

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

      <ConvertForm onJobCreated={onJobCreated} />

      {loadError && (
        <p class="error" role="alert">
          Can't load your conversions. Check that the server is running, then reload.
        </p>
      )}

      {jobs.length > 0 && (
        <section>
          <h2 class="section-title">Conversions</h2>
          <ul class="jobs">
            {jobs.map((job) => (
              <JobItem key={job.id} job={job} />
            ))}
          </ul>
        </section>
      )}
    </main>
  );
}
