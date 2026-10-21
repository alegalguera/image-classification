"""photo-sorter: file research photos into category folders defined in CATEGORIES.md.

  photo-sorter auth                      sign in to Google (Drive source) once
  photo-sorter scan                      list what is waiting in the inbox
  photo-sorter classify [--limit N]      predict a category for each inbox item (no moves)
  photo-sorter file [--dry-run] [--all]  move+rename confident items (and the rest to Review)
  photo-sorter undo [--last N]           put items back where they came from
  photo-sorter refs                      show how many reference images each category has
  photo-sorter eval --labels labels.csv  measure accuracy and pick a threshold
  photo-sorter serve [--open]            local web UI (http://127.0.0.1:8080); pick the photo folder there

No config file is needed: the photo folder holds its own CATEGORIES.md and .sorter/settings.json, and
the folder picked in the web UI is remembered (or pass --source). A categories.json in the current
folder, or -c, still works and takes precedence.
Legacy iCloud shared-album flow (source.kind = "icloud"): fetch, classify, apply.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from .config import Config, apply_folder, base_config, load_config, source_from
from .items import Item

CONFIG_DEFAULT = "categories.json"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="photo-sorter", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", "-c", help=f"settings + categories (.json or .md); default: {CONFIG_DEFAULT} if present")
    ap.add_argument("--source", help="override source root: a Drive folder link, a local folder, or an iCloud album URL")
    ap.add_argument("--provider", choices=["siglip", "vertex", "claude", "gemini"], help="override the classifier provider")
    ap.add_argument("--model", help="override the model name")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("auth", help="sign in to Google Drive (opens a browser)").add_argument("--logout", action="store_true")
    p = sub.add_parser("setup", help="get ready: download the image model, check the folder and Google sign-in")
    p.add_argument("--remove-model", action="store_true", help="delete the downloaded image model (the uninstaller uses this)")
    sub.add_parser("scan", help="list the inbox")
    p = sub.add_parser("classify", help="predict categories, move nothing")
    p.add_argument("--limit", type=int)
    p = sub.add_parser("file", help="classify and move items")
    p.add_argument("--limit", type=int)
    p.add_argument("--dry-run", action="store_true", help="show the moves without doing them")
    p.add_argument("--all", action="store_true", help="also move below-threshold items to the review folder")
    p.add_argument("--threshold", type=float, help="override min_confidence for this run")
    p = sub.add_parser("undo", help="undo recent moves")
    p.add_argument("--last", type=int, default=1)
    sub.add_parser("refs", help="reference images per category (_training_data)")
    p = sub.add_parser("eval", help="accuracy against labelled images")
    p.add_argument("--labels", required=True, help="CSV with columns path,category")
    p.add_argument("--loo", action="store_true", help="leave-one-out: the labelled images are also the references")
    p = sub.add_parser("serve", help="run the web UI")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--open", action="store_true", help="open the browser once the server is up")
    # legacy iCloud commands
    p = sub.add_parser("fetch", help="[icloud] list the shared album")
    p.add_argument("album", nargs="?")
    p = sub.add_parser("apply", help="[icloud] copy album items per results CSV")
    p.add_argument("album", nargs="?")
    p.add_argument("--dest", required=True)
    p.add_argument("--results", "-r", default="results/results.csv")
    p.add_argument("--size", choices=["thumb", "medium", "original"])
    p.add_argument("--skip-review", action="store_true")
    p.add_argument("--dry-run", action="store_true")

    a = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):   # e.g. "≈" or "→" printed to a Windows log file: show "?" instead of crashing
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    try:
        if not a.config and Path(CONFIG_DEFAULT).exists():
            a.config = CONFIG_DEFAULT
        cfg = load_config(a.config) if a.config else base_config()
        _apply_overrides(cfg, a)
        if a.cmd == "auth":
            return cmd_auth(a)
        if a.cmd == "setup":
            return cmd_setup(a, cfg)
        if cfg.source_kind == "icloud" or a.cmd in ("fetch", "apply"):
            from . import legacy_icloud

            return legacy_icloud.run(a, cfg)
        return {
            "scan": cmd_scan, "classify": cmd_classify, "file": cmd_file, "undo": cmd_undo,
            "refs": cmd_refs, "eval": cmd_eval, "serve": cmd_serve,
        }[a.cmd](a, cfg)
    except (FileNotFoundError, ValueError, RuntimeError, ImportError, PermissionError) as e:
        sys.stdout.flush()
        print(f"error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


def _apply_overrides(cfg: Config, a) -> None:
    if a.source:
        cfg.source_kind, cfg.source_root = source_from(a.source)
    if a.provider:
        if a.provider != cfg.provider:
            cfg.model = None
        cfg.provider = a.provider
    if a.model:
        cfg.model = a.model
    if getattr(a, "threshold", None) is not None:
        cfg.min_confidence = a.threshold
    cfg.validate()


# -- building blocks ---------------------------------------------------------
def open_storage_for(cfg: Config):
    """Open the photo folder and take on its own settings and categories (see config.apply_folder)."""
    from .storage import open_storage

    creds = None
    if cfg.source_kind == "drive" and cfg.source_root:
        from .storage.drive_auth import load_token

        creds = load_token()
        if creds is None:
            raise PermissionError("not signed in to Google. Run: photo-sorter auth")
    storage = open_storage(cfg, credentials=creds)
    apply_folder(cfg, storage)
    return storage


def build_classifier(cfg: Config, storage, manifest=None, log=print):
    """The classifier for cfg.provider: embedding-based (SigLIP / Vertex) or an LLM, plus the optional second opinion."""
    if cfg.is_embedding_provider:
        from .embedding_classifier import build_embedding_classifier

        log(f"Loading {cfg.provider} ({cfg.model_name}) ...")
        clf = build_embedding_classifier(cfg, storage, log=log)
        n = clf.refs.total
        log(f"References: {n} image(s) across {len(clf.refs.refs)} categor{'y' if len(clf.refs.refs) == 1 else 'ies'}"
            + ("" if n else f"  (add examples to <category>/{cfg.training_subfolder}/ to teach it)"))
        so = cfg.second_opinion
        if so and so.get("provider"):
            from .classifiers import make_classifier
            from .config import DEFAULT_MODELS
            from .second_opinion import SecondOpinion

            llm = make_classifier(so["provider"], so.get("model") or DEFAULT_MODELS[so["provider"]], so.get("effort", "low"), cfg.pricing)
            clf = SecondOpinion(clf, llm, float(so.get("when_below", cfg.threshold)))
            log(f"Second opinion: {so['provider']} when confidence < {so.get('when_below', cfg.threshold)}")
    else:
        from .classifiers import make_classifier

        clf = make_classifier(cfg.provider, cfg.model_name, cfg.effort, cfg.pricing)
    return clf


def build_sorter(cfg: Config, storage=None, log=print, lazy: bool = False):
    """Storage + manifest + classifier. With lazy=True the classifier is left out (built later by the caller)."""
    from .manifest import Manifest
    from .sorter import Sorter

    storage = storage or open_storage_for(cfg)
    manifest = Manifest(storage)
    clf = None if lazy else build_classifier(cfg, storage, manifest, log)
    return Sorter(cfg, storage, clf, manifest)


def _fmt_item(item: Item) -> str:
    when = item.created.strftime("%Y-%m-%d") if item.created else "          "
    kind = "video" if item.is_video else "photo"
    return f"{when}  {kind}  {item.name}"


# -- commands ------------------------------------------------------------------
def cmd_auth(a) -> int:
    from .storage import drive_auth

    if a.logout:
        print("signed out" if drive_auth.logout() else "no saved sign-in")
        return 0
    creds = drive_auth.load_token()
    if creds is not None:
        print(f"already signed in (token at {drive_auth.TOKEN_PATH}); use --logout to switch accounts")
        return 0
    drive_auth.login_installed()
    print(f"signed in; token saved to {drive_auth.TOKEN_PATH}")
    return 0


def _remove_model(cfg) -> int:
    from huggingface_hub import scan_cache_dir

    try:
        cache = scan_cache_dir()
    except Exception:  # noqa: BLE001 - no cache folder at all
        cache = None
    revs = [r.commit_hash for repo in (cache.repos if cache else []) if repo.repo_id == cfg.model_name for r in repo.revisions]
    if not revs:
        print(f"{cfg.model_name} is not downloaded")
        return 0
    plan = cache.delete_revisions(*revs)
    plan.execute()
    print(f"removed {cfg.model_name} ({plan.expected_freed_size_str})")
    return 0


def cmd_setup(a, cfg) -> int:
    """Get ready: download the image model once and check the photo folder (the installers run this)."""
    if getattr(a, "remove_model", False):
        return _remove_model(cfg)
    if cfg.path:
        print(f"Config: {cfg.path}")
    if cfg.provider == "siglip":
        from .embedders import make_embedder
        from .embedders.siglip import _hf_snapshot

        print(f"Image model: {cfg.model_name} (SigLIP 2 by Google, from huggingface.co)")
        if _hf_snapshot(cfg.model_name)[0] is None:
            print("  downloading it, about 1.4 GB, only this once ...", flush=True)
        info = make_embedder(cfg).meters()[0].info
        folder = re.sub(r"/snapshots/[^/]+$", "", info.get("weights") or "")
        if folder:
            print(f"  saved in {folder} ({info.get('disk', 0) / 2**30:.1f} GB)")
        print(f"  runs on {info['where'].split(' · ')[1]}")
    else:
        print(f"Image model: {cfg.model_name} ({cfg.provider}, runs in the cloud)")
    if not cfg.source_root:
        print("Photo folder: not chosen yet; you pick it in the app")
        return 0
    print(f"Photo folder: {cfg.source_root}")
    if cfg.source_kind == "drive":
        from .storage import drive_auth

        try:
            drive_auth.client_config()
        except FileNotFoundError as e:
            print(f"  {e}")
            return 1
        if drive_auth.load_token() is None:
            print("Signing in to Google (a browser window opens) ...", flush=True)
            drive_auth.login_installed()
        st = open_storage_for(cfg)
        print(f"  Drive ok: {st.root_label}, inbox has {len(st.list_inbox())} item(s)")
    elif cfg.source_kind == "local":
        st = open_storage_for(cfg)
        print(f"  Folder ok: {st.root_label}, inbox has {len(st.list_inbox())} item(s)")
    print("Ready. Try: photo-sorter classify --limit 5   or   photo-sorter serve")
    return 0


def cmd_scan(a, cfg) -> int:
    st = open_storage_for(cfg)
    items = st.list_inbox()
    print(f"{st.root_label} / {cfg.inbox}: {len(items)} item(s)")
    for it in items:
        print("  " + _fmt_item(it))
    skipped = getattr(st, "skipped", [])
    if skipped:
        print(f"  ({len(skipped)} file(s) skipped as not photos/videos, e.g. {', '.join(skipped[:3])})")
    return 0


def _predict(a, cfg, sorter):
    items = sorter.scan()
    if getattr(a, "limit", None):
        items = items[: a.limit]
    print(f"Classifying {len(items)} item(s) from {sorter.storage.root_label} / {cfg.inbox} (threshold {cfg.threshold:.2f})")

    def progress(i, n, p):
        flag = " " if p.confident else "?"
        print(f"  [{i}/{n}] {flag} {p.item.name:28} -> {p.target:45} {p.verdict.confidence:.2f}  {p.verdict.reason[:60]}")

    return sorter.predict(items, progress)


def _metered_predict(a, cfg):
    """Build the sorter and predict, printing a receipt of what the run used (model, tokens, compute, $)."""
    import time

    from . import metering

    t0, cpu0 = time.perf_counter(), time.process_time()
    sorter = build_sorter(cfg)
    load = time.perf_counter() - t0
    meter = metering.RunMeter(sorter.classifier, fresh_load=True)
    meter.t0, meter.cpu0 = t0, cpu0               # include model loading in the compute figures
    preds = _predict(a, cfg, sorter)
    meter.stop()
    rep = meter.report()
    print("\nWhat this run used:")
    for line in metering.summary_lines(rep):
        print("  " + line)
    n_conf = sum(p.confident for p in preds)
    if preds:
        metering.append_run(sorter.storage, metering.run_record(rep, len(preds), n_conf, len(preds) - n_conf, load))
    return sorter, preds


def cmd_classify(a, cfg) -> int:
    sorter, preds = _metered_predict(a, cfg)
    n_conf = sum(p.confident for p in preds)
    print(f"\n{n_conf} confident, {len(preds) - n_conf} would go to {cfg.review_category}. Nothing moved. "
          f"Run `photo-sorter file` to move them.")
    return 0


def cmd_file(a, cfg) -> int:
    sorter, preds = _metered_predict(a, cfg)
    print("\nMoves" + (" (dry run)" if a.dry_run else "") + ":")

    def show(p, rec):
        if rec:
            print(f"  {rec['orig_name']:28} -> {rec['category']} / {rec['new_name']}")

    recs = sorter.file_all(preds, only_confident=not a.all, dry_run=a.dry_run, progress=show)
    moved = len(recs)
    skipped = len(preds) - moved
    print(f"{moved} item(s) {'would be ' if a.dry_run else ''}moved, {skipped} left in the inbox"
          + ("" if a.all or not skipped else " (use --all to send low-confidence items to the review folder)"))
    if not a.dry_run and moved:
        print("Undo with: photo-sorter undo --last", moved)
    return 0


def cmd_undo(a, cfg) -> int:
    from .manifest import Manifest
    from .sorter import Sorter

    st = open_storage_for(cfg)
    sorter = Sorter(cfg, st, classifier=None, manifest=Manifest(st))
    recs = sorter.manifest.last_moves(a.last)
    if not recs:
        print("nothing to undo")
        return 0
    for r in recs:
        sorter.undo(r)
        print(f"  {r['new_name']} -> back to {r['orig_name']}")
    print(f"{len(recs)} move(s) undone")
    return 0


def cmd_refs(a, cfg) -> int:
    from .config import split_path

    st = open_storage_for(cfg)
    total = 0
    for cat in list(cfg.categories) + list(cfg.video_categories or {}):
        n = len(st.list_examples(split_path(cat)))
        total += n
        if n:
            print(f"  {n:4d}  {cat}")
    print(f"{total} reference image(s). Add more under <category>/{cfg.training_subfolder}/")
    return 0


def cmd_eval(a, cfg) -> int:
    import hashlib

    from .evaluate import evaluate, load_local_labelled, read_labels
    from .references import ReferenceIndex

    labels = read_labels(a.labels)
    if cfg.source_kind != "local":
        raise ValueError("eval needs a local source (download the labelled images to a folder first)")
    sorter = build_sorter(cfg)
    data = load_local_labelled(Path(cfg.source_root).expanduser(), labels)
    keys = [hashlib.md5(d[2]).hexdigest() for d in data]
    clf = sorter.classifier
    if a.loo:
        if not hasattr(clf, "classify_vector"):
            raise ValueError("--loo needs an embedding provider")
        evaluate(cfg, clf, data, keys=keys)  # fills the cache
        idx = ReferenceIndex()
        for (name, truth, _), k in zip(data, keys):
            idx.add(truth, clf.cache.get(k), k)
        clf.refs = idx
    rep = evaluate(cfg, clf, data, leave_one_out=a.loo, keys=keys)
    if hasattr(clf, "flush"):
        clf.flush()
    print(rep.text())
    return 0


def cmd_serve(a, cfg) -> int:
    try:
        import uvicorn

        from .web.app import create_app
    except ImportError as e:
        raise ImportError("the web UI needs the web extra: run `uv sync --extra web`") from e
    import socket
    import threading
    import urllib.request
    import webbrowser

    url = f"http://{'127.0.0.1' if a.host in ('0.0.0.0', '::') else a.host}:{a.port}"
    with socket.socket() as sock:
        port_busy = sock.connect_ex(("127.0.0.1", a.port)) == 0
    if port_busy:
        try:          # already running (e.g. the Desktop icon clicked twice): just show it
            urllib.request.urlopen(url + "/healthz", timeout=2).read()
        except OSError:
            raise RuntimeError(f"port {a.port} is used by another program; start with --port 8090 (or another number)") from None
        print(f"photo-sorter is already running: {url}")
        if a.open:
            webbrowser.open(url)
        return 0
    app = create_app(cfg)
    print(f"Open {url}   (keep this window open; closing it stops photo-sorter)", flush=True)
    if a.open:
        threading.Timer(1.5, webbrowser.open, args=(url,)).start()
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
