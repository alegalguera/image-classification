"""Destinations: a local folder, the iCloud Drive folder, or Apple Photos albums.

Category keys may be paths joined with " › " (see config.py). A folder sink turns
them into nested directories; the Photos sink turns them into folders + an album.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from .config import split_path
from .icloud import Asset, download
from .naming import safe_dirname as _safe_dirname

ICLOUD_DRIVE = Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs"


def resolve_dest(dest: str, folder: str) -> Path | str:
    """'icloud' -> iCloud Drive/<folder>; 'photos' -> Apple Photos; anything else -> a local path."""
    if dest == "photos":
        return "photos"
    if dest == "icloud":
        if not ICLOUD_DRIVE.exists():
            raise FileNotFoundError(f"iCloud Drive folder not found at {ICLOUD_DRIVE}. Is iCloud Drive enabled?")
        return ICLOUD_DRIVE / folder
    return Path(dest).expanduser()


class FolderSink:
    """Copies files into <root>/<path parts...>/<filename>. Idempotent: existing files are skipped."""

    def __init__(self, root: Path, size: str, dry_run: bool = False):
        self.root, self.size, self.dry_run = root, size, dry_run
        self.written = self.skipped = 0

    def put(self, asset: Asset, category: str) -> Path:
        target = self.root.joinpath(*[_safe_dirname(p) for p in split_path(category)]) / asset.output_filename(self.size)
        if target.exists():
            self.skipped += 1
            return target
        url = asset.url_for(self.size)
        if not url:
            raise RuntimeError(f"{asset.filename}: no download URL")
        if not self.dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(target.suffix + ".part")
            tmp.write_bytes(download(url))
            os.replace(tmp, target)
        self.written += 1
        return target

    def flush(self) -> None:
        pass

    def summary(self) -> str:
        return f"{self.written} file(s) written, {self.skipped} already present, under {self.root}"


class PhotosSink:
    """Imports into Apple Photos: category path -> folders, last segment -> album.

    Needs the `photos` extra (`uv sync --extra photos`) and Automation permission for Photos.
    Imports use skip_duplicate_check=True so the run never blocks on a Photos dialog; to stay
    idempotent, imported asset ids are recorded in `ledger` and skipped on later runs.
    """

    def __init__(self, size: str, dry_run: bool = False, ledger: Path | None = None):
        try:
            import photoscript  # noqa: F401
        except ImportError as e:
            raise ImportError("photoscript is not installed: run `uv sync --extra photos`") from e
        self.size, self.dry_run = size, dry_run
        self.ledger = ledger or Path("results/photos-imported.txt")
        self.done = set(self.ledger.read_text(encoding="utf-8").split()) if self.ledger.exists() else set()
        self.tmp = Path(tempfile.mkdtemp(prefix="photo-sorter-"))
        self.pending: dict[str, list[Asset]] = {}
        self.written = self.skipped = 0

    def put(self, asset: Asset, category: str) -> str:
        if asset.asset_id in self.done:
            self.skipped += 1
            return f"Photos: already imported"
        self.pending.setdefault(category, []).append(asset)
        parts = split_path(category)
        return "Photos album " + " / ".join(parts)

    def _album(self, lib, category: str):
        parts = split_path(category)
        if len(parts) == 1:
            return lib.album(parts[0], top_level=True) or lib.create_album(parts[0])
        return lib.make_album_folders(parts[-1], parts[:-1])

    def flush(self) -> None:
        if not self.pending:
            return
        if self.dry_run:
            for category, assets in self.pending.items():
                print(f"  [dry-run] would import {len(assets)} item(s) into '{' / '.join(split_path(category))}'")
            return
        import photoscript

        lib = photoscript.PhotosLibrary()
        for category, assets in self.pending.items():
            album = self._album(lib, category)
            paths, ids = [], []
            for a in assets:
                url = a.url_for(self.size)
                if not url:
                    continue
                p = self.tmp / a.output_filename(self.size)
                p.write_bytes(download(url))
                paths.append(str(p))
                ids.append(a.asset_id)
            if paths:
                lib.import_photos(paths, album=album, skip_duplicate_check=True)
                self.written += len(paths)
                self.done.update(ids)
                self.ledger.parent.mkdir(parents=True, exist_ok=True)
                self.ledger.write_text("\n".join(sorted(self.done)) + "\n", encoding="utf-8")
            for p in paths:
                Path(p).unlink(missing_ok=True)
            print(f"  '{' / '.join(split_path(category))}': imported {len(paths)}")

    def summary(self) -> str:
        return f"{self.written} item(s) imported into Apple Photos, {self.skipped} already imported earlier"
