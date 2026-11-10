import preact from "@preact/preset-vite";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [preact()],
  // Relative asset paths, so the built page also works from a sub-path
  // behind a reverse proxy (see "Behind a reverse proxy" in the README).
  base: "./",
  server: {
    port: 5173,
    // In development the FastAPI backend runs separately on :8000.
    proxy: {
      "/api": "http://localhost:8000",
    },
  },
});
