import { appUrl, isActive, type Batch, type FormatInfo, type Job } from "../api";
import { formatQuality } from "../format";
import { JobItem } from "./JobItem";
import { QualitySelect } from "./QualitySelect";

/** The conversions of one playlist, shown as a group with shared actions. */
export function BatchGroup({
  batch,
  jobs,
  formats,
  onRemoveJob,
  onRemoveBatch,
  onJobQualityChange,
  onBatchQualityChange,
}: {
  batch: Batch | undefined;
  jobs: Job[];
  formats: FormatInfo[];
  onRemoveJob: (job: Job) => void;
  onRemoveBatch: (batchId: string, jobs: Job[]) => void;
  onJobQualityChange: (job: Job, quality: string) => void;
  onBatchQualityChange: (batchId: string, quality: string, includeFinished: boolean) => void;
}) {
  const ordered = [...jobs].sort((a, b) => (a.position ?? 0) - (b.position ?? 0));
  const done = jobs.filter((job) => job.status === "done").length;
  const failed = jobs.filter((job) => job.status === "failed").length;
  const waiting = jobs.filter((job) => job.status === "queued");
  const active = jobs.some(isActive);
  const finished = done + failed;

  const format = formats.find((f) => f.id === jobs[0].format);
  // While videos are waiting the menu changes those. Once the whole playlist
  // has finished, it converts every video again at the new quality.
  const redoAll = !active && finished > 0;
  const affected = waiting.length > 0 ? waiting : redoAll ? jobs : [];
  const adjustable = affected.length > 0 && format != null && format.qualities.length > 0;
  // The affected videos may differ once one has been changed on its own, in
  // which case nothing is preselected.
  const affectedQuality =
    affected.length > 0 && new Set(affected.map((job) => job.quality)).size === 1
      ? affected[0].quality
      : null;

  // Videos can end up with different qualities once some were changed.
  const sharedQuality = !format
    ? ""
    : new Set(jobs.map((job) => job.quality)).size === 1
      ? formatQuality(format.kind, jobs[0].quality)
      : "Mixed quality";

  const summary = [
    format?.label ?? jobs[0].format.toUpperCase(),
    format && !adjustable ? sharedQuality : "",
    `${done} of ${jobs.length} done`,
    failed > 0 ? `${failed} failed` : "",
    waiting.length > 0 ? `${waiting.length} waiting` : "",
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <li class="batch">
      <div class="batch-head">
        <div class="job-body">
          <p class="job-title">
            {batch?.title ?? "Playlist"} <span class="kind">playlist</span>
          </p>
          <p class="meta">{summary}</p>
          {active && (
            <div class="progress">
              <progress
                aria-label={`${finished} of ${jobs.length} videos finished`}
                max={jobs.length}
                value={finished}
              />
            </div>
          )}
        </div>
        <div class="job-actions">
          {adjustable && (
            <QualitySelect
              format={format}
              value={affectedQuality}
              label={
                redoAll
                  ? `Quality for all videos of ${batch?.title ?? "this playlist"}`
                  : `Quality for the ${waiting.length} waiting videos of ${batch?.title ?? "this playlist"}`
              }
              onChange={(quality) => onBatchQualityChange(jobs[0].batch_id!, quality, redoAll)}
            />
          )}
          {batch && done > 0 && (
            <a class="button primary" href={appUrl(batch.zip_url)} download>
              {done === jobs.length ? "Download all" : `Download ${done}`} (zip)
            </a>
          )}
          <button
            type="button"
            class="button quiet"
            aria-label={`${active ? "Cancel" : "Remove"} playlist ${batch?.title ?? ""}`}
            onClick={() => onRemoveBatch(jobs[0].batch_id!, jobs)}
          >
            {active ? "Cancel all" : "Remove all"}
          </button>
        </div>
      </div>
      <details open={active}>
        <summary class="meta">Videos</summary>
        <ul class="jobs">
          {ordered.map((job) => (
            <JobItem
              key={job.id}
              job={job}
              formats={formats}
              onRemove={onRemoveJob}
              onQualityChange={onJobQualityChange}
            />
          ))}
        </ul>
      </details>
    </li>
  );
}
