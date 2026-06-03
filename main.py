import asyncio
import queue
import shutil
import threading
import time
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
import requests
from mutagen.flac import FLAC
from mutagen.mp4 import MP4
from starlette.middleware.cors import CORSMiddleware
import subprocess
from pydantic import BaseModel
from starlette.responses import StreamingResponse
from Secrets import Link
import os


app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                   allow_headers=["*"])  # Replace Allow origins with proper url later when deploying

Link.setup() # setup .mkdir() function

@app.get("/")
def home():
    """
    This is where the website search index will be, for now it just serves the index.html
    file which will be used for testing the frontend and backend connection.
    """
    return FileResponse("templates/index.html")



@app.get('/login')
def login():
    """
    Authorization Page for SongsAPI
    """



@app.get("/albums/search")
def searchAlbum(q: str, limit: int = 50):
    """
    Used to search for albums using the iTunes Search API.
    Returns a list of albums with their collection id, name, artist and apple music url
    which will be used for downloading the album later.
    """

    response = requests.get(Link.ITUNES_URL, params={"term": q, "entity": "album", "media": "music","limit": limit})
    results = response.json()["results"]

    albums = []
    for i, album in enumerate(results):
        if album.get("collectionType") != "Album":
            continue  # Skip non-album results
        albums.append(
            {
                "index": i,  # index added to pick which album to download
                "collection_id": album["collectionId"],
                "album_name": album["collectionName"],
                "artist": album["artistName"],
                "apple_music_url": album["collectionViewUrl"],
                "year": album["releaseDate"][:4]  # extracting year from release date
            }
        )
    return {"results": albums}


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
        process = subprocess.Popen( # args containing wrapper elements
            ["gamdl", "--song-codec-priority", Link.CODEC, "--use-wrapper", "--wrapper-account-url", Link.WRAPPER_ACCOUNT_URL, "--wrapper-m3u8-ip", Link.WRAPPER_M3U8_IP, "--wrapper-decrypt-ip", Link.WRAPPER_DECRYPT_IP, "--output-path", Link.DOWNLOAD_DIR, url],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,  # capturing both errs and output from gamdl process
            text=True,
            bufsize=0,  # 0 buffering
            env={**os.environ, "PYTHONUNBUFFERED": "1"}  # forcing gamdl to flush each line
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

        t1.join()
        t2.join()
        process.wait()

        print(f"[gamdl exited with code {process.returncode}]\n\n")

        if process.returncode == 0:

            folder = Link.DOWNLOAD_DIR / f"{match['artist']}" / f"{match['album_name']}"
            update_year(folder, match["year"])
            yield "data: [METADATA] Year Updated!\n\n"
            yield "data: [DONE]\n\n"
        else:
            yield f"data: [ERROR] gamdl exited with code {process.returncode}\n\n"
    return StreamingResponse(streamOutput(),media_type="text/event-stream",headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}) # headers to prevent buffering on nginx if used as a reverse proxy, also cache control to prevent caching of the stream

class MetadataRequest(BaseModel):
    folder: str
    year: str

def update_year(folder: str, year: str):
    folder = Path(folder)
    if not folder.exists():
        raise HTTPException(status_code=404, detail="Folder not found")

    changed = []
    for file in folder.rglob("*"):
        if file.suffix.lower() == ".flac":
            audio = FLAC(file)
            audio.save()
            changed.append(file.name)

        elif file.suffix.lower() == ".m4a":
            audio = MP4(file)
            audio["\xa9day"] = [year]
            audio.save()
            changed.append(file.name)
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
    album_name: str
    overwrite: bool = False # option to overwrite existing flac files, default is false to prevent accidental overwriting



@app.post("/albums/convert-flac")
async def convertToFLAC(body: ConvertRequest):
    """
    Converts all .m4a files in the album folder to .flac using ffmpeg via PowerShell script.
    Streams progress back to the client as SSE.
    """

    folder = Link.DOWNLOAD_DIR / f"{body.artist} - {body.album_name}"
    script_path = Link.DOWNLOAD_DIR / Link.CONVERT_TO_FLAC

    async def streamOutput():
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
def moveAlbum(body: ConvertRequest):
    """
    Move the album from the current directory to OneDrive Repository of Albums
    """

    source = Link.DOWNLOAD_DIR / f"{body.artist} - {body.album_name}"
    destination = Link.DESTINATION_DIR / f"{body.artist} - {body.album_name}" # NOT where the albums will download

    yield f"[MOVE] Source: {source}"
    yield f"[MOVE] Destination: {destination}"
    yield f"[MOVE] Source exists: {source.exists()}"
    yield f"[MOVE] Destination exists: {destination.exists()}"

    if not source.exists():
        yield f"[MOVE] Album not found at {source}"
        raise HTTPException(status_code=404, detail=f"Album not found at {source}")

    if destination.exists():
        yield f"[MOVE] Album already exists at {destination}"
        # If an album already exists in One-Drive

        if body.overwrite: # Enable overwrite clause
            yield f"[MOVE] Overwrite enabled. Removing {destination}..."
            shutil.rmtree(destination)
        else:
            raise HTTPException(status_code=409, detail=f"Album already exists at {destination}")
    try:
        yield f"[MOVE] Moving {source} -> {Link.DESTINATION_DIR} "
        shutil.move(source, Link.DESTINATION_DIR)

        yield f"[MOVE] Move completed!"
        yield f"[MOVE] Final Location {destination}"
        return {"status" : "success", "message": f"Moved to {Link.DESTINATION_DIR}"}

    except Exception as e:
        yield f"[MOVE] ERROR: {e}"
        raise HTTPException(status_code=505, detail=str(e))

# @app.post("/albums/move-album")
# async def moveAlbum(body: ConvertRequest):
#     source = Link.DOWNLOAD_DIR / f"{body.artist} - {body.album_name}"
#     destination = Link.DESTINATION_DIR / f"{body.artist} - {body.album_name}"
#
#     async def streamOutput():
#         yield f"data: [MOVE] Source: {source}\n\n"
#         yield f"data: [MOVE] Destination: {destination}\n\n"
#         yield f"data: [MOVE] Source exists: {source.exists()}\n\n"
#
#         if not source.exists():
#             yield f"data: [ERROR] Album not found at {source}\n\n"
#             return
#
#         yield f"data: [MOVE] Destination exists: {destination.exists()}\n\n"
#
#         if destination.exists():
#             if body.overwrite:
#                 yield f"data: [MOVE] Overwrite enabled, removing {destination}...\n\n"
#                 try:
#                     shutil.rmtree(destination)
#                 except Exception as e:
#                     yield f"data: [ERROR] Failed to remove existing: {e}\n\n"
#                     return
#             else:
#                 yield f"data: [ERROR] Album already exists at {destination}\n\n"
#                 return
#
#         # Retry logic — OneDrive locks folders while syncing
#         max_retries = 5
#         retry_delay = 2  # seconds
#
#         for attempt in range(max_retries):
#             try:
#                 yield f"data: [MOVE] Attempt {attempt + 1}/{max_retries}: Moving {source.name}...\n\n"
#                 shutil.move(str(source), str(Link.DESTINATION_DIR))
#                 yield f"data: [MOVE] Move completed!\n\n"
#                 yield f"data: [MOVE] Final location: {destination}\n\n"
#                 yield "data: [DONE]\n\n"
#                 return
#
#             except PermissionError as e:
#                 if attempt < max_retries - 1:
#                     yield f"data: [MOVE] Access denied (OneDrive may be syncing), retrying in {retry_delay}s...\n\n"
#                     time.sleep(retry_delay)
#                 else:
#                     yield f"data: [ERROR] Permission denied after {max_retries} attempts. OneDrive may be locked.\n\n"
#                     yield f"data: [ERROR] Try pausing OneDrive sync or moving manually.\n\n"
#                     return
#
#             except Exception as e:
#                 yield f"data: [ERROR] {str(e)}\n\n"
#                 return
#
#     return StreamingResponse(
#         streamOutput(),
#         media_type="text/event-stream",
#         headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
#     )

@app.post('/albums/rename-folder')
def renameFolder(body: ConvertRequest):
    artist_dir = Link.DOWNLOAD_DIR / body.artist
    old_album_location = artist_dir / body.album_name
    new_album_name = Link.DOWNLOAD_DIR / f"{body.artist} - {body.album_name}"


    yield f"[RENAME] Looking for: {old_album_location}"
    yield f"[RENAME] Exists: {old_album_location.exists()}"
    yield f"[RENAME] artist_dir contents: {list(artist_dir.iterdir()) if artist_dir.exists() else 'DIR NOT FOUND'}"

    if not old_album_location.exists():
        raise HTTPException(status_code=404, detail=f"Album folder not found at {old_album_location}")

    shutil.move(old_album_location,new_album_name)
    yield f"[RENAME] {old_album_location} -> {new_album_name}"

    if artist_dir.exists() and artist_dir.is_dir():
        if not any(artist_dir.iterdir()):
            artist_dir.rmdir()

    return {"status": "success", "message": f"Renamed to {new_album_name}"}

