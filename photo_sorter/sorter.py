"""The core pipeline shared by the CLI and the web app: scan -> predict -> file / undo."""
from __future__ import annotations

import functools
from dataclasses import dataclass
from typing import Callable

from .classifiers import ClassifyError, Verdict
from .config import Config, split_path
from .items import Item
from .manifest import Manifest
from .naming import Counters, build_name, slugify, unique_name
from .storage import NoPreviewError, Storage


@dataclass
class Prediction:
    item: Item
    verdict: Verdict
    threshold: float
    review_category: str

    @property
    def confident(self) -> bool:
        return self.verdict.confidence >= self.threshold and self.verdict.category != self.review_category

    @property
    def target(self) -> str:
        """Where this item goes if filed automatically."""
        return self.verdict.category if self.confident else self.review_category


class Sorter:
    def __init__(self, cfg: Config, storage: Storage, classifier, manifest: Manifest | None = None):
        self.cfg, self.storage, self.classifier = cfg, storage, classifier
        self.manifest = manifest or Manifest(storage)
        self.counters = Counters()
        self._names: dict[str, set[str]] = {}   # review folder id -> names in it, listed once
        self._in_bulk_dry_run = False

    # -- scan / predict ----------------------------------------------------
    def scan(self) -> list[Item]:
        return self.storage.list_inbox()

    def predict_one(self, item: Item) -> Prediction:
        cats = self.cfg.categories_for(item.is_video)
        if not cats:
            return Prediction(item, Verdict(self.cfg.review_category, 0.0, "no categories defined yet", method="none"),
                              self.cfg.threshold, self.cfg.review_category)
        try:
            read = functools.cache(lambda: self.storage.read_bytes(item, prefer="thumb"))
            kwargs = {}
            if getattr(self.classifier, "cache", None) is None:
                data = read()
            else:
                if item.is_video:
                    read()                    # no poster frame -> NoPreviewError before hashing a large video
                kwargs["key"] = self.storage.md5_of(item)
                data = read                   # read only on a cache miss: re-sorts and undos skip the download
            v = self.classifier.classify(data, "image/jpeg", cats, item.is_video, **kwargs)
        except NoPreviewError as e:
            v = Verdict(self.cfg.review_category, 0.0, f"no preview: {e}", method="none")
        except ClassifyError as e:
            v = Verdict(self.cfg.review_category, 0.0, str(e), method="error")
        except Exception as e:  # noqa: BLE001 - one bad file must not stop the batch
            describe = getattr(self.classifier, "describe_error", None)
            v = Verdict(self.cfg.review_category, 0.0, describe(e) if describe else f"{type(e).__name__}: {e}", method="error")
        return Prediction(item, v, self.cfg.threshold, self.cfg.review_category)

    def predict(self, items: list[Item], progress: Callable[[int, int, Prediction], None] | None = None) -> list[Prediction]:
        preds = []
        for i, item in enumerate(items, 1):
            p = self.predict_one(item)
            preds.append(p)
            if progress:
                progress(i, len(items), p)
        flush = getattr(self.classifier, "flush", None)
        if flush:
            flush()  # persist newly computed embeddings
        return preds

    # -- filing ------------------------------------------------------------
    def plan(self, item: Item, category: str, consume: bool = True) -> tuple[str, str] | None:
        """(folder_id, new_name) for filing item into category, or None when nothing should move.

        With consume=True the counter number is allocated; a dry run peeks instead.
        """
        if category == self.cfg.review_category:
            if self.cfg.review_action == "keep":
                return None
            folder = self.storage.ensure_folder([self.cfg.review_category])
            return folder, unique_name(self._review_names(folder), item.name)
        parts = split_path(category)
        folder = self.storage.ensure_folder(parts)
        slug = slugify(parts[-1])
        if not self.counters.is_seeded(folder, slug):
            self.counters.seed(folder, slug, [x.name for x in self.storage.list_folder(parts)])
        n = self.counters.take(folder, slug) if consume else self.counters.peek(folder, slug)
        return folder, build_name(slug, n, item.ext, self.cfg.naming_pattern)

    def _review_names(self, folder_id: str) -> set[str]:
        if folder_id not in self._names:
            self._names[folder_id] = {x.name for x in self.storage.list_folder([self.cfg.review_category])}
        return self._names[folder_id]

    def file(self, pred: Prediction, category: str | None = None, decided_by: str = "auto", dry_run: bool = False) -> dict | None:
        """Move one item. Returns the manifest record (not persisted on dry_run), or None if kept in place."""
        category = category or pred.target
        if category not in self.cfg.all_categories:
            raise ValueError(f"unknown category {category!r}")
        snap = self.counters.snapshot() if dry_run and not self._in_bulk_dry_run else None
        planned = self.plan(pred.item, category)   # a dry run consumes too, so previews number sequentially
        if snap is not None:
            self.counters.restore(snap)
        if planned is None:
            return None
        folder, new_name = planned
        item = pred.item
        record = {
            "op": "move", "item_id": item.id, "md5": item.md5 or None,
            "orig_name": item.name, "orig_parent": item.parent, "new_name": new_name, "new_parent": folder,
            "category": category, "confidence": round(pred.verdict.confidence, 4), "method": pred.verdict.method,
            "decided_by": decided_by, "reason": pred.verdict.reason[:200],
            "top": [[k, round(v, 4)] for k, v in list((pred.verdict.scores or {}).items())[:3]],
        }
        if dry_run:
            return record
        try:
            record["md5"] = self.storage.md5_of(item)
        except Exception:  # noqa: BLE001 - md5 is a cache key, not essential
            pass
        moved = self.storage.move_rename(item, folder, new_name)
        self._names.get(folder, set()).add(new_name)
        record["new_id"] = moved.id
        pred.item = moved
        return self.manifest.append(record)

    def file_all(self, preds: list[Prediction], only_confident: bool = True, dry_run: bool = False,
                 progress: Callable[[Prediction, dict | None], None] | None = None) -> list[dict]:
        out = []
        snap = self.counters.snapshot() if dry_run else None
        self._in_bulk_dry_run = dry_run
        try:
            with self.manifest.batch():
                for p in preds:
                    if only_confident and not p.confident:
                        continue
                    rec = self.file(p, dry_run=dry_run)
                    if progress:
                        progress(p, rec)
                    if rec:
                        out.append(rec)
        finally:
            self._in_bulk_dry_run = False
            if snap is not None:
                self.counters.restore(snap)
        return out

    def move(self, item: Item, category: str) -> dict | None:
        """Re-file an already filed photo into another category (a user decision, recorded and undoable)."""
        verdict = Verdict(category, 1.0, "moved by hand", method="user")
        return self.file(Prediction(item, verdict, self.cfg.threshold, self.cfg.review_category), category=category, decided_by="user")

    # -- undo --------------------------------------------------------------
    def undo(self, record: dict) -> dict:
        item = Item(id=record.get("new_id") or record["item_id"], name=record["new_name"], parent=record["new_parent"])
        self.storage.move_rename(item, record["orig_parent"] or "", record["orig_name"])
        self._names.get(record["new_parent"], set()).discard(record["new_name"])
        return self.manifest.append({"op": "undo", "ref": record.get("id", record["ts"]), "item_id": record["item_id"]})

    def undo_last(self, n: int = 1) -> list[dict]:
        with self.manifest.batch():
            return [self.undo(r) for r in self.manifest.last_moves(n)]
