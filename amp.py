import time
import httpx
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