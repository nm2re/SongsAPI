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
            # ["gamdl", "--cookies-path", Link.COOKIES_URL, "--output-path", Link.DOWNLOAD_DIR, url],
            ["gamdl", "--song-codec-priority", "alac", "--use-wrapper", "--wrapper-account-url", Link.WRAPPER_ACCOUNT_URL, "--wrapper-m3u8-ip", Link.WRAPPER_M3U8_IP, "--wrapper-decrypt-ip", Link.WRAPPER_DECRYPT_IP, "--output-path", Link.DOWNLOAD_DIR, url],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,  # capturing both errs and output from gamdl process
            text=True,
            bufsize=0,  # 0 buffering
            env={**os.environ, "PYTHONUNBUFFERED": "1"}  # forcing gamdl to flush each line
        )

        q = queue.Queue()
        def enqueue(stream, label):
            # for line in stream:
            #     q.put((label, line.rstrip()))
            # q.put((label, None))  # i think this queue tells the other queue its done
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

            # Convert the album to FLAC
            yield "data: [Converting to FLAC...]\n\n"
            convert_response = convertToFLAC(ConvertRequest(artist=match['artist'], album_name=match['album_name'])) # passing the request as arguments

            if convert_response["status"] == "success":
                yield "data: [DONE]\n\n"
            else:
                yield f"data: [CONVERSION FAILED]\n\n"
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


class ConvertRequest(BaseModel): # a structure to help write the functions based on the artist and album name instead of the folder name which can be different based on the gamdl version and settings
    artist: str
    album_name: str

@app.post("/albums/convert-flac")
def convertToFLAC(body: ConvertRequest): # class folder structure is used here to use artist and album_name
    """
    This function runs a powershell command script to convert the folder containing the .m4a files into .flac
    :return:
    """

    folder = Link.DOWNLOAD_DIR / f"{body.artist} - {body.album_name}"
    script_path = Link.DOWNLOAD_DIR / Link.CONVERT_TO_FLAC
    result = subprocess.Popen(
        ["powershell", "-ExecutionPolicy", "Bypass", "-File", script_path, "-FolderPath", folder],  # powershell command to convert m4a to flac using ffmpeg
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=0
    )

    stdout, stderr = result.communicate()

    if result.returncode == 0:
        print(stdout, flush=True)
        return {"status": "success", "message": stdout}
    else:
        print(stderr, flush=True)
        raise HTTPException(status_code=500, detail=f"Conversion failed: {stderr}")




