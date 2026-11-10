# Notes for coding agents

What this project is and how to run it is in [README.md](README.md). This file
holds what you can't easily see from the code: how to work here, and lessons
that cost time to learn.

## Working here

- **Commit only when asked.** Messages follow `type: summary` (`feat:`, `fix:`,
  `docs:`). No attribution or co-author lines.
- **Run the backend tests before reporting a change as done:**
  `cd backend && python -m pytest -q`. They need no network and no ffmpeg.
- **Type-check the frontend:** `cd frontend && npx tsc --noEmit`
  (`npm run build` does this too).
- **Check real behaviour in the container,** not only in tests:
  `docker compose up -d --build`, then use http://localhost:8000. Anything that
  touches yt-dlp options or YouTube links must be tried against a real video.
- **Use openly licensed videos for testing,** for example the Blender
  channel's films (`aqz-KE-bpKQ` is Big Buck Bunny).
- **Keep user-facing text plain.** Error messages say what happened and what
  to do, in a full sentence, with no yt-dlp or command-line wording.
- **README sections are fixed:** title, Self host, Stack, Routes, Contributing,
  Restrictions, Limitations, Copyright, License. When you add a route, a
  setting, a restriction or a dependency, update the matching section and
  table. A new setting also goes in `.env.example`, `docker-compose.yml` and
  `backend/app/config.py`.

## How it fits together

- `backend/app/converter.py` is the only place that talks to yt-dlp and ffmpeg.
  Everything it raises for the user is a `ConversionError` with a safe message.
- `worker.py` runs jobs on a thread pool and writes status to SQLite. The page
  reads status back: live over Server-Sent Events for running jobs, and by
  reloading the list for the rest.
- A playlist conversion is a "batch": a row in `batches` plus one ordinary job
  per video.
- Endpoints are plain `def`, so FastAPI runs the blocking yt-dlp and SQLite
  calls in its thread pool. Keep it that way, or use `run_in_threadpool`.
- The Docker image serves the built frontend from the same server. In
  development Vite serves it and proxies `/api`.

## Things learned the hard way

### yt-dlp and YouTube

- **A JavaScript runtime is required.** yt-dlp needs one to solve YouTube's
  player challenges. The image ships Deno because yt-dlp uses it without
  configuration and runs it sandboxed; Node or Bun would need yt-dlp's
  `js_runtimes` option and give up the sandbox.
- **Never reuse stream links between yt-dlp sessions.** Links fetched by one
  `YoutubeDL` instance return HTTP 403 when another downloads them, even with
  the same cookies. So a conversion always fetches its own metadata, and
  `extract_info(download=False)` and `process_ie_result(info, download=True)`
  run on the same instance. The lookup cache holds display details only.
- **Search and feed links are endless lists.** A `results?search_query=…` link
  once hung a lookup for minutes. `validate_url` accepts only video and
  playlist paths, and `allowed_extractors` names only the video and playlist
  extractors. Don't widen either without testing the new link type for real.
- **`allowed_extractors` matches extractor names** (`youtube`, `youtube:tab`),
  case-insensitively. Class-style names such as `YoutubeTab` silently match
  nothing.
- **Bursts trigger the bot check** ("Sign in to confirm you're not a bot").
  Lookups are spaced by a global throttle and retried. When testing, don't
  fire many lookups at once, and expect the check on datacenter connections.
- **The PO token provider is unused unless the `mweb` client is requested,**
  which `_base_opts` adds only when `YT_POT_PROVIDER_URL` is set.
- **yt-dlp rewrites its cookies file on exit.** It is given a private temporary
  copy, because the real one may be mounted read-only or shared between jobs.
- **Progress hooks report one stream at a time.** Video and audio download
  separately, so overall progress is computed from the sizes in the top-level
  `requested_formats`; the hook's own `info_dict` describes a single stream.
- **Post-processor option keys are misspelled upstream:** `preferedformat` for
  the remuxer and convertor, `preferredcodec` for audio extraction.
- **Cancelling works from callbacks.** A job stops when a progress callback
  raises `JobCancelled`, so nothing can interrupt ffmpeg mid-step.
- **New yt-dlp error wording falls through** to a shortened copy of the
  original text. Add a phrase to `_ERROR_MESSAGES` when you meet one; order
  matters, most specific first.

### The server and the page

- **The content security policy is strict.** `main.py` allows scripts and
  requests from the app itself and images from `ytimg.com` only. A new outside
  resource (font, script, image host) needs the policy changed, or it fails
  silently in the browser. `/docs` is exempt.
- **The page never uses a path that starts with `/`.** It may be served under
  a sub-path by a reverse proxy, where `/api/...` would reach another service.
  Requests and links go through `appUrl()` in `api.ts`, and Vite builds with
  `base: "./"`. The server's `download_url` and `zip_url` still start with
  `/`; pass them through `appUrl()` before using them in the page.
- **Browsers allow about six connections per site.** Only three running jobs
  are followed live (`MAX_LIVE` in `app.tsx`); more would starve every other
  request on the page.
- **Database changes need a migration.** `Database.init` adds missing columns
  with `ALTER TABLE`; add new columns there as well as in `SCHEMA`, because
  existing installations keep their database.
- **Job ids become file names** (`<job id>.<ext>`) and glob patterns. They are
  only ever generated by the server; look a job up in the database before
  using an id from a request.

### Tests

- **Tests must never reach YouTube.** Replace `converter.convert`,
  `converter.probe` or `converter._extract` with fakes (see `conftest.py`).
- **Wait for every job a test starts** (`wait_for_job`). A job that outlives
  the test's fake converter runs the real one and makes real requests.
- **Settings are cached.** After changing an environment variable in a test,
  call `get_settings.cache_clear()`; the worker also keeps its own copy in
  `worker.settings`.

### Windows development machine

These apply to the maintainer's setup, not to the project in general.

- There is no Python on Windows. Tests run in WSL, in the virtual environment
  `~/.venvs/media-convert`:
  `wsl -e bash -c "cd /mnt/c/.../media-convert/backend && ~/.venvs/media-convert/bin/python -m pytest -q"`.
- From Git Bash, prefix `wsl` and `docker` commands that contain Unix paths
  with `MSYS_NO_PATHCONV=1`, or the paths are rewritten into Windows ones.
- Don't pass multi-line scripts to WSL or `docker compose exec` on the command
  line; write them to a file and run that, or pipe them through stdin.
- Docker Desktop has to be started by the maintainer; it can't be started from
  a script.
