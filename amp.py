import time
import httpx
import re
from Secrets import Link
from gamdl.api.apple_music import AppleMusicApi



STOREFRONT = "in" # matching the accounts country

_token: str | None = None
_token_at: float = 0
_TOKEN_TTL = 60 * 60 * 6   # re-scrape every 6h


async def _get_token(force: bool = False) -> str:
    global _token, _token_at
    if force or not _token or (time.time() - _token_at) > _TOKEN_TTL:
        _token = await AppleMusicApi.get_token()
        _token_at = time.time()
        print("[AMP] fetched new developer token")
    return _token


async def amp_get(path: str, params: dict | None = None) -> dict:
    """GET against the Apple Music catalog API, refreshing the token on 401."""
    for attempt in range(2):
        token = await _get_token(force=(attempt > 0))
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(
                f"{Link.AMP_BASE_URL}{path}",
                params=params or {},
                headers={
                    "authorization": f"Bearer {token}",
                    "origin": "https://music.apple.com",
                },
            )
        if r.status_code == 401 and attempt == 0:
            continue                      # token rotated, retry once
        r.raise_for_status()
        return r.json()
    raise RuntimeError("AMP request failed after token refresh")

def _album_out(a: dict) -> dict:
    at = a["attributes"]
    return {
        "collection_id": int(a["id"]),
        "album_name": at["name"],
        "artist": at.get("artistName", "Unknown"),
        "apple_music_url": at.get("url", ""),
        "year": (at.get("releaseDate") or "N/A")[:4],
        "track_count": at.get("trackCount"),
        "is_single": at.get("isSingle", False),
        "is_compilation": at.get("isCompilation", False),
        "upc": at.get("upc"),
    }


async def amp_search(term: str, limit: int = 25) -> list[dict]:
    data = await amp_get(
        f"/v1/catalog/{STOREFRONT}/search",
        {"term": term, "types": "albums,songs", "limit": limit},
    )
    res = data.get("results", {})
    albums = [_album_out(a) for a in res.get("albums", {}).get("data", [])]

    # pull in albums behind matching songs, deduped
    seen = {a["collection_id"] for a in albums}
    for s in res.get("songs", {}).get("data", []):
        url = s["attributes"].get("url", "")
        m = re.search(r"/album/[^/]+/(\d+)", url)
        if m and int(m.group(1)) not in seen:
            seen.add(int(m.group(1)))
            try:
                alb = await amp_get(f"/v1/catalog/{STOREFRONT}/albums/{m.group(1)}")
                if alb.get("data"):
                    albums.append(_album_out(alb["data"][0]))
            except Exception as e:
                print(f"[SEARCH] skipping song→album {m.group(1)}: {e}")
    return albums


async def amp_equivalent(album_id: str) -> dict | None:
    """Resolve any storefront's album ID into ours."""
    data = await amp_get(f"/v1/catalog/{STOREFRONT}/albums",
                         {"filter[equivalents]": album_id})
    d = data.get("data", [])
    return _album_out(d[0]) if d else None


async def amp_by_upc(upc: str) -> dict | None:
    data = await amp_get(f"/v1/catalog/{STOREFRONT}/albums", {"filter[upc]": upc})
    d = data.get("data", [])
    return _album_out(d[0]) if d else None