"""A plain folder on disk (also works on a Google Drive for Desktop or iCloud Drive mount)."""
from __future__ import annotations

import hashlib
import io
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from ..items import IMAGE_EXTS, VIDEO_EXTS, Item, guess_mime
from ..naming import safe_dirname, safe_filename, unique_name
from . import META_DIR, NoPreviewError

MEDIA_EXTS = IMAGE_EXTS | VIDEO_EXTS


class LocalFolderStorage:
    def __init__(self, root: str | os.PathLike, inbox: str = "Inbox", training_subfolder: str = "_training_data"):
        self.root = Path(root).expanduser().resolve()
        if not self.root.is_dir():
            raise FileNotFoundError(f"folder not found: {self.root}")
        self.inbox = inbox or "."
        self.training_subfolder = training_subfolder
        self.root_label = str(self.root)
        self.skipped: list[str] = []   # files in the inbox we do not treat as photos/videos
        self._md5: dict[tuple, str] = {}   # (path, size, mtime) -> md5, so re-sorting does not re-hash files

    # -- helpers -----------------------------------------------------------
    def _abs(self, rel: str) -> Path:
        return self.root if rel in ("", ".") else self.root / rel

    def _rel(self, p: Path) -> str:
        return p.relative_to(self.root).as_posix()

    def _item(self, p: Path) -> Item:
        st = p.stat()
        ext = p.suffix.lower()
        return Item(
            id=self._rel(p), name=p.name, mime=guess_mime(p.name), size=st.st_size,
            is_video=ext in VIDEO_EXTS,
            created=datetime.fromtimestamp(st.st_mtime, tz=timezone.utc),
            parent=self._rel(p.parent) if p.parent != self.root else "",
        )

    def _media_in(self, folder: Path) -> list[Item]:
        if not folder.is_dir():
            return []
        out = [self._item(p) for p in sorted(folder.iterdir())
               if p.is_file() and not p.name.startswith(".") and p.suffix.lower() in MEDIA_EXTS]
        return out

    def _parts_path(self, path_parts: list[str]) -> Path:
        return self.root.joinpath(*[safe_dirname(p) for p in path_parts])

    # -- Storage interface -------------------------------------------------
    def list_inbox(self) -> list[Item]:
        folder = self._abs(self.inbox)
        self.skipped = sorted(p.name for p in folder.iterdir()
                              if p.is_file() and not p.name.startswith(".") and p.suffix.lower() not in MEDIA_EXTS
                              and p.name != "CATEGORIES.md") if folder.is_dir() else []
        return self._media_in(folder)

    def list_folder(self, path_parts: list[str]) -> list[Item]:
        return self._media_in(self._parts_path(path_parts))

    def folder_id(self, path_parts: list[str]) -> str | None:
        p = self._parts_path(path_parts)
        return self._rel(p) if p.is_dir() else None

    def ensure_folder(self, path_parts: list[str]) -> str:
        p = self._parts_path(path_parts)
        p.mkdir(parents=True, exist_ok=True)
        return self._rel(p)

    def read_bytes(self, item: Item, prefer: str = "thumb") -> bytes:
        if prefer == "thumb" and item.is_video:
            raise NoPreviewError("local videos have no poster frame")
        data = self._abs(item.id).read_bytes()
        if prefer == "thumb":
            return _downscale(data, 640) or data
        return data

    def move_rename(self, item: Item, folder_id: str, new_name: str) -> Item:
        src = self._abs(item.id)
        dst_dir = self._abs(folder_id)
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst = dst_dir / new_name
        if dst.exists():
            raise FileExistsError(f"{dst} already exists")
        os.replace(src, dst)
        moved = self._item(dst)
        moved.md5 = item.md5
        return moved

    def list_examples(self, path_parts: list[str]) -> list[Item]:
        return self._media_in(self._parts_path(path_parts) / self.training_subfolder)

    def read_meta(self, name: str) -> bytes | None:
        p = self.root / META_DIR / name
        return p.read_bytes() if p.exists() else None

    def write_meta(self, name: str, data: bytes) -> None:
        _atomic_write(self.root / META_DIR / name, data)

    def append_meta(self, name: str, data: bytes) -> None:
        d = self.root / META_DIR
        d.mkdir(exist_ok=True)
        with (d / name).open("ab") as f:
            f.write(data)

    def read_root_file(self, name: str) -> bytes | None:
        p = self.root / name
        return p.read_bytes() if p.is_file() else None

    def write_root_file(self, name: str, data: bytes) -> None:
        _atomic_write(self.root / name, data)

    def folder_tree(self, max_depth: int = 3) -> list[list[str]]:
        out: list[list[str]] = []

        def walk(folder: Path, parts: list[str]) -> None:
            if len(parts) >= max_depth:
                return
            for c in sorted(folder.iterdir(), key=lambda x: x.name.lower()):
                if c.is_dir() and not c.name.startswith(".") and c.name != self.training_subfolder:
                    out.append(parts + [c.name])
                    walk(c, parts + [c.name])

        walk(self.root, [])
        return out

    def md5_of(self, item: Item) -> str:
        if not item.md5:
            key = (item.id, item.size, item.created)
            if key not in self._md5:
                h = hashlib.md5()
                with self._abs(item.id).open("rb") as f:
                    for chunk in iter(lambda: f.read(1 << 20), b""):
                        h.update(chunk)
                self._md5[key] = h.hexdigest()
            item.md5 = self._md5[key]
        return item.md5

    def write_file(self, path_parts: list[str], name: str, data: bytes) -> Item:
        folder = self._parts_path(path_parts)
        folder.mkdir(parents=True, exist_ok=True)
        name = unique_name({p.name for p in folder.iterdir()}, safe_filename(name))
        tmp = folder / f".{name}.part"
        tmp.write_bytes(data)
        os.replace(tmp, folder / name)
        return self._item(folder / name)

    def trash(self, item: Item) -> None:
        """Move into .sorter/trash/ instead of deleting, so it can be recovered."""
        src = self._abs(item.id)
        dst = self.root / META_DIR / "trash"
        dst.mkdir(parents=True, exist_ok=True)
        os.replace(src, dst / f"{int(time.time())}_{src.name}")


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _downscale(data: bytes, max_side: int) -> bytes | None:
    """Return a JPEG no larger than max_side on its long edge, or None if PIL cannot decode it."""
    try:
        from PIL import Image, ImageOps

        try:
            import pillow_heif  # noqa: F401

            pillow_heif.register_heif_opener()
        except ImportError:
            pass
        img = Image.open(io.BytesIO(data))
        img.draft("RGB", (max_side * 2, max_side * 2))
        img = ImageOps.exif_transpose(img).convert("RGB")
        img.thumbnail((max_side, max_side))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=85)
        return buf.getvalue()
    except Exception:
        return None
