"""Read-only client for public iCloud Shared Albums.

Supports the current link format, e.g.
    https://photos.icloud.com/shared/album/<token>/
These albums live in CloudKit. The flow (reverse-engineered from the iCloud
Photos web app) is:

  1. POST .../public/records/resolve  with sharing_url_key=<token>
     -> zone id, database partition host and a short-lived anonymous token
  2. POST <partition>/.../shared/changes/zone  with the same token
     -> every record in the zone (CPLAsset + CPLMaster pairs, paged)
  3. Each CPLMaster carries signed download URLs (thumb / medium / original).
     Download URLs and the anonymous token expire after ~20 minutes, so call
     `SharedAlbum.open()` again rather than caching URLs across runs.
"""
from __future__ import annotations

import base64
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterator

CK_BASE = "https://ckdatabasews.icloud.com"
CK_PATH = "/database/1/com.apple.photos.cloud/production"
HEADERS = {
    "content-type": "text/plain",
    "Origin": "https://photos.icloud.com",
    "Referer": "https://photos.icloud.com/",
}
VIDEO_TYPES = ("com.apple.quicktime-movie", "public.mpeg-4", "public.movie", "public.video")


class SharedAlbumError(RuntimeError):
    pass


def parse_token(url_or_token: str) -> str:
    """Extract the sharing token from a shared album URL (or pass a bare token through)."""
    s = url_or_token.strip()
    m = re.search(r"/shared/album/([^/?#]+)", s)
    if m:
        return m.group(1)
    if re.search(r"icloud\.com/sharedalbum/#", s):
        raise SharedAlbumError(
            "This is a legacy 'www.icloud.com/sharedalbum/#...' link, which uses a different "
            "API that is not supported. Open the album in Photos and copy a fresh link "
            "(it should look like https://photos.icloud.com/shared/album/<token>/)."
        )
    if re.fullmatch(r"[A-Za-z0-9_-]{10,}", s):
        return s
    raise SharedAlbumError(f"Could not find a shared-album token in: {url_or_token!r}")


@dataclass
class Asset:
    asset_id: str              # CPLAsset record name (stable id, use this as the key)
    master_id: str             # CPLMaster record name
    filename: str
    created: datetime | None
    is_video: bool
    width: int | None
    height: int | None
    thumb_url: str | None      # ~480px JPEG, ideal for classification
    medium_url: str | None     # larger JPEG
    original_url: str | None   # the original file (may be HEIC / MOV)
    original_type: str | None  # UTI such as public.jpeg or public.heic
    raw: dict = field(default_factory=dict, repr=False)

    def url_for(self, size: str) -> str | None:
        """size: 'thumb' | 'medium' | 'original'. Falls back to the next best available."""
        order = {
            "thumb": (self.thumb_url, self.medium_url, self.original_url),
            "medium": (self.medium_url, self.thumb_url, self.original_url),
            "original": (self.original_url, self.medium_url, self.thumb_url),
        }[size]
        return next((u for u in order if u), None)

    def output_filename(self, size: str) -> str:
        """Filename to save under. Thumb/medium are always JPEG regardless of the original."""
        if size == "original":
            return self.filename
        stem = self.filename.rsplit(".", 1)[0] if "." in self.filename else self.filename
        return f"{stem}.jpg"


@dataclass
class SharedAlbum:
    token: str
    title: str
    owner: str
    zone_id: dict
    partition: str
    access_token: str
    assets: list[Asset]

    @classmethod
    def open(cls, url_or_token: str) -> "SharedAlbum":
        token = parse_token(url_or_token)
        res = _post(
            f"{CK_BASE}{CK_PATH}/public/records/resolve",
            {"sharing_url_key": token},
            {"shortGUIDs": [{"value": token}]},
        )
        results = res.get("results") or []
        if not results or results[0].get("serverErrorCode"):
            raise SharedAlbumError(f"Album not found or no longer shared: {json.dumps(res)[:300]}")
        r = results[0]
        apa = r.get("anonymousPublicAccess")
        if not apa:
            raise SharedAlbumError("Album does not allow public (link) access; ask the owner to enable 'Public Website'.")
        share_fields = r.get("share", {}).get("fields", {})
        owner = r.get("ownerIdentity", {}).get("nameComponents", {})
        album = cls(
            token=token,
            title=share_fields.get("cloudkit.title", {}).get("value", ""),
            owner=" ".join(filter(None, [owner.get("givenName"), owner.get("familyName")])),
            zone_id=r["zoneID"],
            partition=apa["databasePartition"].rstrip("/"),
            access_token=apa["token"],
            assets=[],
        )
        album.assets = list(album._load_assets())
        return album

    # -- internals ---------------------------------------------------------
    def _query(self):
        return {"sharing_url_key": self.token, "publicAccessAuthToken": self.access_token}

    def _records(self) -> Iterator[dict]:
        sync_token = None
        while True:
            body = {"zones": [{"zoneID": self.zone_id, "syncToken": sync_token, "reverse": False}]}
            d = _post(f"{self.partition}{CK_PATH}/shared/changes/zone", self._query(), body)
            zone = (d.get("zones") or [{}])[0]
            yield from zone.get("records", [])
            if not zone.get("moreComing"):
                return
            sync_token = zone.get("syncToken")

    def _load_assets(self) -> Iterator[Asset]:
        masters: dict[str, dict] = {}
        assets: list[dict] = []
        for rec in self._records():
            if rec.get("deleted"):
                continue
            t = rec.get("recordType")
            if t == "CPLMaster":
                masters[rec["recordName"]] = rec
            elif t == "CPLAsset":
                assets.append(rec)
        for a in assets:
            f = a.get("fields", {})
            mref = f.get("masterRef", {}).get("value", {}).get("recordName")
            m = masters.get(mref)
            if not m:
                continue
            mf = m.get("fields", {})
            filename = _b64_str(mf.get("filenameEnc", {}).get("value")) or f"{a['recordName']}.jpg"
            item_type = mf.get("itemType", {}).get("value") or mf.get("resOriginalFileType", {}).get("value")
            is_video = bool(item_type and item_type.startswith(VIDEO_TYPES)) or "resVidSmallRes" in mf
            ts = f.get("assetDate", {}).get("value") or mf.get("originalCreationDate", {}).get("value")
            yield Asset(
                asset_id=a["recordName"],
                master_id=m["recordName"],
                filename=filename,
                created=datetime.fromtimestamp(ts / 1000, tz=timezone.utc) if ts else None,
                is_video=is_video,
                width=mf.get("resOriginalWidth", {}).get("value"),
                height=mf.get("resOriginalHeight", {}).get("value"),
                thumb_url=_dl(mf, "resJPEGThumbRes", filename),
                medium_url=_dl(mf, "resJPEGMedRes", filename),
                original_url=_dl(mf, "resOriginalRes", filename),
                original_type=item_type,
                raw={"asset": f, "master": mf},
            )


def download(url: str, retries: int = 3, timeout: int = 120) -> bytes:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except (urllib.error.URLError, TimeoutError) as e:  # noqa: PERF203
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise SharedAlbumError(f"download failed after {retries} tries: {last}")


def _post(url: str, query: dict, body: dict) -> dict:
    q = {"remapEnums": "true", "getCurrentSyncToken": "true", **query}
    req = urllib.request.Request(
        f"{url}?{urllib.parse.urlencode(q)}",
        data=json.dumps(body).encode(),
        headers=HEADERS,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        payload = e.read().decode(errors="replace")[:500]
        raise SharedAlbumError(f"iCloud returned HTTP {e.code} for {url}: {payload}") from e
    except urllib.error.URLError as e:
        raise SharedAlbumError(f"network error talking to iCloud: {e}") from e


def _b64_str(v: str | None) -> str | None:
    if not v:
        return None
    try:
        return base64.b64decode(v).decode("utf-8")
    except Exception:
        return None


def _dl(master_fields: dict, key: str, filename: str) -> str | None:
    url = master_fields.get(key, {}).get("value", {}).get("downloadURL")
    if not url:
        return None
    return url.replace("${f}", urllib.parse.quote(filename))
