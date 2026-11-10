import { isActive, type FormatInfo, type Job } from "../api";
import { formatDuration, formatQuality, formatSize } from "../format";

export function JobItem({ job, formats }: { job: Job; formats: FormatInfo[] }) {
  const format = formats.find((f) => f.id === job.format);
  const details = [
    format?.label ?? job.format.toUpperCase(),
    format ? formatQuality(format.kind, job.quality) : "",
    formatDuration(job.duration),
    formatSize(job.filesize),
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <li class="job">
      {job.thumbnail ? (
        <img class="job-thumb" src={job.thumbnail} alt="" referrerpolicy="no-referrer" />
      ) : (
        <div class="job-thumb" />
      )}
      <div class="job-body">
        <p class="job-title">{job.title ?? job.url}</p>
        <p class="meta">{details}</p>
        {isActive(job) && <Progress job={job} />}
        {job.status === "failed" && (
          <p class="error" role="alert">
            {job.error ?? "The conversion failed."}
          </p>
        )}
      </div>
      {job.status === "done" && job.download_url && (
        <a class="button primary" href={job.download_url} download>
          Download
        </a>
      )}
    </li>
  );
}

function Progress({ job }: { job: Job }) {
  // Only the download has a measurable percentage; the other stages are
  // shown as an indeterminate bar.
  const downloading = job.stage === "downloading";
  const percent = Math.round(job.progress * 100);
  const label =
    job.status === "queued"
      ? "Waiting to start…"
      : downloading
        ? `Downloading… ${percent}%`
        : job.stage === "converting"
          ? "Converting…"
          : job.stage === "retrying"
            ? "YouTube is busy, trying again…"
            : "Starting…";

  return (
    <div class="progress">
      <progress
        aria-label={label}
        max={100}
        {...(downloading ? { value: percent } : {})}
      />
      <span class="meta" aria-hidden="true">
        {label}
      </span>
    </div>
  );
}
