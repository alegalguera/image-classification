"""Example images (<category>/_training_data). Rule: one picture teaches exactly one category.

Pictures are identified by content (md5), so a renamed copy is the same picture. Whenever a picture is filed
into, moved to, or added as an example of a category, any example copy of it under another category is moved
over (or trashed if the target already has it). That keeps a wrong example from lingering after a fix.
"""
from __future__ import annotations

import hashlib

from .config import Config, split_path
from .items import Item
from .naming import unique_name
from .storage import append_jsonl

LOG = "examples.jsonl"


def training_parts(cfg: Config, cat: str) -> list[str]:
    return split_path(cat) + [cfg.training_subfolder]


def categories(cfg: Config) -> list[str]:
    return list(cfg.categories) + list(cfg.video_categories or {})


def copies(storage, cfg: Config, md5: str) -> list[tuple[str, Item]]:
    """(category, item) for every example file with this content."""
    out = []
    for cat in categories(cfg):
        for it in storage.list_examples(split_path(cat)):
            if storage.md5_of(it) == md5:
                out.append((cat, it))
    return out


def move_example(storage, cfg: Config, item: Item, to_cat: str) -> Item:
    dest = storage.ensure_folder(training_parts(cfg, to_cat))
    name = unique_name({i.name for i in storage.list_examples(split_path(to_cat))}, item.name)
    return storage.move_rename(item, dest, name)


def assign(storage, cfg: Config, md5: str | None, to_cat: str) -> list[str]:
    """Make this picture an example of `to_cat` only, if it is an example anywhere. Returns the categories it left."""
    if not md5 or to_cat not in categories(cfg):
        return []
    found = copies(storage, cfg, md5)
    have = any(cat == to_cat for cat, _ in found)
    left = []
    for cat, it in found:
        if cat == to_cat:
            continue
        if have:
            storage.trash(it)
        else:
            move_example(storage, cfg, it, to_cat)
            have = True
        left.append(cat)
    if left:
        append_jsonl(storage, LOG, {"category": to_cat, "md5": md5, "source": "reassigned", "from": left})
    return left


def add(storage, cfg: Config, cat: str, name: str, data: bytes, source: dict) -> tuple[Item | None, str]:
    """Add a picture as an example of `cat`. Returns (new item or None, status: added | duplicate | moved)."""
    md5 = hashlib.md5(data).hexdigest()
    found = copies(storage, cfg, md5)
    if any(c == cat for c, _ in found):
        return None, "duplicate"
    if found:
        assign(storage, cfg, md5, cat)
        return None, "moved"
    item = storage.write_file(training_parts(cfg, cat), name, data)
    append_jsonl(storage, LOG, {"category": cat, "file": item.name, "md5": md5, **source})
    return item, "added"


def check(clf, storage, cfg: Config, cat: str, items: list[Item]) -> dict[str, tuple[str, float]]:
    """Re-judge photos of `cat` against everything else, each photo left out of its own comparison.
    Returns item.id -> (category it now looks like, confidence) for the ones that look like another category."""
    clf = getattr(clf, "primary", clf)                      # unwrap the optional LLM second opinion
    if not hasattr(clf, "classify_vector"):
        raise ValueError("checking needs an embedding model (SigLIP or Vertex)")
    clf.rebuild_refs(storage)
    vecs = clf.cache.embed_items(items, clf.embedder, storage)
    clf.cache.save()
    cats = cfg.categories_for(False)
    out = {}
    for it, v in zip(items, vecs):
        if v is None:
            continue
        vd = clf.classify_vector(v, cats, exclude_key=storage.md5_of(it))
        if vd.category != cat and vd.confidence >= cfg.threshold:
            out[it.id] = (vd.category, vd.confidence)
    return out
