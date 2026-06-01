import queue
import shutil
import threading
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

class DownloadRequest(BaseModel):
    collection_id: int
    results: list[dict]  # Full list of search results to pick the album from

@app.get("/")
def home():
    """
    This is where the website search index will be, for now it just serves the index.html file which will be used for testing the frontend and backend connection.
    """
    return FileResponse("index.html")


@app.get("/albums/search")
def searchAlbum(q: str, limit: int = 20):
    """
    Used to search for albums using the iTunes Search API.
    Returns a list of albums with their collection id, name, artist and apple music url which will be used for downloading the album later.
    """

    response = requests.get(Link.ITUNES_URL, params={"term": q, "entity": "album", "limit": limit})
    results = response.json()["results"]

    albums = []
    for i, album in enumerate(results):
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
            ["gamdl", "--cookies-path", Link.COOKIES_URL, "--output-path", Link.DOWNLOAD_DIR, url],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,  # i maybe autistic but this might be good merging stderr + stdout
            text=True,
            bufsize=0,  # 0 buffering
            env={**os.environ, "PYTHONUNBUFFERED": "1"}  # forcing gamdl to flush each line
        )

        q = queue.Queue()
        def enqueue(stream, label):
            for line in stream:
                q.put((label, line.rstrip()))
            q.put((label, None))  # i think this queue tells the other queue its done

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

        def renameFolder(artist, album):
            artist_dir = Link.DOWNLOAD_DIR / artist
            old_album = artist_dir / album
            new_name = Link.DOWNLOAD_DIR / f"{artist} - {album}"

            if old_album.exists():
                shutil.move(str(old_album), str(new_name))
                print(f"[rename] {old_album} -> {new_name}", flush=True)

            # removing of the old artist folder
            if not any(artist_dir.iterdir()):
                artist_dir.rmdir()

        renameFolder(match["artist"], match["album_name"]) # renaming the folder to "artist - album name" instead of "artist/album name"
        print(f"[gamdl exited with code {process.returncode}]\n\n")

        if process.returncode == 0:
            folder = Link.DOWNLOAD_DIR / f"{match['artist']} - {match['album_name']}"
            update_year(str(folder), match["year"])
            yield "data: [DONE]\n\n"
        else:
            yield f"data: [ERROR] gamdl exited with code {process.returncode}\n\n"

    return StreamingResponse(
        streamOutput(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )



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




