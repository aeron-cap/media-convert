# Media Converter

A self-hosted web app that converts YouTube videos and playlists into audio and
video files. Paste a link, pick a format, download the result.

- **Audio:** MP3, M4A, OGG, FLAC, WAV
- **Video:** MP4, WEBM, MKV, up to the video's best resolution
- **Playlists:** pick the videos you want and download them as one zip
- **History:** finished conversions stay available until they expire

Built with FastAPI, yt-dlp, ffmpeg, Preact, SQLite and Docker.

> **There is no login.** Anyone who can reach the page can use it. Run it on a
> network you trust, or put it behind a VPN or a reverse proxy that adds
> authentication. Do not expose it to the internet as it is.

## Set it up

You need [Docker](https://docs.docker.com/get-docker/) with Docker Compose
(included in Docker Desktop), about 1 GB of disk space for the image, and room
for the files you convert.

1. Get the code:

   ```bash
   git clone <this repository's address> media-convert
   ```

   ```bash
   cd media-convert
   ```

2. Start it:

   ```bash
   docker compose up -d --build
   ```

   The first build takes a few minutes.

3. Open http://localhost:8000, or `http://<the server's address>:8000` from
   another device on the same network.

To stop it, run `docker compose down`. Converted files and history are kept in
a Docker volume and are still there when you start it again.

## Use it

1. Paste a link to a YouTube video or playlist and press **Find**.
2. Choose a format and, where there is one, a quality.
3. Press **Convert**. Progress is shown in the list below.
4. Press **Download** when it finishes. For a playlist, **Download all (zip)**
   fetches every finished file at once.

Links that work: `youtube.com/watch?v=…`, `youtu.be/…`, `youtube.com/shorts/…`,
`music.youtube.com/watch?v=…` and `youtube.com/playlist?list=…`. A video opened
from inside a playlist (`watch?v=…&list=…`) is treated as that one video.

What can't be converted: private, members-only, paid and region-blocked videos,
live streams that are still running, and age-restricted videos unless you set
up [account cookies](#youtube-bot-checks).

Good to know about formats:

- **M4A** copies YouTube's own audio without re-encoding, so it has no quality
  choice and is the fastest audio format.
- **MP4** and **MKV** copy the video as it is. Above 1080p, YouTube's video is
  VP9 or AV1 rather than H.264, which older players may not open.
- **WEBM** is re-encoded when YouTube has no WEBM version, which is slow.

## Settings

All settings are optional. Copy `.env.example` to `.env`, change what you need,
and run `docker compose up -d` again.

| Setting | Default | What it does |
| --- | --- | --- |
| `PORT` | `8000` | Port the app is reachable on. |
| `MAX_CONCURRENT_JOBS` | `2` | Conversions that run at the same time. |
| `RETENTION_HOURS` | `24` | Hours a finished conversion and its file are kept. `0` keeps them until you remove them. |
| `MAX_DURATION_MINUTES` | `180` | Longest video accepted. `0` removes the limit. |
| `MAX_PLAYLIST_ITEMS` | `50` | Most videos listed and converted from one playlist. |
| `MAX_PENDING_JOBS` | `100` | Most conversions that may be waiting or running; more are refused. `0` removes the limit. |
| `YT_MIN_INTERVAL_SECONDS` | `2` | Minimum gap between lookups on YouTube. |
| `YT_REQUEST_SLEEP_SECONDS` | `0.5` | Pause between the requests of one lookup. |
| `YT_RETRIES` | `2` | Retries when YouTube shows its bot check. |
| `YT_PROXY` | none | Proxy for all YouTube traffic. |
| `YT_POT_PROVIDER_URL` | none | Address of a PO token provider (see below). |

## YouTube bot checks

YouTube sometimes answers a server with "Sign in to confirm you're not a bot".
The app already does three things to avoid that, with no setup:

- It remembers a video's details for a few minutes, so looking the same link up
  again doesn't ask YouTube again.
- It spaces its requests out (`YT_MIN_INTERVAL_SECONDS`, `YT_REQUEST_SLEEP_SECONDS`).
- It retries a few times when the check does appear (`YT_RETRIES`).

If you still see the check often, these are opt-in, strongest last:

1. **Use a home connection.** Datacenter addresses are flagged far more than
   residential ones. Host the app at home, or set `YT_PROXY` to a proxy that
   runs on your home network (for example reached over WireGuard or Tailscale).
2. **PO tokens.** Set `YT_POT_PROVIDER_URL=http://pot-provider:4416` in `.env`
   and start the provider with `docker compose --profile pot up -d`. This runs
   the third-party [bgutil-ytdlp-pot-provider](https://github.com/Brainicism/bgutil-ytdlp-pot-provider).
   It needs no account.
3. **Account cookies.** Export cookies from a logged-in browser as a
   Netscape-format `cookies.txt`, put it next to `docker-compose.yml`, and
   uncomment the `cookies.txt` volume line there. YouTube can flag or ban
   accounts used this way, so use a throwaway account, never your main one.
   Cookies expire; export them again when the check comes back. Cookies are
   also what lets age-restricted videos through.

`GET /api/health` shows which of these are switched on.

## Keep it working

YouTube changes often, and conversions start failing when the downloader
(yt-dlp) falls behind. Rebuilding picks up its latest release:

```bash
docker compose build --pull --no-cache
```

```bash
docker compose up -d
```

Do this whenever conversions that used to work start failing, and every few
weeks otherwise.

## Troubleshooting

| What you see | What to do |
| --- | --- |
| "YouTube asked this server to prove it isn't a bot" | Wait a minute and retry. If it keeps happening, see [YouTube bot checks](#youtube-bot-checks). |
| Every conversion fails, including ones that used to work | Rebuild to update yt-dlp: see [Keep it working](#keep-it-working). |
| "This server couldn't reach YouTube" | Check the server's internet connection and, if set, `YT_PROXY`. |
| "Too many conversions are waiting already" | Wait for some to finish, or raise `MAX_PENDING_JOBS`. |
| "The server ran out of disk space" | Press **Clear finished**, or lower `RETENTION_HOURS`. |
| A conversion shows "Interrupted by a server restart" | The app was restarted while it ran. Start it again. |
| The page says it can't reach the server | Check `docker compose ps` and `docker compose logs media-convert`. |

## Where things are stored

Everything lives in the Docker volume `media-convert-data`: the SQLite database
(`media-convert.db`) and the converted files (`files/`). Files and their history
entries are deleted after `RETENTION_HOURS`. To wipe everything:

```bash
docker compose down -v
```

## What is and isn't protected

The app only accepts links to YouTube's own addresses and only runs yt-dlp's
YouTube code, so it can't be used to make the server fetch other sites. It
runs as an unprivileged user in the container, limits video length, playlist
size and queue length, and sends downloads as attachments.

It does **not** have accounts, passwords or per-user separation: everyone who
can open the page sees and can remove every conversion. Keeping strangers out
is up to your network, VPN or reverse proxy. If you use a reverse proxy, turn
off response buffering for `/api/jobs/*/events` so progress updates arrive live.

Only convert videos you have the right to download. You are responsible for
following YouTube's terms and the copyright law where you live.

## Develop

Backend (Python 3.12 or newer, with ffmpeg and [Deno](https://deno.com) on your PATH):

```bash
cd backend
python -m venv .venv
.venv/Scripts/activate        # Windows; use `source .venv/bin/activate` elsewhere
pip install -r requirements-dev.txt
uvicorn app.main:app --reload
pytest
```

Frontend (Node 22):

```bash
cd frontend
npm install
npm run dev
```

The Vite dev server runs on http://localhost:5173 and proxies `/api` to the
backend on port 8000. The API is documented at http://localhost:8000/docs.

## Layout

```
backend/app/            FastAPI application
  api/                  HTTP endpoints: health, jobs, playlists (batches)
  converter.py          yt-dlp and ffmpeg: lookup, download, conversion
  worker.py             runs conversions on a small thread pool
  cleanup.py            deletes expired conversions and stray files
  db.py                 SQLite storage
backend/tests/          pytest tests
frontend/src/           Preact single-page UI
Dockerfile              multi-stage image: builds the UI, runs the API with ffmpeg
docker-compose.yml      the app, plus the optional PO token provider
```
