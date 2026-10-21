"""Folder and file naming: safe folder names, category slugs, per-folder counters."""
from __future__ import annotations

import re

DEFAULT_PATTERN = "{slug}_{n:03d}{ext}"


def safe_dirname(name: str) -> str:
    """A category path part as a folder name: slashes, backslashes and colons become '-'."""
    return re.sub(r"[/\\:]", "-", name).strip() or "_"


MEDIA_PREFIX = re.compile(r"^(photos|videos)\s*-\s*", re.I)


def strip_media_prefix(name: str) -> str:
    """'Photos - Fragging' -> 'Fragging'. The album tree uses this prefix for top-level folders."""
    return MEDIA_PREFIX.sub("", name).strip()


def slugify(leaf: str) -> str:
    """'Sponge Disease/Bleaching' -> 'sponge_disease_bleaching'; 'Photos - Fragging' -> 'fragging'."""
    s = re.sub(r"[^a-z0-9]+", "_", strip_media_prefix(leaf).lower()).strip("_")
    return s or "item"


def build_name(slug: str, n: int, ext: str, pattern: str = DEFAULT_PATTERN) -> str:
    return pattern.format(slug=slug, n=n, ext=ext.lower())


def max_counter(names: list[str], slug: str) -> int:
    """Highest N among names matching '<slug>_<N>.<ext>' (0 if none)."""
    rx = re.compile(rf"^{re.escape(slug)}_(\d+)\.[^.]+$", re.I)
    best = 0
    for name in names:
        m = rx.match(name)
        if m:
            best = max(best, int(m.group(1)))
    return best


class Counters:
    """Next free number per (folder, slug). Seeded from a folder listing once, then counted up."""

    def __init__(self) -> None:
        self._next: dict[tuple[str, str], int] = {}

    def seed(self, folder_id: str, slug: str, existing_names: list[str]) -> None:
        self._next.setdefault((folder_id, slug), max_counter(existing_names, slug) + 1)

    def is_seeded(self, folder_id: str, slug: str) -> bool:
        return (folder_id, slug) in self._next

    def peek(self, folder_id: str, slug: str) -> int:
        return self._next.get((folder_id, slug), 1)

    def snapshot(self) -> dict:
        return dict(self._next)

    def restore(self, snap: dict) -> None:
        self._next = dict(snap)

    def take(self, folder_id: str, slug: str) -> int:
        key = (folder_id, slug)
        n = self._next.get(key, 1)
        self._next[key] = n + 1
        return n


def unique_name(existing: set[str], name: str) -> str:
    """name, or name_2 / name_3 ... if it is taken."""
    if name not in existing:
        return name
    stem, dot, ext = name.rpartition(".")
    stem, dot = (stem, dot) if stem else (name, "")
    ext = ext if dot else ""
    k = 2
    while f"{stem}_{k}{dot}{ext}" in existing:
        k += 1
    return f"{stem}_{k}{dot}{ext}"


def safe_filename(name: str, default: str = "image") -> str:
    name = re.sub(r"[/\\:\x00-\x1f]", "-", name).strip(" .")
    return name[:120] or default
