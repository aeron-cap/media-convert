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
