"""Where the photos live. Two backends share one interface: a local folder and Google Drive.

Category paths (see config.split_path) map to nested folders. Files under META_DIR hold the
manifest and embedding caches; TRAINING_DIR inside a category folder holds reference examples.
"""
from __future__ import annotations

from typing import Protocol

from ..items import Item

META_DIR = ".sorter"


class NoPreviewError(RuntimeError):
    """The backend cannot produce an image preview for this item (e.g. a local video)."""


class Storage(Protocol):
    root_label: str
    training_subfolder: str

    def list_inbox(self) -> list[Item]: ...
    def list_folder(self, path_parts: list[str]) -> list[Item]: ...
    def folder_id(self, path_parts: list[str]) -> str | None: ...
    def ensure_folder(self, path_parts: list[str]) -> str: ...
    def read_bytes(self, item: Item, prefer: str = "thumb") -> bytes: ...
    def move_rename(self, item: Item, folder_id: str, new_name: str) -> Item: ...
    def list_examples(self, path_parts: list[str]) -> list[Item]: ...
    def read_meta(self, name: str) -> bytes | None: ...
    def write_meta(self, name: str, data: bytes) -> None: ...
    def append_meta(self, name: str, data: bytes) -> None: ...                           # add to the end of a log
    def read_root_file(self, name: str) -> bytes | None: ...                             # a file at the top (CATEGORIES.md)
    def write_root_file(self, name: str, data: bytes) -> None: ...
    def folder_tree(self, max_depth: int = 3) -> list[list[str]]: ...                    # existing subfolders, as paths
    def md5_of(self, item: Item) -> str: ...
    def write_file(self, path_parts: list[str], name: str, data: bytes) -> Item: ...   # unique name, folders created
    def trash(self, item: Item) -> None: ...                                             # reversible delete


def append_jsonl(storage, name: str, record: dict) -> None:
    """Append one JSON line to .sorter/<name> (logs never break the caller)."""
    import json

    try:
        storage.append_meta(name, (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8"))
    except Exception:  # noqa: BLE001
        pass


def read_jsonl(storage, name: str) -> list[dict]:
    import json

    try:
        raw = storage.read_meta(name) or b""
    except Exception:  # noqa: BLE001
        return []
    out = []
    for line in raw.decode("utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def open_storage(cfg, credentials=None) -> Storage:
    kind = cfg.source_kind
    if kind in ("local", "drive") and not cfg.source_root:
        raise ValueError("no photo folder chosen yet: run `photo-sorter serve` and pick one, or pass --source <folder or Drive link>")
    if kind == "local":
        from .local import LocalFolderStorage

        return LocalFolderStorage(cfg.source_root, inbox=cfg.inbox, training_subfolder=cfg.training_subfolder)
    if kind == "drive":
        from .drive import GoogleDriveStorage

        return GoogleDriveStorage.open(cfg.source_root, credentials=credentials, inbox=cfg.inbox,
                                       training_subfolder=cfg.training_subfolder)
    raise ValueError(f"source.kind {kind!r} has no storage backend (icloud uses the legacy fetch/classify/apply commands)")
