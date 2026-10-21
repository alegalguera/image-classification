"""Measure the classifier against labelled images and sweep the confidence threshold.

Labels file: CSV with columns `path,category` (path absolute or relative to the storage root for
local sources; for Drive sources `path` is the file name inside the inbox). Category must be a key
from CATEGORIES.md (full " › " path) or the review category.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config, split_path
from .items import Item


@dataclass
class EvalRow:
    name: str
    truth: str
    predicted: str
    confidence: float
    reason: str


@dataclass
class EvalReport:
    rows: list[EvalRow] = field(default_factory=list)

    def accuracy(self, level: str = "exact") -> float:
        if not self.rows:
            return 0.0
        return sum(_match(r.truth, r.predicted, level) for r in self.rows) / len(self.rows)

    def sweep(self, thresholds=(0.0, 0.05, 0.1, 0.15, 0.2, 0.3, 0.4, 0.5)) -> list[tuple[float, float, float]]:
        """(threshold, accuracy among filed, share sent to review)."""
        out = []
        for t in thresholds:
            filed = [r for r in self.rows if r.confidence >= t]
            acc = sum(_match(r.truth, r.predicted, "exact") for r in filed) / len(filed) if filed else 0.0
            out.append((t, acc, 1 - len(filed) / len(self.rows) if self.rows else 0.0))
        return out

    def confusions(self, limit: int = 10) -> list[tuple[str, str, int]]:
        pairs: dict[tuple[str, str], int] = {}
        for r in self.rows:
            if r.truth != r.predicted:
                pairs[(r.truth, r.predicted)] = pairs.get((r.truth, r.predicted), 0) + 1
        return sorted(((a, b, n) for (a, b), n in pairs.items()), key=lambda x: -x[2])[:limit]

    def text(self) -> str:
        lines = [f"{len(self.rows)} labelled items",
                 f"accuracy exact: {self.accuracy('exact'):.1%}   top-level: {self.accuracy('top'):.1%}   parent: {self.accuracy('parent'):.1%}",
                 "threshold  acc(filed)  review%"]
        for t, acc, rev in self.sweep():
            lines.append(f"   {t:4.2f}     {acc:6.1%}    {rev:6.1%}")
        conf = self.confusions()
        if conf:
            lines.append("top confusions (truth -> predicted: n)")
            lines += [f"   {a} -> {b}: {n}" for a, b, n in conf]
        return "\n".join(lines)


def _match(truth: str, pred: str, level: str) -> bool:
    if level == "exact":
        return truth == pred
    tp, pp = split_path(truth), split_path(pred)
    if level == "top":
        return tp[:1] == pp[:1]
    return tp[:-1] == pp[:-1] and (len(tp) == 1 or True)  # same parent folder


def read_labels(path: str | Path) -> list[tuple[str, str]]:
    with Path(path).open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows or "path" not in rows[0] or "category" not in rows[0]:
        raise ValueError(f"{path}: need columns path,category")
    return [(r["path"], r["category"]) for r in rows if r["path"] and r["category"]]


def evaluate(cfg: Config, classifier, labelled: list[tuple[str, str, bytes]], leave_one_out: bool = False,
             keys: list[str] | None = None) -> EvalReport:
    """labelled: (name, truth, image bytes). With leave_one_out the item's own reference (by key) is excluded."""
    rep = EvalReport()
    for i, (name, truth, data) in enumerate(labelled):
        cats = cfg.categories
        key = keys[i] if keys else None
        if leave_one_out and hasattr(classifier, "classify_vector"):
            vec = classifier.cache.get(key) if key else None
            if vec is None:
                vec = classifier.embedder.embed_images([data])[0]
            v = classifier.classify_vector(vec, cats, exclude_key=key)
        else:
            v = classifier.classify(data, "image/jpeg", cats, False, **({"key": key} if key and hasattr(classifier, "cache") else {}))
        rep.rows.append(EvalRow(name, truth, v.category, v.confidence, v.reason))
    return rep


def load_local_labelled(root: Path, labels: list[tuple[str, str]]) -> list[tuple[str, str, bytes]]:
    out = []
    for rel, truth in labels:
        p = Path(rel) if Path(rel).is_absolute() else root / rel
        out.append((p.name, truth, p.read_bytes()))
    return out
