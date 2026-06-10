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
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:8000"], allow_methods=["*"],allow_headers=["*"], allow_credentials=True,)  # Replace Allow origins with proper url later when deploying

Link.setup() # setup .mkdir() function

try:
    initialize_database()
    initial_admin_user()
    print("[INFO] Database ready")
except Exception as e:
    print(f"[ERROR] Database initialization failed: {e}")


@app.get("/")
async def index(request: Request):
    """
    This is where the website search index will be, for now it just serves the index.html
    file which will be used for testing the frontend and backend connection.
    """
    if "user" not in request.session:
        return RedirectResponse(url="/login", status_code=302)
    return FileResponse("templates/index.html")

# -------------------- ADMIN API --------------------

@app.get("/login")
async def login_page():
    return FileResponse("templates/login.html")

@app.post("/api/login")
async def login(request: Request):
    data = await request.json()
    username = data.get("username")
    password = data.get("password")

    print(f"[LOGIN] Attempt with username: {username}")
    print(f"[LOGIN] Password entered: {password}")

    if not username or not password:
        raise HTTPException(status_code=400, detail="Missing credentials")

    user = get_user(username)
    print(f"[LOGIN] User from database: {user}")

    if not user:
        print(f"[LOGIN] User not found!")
        raise HTTPException(status_code=401, detail="Invalid credentials")

    is_correct = check_password(password, user["hashed_password"])
    print(f"[LOGIN] Password check result: {is_correct}")
    print(f"[LOGIN] Hashed password from DB starts with: {user['hashed_password'][:30]}...")

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


@app.get("/admin")
async def admin_page(request: Request):
    if not request.session.get("is_admin"):
        return RedirectResponse(url="/login", status_code=302)

    return FileResponse("templates/admin.html")


@app.get("/api/users")
async def list_all_users(request: Request):
    if not request.session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Unauthorized")

    return {"users": list_users()}


@app.post("/api/users/create")
async def create_new_user(request: Request):
    if not request.session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Unauthorized")

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
    if not request.session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Unauthorized")

    data = await request.json()
    username = data.get("username")

    if not username:
        raise HTTPException(status_code=400, detail="Username required")

    if username == request.session.get("user"):
        raise HTTPException(status_code=400, detail="Cannot delete your own account")

    if not get_user(username):
        raise HTTPException(status_code=404, detail="User not found")

    delete_user(username)
    return {"status": "success"}


@app.post("/api/users/change-password")
async def change_user_password(request: Request):
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    data = await request.json()
    old_password = data.get("old_password")
    new_password = data.get("new_password")

    if not old_password or not new_password:
        raise HTTPException(status_code=400, detail="Both passwords required")

    try:
        change_password(old_password, new_password, user)
        return {"status": "success"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))



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





# --------------------- DOWNLOAD ---------------------
class DownloadRequest(BaseModel):
    collection_id: int
    results: list[dict]  # Full list of search results to pick the album from

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
            folder = Link.DOWNLOAD_DIR / f"{match['artist']}" / f"{match['album_name']}"
            update_year(match["artist"], match["album_name"], match["year"])
            yield "data: [METADATA] Year Updated!\n\n"
            yield "data: [DONE]\n\n"
        else:
            yield f"data: [ERROR] gamdl exited with code {process.returncode}\n\n"
    return StreamingResponse(streamOutput(),media_type="text/event-stream",headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}) #

class MetadataRequest(BaseModel):
    folder: str
    year: str

def update_year(artist: str, album_name: str, year: str):
    """
    Updates the year metadata on all audio files in the album folder.
    Finds the folder by matching against the album name, or uses most recently modified.
    """
    artist_dir = Link.DOWNLOAD_DIR / artist

    if not artist_dir.exists():
        print(f"[update_year] Artist directory not found: {artist_dir}")
        return []

    # Try to find exact or close match first
    try:
        folders = [f for f in artist_dir.iterdir() if f.is_dir()]
    except Exception as e:
        print(f"[update_year] Error reading directory: {e}")
        return []

    if not folders:
        print(f"[update_year] No album folder found in {artist_dir}")
        return []

    # Look for album name match (exact or with _ substitutions)
    sanitized_album = album_name.replace(":", "_").replace("?", "_").replace("|", "_").replace('"', "_").replace("<", "_").replace(">", "_").replace("*", "_")

    folder = None
    for f in folders:
        if sanitized_album in f.name or f.name.startswith(album_name):
            folder = f
            break

    # If no match found, use most recently modified folder
    if not folder:
        folder = max(folders, key=lambda f: f.stat().st_mtime)
        print(f"[update_year] Album name not found, using most recently modified: {folder.name}")
    else:
        print(f"[update_year] Found matching folder: {folder.name}")

    changed = []
    try:
        for file in folder.rglob("*"):
            if file.suffix.lower() == ".flac":
                try:
                    audio = FLAC(file)
                    audio["\xa9day"] = [year]
                    audio.save()
                    changed.append(file.name)
                except Exception as e:
                    print(f"[update_year] Error updating FLAC {file.name}: {e}")

            elif file.suffix.lower() == ".m4a":
                try:
                    audio = MP4(file)
                    audio["\xa9day"] = [year]
                    audio.save()
                    changed.append(file.name)
                except Exception as e:
                    print(f"[update_year] Error updating M4A {file.name}: {e}")
    except Exception as e:
        print(f"[update_year] Error processing files: {e}")

    return changed

@app.post("/albums/metadata/year")
def updateYear(body: MetadataRequest):
    """
    Keep Years in check for albums as they can cause issues when importing to music library, this is a temporary solution until I can find a better one,
    maybe using the iTunes API to get the correct year and update the metadata of the files using mutagen or something like that.
    """
    folder = Path(body.folder)
    if not folder.exists():
        raise HTTPException(status_code=404, detail="Folder not found")
    changed = update_year(str(folder), body.year)

    return {"updated" : changed, "year" : body.year}


class ConvertRequest(BaseModel): # a structure to help write the functions based on the artist and album name instead of the folder name which can be different based on the gamdl version and settings
    artist: str
    new_artist: str  = None # In case album artist does not match the folder
    album_name: str
    new_album_name: str  = None # In case album name does not the folder
    overwrite: bool = False # option to overwrite existing flac files, default is false to prevent accidental overwriting


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
            yield f"data: [ERROR] Could not read download dir: {e}\n\n"
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
            yield f"data: [ERROR] No matching folder found for: {expected_name}\n\n"
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
            yield f"data: [ERROR] Conversion failed with code {process.returncode}\n\n"
        else:
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        streamOutput(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )

@app.post("/albums/move-album")
async def moveAlbum(body: ConvertRequest):
    expected_name = f"{body.artist} - {body.album_name}"
    normalized_expected = normalize_name(expected_name)

    async def streamOutput():
        try:
            folders = [f for f in Link.DOWNLOAD_DIR.iterdir() if f.is_dir()]
        except Exception as e:
            yield f"data: [ERROR] Could not read download dir: {e}\n\n"
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
            yield f"data: [ERROR] No matching folder found for: {expected_name}\n\n"
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
                    yield f"data: [ERROR] Failed to remove existing: {e}\n\n"
                    return
            else:
                yield f"data: [ERROR] Album already exists at {destination}\n\n"
                return

        max_retries = 5
        retry_delay = 2

        for attempt in range(max_retries):
            try:
                yield f"data: [MOVE] Attempt {attempt + 1}/{max_retries}: Moving {source.name}...\n\n"
                shutil.move(str(source), str(destination))
                yield f"data: [MOVE] Move completed!\n\n"
                yield f"data: [MOVE] Final location: {destination}\n\n"
                yield "data: [DONE]\n\n"
                return

            except PermissionError:
                if attempt < max_retries - 1:
                    yield f"data: [MOVE] Access denied (OneDrive may be syncing), retrying in {retry_delay}s...\n\n"
                    time.sleep(retry_delay)
                else:
                    yield f"data: [ERROR] Permission denied after {max_retries} attempts. OneDrive may be locked.\n\n"
                    yield f"data: [ERROR] Try pausing OneDrive sync or moving manually.\n\n"
                    return

            except Exception as e:
                yield f"data: [ERROR] {str(e)}\n\n"
                return

    return StreamingResponse(
        streamOutput(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


@app.post('/albums/rename-folder')
async def renameFolder(body: ConvertRequest):
    new_artist = body.new_artist or body.artist
    new_album = body.new_album_name or body.album_name
    artist_dir = Link.DOWNLOAD_DIR / body.artist
    new_folder_name = Link.DOWNLOAD_DIR / f"{new_artist} - {new_album}"
    normalized_album = normalize_name(body.album_name)

    async def streamOutput():
        if not artist_dir.exists():
            yield f"data: [ERROR] Artist directory not found: {artist_dir}\n\n"
            return

        try:
            folders = [f for f in artist_dir.iterdir() if f.is_dir()]
        except Exception as e:
            yield f"data: [ERROR] Could not read directory: {e}\n\n"
            return

        source = next(
            (f for f in folders if normalize_name(f.name) == normalized_album),
            None
        )

        # Fallback: already partially renamed, try matching new name too
        if source is None:
            source = next(
                (f for f in folders if normalize_name(f.name) == normalize_name(new_album)),
                None
            )

        if source is None:
            yield f"data: [ERROR] No matching folder found for: {body.album_name}\n\n"
            return

        yield f"data: [RENAME] Found: {source.name}\n\n"
        yield f"data: [RENAME] Renaming to: {new_folder_name.name}\n\n"

        max_retries = 3
        for attempt in range(max_retries):
            try:
                yield f"data: [RENAME] Attempt {attempt + 1}: Renaming...\n\n"
                shutil.move(str(source), str(new_folder_name))

                # Clean up empty artist directory
                try:
                    if artist_dir.exists() and not any(artist_dir.iterdir()):
                        artist_dir.rmdir()
                        yield f"data: [RENAME] Removed empty artist directory\n\n"
                except Exception:
                    pass

                yield f"data: [RENAME] Done: {new_folder_name.name}\n\n"
                yield "data: [DONE]\n\n"
                return

            except PermissionError:
                if attempt < max_retries - 1:
                    yield f"data: [RENAME] File locked, retrying in 1s...\n\n"
                    await asyncio.sleep(1)
                else:
                    yield f"data: [ERROR] Permission denied. Try closing any file explorer windows.\n\n"
                    return

            except Exception as e:
                yield f"data: [ERROR] {type(e).__name__}: {str(e)}\n\n"
                return

    return StreamingResponse(
        streamOutput(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )

