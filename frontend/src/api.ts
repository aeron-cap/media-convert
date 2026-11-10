export interface Health {
  status: string;
  ffmpeg: boolean;
}

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path);
  if (!response.ok) {
    throw new Error(`${path} returned ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export function getHealth(): Promise<Health> {
  return getJson<Health>("/api/health");
}
