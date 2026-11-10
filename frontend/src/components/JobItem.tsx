import { isActive, type FormatInfo, type Job } from "../api";
import { formatDuration, formatExpiry, formatQuality, formatSize } from "../format";
import { QualitySelect } from "./QualitySelect";

export function JobItem({
  job,
  formats,
  onRemove,
  onQualityChange,
}: {
  job: Job;
  formats: FormatInfo[];
  onRemove: (job: Job) => void;
  onQualityChange: (job: Job, quality: string) => void;
}) {
  const format = formats.find((f) => f.id === job.format);
  // Quality can be changed while the conversion waits, and again once it has
  // finished (it is then converted again); only a running one is fixed.
  const adjustable = job.status !== "running" && format != null && format.qualities.length > 0;
  const details = [
    format?.label ?? job.format.toUpperCase(),
    format && !adjustable ? formatQuality(format.kind, job.quality) : "",
    formatDuration(job.duration),
    formatSize(job.filesize),
    job.status === "done" ? formatExpiry(job.expires_at) : "",
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
      <div class="job-actions">
        {adjustable && (
          <QualitySelect
            format={format}
            value={job.quality}
            label={`Quality for ${job.title ?? "this conversion"}`}
            onChange={(quality) => onQualityChange(job, quality)}
          />
        )}
        {job.status === "done" && job.download_url && (
          <a class="button primary" href={job.download_url} download>
            Download
          </a>
        )}
        <button
          type="button"
          class="button quiet"
          aria-label={`${isActive(job) ? "Cancel" : "Remove"} ${job.title ?? "this conversion"}`}
          onClick={() => onRemove(job)}
        >
          {isActive(job) ? "Cancel" : "Remove"}
        </button>
      </div>
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
      ? "Waiting to start"
      : downloading
        ? `Downloading ${percent}%`
        : job.stage === "converting"
          ? "Converting…"
          : job.stage === "retrying"
            ? "YouTube is busy, trying again…"
            : "Starting…";

  if (job.status === "queued") {
    return <p class="state">{label}</p>;
  }
  return (
    <div class="progress">
      <progress aria-label={label} max={100} {...(downloading ? { value: percent } : {})} />
      <span class="state" aria-hidden="true">
        {label}
      </span>
    </div>
  );
}
