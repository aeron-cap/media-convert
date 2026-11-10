import { useEffect, useState } from "preact/hooks";

import { getHealth, type Health } from "./api";

type BackendState =
  | { kind: "loading" }
  | { kind: "up"; health: Health }
  | { kind: "down" };

export function App() {
  const [backend, setBackend] = useState<BackendState>({ kind: "loading" });

  useEffect(() => {
    getHealth()
      .then((health) => setBackend({ kind: "up", health }))
      .catch(() => setBackend({ kind: "down" }));
  }, []);

  return (
    <main class="page">
      <h1>Media Converter</h1>
      <p class="lead">Convert YouTube videos to audio and video files.</p>
      <StatusLine backend={backend} />
    </main>
  );
}

function StatusLine({ backend }: { backend: BackendState }) {
  if (backend.kind === "loading") {
    return <p class="status">Checking the server…</p>;
  }
  if (backend.kind === "down") {
    return <p class="status status-bad">Can't reach the server.</p>;
  }
  if (!backend.health.ffmpeg) {
    return <p class="status status-bad">Server is running, but ffmpeg is missing.</p>;
  }
  return <p class="status status-good">Server is running and ffmpeg is available.</p>;
}
