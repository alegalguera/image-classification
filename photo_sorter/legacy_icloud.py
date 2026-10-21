"""The original iCloud shared-album flow: fetch -> classify (CSV) -> apply (copy / import).

Used when source.kind is "icloud". Kept for albums shared from Photos; the main flow is cli.py.
"""
from __future__ import annotations

import csv
import time
from pathlib import Path

from .icloud import SharedAlbum, download
from .outputs import FolderSink, PhotosSink, resolve_dest

FIELDS = ["asset_id", "filename", "is_video", "created", "category", "confidence", "reason"]


def run(a, cfg) -> int:
    url = getattr(a, "album", None) or cfg.album
    if not url:
        raise ValueError("no iCloud album URL: set source.root (or album) in the config or pass --source")
    if a.cmd == "fetch" or a.cmd == "scan":
        return fetch(url)
    if a.cmd == "classify":
        return classify(a, cfg, url)
    if a.cmd == "apply":
        return apply(a, cfg, url)
    raise ValueError(f"'{a.cmd}' is not available for iCloud sources; use fetch, classify or apply")


def _open(url: str) -> SharedAlbum:
    print("Opening album ...", end=" ", flush=True)
    album = SharedAlbum.open(url)
    print(f"'{album.title}' by {album.owner}: {len(album.assets)} items")
    return album


def fetch(url) -> int:
    album = _open(url)
    for x in sorted(album.assets, key=lambda x: (x.created.timestamp() if x.created else 0, x.filename)):
        when = x.created.strftime("%Y-%m-%d %H:%M") if x.created else "?"
        print(f"  {when}  {'video' if x.is_video else 'photo':5}  {x.width or '?'}x{x.height or '?'}  {x.filename}")
    return 0


def _read(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    with path.open(newline="") as f:
        return {r["asset_id"]: r for r in csv.DictReader(f)}


def _write(path: Path, rows: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows.values())
    tmp.replace(path)


def _classifier(cfg):
    if cfg.is_embedding_provider:
        from .embedding_classifier import build_embedding_classifier
        from .storage.local import LocalFolderStorage

        cache_dir = Path("results")
        cache_dir.mkdir(exist_ok=True)
        return build_embedding_classifier(cfg, LocalFolderStorage(cache_dir, inbox="."), log=print)
    from .classifiers import make_classifier

    return make_classifier(cfg.provider, cfg.model_name, cfg.effort, cfg.pricing)


def classify(a, cfg, url) -> int:
    album = _open(url)
    results = Path(getattr(a, "results", None) or "results/results.csv")
    rows = _read(results)
    todo = sorted((x for x in album.assets if x.asset_id not in rows),
                  key=lambda x: (x.created.timestamp() if x.created else 0, x.filename))
    if getattr(a, "limit", None):
        todo = todo[: a.limit]
    if not todo:
        print("Nothing to classify (everything is already in the CSV).")
        return 0
    print(f"Classifying {len(todo)} item(s) with {cfg.provider}/{cfg.model_name} ...")
    clf = _classifier(cfg)
    t0 = time.time()
    for i, x in enumerate(todo, 1):
        cats = cfg.categories_for(x.is_video)
        row = {"asset_id": x.asset_id, "filename": x.filename, "is_video": int(x.is_video),
               "created": x.created.isoformat() if x.created else "", "category": cfg.review_category,
               "confidence": "", "reason": ""}
        try:
            img_url = x.url_for("thumb")
            v = clf.classify(download(img_url), "image/jpeg", cats, x.is_video)
            ok = v.confidence >= cfg.threshold
            row["category"] = v.category if ok else cfg.review_category
            row["confidence"] = f"{v.confidence:.2f}"
            row["reason"] = v.reason if ok else f"low confidence for {v.category}: {v.reason}"
        except Exception as e:  # noqa: BLE001
            if getattr(clf, "is_fatal", lambda e: False)(e):
                print(f"\nAborting: {clf.describe_error(e)}")
                return 1
            row["reason"] = getattr(clf, "describe_error", lambda e: str(e))(e)
        rows[x.asset_id] = row
        _write(results, rows)
        print(f"  [{i}/{len(todo)}] {x.filename:24} -> {row['category']:40} {row['confidence']:>4}  {row['reason'][:60]}")
    if hasattr(clf, "flush"):
        clf.flush()
    print(f"\nDone in {time.time() - t0:.0f}s. Saved {results}. Review it, then: photo-sorter apply --dest ...")
    return 0


def apply(a, cfg, url) -> int:
    results = Path(a.results)
    rows = _read(results)
    if not rows:
        raise FileNotFoundError(f"no results at {results}; run classify first")
    bad = sorted({r["category"] for r in rows.values() if r["category"] not in cfg.all_categories})
    if bad:
        raise ValueError(f"unknown categories in {results}: {bad}. Add them to {cfg.path} or fix the CSV.")
    size = a.size or cfg.output_size
    album = _open(url)
    by_id = {x.asset_id: x for x in album.assets}
    dest = resolve_dest(a.dest, cfg.output_folder)
    sink = (PhotosSink(size, a.dry_run, results.parent / "photos-imported.txt") if dest == "photos"
            else FolderSink(dest, size, a.dry_run))
    print(f"Destination: {dest}  (size={size}{', DRY RUN' if a.dry_run else ''})")
    for r in rows.values():
        if not r["category"] or (a.skip_review and r["category"] == cfg.review_category):
            continue
        x = by_id.get(r["asset_id"])
        if x:
            print(f"  {x.filename:24} -> {sink.put(x, r['category'])}")
    sink.flush()
    print(sink.summary())
    return 0
