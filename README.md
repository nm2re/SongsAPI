# A SongsAPI, ReVault

A full-stack web application that automates downloading, organizing, and converting Apple Music albums to FLAC format. Download albums via iTunes search or direct Apple Music URLs, automatically rename folders, convert to lossless FLAC, and sync to OneDrive with a single click.

## Features

- **iTunes Integration**: Search albums with fallback logic for special characters and direct URL lookup
- **One-Click Upload**: Orchestrates complete workflow (download → rename → convert → move) with real-time progress
- **Individual Controls**: Perform any step independently for maximum flexibility
- **Smart Organization**: Auto-rename folders to `Artist - Album` format with special character handling
- **FLAC Conversion**: Convert M4A to FLAC losslessly via FFmpeg
- **OneDrive Sync**: Move albums directly to OneDrive via rclone, bypassing FUSE mount issues
- **Metadata Management**: Auto-update year and tags on all audio files
- **Persistent State**: localStorage saves search results, logs, and album pins across reloads
- **Real-time Streaming**: SSE-based progress updates for all operations
- **User Management**: SQLite authentication with admin panel for user CRUD
- **Session Auth**: Secure session-based authentication with argon2 password hashing

## Quick Start

### Prerequisites

- Python 3.12+
- FFmpeg
- rclone (for OneDrive)
- gamdl (Apple Music downloader)
- Wrapper service (for DRM decryption)

### Installation

```bash
git clone <repo>
cd SongsAPI
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Initialize database
python3 -c "from models.database import initialize_database; initialize_database()"

# Run server
uvicorn main:app --host 0.0.0.0 --port 8000
```

Access at `http://localhost:8000`

### Configuration

Edit `Secrets.py`:

```python
class Link:
    # iTunes API endpoint (no change needed)
    ITUNES_URL = "https://itunes.apple.com/search"
    # Local download directory for initial album storage
    DOWNLOAD_DIR = Path("/home/ubuntu/SongsAPI/Albums")
    # Final destination (OneDrive mounted via rclone)
    DESTINATION_DIR = Path("/mnt/music")
    # Apple Music cookies for gamdl authentication
    COOKIES_URL = Path("/home/ubuntu/.gamdl/cookies.txt")
    # Wrapper service for DRM decryption (running on separate VPS)
    WRAPPER_ACCOUNT_URL = "http://wrapper-vps-ip:30020"
    WRAPPER_M3U8_IP = "wrapper-vps-ip:20020"
    WRAPPER_DECRYPT_IP = "wrapper-vps-ip:10020"
    # Public access URL (if deployed with domain)
    PUBLIC_URL = "https://your-domain.com/songs-api"
    # Local testing URL
    LOCALHOST_URL = "http://localhost:8000"
    # API base path, you dont need this and can set it to '/'
    BASE_URL = "/songs-api"
    # Shell interpreter path
    BASH_URL = "/usr/bin/bash"
    # FLAC conversion script, this was my script name you can name it whatever
    CONVERT_TO_FLAC = "flac_script.sh"
    # Audio codec (options: alac, aac, opus)
    CODEC = "alac"
    

class Token:
	"""
	Credentials Here
	"""
	DB_NAME = 
	SECRET_KEY=
	ADMIN_USERNAME=
	ADMIN_PASSWORD=
```

### Systemd Service

```ini
[Unit]
Description=Songs API
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/SongsAPI
Environment="PATH=/home/ubuntu/SongsAPI/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
ExecStart=/home/ubuntu/SongsAPI/venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000 --loop uvloop --http httptools
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

## Usage

### Web Interface

1. **Search**: Type album name to find on iTunes
2. **Pin**: Add to "Active Downloads" section
3. **Upload**: Click UPLOAD button to start full workflow
4. **Monitor**: Watch real-time logs in logbox
5. **Complete**: Album auto-removes from pinned when done

### Individual Operations

- **Download Only**: Click "1. Download"
- **Rename Only**: Click "2. Rename" → customize names
- **Convert Only**: Click "3. → FLAC"
- **Move Only**: Click "4. Move"

### Direct URL Lookup

Paste Apple Music URL instead of searching (note: must be available in your region)

## API Endpoints

### Authentication

- `POST /api/login` - Login with username/password
- `POST /api/logout` - Clear session
- `GET /api/user-info` - Current user + admin status

### Albums

- `GET /albums/search?q=query&limit=20` - iTunes search (SSE)
- `GET /albums/lookup?url=appleMusicUrl` - Direct lookup
- `POST /albums/download` - Download + metadata (SSE)
- `POST /albums/rename-folder` - Rename folder (SSE)
- `POST /albums/convert-flac` - Convert to FLAC (SSE)
- `POST /albums/move-album` - Move to destination (SSE)

### Admin

- `GET /admin` - Admin panel (admin only)
- `GET /api/users` - List users
- `POST /api/users/create` - Create user
- `POST /api/users/delete` - Delete user
- `POST /api/users/change-password` - Change password

### Response Format

SSE endpoints return:

```
data: [OPERATION] message\n\n
data: [DONE]\n\n
```

JSON endpoints return album metadata with collection_id, artist, album_name, year, etc.

## Architecture

### File Structure

```
├── main.py                 # FastAPI app + all endpoints
├── Secrets.py              # Configuration (paths, URLs, credentials)
├── models/database.py      # SQLite ORM + auth
├── templates/
│   ├── index.html          # Main app
│   ├── login.html          # Login
│   └── admin.html          # Admin panel
├── convert.ps1             # FFmpeg conversion script
└── cookies.txt             # Apple Music cookies (gamdl)
```

### Album Storage Structure

#### Download Phase

```
/home/ubuntu/SongsAPI/Albums/
├── Emotional Oranges/
│   ├── STILL EMO/
│   │   ├── 01 Wrong Hands.m4a
│   │   ├── 02 Be Somebody (feat. Tkay Maidza).m4a
│   │   └── 03 Justified.m4a
│   └── The Juice_ Vol. III/          # Special chars converted to _
│       ├── 01 Track 1.m4a
│       └── 02 Track 2.m4a
├── Drake/
│   └── If You're Reading This It's Too Late/
│       ├── 01 Song.m4a
│       └── 02 Song.m4a
├── Taylor Swift/
│   └── Red (Deluxe Version)/
│       └── [16 tracks].m4a
└── Compilations/                     # Multi-artist albums
    └── Various Artists Album/
        └── [tracks].m4a
```

#### After Rename

```
/home/ubuntu/SongsAPI/Albums/
├── Emotional Oranges - STILL EMO/       # Renamed to Artist - Album
│   ├── 01 Wrong Hands.m4a
│   ├── 02 Be Somebody (feat. Tkay Maidza).m4a
│   └── 03 Justified.m4a
├── Drake - If You're Reading This It's Too Late/
│   ├── 01 Song.m4a
│   └── 02 Song.m4a
└── Taylor Swift - Red (Deluxe Version)/
    └── [16 tracks].m4a
```

#### After Conversion

```
/home/ubuntu/SongsAPI/Albums/
├── Emotional Oranges - STILL EMO/
│   ├── 01 Wrong Hands.flac            # Converted to FLAC
│   ├── 02 Be Somebody.flac
│   └── 03 Justified.flac
├── Drake - If You're Reading This It's Too Late/
│   ├── 01 Song.flac
│   └── 02 Song.flac
└── Taylor Swift - Red (Deluxe Version)/
    └── [16 tracks].flac
```

#### After Move to OneDrive

```
/mnt/music/                             # OneDrive mount via rclone
├── Emotional Oranges - STILL EMO/
│   ├── 01 Wrong Hands.flac
│   ├── 02 Be Somebody.flac
│   └── 03 Justified.flac
├── Drake - If You're Reading This It's Too Late/
│   ├── 01 Song.flac
│   └── 02 Song.flac
└── Taylor Swift - Red (Deluxe Version)/
    └── [16 tracks].flac
```

## Workflow Summary

|Stage|Location|Format|Folder Name|
|---|---|---|---|
|1. Download|`Albums/Artist/Album/`|`.m4a`|`Album Name`|
|2. Rename|`Albums/`|`.m4a`|`Artist - Album Name`|
|3. Convert|`Albums/`|`.flac`|`Artist - Album Name`|
|4. Move|`/mnt/music/`|`.flac`|`Artist - Album Name`|

## Key Points

- **Initial Downloads**: Organized by artist → album (gamdl default)
- **Special Characters**: Converted to underscores (`The Juice: Vol. III` → `The Juice_ Vol. III`)
- **Rename Logic**: Flattens to `Artist - Album` at root of Albums folder
- **Conversion**: In-place FLAC conversion while in Albums folder
- **Final Move**: Complete folder transferred to OneDrive
- **Compilations**: Stored separately in `Compilations/` folder if multi-artist
- **Cleanup**: Source folder deleted after successful move to OneDrive

## Example Full Workflow

```
1. Search "Emotional Oranges Still Emo"
   ↓
2. Download to: /home/ubuntu/SongsAPI/Albums/Emotional Oranges/STILL EMO/
   [M4A files only]
   ↓
3. Rename folder to: /home/ubuntu/SongsAPI/Albums/Emotional Oranges - STILL EMO/
   [M4A files, same location]
   ↓
4. Convert in-place: /home/ubuntu/SongsAPI/Albums/Emotional Oranges - STILL EMO/
   [FLAC files replace M4A]
   ↓
5. Move entire folder to: /mnt/music/Emotional Oranges - STILL EMO/
   [FLAC files now on OneDrive]
   ↓
6. Source folder deleted from Albums/
   [OneDrive is now source of truth]
```

## Troubleshooting

### Download Fails

- Verify wrapper service is running: `curl http://wrapper-ip:30020`
- Check cookies.txt is valid
- Try searching instead of URL lookup (regional availability)

### Folder Not Found

- Auto-detection uses normalized names + recent file logic
- Check manual path: `/home/ubuntu/SongsAPI/Albums/{artist}/`

### Unresponsive Website

- Don't use uvicorn `--workers` (breaks SSE streaming)
- Use single async worker: `uvicorn main:app --host 0.0.0.0 --port 8000`
- For multiple instances, use nginx reverse proxy
## Security

- **Passwords**: Argon2 hashing (no truncation limits)
- **Sessions**: Signed cookies with FastAPI SessionMiddleware
- **CORS**: Restricted to localhost (configure for production)
- **Admin Role**: Required for user management endpoints
- **Credentials**: Store Secrets.py securely (never commit)

## Tech Stack

|Component|Technology|
|---|---|
|Backend|FastAPI, Uvicorn, SQLite|
|Frontend|HTML5, CSS3, ES6+ JavaScript|
|Audio|gamdl, FFmpeg, mutagen|
|Cloud|rclone, OneDrive|
|Auth|argon2, Starlette Sessions|
|Process|subprocess, threading, asyncio|

**Note**: Requires valid Apple Music account and wrapper service for DRM decryption. Regional availability may vary by country.
