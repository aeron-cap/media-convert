export type Format = string;
export type Status = "queued" | "running" | "done" | "failed";

export interface VideoInfo {
  video_id: string | null;
  title: string | null;
  thumbnail: string | null;
  duration: number | null;
  uploader: string | null;
  /** Height in pixels of the best video stream available. */
  max_height: number | null;
}

export interface FormatInfo {
  id: Format;
  label: string;
  kind: "audio" | "video";
  /** Bitrates in kbps for audio, maximum heights or "best" for video. */
  qualities: string[];
  default_quality: string | null;
  note: string | null;
}

export interface Job {
  id: string;
  url: string;
  format: Format;
  quality: string | null;
  status: Status;
  stage: string | null;
  progress: number;
  error: string | null;
  video_id: string | null;
  title: string | null;
  thumbnail: string | null;
  duration: number | null;
  filesize: number | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  download_url: string | null;
  /** When the job and its file are deleted automatically; null if never. */
  expires_at: string | null;
}

/** An error whose message is meant to be shown to the user as is. */
export class ApiError extends Error {}

async function request<T>(path: string, body?: unknown, method?: string): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      method: method ?? (body === undefined ? "GET" : "POST"),
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError("Can't reach the server. Check that it's running and try again.");
  }
  if (!response.ok) {
    throw new ApiError(await errorMessage(response));
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

async function errorMessage(response: Response): Promise<string> {
  try {
    const data = await response.json();
    if (typeof data.detail === "string") return data.detail;
  } catch {
    // Not JSON; fall through to the generic message.
  }
  return `The server returned an error (${response.status}).`;
}

export function probe(url: string): Promise<VideoInfo> {
  return request<VideoInfo>("/api/probe", { url });
}

export function listFormats(): Promise<FormatInfo[]> {
  return request<FormatInfo[]>("/api/formats");
}

export function createJob(url: string, format: Format, quality: string | null): Promise<Job> {
  return request<Job>("/api/jobs", { url, format, quality });
}

export function listJobs(): Promise<Job[]> {
  return request<Job[]>("/api/jobs");
}

/** Delete a job and its file; a queued or running job is cancelled. */
export function deleteJob(jobId: string): Promise<void> {
  return request<void>(`/api/jobs/${jobId}`, undefined, "DELETE");
}

/** Delete every finished job and its file. */
export function clearFinishedJobs(): Promise<{ deleted: number }> {
  return request<{ deleted: number }>("/api/jobs", undefined, "DELETE");
}

export function isActive(job: Job): boolean {
  return job.status === "queued" || job.status === "running";
}

/**
 * Follow a job's progress. Calls `onUpdate` on every change and stops by
 * itself when the job ends. Returns a function that stops it early.
 */
export function watchJob(jobId: string, onUpdate: (job: Job) => void): () => void {
  const source = new EventSource(`/api/jobs/${jobId}/events`);
  source.onmessage = (event) => {
    const job = JSON.parse(event.data) as Job;
    onUpdate(job);
    if (!isActive(job)) source.close();
  };
  // On a dropped connection EventSource reconnects by itself, and the server
  // replays the current state, so no extra handling is needed here.
  return () => source.close();
}
