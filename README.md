# TITLE_HERE

A small web app that turns YouTube videos and playlists into audio or video
files. Paste a link, pick a format, and download the result.

- **Audio:** MP3, M4A, OGG, FLAC, WAV
- **Video:** MP4, WEBM, MKV, up to the video's best resolution
- **Playlists:** tick the videos you want and download them as one zip
- **History:** finished conversions stay in the list until they expire

## Self host

This project is meant to be self-hosted: you run it on your own computer or
server, for yourself and people you trust. There is no hosted version and no
public instance.

> **There is no login.** Anyone who can open the page can use it and can see
> and remove every conversion. Keep it on a network you trust, or put it behind
> a VPN or a reverse proxy that asks for a password. Do not put it on the open
> internet as it is.

You need [Docker](https://docs.docker.com/get-docker/) with Docker Compose,
about 1 GB of disk space for the image, and room for the files you convert.

```bash
git clone <this repository's address> media-convert
```

```bash
cd media-convert
```

```bash
docker compose up -d --build
```

The first build takes a few minutes. Then open http://localhost:8000, or
`http://<the server's address>:8000` from another device on your network.

To stop it, run `docker compose down`. Your files and history are kept in a
Docker volume and are still there next time. `docker compose down -v` wipes
them.

### Settings

Everything works without configuration. To change something, copy
`.env.example` to `.env`, edit it, and run `docker compose up -d` again.

| Setting | Default | What it does |
| --- | --- | --- |
| `PORT` | `8000` | Port the app is reachable on. |
| `MAX_CONCURRENT_JOBS` | `2` | Conversions that run at the same time. |
| `RETENTION_HOURS` | `24` | Hours a finished file is kept. `0` keeps it until you remove it. |
| `MAX_DURATION_MINUTES` | `180` | Longest video accepted. `0` removes the limit. |
| `MAX_PLAYLIST_ITEMS` | `50` | Most videos taken from one playlist. |
| `MAX_PENDING_JOBS` | `100` | Most conversions waiting or running at once. `0` removes the limit. |
| `YT_MIN_INTERVAL_SECONDS` | `2` | Minimum gap between lookups on YouTube. |
| `YT_REQUEST_SLEEP_SECONDS` | `0.5` | Pause between the requests of one lookup. |
| `YT_RETRIES` | `2` | Retries when YouTube shows its bot check. |
| `YT_PROXY` | none | Proxy for all YouTube traffic. |
| `YT_POT_PROVIDER_URL` | none | Address of a PO token provider (see below). |

### When YouTube asks "are you a bot?"

YouTube sometimes refuses a server with "Sign in to confirm you're not a bot".
The app already spaces its requests out and retries on its own. If you still
see the message often, try these in order:

1. **Use a home connection.** Servers in datacenters are flagged far more than
   home internet. Host the app at home, or point `YT_PROXY` at a proxy on your
   home network.
2. **PO tokens.** Set `YT_POT_PROVIDER_URL=http://pot-provider:4416` in `.env`
   and run `docker compose --profile pot up -d`. No account is needed.
3. **Account cookies.** Export a `cookies.txt` (Netscape format) from a
   logged-in browser, put it next to `docker-compose.yml`, and uncomment the
   `cookies.txt` line in that file. YouTube can ban accounts used this way, so
   use a throwaway account, never your main one.

### Keeping it working

YouTube changes often, and conversions start failing when the downloader falls
behind. If things that used to work stop working, rebuild:

```bash
docker compose build --pull --no-cache
```

```bash
docker compose up -d
```

## Stack

| Part | What it does here |
| --- | --- |
| [Python](https://www.python.org/) + [FastAPI](https://fastapi.tiangolo.com/) | The server and its API. |
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | Looks videos up and downloads them. |
| [Deno](https://deno.com/) | Runs the small scripts yt-dlp needs to read YouTube's player, in a sandbox. |
| [ffmpeg](https://ffmpeg.org/) | Converts the download into the format you chose. |
| [Preact](https://preactjs.com/) + [Vite](https://vite.dev/) | The web page. |
| [SQLite](https://sqlite.org/) | Remembers conversions and their status. |
| [Docker](https://www.docker.com/) | Packages all of the above into one image. |

## Routes

The page talks to the server through these. Interactive documentation is at
`/docs` while the app is running.

| Method | Route | What it does |
| --- | --- | --- |
| `GET` | `/` | The web page. |
| `GET` | `/api/health` | Says whether the server and ffmpeg are working. |
| `GET` | `/api/formats` | Lists the output formats and their quality choices. |
| `POST` | `/api/probe` | Looks a link up and returns the video's or playlist's details. |
| `POST` | `/api/jobs` | Starts converting one video. |
| `GET` | `/api/jobs` | Lists all conversions, newest first. |
| `DELETE` | `/api/jobs` | Removes every finished conversion and its file. |
| `GET` | `/api/jobs/{id}` | Returns one conversion and its status. |
| `PATCH` | `/api/jobs/{id}` | Changes a conversion's quality. A finished one is converted again and its file replaced. |
| `DELETE` | `/api/jobs/{id}` | Removes one conversion; cancels it if it is still running. |
| `GET` | `/api/jobs/{id}/events` | Sends live progress updates for one conversion. |
| `GET` | `/api/jobs/{id}/file` | Downloads the finished file. |
| `POST` | `/api/batches` | Starts converting the chosen videos of a playlist. |
| `GET` | `/api/batches` | Lists playlist conversions. |
| `PATCH` | `/api/batches/{id}` | Changes the quality of a playlist's waiting videos and, if asked, converts its finished ones again. |
| `DELETE` | `/api/batches/{id}` | Removes a playlist conversion and all its files. |
| `GET` | `/api/batches/{id}/zip` | Downloads a playlist's finished files as one zip. |

## Contributing

Contributions are welcome. A few courtesies keep things pleasant:

- **Open an issue first** for anything bigger than a small fix, so nobody
  builds something that won't be merged.
- **Keep changes small and focused.** One topic per pull request.
- **Run the tests** before you send it, and add one for what you changed.
- **Describe what and why** in the pull request, in plain words.
- **Be kind.** Assume good intent, and review the code, not the person.
- **Don't send** features whose purpose is to get around paywalls, DRM or
  private content. They won't be merged.

If you work with a coding agent, [AGENTS.md](AGENTS.md) has the project's
conventions and the pitfalls already found.

To work on it locally you need Python 3.12 or newer, Node 22, and ffmpeg and
[Deno](https://deno.com) on your PATH.

```bash
cd backend
python -m venv .venv
source .venv/bin/activate     # on Windows: .venv/Scripts/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload
pytest
```

```bash
cd frontend
npm install
npm run dev
```

The page then runs on http://localhost:5173 and talks to the server on
port 8000.

## Restrictions

Things the app refuses on purpose:

- **YouTube only.** Links to any other site are rejected.
- **Videos and playlists only.** Channel, search and feed links are rejected.
- **No protected content.** Private, members-only, paid and region-blocked
  videos are not converted, and nothing in the app tries to get around that.
- **No live streams** while they are still running.
- **Age-restricted videos** only work if you supply cookies from an account
  that is allowed to watch them.
- **Length limit.** Videos longer than 3 hours are refused by default.
- **Playlist limit.** Only the first 50 videos of a playlist are offered by
  default.
- **Queue limit.** At most 100 conversions may be waiting or running by
  default.
- **Files expire.** Finished files are deleted after 24 hours by default.

## Limitations

Things the app can't do, or doesn't do well:

- **No accounts.** Everyone who can open the page shares one list of
  conversions.
- **It depends on YouTube.** When YouTube changes something, conversions can
  fail until you rebuild the app. YouTube may also block the server with its
  bot check, especially on datacenter connections.
- **Conversions don't survive a restart.** One that was running when the
  server stopped is marked as failed and has to be started again.
- **Changing the quality of a finished file downloads it again.** The app
  keeps only the converted file, not YouTube's original, so a new quality
  means fetching the video again; the old file is removed straight away.
- **A running conversion can't be changed,** and the format can't be changed
  at all. Cancel it and start again instead.
- **Cancelling isn't instant** once a download has finished and ffmpeg is
  converting; it stops when that step ends.
- **WEBM can be slow**, because the video is re-encoded when YouTube has no
  WEBM version of it.
- **MP4 and MKV above 1080p** use newer video formats (VP9 or AV1) that some
  older players can't open.
- **M4A has no quality choice**; it keeps YouTube's audio exactly as it is.
- **One server only.** It isn't built to be spread across several machines or
  to serve many people at once.

## Copyright

**This software.** Copyright © 2026 aeron-cap and contributors. It is released
under the MIT License; see [License](#license).

**No affiliation.** This project is not affiliated with, endorsed by, or
sponsored by YouTube or Google. "YouTube" is a trademark of Google LLC and is
used here only to say what the app works with.

**The videos are not ours, and not automatically yours.** This project
contains no videos, music or other media, hosts none, and gives you no rights
to any. Videos on YouTube belong to their creators or rights holders. Only
download what you are allowed to: your own uploads, works in the public
domain, works under a licence that permits it (such as Creative Commons), or
works you have the owner's permission for.

**You are responsible for how you use it.** The person who runs or uses a copy
of this app decides what it downloads. It is your job to follow the copyright
law where you live and the terms of any service you use it with, including
[YouTube's Terms of Service](https://www.youtube.com/t/terms), which restrict
downloading. The authors and contributors do not control, monitor, or take
part in what any copy of this software is used for, and accept no
responsibility for it. That includes anyone who uses it to infringe copyright,
break a service's terms, resell content, or run a public or paid service.

**Not for getting around protection.** The app does not break DRM or
encryption and is not meant to bypass paywalls, private videos, or other
access controls.

**No warranty.** The software is provided as it is, without any guarantee, as
the licence says.

**Rights holders.** This repository holds only source code. If you believe
something in the repository itself infringes your rights, open an issue and it
will be looked at.

**Third-party software.** The app uses these projects, each under its own
licence. Their licences apply to them, not this project's.

| Project | Licence |
| --- | --- |
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | The Unlicense |
| [ffmpeg](https://ffmpeg.org/legal.html) | LGPL 2.1 or later; GPL for the build in the Docker image |
| [FastAPI](https://github.com/fastapi/fastapi) | MIT |
| [Uvicorn](https://github.com/encode/uvicorn) | BSD 3-Clause |
| [Preact](https://github.com/preactjs/preact) | MIT |
| [Vite](https://github.com/vitejs/vite) | MIT |
| [Deno](https://github.com/denoland/deno) | MIT |
| [SQLite](https://sqlite.org/copyright.html) | Public domain |
| [Python](https://docs.python.org/3/license.html) | PSF License |
| [bgutil-ytdlp-pot-provider](https://github.com/Brainicism/bgutil-ytdlp-pot-provider) (optional) | GPL 3.0 |

None of these are copied into this repository; they are downloaded when the
Docker image is built. If you share a built image with others, you are
distributing those projects too and their licences (notably the GPL for
ffmpeg and the PO token plugin) apply to what you share.

*This section is a plain-language notice, not legal advice.*

## License

Released under the [MIT License](LICENSE). In short: you may use, copy, change
and share this software, including commercially, as long as you keep the
copyright notice, and it comes with no warranty.
