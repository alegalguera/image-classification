"""Find openly licensed example images on the web (Openverse: Wikimedia Commons, Flickr CC and more).

No API key. Every result carries its licence, author and source page so downloads can be attributed.
"""
from __future__ import annotations

import io
import json
import re
import urllib.parse
import urllib.request

OPENVERSE = "https://api.openverse.org/v1/images/"
UA = "photo-sorter/0.2 (research image sorter)"
MAX_BYTES = 20 * 2**20
MAX_SIDE = 1600


class SearchError(RuntimeError):
    pass


def _get(url: str, timeout: int = 30) -> tuple[bytes, str]:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise SearchError("image is larger than 20 MB")
            return data, r.headers.get("Content-Type", "")
    except SearchError:
        raise
    except Exception as e:  # noqa: BLE001
        raise SearchError(f"could not reach {urllib.parse.urlparse(url).netloc}: {e}") from e


def search_images(query: str, page: int = 1, page_size: int = 20) -> list[dict]:
    """Openly licensed images for a sentence. Returns dicts: id, title, thumb, url, license, creator, landing, provider."""
    q = urllib.parse.urlencode({"q": query.strip()[:200], "page": max(1, page), "page_size": page_size, "mature": "false"})
    raw, _ = _get(f"{OPENVERSE}?{q}")
    try:
        results = json.loads(raw).get("results", [])
    except ValueError as e:
        raise SearchError("unexpected answer from the image search") from e
    out = []
    for r in results:
        lic = (r.get("license") or "").upper()
        out.append({
            "id": r.get("id", ""), "title": (r.get("title") or "untitled")[:120], "thumb": r.get("thumbnail") or r.get("url"),
            "url": r.get("url"), "license": f"CC {lic} {r.get('license_version') or ''}".strip() if lic not in ("", "PDM", "CC0") else lic,
            "creator": r.get("creator") or "", "landing": r.get("foreign_landing_url") or "", "provider": r.get("provider") or "",
        })
    return [r for r in out if r["url"]]


def fetch_image(url: str) -> tuple[bytes, str]:
    """Download an image and return (jpeg bytes, '.jpg'); large images are scaled down to 1600 px."""
    from PIL import Image, ImageOps

    data, ctype = _get(url, timeout=60)
    try:
        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img).convert("RGB")
    except Exception as e:  # noqa: BLE001
        raise SearchError(f"not an image ({ctype or 'unknown type'})") from e
    img.thumbnail((MAX_SIDE, MAX_SIDE))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88)
    return buf.getvalue(), ".jpg"


def filename_for(result: dict) -> str:
    stem = re.sub(r"[^a-z0-9]+", "_", (result.get("title") or "web").lower()).strip("_")[:40] or "web"
    return f"{stem}_{(result.get('id') or '')[:8]}.jpg"
