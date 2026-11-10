# Media Converter

A self-hosted web app that converts YouTube videos into audio and video files
(MP3, MP4 and more). Built with FastAPI, ffmpeg, Preact, SQLite and Docker.

> There is no login. Run it on a trusted network, or put it behind a VPN or a
> reverse proxy that adds authentication. Do not expose it to the internet as is.

## Run it

```bash
docker compose up --build
```

Then open http://localhost:8000.

Settings are optional; copy `.env.example` to `.env` to change them.

## YouTube bot checks

YouTube sometimes answers a server with "Sign in to confirm you're not a bot".
The app already does three things to avoid that, with no setup:

- It reuses a video's details between "Find video" and the conversion, instead
  of asking YouTube twice.
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
   Cookies expire; export them again when the check comes back.

`GET /api/health` shows which of these are switched on.

## Develop

Backend (Python 3.13, ffmpeg on your PATH):

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
backend on port 8000.

## Layout

```
backend/app/     FastAPI application
backend/tests/   pytest tests
frontend/src/    Preact single-page UI
Dockerfile       multi-stage image: builds the UI, runs the API with ffmpeg
```
