import queue
import threading
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
import requests
from starlette.middleware.cors import CORSMiddleware
import subprocess
from pydantic import BaseModel
from starlette.responses import StreamingResponse
from pathlib import Path
import os

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                   allow_headers=["*"])  # Replace Allow origins with proper url later when deploying
ITUNES_URL = "https://itunes.apple.com/search"
DOWNLOAD_DIR = Path("C:/Users/Administrator/Downloads/Albums")
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)  # creates a parent for you incase you dont have one (directory)
COOKIES_URL = Path("C:/Users/Administrator/cookies.txt")


class DownloadRequest(BaseModel):
    collection_id: int
    results: list[dict]  # Full list of search results to pick the album from


@app.get("/")
def home():
    """
    This is where the website search index will be
    :return:
    """
    return FileResponse("index.html")


@app.get("/albums/search")
def searchAlbum(q: str, limit: int = 10):
    """
    Used to search for albums
    :return:
    """

    response = requests.get(ITUNES_URL, params={"term": q, "entity": "album", "limit": limit})
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
            }
        )
    return {"results": albums}


@app.post("/albums/download")
async def albumDownload(body: DownloadRequest): # async functions important for yielding to SSE otherwise it would not run
    """
    Selected albums will be downloaded
    :return:
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
        process = subprocess.Popen(

            ["gamdl", "--cookies-path", COOKIES_URL, "--output-path", DOWNLOAD_DIR, url],
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
        print(f"[gamdl exited with code {process.returncode}]\n\n")

        if process.returncode != 0:
            yield f"data: [ERROR] gamdl exited with code {process.returncode}\n\n"
        else:
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        streamOutput(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )
