import asyncio
import queue
import re
import shutil
import threading
import time
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
import requests
from mutagen.flac import FLAC
from mutagen.mp4 import MP4
from starlette.middleware.cors import CORSMiddleware
import subprocess
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware
from fastapi.responses import RedirectResponse
from starlette.responses import StreamingResponse
from Secrets import Link
import os

from models.database import *

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key=Token.SECRET_KEY)
app.add_middleware(CORSMiddleware, allow_origins=[Link.LOCALHOST_URL, Link.PUBLIC_URL], allow_methods=["*"],allow_headers=["*"], allow_credentials=True,)

Link.setup() # setup .mkdir() function

try:
    initialize_database()
    initial_admin_user()
    print("[INFO] Database ready")
except Exception as e:
    print(f"[ERROR] Database initialization failed: {e}")



# --------------------- PAGES ---------------------
@app.get("/")
async def index(request: Request):
    """
    This is where the website search index will be, for now it just serves the index.html
    file which will be used for testing the frontend and backend connection.
    """
    if "user" not in request.session: return RedirectResponse(url="/login", status_code=302)
    return FileResponse("templates/index.html")

@app.get("/login")
async def login_page():
    """
    Login Page Rendering
    """
    return FileResponse("templates/login.html")

@app.get("/admin")
async def admin_page(request: Request):
    if not request.session.get("is_admin"): return RedirectResponse(url="/login", status_code=302)
    return FileResponse("templates/admin.html")


# --------------------- AUTH ENDPOINTS ---------------------
@app.post("/api/login")
async def login(request: Request):
    data = await request.json()
    username = data.get("username")
    password = data.get("password")

    if not username or not password:
        raise HTTPException(status_code=400, detail="Missing credentials")

    user = get_user(username)
    if not user:
        print(f"[LOGIN] User not found!")
        raise HTTPException(status_code=401, detail="Invalid credentials")

    is_correct = check_password(password, user["hashed_password"])
    print(f"[LOGIN] Password check result: {is_correct}")

    if not is_correct:
        print(f"[LOGIN] Password verification failed!")
        raise HTTPException(status_code=401, detail="Invalid credentials")

    print(f"[LOGIN] Login successful! Setting session...")
    request.session["user"] = username
    request.session["is_admin"] = user["is_admin"]
    return {"status": "success", "is_admin": user["is_admin"]}

@app.get("/api/user-info")
async def user_info(request: Request):
    """Get current user info"""
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    is_user_admin = request.session.get("is_admin", False)
    return {"username": user, "is_admin": is_user_admin}

@app.get("/api/users")
async def list_all_users(request: Request):
    if not request.session.get("is_admin"): raise HTTPException(status_code=403, detail="Unauthorized")
    return {"users": list_users()}

@app.post("/api/users/create")
async def create_new_user(request: Request):
    if not request.session.get("is_admin"): raise HTTPException(status_code=403, detail="Unauthorized")

    data = await request.json()
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()

    if not username or not password:
        raise HTTPException(status_code=400, detail="Username and password required")

    if len(username) < 3:
        raise HTTPException(status_code=400, detail="Username must be at least 3 characters")

    if len(password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")

    if get_user(username):
        raise HTTPException(status_code=409, detail="User already exists")

    try:
        create_user(username, password, is_admin=False)
        return {"status": "success", "username": username}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/users/delete")
async def delete_existing_user(request: Request):
    if not request.session.get("is_admin"): raise HTTPException(status_code=403, detail="Unauthorized")

    data = await request.json()
    username = data.get("username")

    if not username: raise HTTPException(status_code=400, detail="Username required")
    if username == request.session.get("user"): raise HTTPException(status_code=400, detail="Cannot delete your own account")
    if not get_user(username): raise HTTPException(status_code=404, detail="User not found")

    delete_user(username)
    return {"status": "success"}

@app.post("/api/users/change-password")
async def change_user_password(request: Request):
    user = request.session.get("user")
    if not user: raise HTTPException(status_code=401, detail="Not authenticated")

    data = await request.json()
    old_password = data.get("old_password")
    new_password = data.get("new_password")

    if not old_password or not new_password: raise HTTPException(status_code=400, detail="Both passwords required")

    try:
        change_password(old_password, new_password, str(user))
        return {"status": "success"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))



# --------------------- GET ENDPOINT - SEARCHES AND URL LOOKUPS ---------------------
@app.get("/albums/search")
def searchAlbum(q: str, limit: int = 50):
    """
    Used to search for albums using the iTunes Search API.
    Returns a list of albums with their collection id, name, artist and apple music url
    which will be used for downloading the album later.
    """

    response = requests.get(Link.ITUNES_URL, params={"term": q, "entity": "album", "media": "music","limit": limit})
    results = response.json().get("results", [])

    # If no results, try removing special characters
    if not results:
        cleaned_query = "".join(c if c.isalnum() or c.isspace() else "" for c in q).strip()
        if cleaned_query and cleaned_query != q:
            print(f"[SEARCH] No results for '{q}', trying cleaned: '{cleaned_query}'")
            response = requests.get(Link.ITUNES_URL, params={
                "term": cleaned_query,
                "entity": "album",
                "limit": limit,
                "country": "us"
            })

    results = response.json().get("results", []) # returns blank [] if there are no results


    if not results and " by " in q:
        artist = q.split(" by ")[-1].strip()
        print(f"[SEARCH] No results for '{q}', trying artist: '{artist}'")
        response = requests.get(Link.ITUNES_URL, params={
            "term": artist,
            "entity": "album",
            "limit": limit,
            "country": "us"
        })
        results = response.json().get("results", [])


    albums = []
    for i, album in enumerate(results):
        if album.get("collectionType") != "Album":
            continue
        albums.append({
            "index": len(albums),
            "collection_id": album["collectionId"],
            "album_name": album["collectionName"],
            "artist": album["artistName"],
            "apple_music_url": album["collectionViewUrl"],
            "year": album["releaseDate"][:4] if album.get("releaseDate") else "N/A"
        })
    return {"results": albums}

@app.get("/albums/lookup")
def urlAlbumLookup(url: str, collection_id: int = None):
    """
    If the search function does not result in the album that you want to download, then directly paste the album link to find it and download
    """
    if url and not collection_id:
        # Example URL for apple music -> https://music.apple.com/us/album/1359292515
        parts = url.rstrip("/").split("/")

        try:
            collection_id = int(parts[-1]) # last component of the URL is the ID
        except (ValueError, IndexError):
            raise HTTPException(status_code=400, detail="Invalid URL Format")


        if not collection_id:
            raise HTTPException(status_code=400, detail="Collection ID not found in URL")

        response = requests.get("https://itunes.apple.com/lookup", params={"id": collection_id, "entity": "album"})

        results = response.json().get("results", [])
        album = next((r for r in results if r.get("wrapperType") == "collection"), None)

        if not album:
            raise HTTPException(status_code=404, detail="Album not found")

        return {
            "collection_id": album["collectionId"],
            "album_name": album["collectionName"],
            "artist": album["artistName"],
            "apple_music_url": album["collectionViewUrl"],
            "year": album["releaseDate"][:4] if album.get("releaseDate") else "N/A"
        }
    return None

# --------------------- REQUEST CLASSES ---------------------
class DownloadRequest(BaseModel):
    collection_id: int
    results: list[dict]  # Full list of search results to pick the album from
class MetadataRequest(BaseModel):
    folder: str
    year: str
class ConvertRequest(BaseModel): # a structure to help write the functions based on the artist and album name instead of the folder name which can be different based on the gamdl version and settings
    artist: str
    new_artist: str  = None # In case album artist does not match the folder
    album_name: str
    new_album_name: str  = None # In case album name does not the folder
    overwrite: bool = False # option to overwrite existing flac files, default is false to prevent accidental overwriting

# --------------------- POST ENDPOINT - DOWNLOAD ---------------------
@app.post("/albums/download")
async def albumDownload(body: DownloadRequest): # async functions important for yielding to SSE otherwise it would not run
    """
    Selected albums will be downloaded using gamdl which is a command line tool that can download albums from Apple Music.
    """
    match = None
    for album in body.results:
        if album["collection_id"] == body.collection_id:
            match = album
            break

    if match is None:
        raise HTTPException(status_code=404, detail="Album not found")

    url = match["apple_music_url"]

    async def streamOutput():

        env = {**os.environ, "PYTHONIOENCODING": "utf-8"} # encoding for special characters in album

        process = subprocess.Popen( # args containing wrapper elements
            ["gamdl", "--song-codec-priority", Link.CODEC, "--use-wrapper", "--wrapper-account-url", Link.WRAPPER_ACCOUNT_URL, "--wrapper-m3u8-ip", Link.WRAPPER_M3U8_IP, "--wrapper-decrypt-ip", Link.WRAPPER_DECRYPT_IP, "--output-path", Link.DOWNLOAD_DIR, url],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,  # capturing both errs and output from gamdl process
            text=True,
            encoding='utf-8',
            errors='replace',
            bufsize=0,  # 0 buffering
            env=env
        )

        q = queue.Queue()
        def enqueue(stream, label):
            for line in stream:
                stripped = line.rstrip()
                if not stripped.startswith("[download]"):
                    q.put((label,stripped))
            q.put((label, None))

        t1 = threading.Thread(target=enqueue, args=(process.stdout, "stdout"))
        t2 = threading.Thread(target=enqueue, args=(process.stderr, "stderr"))

        t1.start()
        t2.start()

        finished = 0
        while finished < 2:  # if thread is still running
            label, line = q.get()
            if line is None:
                finished += 1
                continue
            print(f"[{label}] {line}", flush=True)  # Debug to terminal
            yield f"data: {line}\n\n"  # Send line to client as SSE
            await asyncio.sleep(0)
        t1.join()
        t2.join()
        process.wait()

        print(f"[gamdl exited with code {process.returncode}]\n\n")

        if process.returncode == 0:
            # folder = Link.DOWNLOAD_DIR / f"{match['artist']}" / f"{match['album_name']}"
            try:
                yield "data: [METADATA] Updating Year Metadata.../n/n"

                # Running the update_year in a thread pool to avoid blocking the main event loop
                async for log_message in update_year(match["artist"], match["album_name"], match["year"]):
                    yield f"data: {log_message}\n\n"
                yield "data: [METADATA][UPDATE YEAR] Year Updated!\n\n"
                yield "data: [ALBUM DOWNLOAD][DONE]\n\n"

            except Exception as e:
                print(f"[ERROR] Metadata update failed: {e}")
                yield f"data: [WARNING] Metadata update failed: {str(e)}\n\n"
        else:
            yield f"data: [GAMDL][ERROR] gamdl exited with code {process.returncode}\n\n"
    return StreamingResponse(streamOutput(),media_type="text/event-stream",headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

# --------------------- POST ENDPOINT - METADATA (YEAR) ---------------------
async def update_year(artist: str, album_name: str, year: str):
    """
    Updates the year metadata on all audio files in the album folder.
    Finds the folder by matching against the album name, or uses most recently modified.
    """
    artist_dir = Link.DOWNLOAD_DIR / artist
    # Sometimes albums are downloaded into the folder called "Compilations" instead of the artist name, especially if there are multiple artists, so we check that too
    compilations_dir = Link.DOWNLOAD_DIR / "Compilations"

    all_search_dirs = []

    if artist_dir.exists():
        all_search_dirs.append(artist_dir)

    if compilations_dir.exists():
        all_search_dirs.append(compilations_dir)

    if not all_search_dirs:
        yield "[UPDATE_YEAR] Artist directory not found\n\n"
        # print(f"[UPDATE_YEAR] Neither Artist directory nor Compilations folder found: {artist_dir}, {compilations_dir}")


    # Try to find exact or close match first

    all_folders = []
    for search_dir in all_search_dirs:
        try:
            folders = [f for f in search_dir.iterdir() if f.is_dir()]
            all_folders.extend(folders)
        except Exception as e:
            yield f"[UPDATE_YEAR] Error reading directory: {search_dir}: {e}"
            # print(f"[UPDATE_YEAR] Error reading directory: {search_dir}: {e}")


    if not all_folders:
        yield "[UPDATE_YEAR] No folders found"
        print(f"[UPDATE_YEAR] No album folder found")


    # Look for album name match (exact or with _ substitutions)
    sanitized_album = album_name.replace(":", "_").replace("?", "_").replace("|", "_").replace('"', "_").replace("<", "_").replace(">", "_").replace("*", "_")

    folder = None
    for f in all_folders:
        if sanitized_album in f.name or album_name in f.name:
            folder = f
            break

    # If no match found, use most recently modified folder
    if not folder:
        folder = max(all_folders, key=lambda f: f.stat().st_mtime)
        yield f"[UPDATE_YEAR] Using most recent: {folder.name}"
        print(f"[UPDATE_YEAR] Album name not found, using most recently modified: {folder.name}")
    else:
        yield f"[UPDATE_YEAR] Found: {folder.name}"
        # print(f"[UPDATE_YEAR] Found matching folder: {folder.name}")

    changed = []
    file_count = 0
    try:
        for file in folder.rglob("*"):
            if file.suffix.lower() in [".flac", ".m4a"]:
                file_count += 1
                yield f"[UPDATE_YEAR] Processing {file.name}"

                try:
                    if file.suffix.lower() == ".flac":
                        audio = FLAC(file)
                        audio["\xa9day"] = [year]
                        audio.save()
                    else:
                        audio = MP4(file)
                        audio["\xa9day"] = [year]
                        audio.save()

                    changed.append(file.name)
                    yield f"[UPDATE_YEAR] ✓ Updated {file.name}"
                except Exception as e:
                    yield f"[UPDATE_YEAR] ✗ Error on {file.name}: {e}"

        yield f"[UPDATE_YEAR] Complete: {len(changed)}/{file_count} files updated"
    except Exception as e:
        yield f"[UPDATE_YEAR] Error: {e}"

@app.post("/albums/metadata/year")
async def updateYear(body: MetadataRequest):
    """
    Updates year metadata on audio files in a specific folder.
    Streams progress via SSE.
    """
    folder = Path(body.folder)
    if not folder.exists():
        raise HTTPException(status_code=404, detail="Folder not found")

    # Extract artist and album name from folder path
    # Assumes folder structure: "Artist - Album Name"
    folder_name = folder.name
    artist = body.artist if hasattr(body, 'artist') else folder_name.split(" - ")[0]
    album_name = body.album_name if hasattr(body, 'album_name') else folder_name.split(" - ")[1] if " - " in folder_name else folder_name

    async def streamOutput():
        yield "data: [METADATA] Starting year update...\n\n"

        try:
            async for log_message in update_year(artist, album_name, body.year):
                yield f"data: {log_message}\n\n"

            yield "data: [DONE]\n\n"
        except Exception as e:
            yield f"data: [ERROR] {str(e)}\n\n"

    return StreamingResponse(
        streamOutput(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )

# --------------------- POST ENDPOINT - M4A -> FLAC ---------------------
def normalize_name(name: str) -> str:
    """Replace any non-alphanumeric characters (except spaces) with underscores, collapse multiples"""
    return re.sub(r'[^a-z0-9 ]+', '_', name.lower()).strip()
@app.post("/albums/convert-flac")
async def convertToFLAC(body: ConvertRequest):
    expected_name = f"{body.artist} - {body.album_name}"
    normalized_expected = normalize_name(expected_name)
    script_path = Link.DOWNLOAD_DIR / Link.CONVERT_TO_FLAC

    async def streamOutput():
        try:
            folders = [f for f in Link.DOWNLOAD_DIR.iterdir() if f.is_dir()]
        except Exception as e:
            yield f"data: [CONVERT][ERROR] Could not read download dir: {e}\n\n"
            return

        # 1. Try exact normalized match
        folder = next(
            (f for f in folders if normalize_name(f.name) == normalized_expected),
            None
        )

        # 2. Fallback: find folders starting with the artist name, pick most recently modified
        if folder is None:
            normalized_artist = normalize_name(body.artist)
            artist_folders = [
                f for f in folders
                if normalize_name(f.name).startswith(normalized_artist)
            ]
            if artist_folders:
                folder = max(artist_folders, key=lambda f: f.stat().st_mtime)
                yield f"data: [CONVERT] Exact match failed, using most recently modified: {folder.name}\n\n"

        if folder is None:
            yield f"data: [CONVERT][ERROR] No matching folder found for: {expected_name}\n\n"
            return

        yield f"data: [CONVERT] Matched folder: {folder.name}\n\n"

        loop = asyncio.get_event_loop()
        q = asyncio.Queue()

        def enqueue(stream):
            for line in stream:
                loop.call_soon_threadsafe(q.put_nowait, line.rstrip())
            loop.call_soon_threadsafe(q.put_nowait, None)

        process = subprocess.Popen(
            ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(script_path), "-FolderPath", str(folder)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=0
        )

        t1 = threading.Thread(target=enqueue, args=(process.stdout,))
        t2 = threading.Thread(target=enqueue, args=(process.stderr,))
        t1.start()
        t2.start()

        finished = 0
        while finished < 2:
            line = await q.get()
            if line is None:
                finished += 1
                continue
            print(f"[convert] {line}", flush=True)
            yield f"data: {line}\n\n"

        t1.join()
        t2.join()
        process.wait()

        if process.returncode != 0:
            yield f"data: [CONVERT][ERROR] Conversion failed with code {process.returncode}\n\n"
        else:
            yield "data: [CONVERT][DONE] Finished with converting files to FLAC\n\n"

    return StreamingResponse(
        streamOutput(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )

# --------------------- POST ENDPOINT - MOVING ALBUM TO ONE DRIVE ---------------------
@app.post("/albums/move-album")
async def moveAlbum(body: ConvertRequest):
    expected_name = f"{body.artist} - {body.album_name}"
    normalized_expected = normalize_name(expected_name)

    async def streamOutput():
        try:
            folders = [f for f in Link.DOWNLOAD_DIR.iterdir() if f.is_dir()]
        except Exception as e:
            yield f"data: [MOVE][ERROR] Could not read download dir: {e}\n\n"
            return

        # 1. Try exact normalized match
        source = next(
            (f for f in folders if normalize_name(f.name) == normalized_expected),
            None
        )

        # 2. Fallback: artist prefix + most recently modified
        if source is None:
            normalized_artist = normalize_name(body.artist)
            artist_folders = [
                f for f in folders
                if normalize_name(f.name).startswith(normalized_artist)
            ]
            if artist_folders:
                source = max(artist_folders, key=lambda f: f.stat().st_mtime)
                yield f"data: [MOVE] Exact match failed, using most recently modified: {source.name}\n\n"

        if source is None:
            yield f"data: [MOVE][ERROR] No matching folder found for: {expected_name}\n\n"
            return

        # Use the actual folder name for destination to preserve the renamed name
        destination = Link.DESTINATION_DIR / source.name

        yield f"data: [MOVE] Source: {source}\n\n"
        yield f"data: [MOVE] Destination: {destination}\n\n"

        if destination.exists():
            if body.overwrite:
                yield f"data: [MOVE] Overwrite enabled, removing {destination}...\n\n"
                try:
                    shutil.rmtree(destination)
                except Exception as e:
                    yield f"data: [MOVE][ERROR] Failed to remove existing: {e}\n\n"
                    return
            else:
                yield f"data: [MOVE][ERROR] Album already exists at {destination}\n\n"
                return

        max_retries = 5
        retry_delay = 2

        for attempt in range(max_retries):
            try:
                yield f"data: [MOVE] Attempt {attempt + 1}/{max_retries}: Moving {source.name}...\n\n"
                shutil.move(str(source), str(destination))
                yield f"data: [MOVE] Move completed!\n\n"
                yield f"data: [MOVE] Final location: {destination}\n\n"
                yield "data: [MOVE][DONE] Album moved successfully!\n\n"
                return

            except PermissionError:
                if attempt < max_retries - 1:
                    yield f"data: [MOVE] Access denied (OneDrive may be syncing), retrying in {retry_delay}s...\n\n"
                    time.sleep(retry_delay)
                else:
                    yield f"data: [MOVE][ERROR] Permission denied after {max_retries} attempts. OneDrive may be locked.\n\n"
                    yield f"data: [MOVE][ERROR] Try pausing OneDrive sync or moving manually.\n\n"
                    return

            except Exception as e:
                yield f"data: [MOVE][ERROR] {str(e)}\n\n"
                return

    return StreamingResponse(
        streamOutput(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )

# --------------------- POST ENDPOINT - RENAMING AND RESTRUCTURING ---------------------
@app.post('/albums/rename-folder')
async def renameFolder(body: ConvertRequest):
    new_artist = body.new_artist or body.artist
    new_album = body.new_album_name or body.album_name

    artist_dir = Link.DOWNLOAD_DIR / body.artist
    compilations_dir = Link.DOWNLOAD_DIR / "Compilations"

    new_folder_name = Link.DOWNLOAD_DIR / f"{new_artist} - {new_album}"

    async def streamOutput():
        # Check both artist and compilations directories
        all_search_dirs = []
        if artist_dir.exists():
            all_search_dirs.append(artist_dir)

        if compilations_dir.exists():
            all_search_dirs.append(compilations_dir)

        if not all_search_dirs:
            yield f"data: [RENAME][ERROR] Artist directory not found: {artist_dir}\n\n"
            return

        # Collect all folders from both directories
        all_folders = []
        for search_dir in all_search_dirs:
            try:
                folders = [f for f in search_dir.iterdir() if f.is_dir()]
                all_folders.extend(folders)
            except Exception as e:
                yield f"data: [RENAME][ERROR] Could not read {search_dir}: {e}\n\n"
                return

        if not all_folders:
            yield f"data: [RENAME][ERROR] No album folders found\n\n"
            return

        # Try to find matching folder
        source = None

        # First try: exact match with normalized name
        if hasattr(locals(), 'normalize_name'):
            normalized_album = normalize_name(body.album_name)
            source = next(
                (f for f in all_folders if normalize_name(f.name) == normalized_album),
                None
            )

            # Second try: match new album name (for re-renames)
            if source is None:
                source = next(
                    (f for f in all_folders if normalize_name(f.name) == normalize_name(new_album)),
                    None
                )

        # Fallback: use most recently modified folder
        if source is None:
            source = max(all_folders, key=lambda f: f.stat().st_mtime)
            yield f"data: [RENAME] No exact match found, using most recent: {source.name}\n\n"
        else:
            yield f"data: [RENAME] Found: {source.name}\n\n"

        yield f"data: [RENAME] Renaming to: {new_folder_name.name}\n\n"

        max_retries = 5
        for attempt in range(max_retries):
            try:
                yield f"data: [RENAME] Attempt {attempt + 1}: Renaming...\n\n"
                shutil.move(str(source), str(new_folder_name))

                # Clean up empty directories
                try:
                    if source.parent.exists() and source.parent != Link.DOWNLOAD_DIR:
                        if not any(source.parent.iterdir()):
                            source.parent.rmdir()
                            yield f"data: [RENAME] Removed empty parent directory\n\n"
                except Exception:
                    pass

                yield f"data: [RENAME] Album has been renamed to : {new_folder_name.name}\n\n"
                yield "data: [RENAME][DONE]\n\n"
                return

            except PermissionError:
                if attempt < max_retries - 1:
                    yield f"data: [RENAME][ERROR] File locked (attempt {attempt + 1}/{max_retries}), retrying in 2s...\n\n"
                    await asyncio.sleep(2)
                else:
                    yield f"data: [RENAME][ERROR] Permission denied after {max_retries} attempts. Close file explorer and try again.\n\n"
                    return

            except Exception as e:
                yield f"data: [RENAME][ERROR] {type(e).__name__}: {str(e)}\n\n"
                return

    return StreamingResponse(
        streamOutput(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )

