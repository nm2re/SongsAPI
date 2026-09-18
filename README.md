# RE;VAULT — SongsAPI

A full-stack web application that automates downloading, organizing, and converting
Apple Music albums to FLAC. Search the Apple Music catalog or paste a link from any
region, download losslessly through a self-hosted decryption wrapper, auto-rename and
convert to FLAC, and sync to cloud storage — driven from one dark-themed web UI with
real-time progress.

Built and deployed solo on an ARM64 VPS.

## Features

- **Apple Music catalog search** — uses the same AMP catalog API as the Apple Music
  app (not the legacy iTunes Search API), searching albums *and* songs so singles and
  album tracks both surface. Falls back to iTunes search if the AMP token can't be fetched.
- **Any-region URL lookup** — paste a `/us/`, `/jp/`, `/gb/` (etc.) Apple Music album
  URL and it resolves to your account's storefront automatically via `filter[equivalents]`,
  so gamdl can actually fetch it.
- **One-click Upload** — orchestrates the full workflow (download → rename → convert → move)
  with live SSE progress.
- **Individual controls** — run any step on its own.
- **Smart organization** — auto-renames folders to `Artist - Album`, handling
  Windows-illegal characters and albums that land in `Compilations/`.
- **Lossless FLAC conversion** — parallel FFmpeg transcode from ALAC.
- **Cloud sync** — moves finished albums via `rclone move` (bypasses FUSE-mount I/O errors).
- **Metadata** — auto-updates year tags on all tracks.
- **Persistent UI state** — localStorage keeps search results, logs, and pinned albums
  across reloads (logs capped to avoid unbounded growth).
- **Auth** — SQLite users, session cookies, argon2 hashing, admin panel.

## Architecture at a glance

```
Browser (index.html, SSE) ──HTTP──▶ FastAPI (main.py)
                                      │
                    ┌─────────────────┼──────────────────┐
                    ▼                 ▼                  ▼
              amp.py (AMP        gamdl 3.8.5        flac_script.sh
              catalog search /   subprocess          (FFmpeg)
              storefront resolve)     │
                                      ▼
                             wrapper-v2 (Docker)
                          HTTP :8080 / decrypt :10021
                          loads Apple's Android libs,
                          does FairPlay key exchange
                                      │
                                      ▼
                                    rclone ──▶ cloud storage
```

## Prerequisites

- Python 3.12+
- FFmpeg
- rclone (configured remote + mount for cloud sync)
- gamdl **3.8.5** (`pip install gamdl==3.8.5`)
- **wrapper-v2** running in Docker (see its own build/run notes — ARM64 requires
  building the image on an x86_64 host and shipping it over)
- A valid Apple Music subscription (the wrapper logs in with it; 2FA on first run)

> No `cookies.txt` is needed. Authentication is handled by the wrapper's Apple login,
> and the AMP developer token is scraped automatically from the Apple Music web player.

## Installation

```bash
git clone <repo>
cd SongsAPI
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt          # includes gamdl==3.8.5, httpx, fastapi, ...

# Initialize database
python3 -c "from models.database import initialize_database; initialize_database()"

# Run server
uvicorn main:app --host 0.0.0.0 --port 8000
```

Make sure wrapper-v2 is up and authenticated first:

```bash
curl -s http://127.0.0.1:8080/me | jq .   # expect auth.state = "authenticated"
```

## Configuration

Edit `Secrets.py`:

```python
from pathlib import Path


class Link:
    # ---- Search / catalog ----
    ITUNES_URL = "https://itunes.apple.com/search"   # fallback search only
    AMP_BASE_URL = "https://amp-api.music.apple.com"  # primary catalog API
    STOREFRONT = "in"                                 # must match the wrapper account's storefront

    # ---- Paths ----
    # initial download staging, this folder exists in the project directory as "Albums" containing a flac_script.sh file
    DOWNLOAD_DIR = Path("/Path/To/Your/Temporary/Album/Storage")    
    
    DESTINATION_DIR = Path("/mnt/music") # rclone mount (final location)

    # ---- wrapper-v2 ----
    # Single base URL for HTTP + separate host/port for the raw TCP decrypt socket.
    # 10021 avoids colliding with an old wrapper on 10020; move to 10020 once retired.
    WRAPPER_URL = "http://127.0.0.1:8080"
    WRAPPER_DECRYPT_HOST = "127.0.0.1"
    WRAPPER_DECRYPT_PORT = 10021

    # ---- URLs ----
    PUBLIC_URL = "https://your-domain.com/songs-api"      # external, if reverse-proxied
    LOCALHOST_URL = "http://localhost:8000"
    BASE_URL = "/songs-api"                               # API base path ('/' if none)

    # ---- Conversion ----
    BASH_URL = "/usr/bin/bash"
    CONVERT_TO_FLAC = "flac_script.sh"                    # your FFmpeg script name
    CODEC = "alac"                                        # alac (lossless) | aac | opus

    # ---- Cloud ----
    RCLONE_DRIVE_MOUNT = "YOUR_REMOTE_NAME:/PATH/TO/MOUNT"

    @classmethod
    def setup(cls):
        cls.DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)


class Token:
    """Credentials for SQLite auth and Apple Music AMP token scraping."""
    DB_NAME = ""
    SECRET_KEY = ""
    ADMIN_USERNAME = ""
    ADMIN_PASSWORD = ""
```


> `.m4a` is a container, not a codec. With `CODEC = "alac"`, downloaded `.m4a` files
> hold **ALAC (lossless)**, so the FLAC conversion is a true lossless transcode. Verify
> a file with `ffprobe -show_entries stream=codec_name ...` — `alac` good, `aac` means
> a lossy fallback slipped in.

### Systemd service

```ini
[Unit]
Description=Songs API
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/SongsAPI
Environment="PATH=/home/ubuntu/SongsAPI/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
ExecStart=/home/ubuntu/SongsAPI/venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5
TimeoutStopSec=300
TimeoutStartSec=60
KillMode=mixed
StandardOutput=journal
StandardError=journal
SyslogIdentifier=songs-api

[Install]
WantedBy=multi-user.target
```

> Do **not** add `--loop uvloop --http httptools` unless those packages are installed —
> the service fails to start otherwise. Plain asyncio is fine. Do **not** use
> `--workers`; it breaks SSE streaming. For horizontal scale, front multiple instances
> with nginx instead.

## Usage

### Web interface

1. **Search** an artist or album, or **paste** an Apple Music URL (any region).
2. **Upload** for the full pipeline, or run steps individually.
3. **Monitor** live logs per album.
4. On success the album auto-clears from *Active Downloads*.

> The browser tab must stay open for the duration — progress streams over SSE, so
> closing the tab ends the run. (A background job queue would remove this constraint;
> not yet implemented.)



The UI matches loosely (`includes("[DONE]")` / `includes("[ERROR]")`) for single-step
actions, and on exact strings inside the one-click Upload chain so one step can't
resolve on another's message.

## File structure

```
├── main.py                 # FastAPI app + all endpoints
├── amp.py                  # Apple Music API client (token, search, equivalents, UPC)
├── Secrets.py              # Configuration (paths, URLs, credentials)
├── flac_script.sh          # Parallel FFmpeg ALAC→FLAC converter
├── models/database.py      # SQLite ORM + auth
└── templates/
    ├── index.html          # Main app
    ├── login.html          # Login
    └── admin.html          # Admin panel
```

## Workflow & storage

| Stage | Location | Format | Folder name |
|---|---|---|---|
| 1. Download | `Albums/Artist/Album/` | `.m4a` (ALAC) | `Album Name` |
| 2. Rename | `Albums/` | `.m4a` (ALAC) | `Artist - Album Name` |
| 3. Convert | `Albums/` | `.flac` | `Artist - Album Name` |
| 4. Move | `DESTINATION_DIR` | `.flac` | `Artist - Album Name` |

- gamdl lays files out by `Artist/Album`; multi-artist releases go under `Compilations/`.
- Windows-illegal characters (`: ? | " < > *`) become `_`, so rename/convert/move locate
  the folder by scanning rather than exact name, falling back to most-recently-modified.
- After a verified move, the local source folder is deleted — cloud becomes the source of truth.



## Tech stack

| Component | Technology |
|---|---|
| Backend | FastAPI, Uvicorn, SQLite |
| Frontend | HTML5, CSS3, vanilla ES6+ (SSE, localStorage) |
| Catalog | Apple Music AMP API (+ iTunes fallback) |
| Audio | gamdl 3.8.5, FFmpeg, mutagen |
| DRM | wrapper-v2 (Docker) |
| Cloud | rclone |
| Auth | argon2, Starlette Sessions |
| Process | subprocess, threading, asyncio |

**Note:** Requires a valid Apple Music subscription and a running wrapper-v2 instance.
Catalog availability varies by storefront.