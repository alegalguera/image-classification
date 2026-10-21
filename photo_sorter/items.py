"""Backend-neutral description of one file in the inbox or a category folder."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".avif", ".tif", ".tiff", ".webp", ".gif", ".bmp", ".dng"}
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".avi", ".mkv"}


@dataclass
class Item:
    id: str                      # Drive file id, or path relative to the root for local folders
    name: str                    # current filename
    mime: str = ""
    size: int | None = None
    is_video: bool = False
    created: datetime | None = None
    md5: str | None = None       # Drive md5Checksum; local: computed lazily by the storage
    parent: str | None = None    # folder id (Drive) or relative folder path (local)
    extra: dict = field(default_factory=dict, repr=False)

    @property
    def ext(self) -> str:
        """Lower-cased extension including the dot, '' if none."""
        i = self.name.rfind(".")
        return self.name[i:].lower() if i > 0 else ""

    @property
    def is_image(self) -> bool:
        return not self.is_video and (self.mime.startswith("image/") or self.ext in IMAGE_EXTS)


def guess_is_video(name: str, mime: str = "") -> bool:
    if mime:
        return mime.startswith("video/")
    i = name.rfind(".")
    return i > 0 and name[i:].lower() in VIDEO_EXTS


def guess_mime(name: str) -> str:
    import mimetypes

    return mimetypes.guess_type(name)[0] or ""
