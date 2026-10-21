"""Embedding cache (per embedding space) and the few-shot reference index built from folders."""
from __future__ import annotations

import hashlib
import io

import numpy as np

from .config import Config, split_path
from .items import Item


def text_key(text: str) -> str:
    return "text:" + hashlib.sha1(text.encode("utf-8")).hexdigest()


class EmbeddingCache:
    """md5 (or text key) -> vector, persisted as .sorter/embeddings-<slug>.npz (float16)."""

    def __init__(self, storage, slug: str):
        self.storage, self.name = storage, f"embeddings-{slug}.npz"
        self.vectors: dict[str, np.ndarray] = {}
        self.dirty = False
        raw = storage.read_meta(self.name) if storage else None
        if raw:
            with np.load(io.BytesIO(raw), allow_pickle=False) as z:
                for k, v in zip(z["keys"].tolist(), z["vectors"]):
                    self.vectors[k] = v.astype(np.float32)

    def get(self, key: str) -> np.ndarray | None:
        return self.vectors.get(key)

    def put(self, key: str, vec: np.ndarray) -> None:
        self.vectors[key] = np.asarray(vec, dtype=np.float32)
        self.dirty = True

    def save(self) -> None:
        if not self.dirty or not self.storage:
            return
        keys = np.array(list(self.vectors), dtype=str)
        vecs = np.stack([self.vectors[k] for k in keys]).astype(np.float16) if len(keys) else np.zeros((0, 0), np.float16)
        buf = io.BytesIO()
        np.savez_compressed(buf, keys=keys, vectors=vecs)
        self.storage.write_meta(self.name, buf.getvalue())
        self.dirty = False

    def embed_items(self, items: list[Item], embedder, storage) -> list[np.ndarray | None]:
        """Vectors for items, embedding only the ones not cached. None where the file could not be read."""
        out: list[np.ndarray | None] = [None] * len(items)
        todo, todo_bytes = [], []
        for i, item in enumerate(items):
            key = storage.md5_of(item)
            vec = self.get(key)
            if vec is not None:
                out[i] = vec
                for m in (getattr(embedder, "meters", lambda: [])() or [])[:1]:
                    m.add(cache_hits=1)
                continue
            try:
                todo_bytes.append(storage.read_bytes(item, prefer="thumb"))
                todo.append((i, key))
            except Exception:  # noqa: BLE001 - unreadable example: skip it
                continue
        if todo:
            vecs = embedder.embed_images(todo_bytes)
            for (i, key), vec in zip(todo, vecs):
                self.put(key, vec)
                out[i] = vec
        return out    # the caller saves once (the cache file is rewritten whole)


class ReferenceIndex:
    """Per-category reference vectors from _training_data folders (+ optional filed images, + corrections)."""

    def __init__(self):
        self.refs: dict[str, np.ndarray] = {}      # category -> [n, D]
        self.keys: dict[str, list[str]] = {}       # category -> cache keys (for leave-one-out)
        self.mid: dict[str, float] = {}            # category -> similarity at which an image "looks like" its examples

    def add(self, category: str, vec: np.ndarray, key: str = "") -> None:
        self.add_many(category, [vec], [key])

    def add_many(self, category: str, vecs: list[np.ndarray], keys: list[str]) -> None:
        mat = np.stack([np.asarray(v, dtype=np.float32) for v in vecs])
        self.refs[category] = np.concatenate([self.refs[category], mat]) if category in self.refs else mat
        self.keys.setdefault(category, []).extend(keys)

    @property
    def total(self) -> int:
        return sum(len(v) for v in self.refs.values())

    def counts(self) -> dict[str, int]:
        return {c: len(v) for c, v in self.refs.items()}

    def calibrate(self, default_mid: float, k: int = 3, margin: float = 0.04) -> None:
        """Per category: how similar its examples are to each other (median of each example's top-k neighbours).
        An image as close to the examples as they are to one another counts as a match. Needs 3+ examples."""
        self.mid = {}
        for cat, mat in self.refs.items():
            n = len(mat)
            if n < 3:
                self.mid[cat] = default_mid
                continue
            sims = mat @ mat.T
            np.fill_diagonal(sims, -np.inf)
            kk = min(k, n - 1)
            within = np.sort(sims, axis=1)[:, -kk:].mean(axis=1)
            self.mid[cat] = float(np.clip(np.median(within) - margin, 0.6, 0.97))

    def knn_scores(self, x: np.ndarray, k: int = 3, exclude_key: str | None = None) -> dict[str, float]:
        """category -> mean cosine of its top-k references to x (only categories with references)."""
        out = {}
        for cat, mat in self.refs.items():
            sims = mat @ x
            if exclude_key is not None:
                mask = np.array([kk != exclude_key for kk in self.keys[cat]])
                sims = sims[mask]
                if sims.size == 0:
                    continue
            top = np.sort(sims)[-k:]
            out[cat] = float(top.mean())
        return out

    @classmethod
    def build(cls, cfg: Config, storage, embedder, cache: EmbeddingCache, log=None) -> "ReferenceIndex":
        """Examples per category: everything in <category>/_training_data/ plus (by default) the newest photos
        already filed in the category folder. Filing is always a user action, so filed photos are confirmed."""
        idx = cls()
        all_cats = list(cfg.categories) + list(cfg.video_categories or {})
        use_filed = bool(cfg.embeddings.get("use_filed_as_examples", True))
        cap = int(cfg.embeddings.get("max_filed_examples_per_category", 50))
        for cat in all_cats:
            parts = split_path(cat)
            items = [i for i in storage.list_examples(parts) if not i.is_video]
            if use_filed and cap > 0:
                items += [i for i in storage.list_folder(parts) if not i.is_video][-cap:]
            if not items:
                continue
            got = [(item, vec) for item, vec in zip(items, cache.embed_items(items, embedder, storage)) if vec is not None]
            if got:
                idx.add_many(cat, [v for _, v in got], [i.md5 or "" for i, _ in got])
            if log:
                log(f"  examples: {cat}: {len(got)}")
        cache.save()
        idx.calibrate(float(cfg.embeddings.get("knn_midpoint", 0.85)), k=int(cfg.embeddings.get("knn_k", 3)))
        return idx
