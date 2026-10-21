"""Web UI: an inbox pile you sort on demand, piles per category you file in one click, undo.

FastAPI + Jinja2 + HTMX. Runs on a laptop (`photo-sorter serve`) or hosted (Cloud Run, a VPS).

Each browser session opens one photo folder, chosen on the setup page (/setup):
  * laptop: any local folder (Google Drive for Desktop folders included) or a Google Drive link; Google
    sign-in uses the token saved on this computer (`photo-sorter auth` or the setup page's button)
  * hosted: only Google Drive links; the user signs in with Google and the token lives only in the
    encrypted session cookie, so the server keeps no photos and no accounts
The folder brings its own CATEGORIES.md and .sorter/settings.json (see config.apply_folder).

Page states (index.html):
  * running          -> the status panel only (phase steps, current image, progress, results stream)
  * unsorted images  -> the inbox pile (click to list them) and the Sort button
  * sorted images    -> one pile per predicted category + a "needs review" pile; click to list, File to move
  * nothing at all   -> a short guide: where to put photos, categories, rescan
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import secrets
import threading
import time
from collections import OrderedDict, deque
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlencode, urlparse

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, URLSafeSerializer

from .. import examples as ex
from .. import metering, websearch
from ..config import (SEP, Config, apply_folder, base_config, folder_settings, load_config, parse_categories,
                      remember_folder, save_folder_settings, source_from, split_path)
from ..storage import META_DIR, append_jsonl
from ..items import IMAGE_EXTS, Item
from ..classifiers import Verdict
from ..naming import safe_dirname
from ..sorter import Prediction, Sorter
from ..taxonomy import Taxonomy, dump, from_paths, presets, tree_rows

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
SESSION_COOKIE = "ps_session"
ROWS_PER_LOAD = 100
THUMB_CACHE = 400


def _fmt_secs(x: float | None) -> str:
    if x is None:
        return "–"
    x = max(0, int(round(x)))
    return f"{x // 60}:{x % 60:02d}" if x >= 60 else f"{x}s"


def _fsize(n: int | None) -> str:
    if not n:
        return ""
    return f"{n / 1048576:.1f} MB" if n >= 1048576 else f"{max(1, round(n / 1024))} KB"


def _short(label: str) -> str:
    """'/Users/me/sorter-test' -> 'sorter-test'; 'Drive: Lab Photos' stays."""
    return label if label.startswith("Drive:") else (Path(label).name or label)


def _explain(p: Prediction) -> str:
    """A plain-language reason for the review list (the technical one stays in the tooltip)."""
    r = p.verdict.reason or ""
    if r.startswith("no category fits"):
        return "Nothing in your list matches this image well."
    if p.verdict.method in ("error", "none"):
        return r
    top = [k for k in (p.verdict.scores or {})][:2]
    if len(top) == 2:
        return f"Close call between {top[0].split(SEP)[-1]} and {top[1].split(SEP)[-1]}."
    return "Not sure enough to file it automatically."


TEMPLATES.env.filters["secs"] = _fmt_secs
TEMPLATES.env.filters["fsize"] = _fsize
TEMPLATES.env.filters["short"] = _short
TEMPLATES.env.filters["explain"] = _explain
TEMPLATES.env.filters["num"] = metering.fmt_num
TEMPLATES.env.filters["usd"] = metering.fmt_usd
TEMPLATES.env.filters["gb"] = lambda b: f"{(b or 0) / 2**30:.1f} GB"
TEMPLATES.env.filters["leaf"] = lambda c: (c or "").split(SEP)[-1]
TEMPLATES.env.filters["parent"] = lambda c: SEP.join((c or "").split(SEP)[:-1])


def is_hosted() -> bool:
    """Cloud Run (K_SERVICE is set there) or any server started with PHOTO_SORTER_HOSTED=1."""
    return os.environ.get("PHOTO_SORTER_HOSTED") == "1" or bool(os.environ.get("K_SERVICE"))


class NeedsGoogle(Exception):
    """The folder is on Google Drive and this browser (hosted) or computer (laptop) is not signed in."""


class CookieJar:
    """Session cookies. They can carry a Google token (hosted), so they are encrypted when the cryptography package
    is installed (it comes with Google sign-in), and only signed otherwise (laptop without Drive support)."""

    def __init__(self, secret: str):
        try:
            from cryptography.fernet import Fernet

            self._fernet = Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest()))
        except ImportError:
            self._fernet = None
        self._signer = URLSafeSerializer(secret, salt="session")

    @property
    def encrypted(self) -> bool:
        return self._fernet is not None

    def dumps(self, data: dict) -> str:
        if self._fernet is None:
            return self._signer.dumps(data)
        return self._fernet.encrypt(json.dumps(data, separators=(",", ":")).encode()).decode()

    def loads(self, raw: str | None) -> dict | None:
        if not raw:
            return None
        try:
            data = json.loads(self._fernet.decrypt(raw.encode())) if self._fernet else self._signer.loads(raw)
        except Exception:  # noqa: BLE001 - tampered, or made with another key (server restart): start over
            return None
        return data if isinstance(data, dict) else None


def local_shortcuts() -> list[tuple[str, str]]:
    """Places worth one click on the setup page: Google Drive for Desktop folders first, then the usual ones."""
    home, out = Path.home(), []
    accounts = sorted((home / "Library" / "CloudStorage").glob("GoogleDrive-*"))     # macOS
    for acct in accounts:
        for sub in ("My Drive", "Shared drives"):
            if (acct / sub).is_dir():
                who = f" ({acct.name.split('-', 1)[1]})" if len(accounts) > 1 else ""
                out.append((f"Google Drive · {sub}{who}", str(acct / sub)))
    if os.name == "nt":                                                                 # Windows: a drive letter
        for letter in "GHIJKLMNOPQRSTUVWXYZDEF":
            for sub in ("My Drive", "Shared drives"):
                try:
                    if Path(f"{letter}:/{sub}").is_dir():
                        out.append((f"Google Drive · {sub}", f"{letter}:\\{sub}"))
                except OSError:
                    pass
    for label, path in (("Google Drive", home / "Google Drive"), ("Desktop", home / "Desktop"),
                        ("Pictures", home / "Pictures"), ("Documents", home / "Documents"), ("Home", home)):
        if path.is_dir():
            out.append((label, str(path)))
    return out


def _friendly(e: Exception) -> str:
    """Google API errors are long; say what they mean."""
    text = str(e)
    if "404" in text or "notFound" in text:
        return "Google Drive says this folder does not exist or is not shared with your account"
    if "403" in text or "insufficient" in text.lower():
        return "your Google account has no permission to change this folder (it needs Editor access)"
    return f"{type(e).__name__}: {text}" if text else type(e).__name__


class Session:
    """One signed-in user: storage, sorter, the inbox listing and predictions (filled in the background)."""

    def __init__(self, cfg: Config, credentials=None, log=print, sync: bool = False):
        from ..cli import build_sorter
        from ..storage import open_storage

        storage = open_storage(cfg, credentials=credentials)
        self._cat_text = apply_folder(cfg, storage)   # the folder's own settings and CATEGORIES.md (None: no file yet)
        self.cfg = cfg
        self.log = log
        self.undo_text: str | None = None   # the category list before the last undoable edit
        self.sorter: Sorter = build_sorter(cfg, storage=storage, log=log, lazy=True)   # model loads on first Sort
        self.phase = "idle"                 # idle | loading | sorting
        self.items: dict[str, Item] = {}
        self.preds: dict[str, Prediction] = {}
        self.lock = threading.Lock()        # filing and undo
        self.sync = sync
        self._thread: threading.Thread | None = None
        self.done = self.total = 0
        self.error: str | None = None
        self.scanned = False
        self.events: deque[tuple[str, str]] = deque(maxlen=80)   # (HH:MM:SS, text), newest first
        self.recent: deque[Prediction] = deque(maxlen=8)
        self.current: Item | None = None
        self.t_start = self.t_end = 0.0
        self.t_load = 0.0                   # seconds the model took to load (0 = not loaded in this run)
        self.rate = 0.4                     # seconds per photo, refined after each run (for the estimate)
        self._stop = False
        self.last_run: dict | None = None   # summary of the last finished run
        self.stale = ""                     # "categories" | "examples": suggestions predate a change; offer Re-sort
        self.meter: metering.RunMeter | None = None   # usage of the current / last run
        self.filed: deque[tuple[Prediction, dict]] = deque(maxlen=2000)   # this session, newest first
        self.known: dict[str, Item] = {}    # items outside the inbox we may show thumbnails for (filed, examples)
        self._thumbs: OrderedDict[str, bytes] = OrderedDict()
        self._views: OrderedDict[str, bytes] = OrderedDict()
        self.event("opened " + _short(self.sorter.storage.root_label))

    # -- bookkeeping ------------------------------------------------------------------
    def event(self, text: str) -> None:
        self.events.appendleft((datetime.now().strftime("%H:%M:%S"), text))

    @property
    def model_loaded(self) -> bool:
        return self.sorter.classifier is not None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def elapsed(self) -> float:
        if not self.t_start:
            return 0.0
        return (self.t_end or time.time()) - self.t_start

    @property
    def speed(self) -> float:
        """Images per second in the current (or last) run."""
        return self.done / self.elapsed if self.elapsed > 0 and self.done else 0.0

    @property
    def eta(self) -> float | None:
        if not self.running or self.phase != "sorting" or not self.done:
            return None
        return self.elapsed / self.done * (self.total - self.done)

    @property
    def pct(self) -> int:
        return int(100 * self.done / self.total) if self.total else 0

    def pending(self) -> list[Item]:
        """Inbox items without a prediction yet (unsorted), by name."""
        return sorted((i for i in self.items.values() if i.id not in self.preds), key=lambda i: i.name)

    @property
    def estimate(self) -> float:
        return self.rate * len(self.pending())

    def thumb(self, item: Item) -> bytes:
        data = self._thumbs.get(item.id)
        if data is None:
            data = self.sorter.storage.read_bytes(item, prefer="thumb")
            self._thumbs[item.id] = data
            if len(self._thumbs) > THUMB_CACHE:
                self._thumbs.popitem(last=False)
        else:
            self._thumbs.move_to_end(item.id)
        return data

    def view(self, item: Item, side: int = 1400) -> bytes:
        """A large JPEG for the one-by-one viewer (originals may be HEIC/AVIF or huge)."""
        data = self._views.get(item.id)
        if data is None:
            import io

            from ..embedders import decode_image

            img = decode_image(self.sorter.storage.read_bytes(item, prefer="original"), max_side=side)
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=86)
            data = self._views[item.id] = buf.getvalue()
            if len(self._views) > 60:
                self._views.popitem(last=False)
        return data

    def restore(self, item_ids: list[str]) -> None:
        """After an undo: images are back in the inbox; give them their suggestion again (cached, fast)."""
        if self.sorter.classifier is None:
            return
        for iid in item_ids:
            item = self.items.get(iid)
            if item is not None and iid not in self.preds:
                self.preds[iid] = self.sorter.predict_one(item)

    # -- listing / sorting ------------------------------------------------------------
    def scan(self, force: bool = False) -> None:
        """List the inbox. Does not classify anything: that waits for start()."""
        if self.running:
            return
        items = self.sorter.scan()
        self.items = {i.id: i for i in items}
        if force:
            self.preds = {}
            self.recent.clear()
        else:
            self.preds = {k: v for k, v in self.preds.items() if k in self.items}
        self.scanned = True
        self.error = None
        self.event(f"found {len(items)} photo(s), {len(self.pending())} not sorted yet")

    def start(self) -> None:
        """Classify everything unsorted, in the background (or inline in sync mode)."""
        if self.running:
            return
        if not self.scanned:
            self.scan()
        todo = self.pending()
        self.done, self.total, self.error, self._stop = 0, len(todo), None, False
        self.stale = ""
        self.recent.clear()
        if not todo:
            return
        self.event(f"sorting {len(todo)} image(s)")
        self.t_start, self.t_end, self.t_load = time.time(), 0.0, 0.0
        self.phase = "sorting" if self.model_loaded else "loading"
        if self.sync:
            self._run(todo)
        else:
            self._thread = threading.Thread(target=self._run, args=(todo,), daemon=True)
            self._thread.start()

    def pause(self) -> None:
        self._stop = True

    def ensure_classifier(self) -> None:
        if self.model_loaded:
            return
        from ..cli import build_classifier

        before, self.phase = self.phase, "loading"
        self.event(f"loading the {self.cfg.provider} model and reference images")
        t = time.time()
        self.sorter.classifier = build_classifier(self.cfg, self.sorter.storage, self.sorter.manifest, log=self.log)
        self.t_load = time.time() - t
        refs = getattr(self.sorter.classifier, "refs", None)
        self.event(f"model ready in {self.t_load:.1f}s" + (f", {refs.total} reference image(s)" if refs is not None else ""))
        self.phase = before

    def usage(self) -> dict | None:
        """Live (or final) report of what the current / last run used."""
        return self.meter.report() if self.meter else None

    def _run(self, todo: list[Item]) -> None:
        paused = False
        cpu0, wall0 = time.process_time(), time.perf_counter()
        try:
            fresh = not self.model_loaded
            self.ensure_classifier()
            rebuild = getattr(self.sorter.classifier, "rebuild_refs", None)
            if rebuild and not fresh:            # pick up new examples / filed photos without a restart
                n = rebuild(self.sorter.storage)
                self.event(f"using {n} example image(s)")
            self.meter = metering.RunMeter(self.sorter.classifier, fresh_load=fresh)
            self.meter.cpu0, self.meter.t0 = cpu0, wall0       # model loading counts as compute too
            self.phase = "sorting"
            self.t_start = time.time()      # sorting time excludes model loading
            for item in todo:
                if self._stop:
                    paused = True
                    self.event(f"paused at {self.done}/{self.total}")
                    break
                if item.id not in self.items:
                    continue
                self.current = item
                p = self.sorter.predict_one(item)
                self.preds[item.id] = p
                self.recent.appendleft(p)
                self.done += 1
        except Exception as e:  # noqa: BLE001
            self.error = f"{type(e).__name__}: {e}"
            self.event(f"stopped: {self.error}")
        finally:
            self.current = None
            self.phase = "idle"
            self.t_end = time.time()
            flush = getattr(self.sorter.classifier, "flush", None)
            if flush:
                flush()
            if self.done:
                self.rate = self.elapsed / self.done
            todo_ids = {i.id for i in todo}
            batch = [p for p in self.preds.values() if p.item.id in todo_ids]
            n_ok = sum(1 for p in batch if p.confident)
            rep = None
            if self.meter:
                self.meter.stop()
                rep = self.meter.report()
                if self.done:
                    metering.append_run(self.sorter.storage, metering.run_record(rep, self.done, n_ok, len(batch) - n_ok, self.t_load))
            self.last_run = {"done": self.done, "total": self.total, "secs": self.elapsed, "load": self.t_load,
                             "labeled": n_ok, "review": len(batch) - n_ok, "paused": paused, "error": self.error, "usage": rep}
            if not paused and not self.error:
                self.event(f"sorted {self.done} in {self.elapsed:.1f}s: {n_ok} labeled, {len(batch) - n_ok} need review")

    def reload_categories(self, cfg: Config) -> None:
        """Categories changed: use the new list. Suggestions for categories that no longer exist go back to the
        inbox; the rest stay, flagged as stale so the page can offer Re-sort."""
        self.event(f"categories changed ({len(cfg.categories)})")
        self.cfg = cfg
        self.sorter.cfg = cfg
        if self.sorter.classifier is not None and hasattr(self.sorter.classifier, "cfg"):
            self.sorter.classifier.cfg = cfg
        self.preds = {k: p for k, p in self.preds.items() if p.verdict.category in cfg.all_categories}
        if self.preds:
            self.mark_stale("categories")
        self.last_run = None
        self.scan()

    def refresh_categories(self) -> None:
        """CATEGORIES.md may have been edited elsewhere (a colleague, Drive, a text editor): use what is there now."""
        store = self.cfg.categories_store
        if store is None:
            return
        text = store.read()
        if text == self._cat_text:
            return
        self._cat_text = text
        c = copy.copy(self.cfg)
        c.categories, c.video_categories = parse_categories(text or "")
        if (c.categories, c.video_categories) != (self.cfg.categories, self.cfg.video_categories):
            self.reload_categories(c)

    def mark_stale(self, why: str) -> None:
        if self.preds:
            self.stale = why

    def resort(self) -> None:
        """Forget all suggestions and sort the whole inbox again (fast: image analysis is cached)."""
        if self.running:
            return
        self.scan(force=True)
        self.start()

    def examples(self, cat: str) -> list[Item]:
        items = [i for i in self.sorter.storage.list_examples(split_path(cat)) if not i.is_video]
        self.known.update({i.id: i for i in items})
        return items

    # -- views ------------------------------------------------------------------------
    def filtered(self, review: bool, cat: str = "", q: str = "") -> list[Prediction]:
        q = q.lower().strip()
        out = [p for p in self.preds.values() if p.confident != review
               and (not cat or p.target == cat) and (not q or q in p.item.name.lower())]
        return sorted(out, key=lambda p: (-p.verdict.confidence, p.item.name))

    def piles(self) -> list[dict]:
        """Needs-review pile first, then one pile per predicted category (largest first), then 'filed'."""
        out = []
        review = self.filtered(True)
        if review:
            out.append({"key": "review", "kind": "review", "label": "Needs review", "path": "",
                        "count": len(review), "ids": [p.item.id for p in review[:4]]})
        groups: dict[str, list[Prediction]] = {}
        for p in self.filtered(False):
            groups.setdefault(p.verdict.category, []).append(p)
        for cat, ps in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
            out.append({"key": cat, "kind": "cat", "label": cat.split(SEP)[-1], "path": SEP.join(cat.split(SEP)[:-1]),
                        "count": len(ps), "ids": [p.item.id for p in ps[:4]],
                        "mean": sum(p.verdict.confidence for p in ps) / len(ps)})
        if self.filed:
            out.append({"key": "filed", "kind": "filed", "label": "Filed", "path": "this session",
                        "count": len(self.filed), "ids": [p.item.id for p, _ in list(self.filed)[:4]]})
        return out

    def pile_rows(self, key: str) -> list[dict]:
        if key == "unsorted":
            return [{"kind": "unsorted", "item": i} for i in self.pending()]
        if key == "review":
            return [{"kind": "review", "item": p.item, "pred": p} for p in self.filtered(True)]
        if key == "filed":
            return [{"kind": "filed", "item": p.item, "pred": p, "rec": rec} for p, rec in self.filed]
        return [{"kind": "cat", "item": p.item, "pred": p} for p in self.filtered(False, cat=key)]

    def forget(self, item_id: str) -> None:
        self.preds.pop(item_id, None)
        self.items.pop(item_id, None)

    def record_filed(self, p: Prediction, rec: dict | None) -> None:
        if rec:
            self.filed.appendleft((p, rec))
            self.known[p.item.id] = p.item           # p.item is the moved item now

    def counts(self) -> dict:
        return {"unsorted": len(self.pending()), "labeled": len(self.filtered(False)),
                "review": len(self.filtered(True)), "filed": len(self.filed), "all": len(self.items)}

    def inbox_info(self) -> dict:
        """Where new photos go, for the empty-state guide."""
        st, inbox = self.sorter.storage, self.cfg.inbox
        info = {"name": inbox or _short(st.root_label).removeprefix("Drive: "), "root": inbox in ("", "."),
                "path": None, "url": None, "exists": True, "skipped": list(getattr(st, "skipped", []))}
        if hasattr(st, "root"):          # local folder
            p = st.root if inbox in ("", ".") else st.root / inbox
            info.update(path=str(p), exists=p.is_dir())
        elif hasattr(st, "root_id"):     # Google Drive
            fid = st.folder_id([inbox]) if inbox not in ("", ".") else st.root_id
            info.update(exists=fid is not None, url=f"https://drive.google.com/drive/folders/{fid or st.root_id}")
        return info


def create_app(cfg: Config) -> FastAPI:
    """cfg: the app-level settings (a config file, or config.base_config()). Every browser session opens one photo
    folder: on a laptop a local folder or a Drive link, when hosted only Drive links."""
    app = FastAPI(title="photo-sorter")
    hosted = is_hosted()
    cookies = CookieJar(os.environ.get("PHOTO_SORTER_SECRET") or secrets.token_hex(32))
    if hosted and not cookies.encrypted:
        raise RuntimeError("the hosted app keeps Google tokens in cookies and needs the cryptography package to encrypt them")
    sessions: dict[str, Session] = {}
    create_lock = threading.Lock()
    base_url = os.environ.get("PHOTO_SORTER_BASE_URL")  # e.g. https://photo-sorter-xyz.a.run.app (Cloud Run)
    sync = os.environ.get("PHOTO_SORTER_SYNC") == "1"
    default = cfg.source_root if cfg.source_kind in ("local", "drive") and cfg.source_root and not hosted else None
    state = {"folder": default, "google": None, "google_error": ""}   # laptop: the folder new sessions open

    # -- sessions ---------------------------------------------------------------------
    def cookie_data(request: Request) -> dict | None:
        data = cookies.loads(request.cookies.get(SESSION_COOKIE))
        return data if data and data.get("sid") else None

    def save_cookie(resp: Response, data: dict) -> None:
        resp.set_cookie(SESSION_COOKIE, cookies.dumps({k: v for k, v in data.items() if v}), httponly=True, samesite="lax",
                        max_age=30 * 24 * 3600, secure=bool(base_url and base_url.startswith("https")))

    def google_credentials(token: str | None):
        """Hosted: the token in this browser's cookie. Laptop: the sign-in saved on this computer."""
        try:
            from ..storage import drive_auth

            if hosted:
                return drive_auth.credentials_from_json(token) if token else None
            return drive_auth.load_token()
        except Exception:  # noqa: BLE001 - not installed, expired or revoked: sign in again
            return None

    def open_session(sid: str, folder: str, token: str | None) -> Session:
        kind, root = source_from(folder)
        if kind == "icloud":
            raise ValueError("iCloud albums are read with the photo-sorter fetch / classify / apply commands")
        if hosted and kind != "drive":
            raise ValueError("on the server only Google Drive folders can be used")
        c = copy.deepcopy(cfg)
        c.source_kind, c.source_root = kind, root
        creds = None
        if kind == "drive":
            creds = google_credentials(token)
            if creds is None:
                raise NeedsGoogle()
        sessions[sid] = Session(c, credentials=creds, sync=sync)   # opens the folder and reads its settings
        return sessions[sid]

    def session_of(request: Request) -> "Session | str":
        """This browser's session, or where to send it first: "/login" or "/setup"."""
        data = cookie_data(request)
        if data is None:
            return "/login"
        sid = data["sid"]
        if sid in sessions:
            return sessions[sid]
        folder = data.get("folder") or state["folder"]
        if not folder:
            return "/setup"
        with create_lock:                       # parallel first requests must not build two sessions
            if sid in sessions:
                return sessions[sid]
            try:
                return open_session(sid, folder, data.get("token"))
            except NeedsGoogle:
                return "/login" if hosted else "/setup"
            except Exception as e:  # noqa: BLE001 - moved, deleted, not shared: let the user pick again
                return "/setup?" + urlencode({"err": f"Could not open {folder}: {_friendly(e)}"})

    def safe_next(url: str) -> str:
        """Only same-site paths, never //host or absolute URLs."""
        return url if url.startswith("/") and not url.startswith("//") else "/"

    def go(request: Request, target: str):
        """Send the browser to target. Fragment requests (htmx) reload the whole page instead of getting it pasted in."""
        hx = bool(request.headers.get("hx-request"))
        if target == "/login":
            if hx:
                page = urlparse(request.headers.get("hx-current-url", "/"))
                nxt = page.path + (f"?{page.query}" if page.query else "")
            else:
                nxt = request.url.path + (f"?{request.url.query}" if request.url.query else "")
            target = "/login?next=" + quote(safe_next(nxt))
        if hx:
            raise HTTPException(status_code=401, headers={"HX-Redirect": target})
        raise HTTPException(status_code=307, headers={"Location": target})

    def require(request: Request, fresh: bool = False) -> Session:
        """The session for a page or action. fresh=True (full pages) re-reads CATEGORIES.md in case it was edited."""
        s = session_of(request)
        if isinstance(s, str):
            go(request, s)
        request.state.session = s
        if fresh and not s.running:
            s.refresh_categories()
        if not s.scanned:
            s.scan()
        return s

    def render(request: Request, name: str, headers: dict | None = None, **ctx) -> HTMLResponse:
        s = ctx.get("s") or getattr(request.state, "session", None)
        ctx["s"] = s
        resp = TEMPLATES.TemplateResponse(request, name, {"request": request, "cfg": s.cfg if s else cfg, "hosted": hosted, **ctx})
        for k, v in (headers or {}).items():
            resp.headers[k] = v
        return resp

    def back(request: Request, default: str = "/") -> Response:
        if request.headers.get("hx-request"):
            return Response(status_code=204, headers={"HX-Refresh": "true"})
        return RedirectResponse(request.headers.get("referer") or default, status_code=303)

    def board_ctx(s: Session) -> dict:
        n = len(s.pending())
        return {"s": s, "c": s.counts(), "piles": s.piles(), "inbox": s.inbox_info(), "unsorted_ids": [i.id for i in s.pending()[:4]],
                "plan": metering.planned(s.cfg), "est_cost": metering.estimate_cost(s.cfg, n),
                "loaded_info": (metering.meters_of(s.sorter.classifier)[0].info if s.model_loaded and metering.meters_of(s.sorter.classifier) else None)}

    # -- auth ---------------------------------------------------------------------------
    @app.get("/login", response_class=HTMLResponse)
    def login(request: Request, next: str = "/"):
        nxt = safe_next(next)
        if not hosted:      # on a laptop there is no account: the browser just gets a session
            resp = RedirectResponse(nxt, status_code=303)
            save_cookie(resp, cookie_data(request) or {"sid": secrets.token_hex(8)})
            return resp
        resp = render(request, "login.html")
        resp.set_cookie("ps_next", nxt, httponly=True, max_age=900, samesite="lax")
        return resp

    @app.get("/auth/start")
    def auth_start(request: Request):
        from ..storage.drive_auth import web_flow

        redirect = (base_url or str(request.base_url).rstrip("/")) + "/auth/callback"
        st = secrets.token_urlsafe(16)
        flow = web_flow(redirect, state=st)
        url, _ = flow.authorization_url(access_type="offline", prompt="consent", include_granted_scopes="true")
        resp = RedirectResponse(url, status_code=303)
        # the PKCE verifier must come back with the callback, which builds a new flow
        resp.set_cookie("ps_oauth", cookies.dumps({"state": st, "redirect": redirect, "verifier": flow.code_verifier}),
                        httponly=True, max_age=600, samesite="lax", secure=bool(base_url and base_url.startswith("https")))
        return resp

    @app.get("/auth/callback")
    def auth_callback(request: Request, code: str = "", state: str = "", error: str = ""):
        from ..storage.drive_auth import web_flow

        if error:
            raise HTTPException(400, f"Google sign-in failed: {error}")
        saved = cookies.loads(request.cookies.get("ps_oauth"))
        if not saved:
            raise HTTPException(400, "sign-in session expired, try again")
        if saved.get("state") != state:
            raise HTTPException(400, "state mismatch")
        flow = web_flow(saved["redirect"], state=state, code_verifier=saved.get("verifier"))
        flow.fetch_token(code=code)
        old = cookie_data(request) or {}
        sessions.pop(old.get("sid"), None)
        resp = RedirectResponse(safe_next(request.cookies.get("ps_next", "/")), status_code=303)
        save_cookie(resp, {"sid": secrets.token_hex(8), "token": flow.credentials.to_json(), "folder": old.get("folder")})
        resp.delete_cookie("ps_oauth")
        return resp

    @app.get("/logout")
    def logout(request: Request):
        sessions.pop((cookie_data(request) or {}).get("sid"), None)
        resp = RedirectResponse("/login", status_code=303)
        resp.delete_cookie(SESSION_COOKIE)
        return resp

    # -- setup: which folder, where new photos arrive, which categories ------------------------
    def setup_cookie(request: Request) -> dict:
        data = cookie_data(request)
        if data is None:
            go(request, "/login")
        return data

    def setup_error(msg: str) -> Response:
        return RedirectResponse("/setup?" + urlencode({"err": msg}), status_code=303)

    def google_state(data: dict) -> dict:
        from ..storage import drive_auth

        t = state["google"]
        return {"client": drive_auth.has_client(), "signed_in": google_credentials(data.get("token")) is not None,
                "pending": bool(t and t.is_alive()), "error": state["google_error"],
                "client_path": str(drive_auth.CONFIG_DIR / "client_secret.json")}

    def browse(path: str) -> dict:
        """A local folder for the picker: its subfolders and how many photos sit directly in it."""
        start = state["folder"] if state["folder"] and source_from(state["folder"])[0] == "local" else str(Path.home())
        p = Path(path or start).expanduser()
        try:
            p = p.resolve()
            kids = sorted((c for c in p.iterdir() if not c.name.startswith(".")), key=lambda c: c.name.lower())
            dirs = [c.name for c in kids if c.is_dir()]
            photos, err = sum(1 for c in kids if c.suffix.lower() in IMAGE_EXTS), ""
        except OSError as e:
            dirs, photos, err = [], 0, f"can't open this folder ({e.strerror or e})"
        return {"path": str(p), "parent": str(p.parent) if p.parent != p else "", "dirs": dirs[:400],
                "more": max(0, len(dirs) - 400), "photos": photos, "err": err, "shortcuts": local_shortcuts()}

    def next_step(s: Session) -> str:
        """After a folder is chosen. Photos are sorted right where they are (no separate inbox), unless the folder
        already has an Inbox subfolder; /setup/inbox changes that later."""
        st = s.sorter.storage
        if folder_settings(st) is None:
            inbox = "Inbox" if st.folder_id(["Inbox"]) is not None else ""
            save_folder_settings(st, inbox=inbox)
            s.cfg.inbox = st.inbox = inbox
            s.scan(force=True)
        if s.cfg.categories_in_folder and s._cat_text is None:
            return "/setup/categories"
        return "/"

    @app.get("/setup", response_class=HTMLResponse)
    def setup(request: Request, err: str = ""):
        data = setup_cookie(request)
        return render(request, "setup.html", step="folder", s=sessions.get(data["sid"]), err=err, google=google_state(data),
                      b=None if hosted else browse(""), current=data.get("folder") or state["folder"])

    @app.get("/setup/browse", response_class=HTMLResponse)
    def setup_browse(request: Request, path: str = ""):
        setup_cookie(request)
        if hosted:
            raise HTTPException(404)
        return render(request, "_browse.html", b=browse(path))

    @app.post("/setup/folder")
    def setup_folder(request: Request, kind: str = Form(...), path: str = Form(""), link: str = Form("")):
        data = setup_cookie(request)
        try:
            if kind == "local":
                if hosted:
                    raise ValueError("on the server only Google Drive folders can be used")
                p = Path(path).expanduser()
                if not path or not p.is_dir():
                    raise ValueError(f"{path or 'that'} is not a folder")
                folder = str(p.resolve())
            else:
                from ..storage.drive import folder_id_from

                folder = f"https://drive.google.com/drive/folders/{folder_id_from(link)}"
        except ValueError as e:
            return setup_error(str(e))
        sid = data["sid"]
        if sid in sessions and sessions[sid].running:
            return setup_error("wait for the sort to finish (or pause it) before switching folders")
        with create_lock:
            sessions.pop(sid, None)
            try:
                s = open_session(sid, folder, data.get("token"))
            except NeedsGoogle:
                return setup_error("sign in with Google first")
            except Exception as e:  # noqa: BLE001
                return setup_error(f"could not open that folder: {_friendly(e)}")
        if not hosted:
            state["folder"] = folder
            remember_folder(folder)
        resp = RedirectResponse(next_step(s), status_code=303)
        save_cookie(resp, {**data, "folder": folder})
        return resp

    @app.get("/setup/inbox", response_class=HTMLResponse)
    def setup_inbox(request: Request):
        s = require(request)
        st = s.sorter.storage
        has_sub = st.folder_id(["Inbox"]) is not None
        at_top = len(st.list_folder([]))
        return render(request, "setup.html", step="inbox", s=s, has_sub=has_sub, at_top=at_top,
                      mode="sub" if s.cfg.inbox else "self")

    @app.post("/setup/inbox")
    def setup_inbox_save(request: Request, mode: str = Form("sub")):
        s = require(request)
        inbox = "" if mode == "self" else "Inbox"
        st = s.sorter.storage
        if inbox:
            st.ensure_folder([inbox])
        save_folder_settings(st, inbox=inbox)
        s.cfg.inbox = st.inbox = inbox
        s.scan(force=True)
        return RedirectResponse(next_step(s), status_code=303)

    def folder_categories(s: Session) -> list[list[str]]:
        """Subfolders that could be categories (not the inbox, Review, .sorter or example folders)."""
        skip = {safe_dirname(s.cfg.review_category), s.cfg.inbox, META_DIR}
        return [p for p in s.sorter.storage.folder_tree(3)
                if p[0] not in skip and not any(":" in x or "›" in x or x.startswith(("-", "*", "#")) for x in p)]

    def has_categories(s: Session) -> bool:
        return bool(s.cfg.categories or s.cfg.video_categories) or s.cfg.categories_store is None

    @app.get("/setup/categories", response_class=HTMLResponse)
    def setup_categories(request: Request):
        """Start a category list: from the folders already there, from a ready-made list, or empty."""
        s = require(request, fresh=True)
        if has_categories(s):
            return RedirectResponse("/categories", status_code=303)
        rows = tree_rows(from_paths(folder_categories(s)))
        return render(request, "setup.html", step="categories", s=s, rows=rows, presets=presets(),
                      leaves=sum(1 for _, _, n in rows if n.is_leaf))

    @app.post("/setup/categories")
    def setup_categories_save(request: Request, mode: str = Form("empty")):
        s = require(request, fresh=True)
        if has_categories(s):        # never replace a list someone already made
            return RedirectResponse("/categories?" + urlencode({"err": "this folder already has categories; edit them here"}), status_code=303)
        if mode.startswith("preset:"):
            text = next((p["text"] for p in presets() if p["key"] == mode[len("preset:"):]), None)
            if text is None:
                raise HTTPException(404, "no such list")
        else:
            text = dump(from_paths(folder_categories(s)) if mode == "folders" else [])
        s.cfg.categories_store.write(text)
        s.refresh_categories()
        return RedirectResponse("/categories" if mode == "empty" else "/", status_code=303)

    @app.post("/setup/google", response_class=HTMLResponse)
    def setup_google(request: Request):
        """Laptop: sign in to Google in a new browser tab (the same sign-in as `photo-sorter auth`)."""
        data = setup_cookie(request)
        if hosted:
            return RedirectResponse("/auth/start", status_code=303)
        from ..storage import drive_auth

        t = state["google"]
        if not (t and t.is_alive()):
            state["google_error"] = ""

            def run():
                try:
                    drive_auth.login_installed()
                except Exception as e:  # noqa: BLE001
                    state["google_error"] = _friendly(e)

            state["google"] = threading.Thread(target=run, daemon=True)
            state["google"].start()
        return render(request, "_google.html", google=google_state(data))

    @app.get("/setup/google", response_class=HTMLResponse)
    def setup_google_status(request: Request):
        data = setup_cookie(request)
        g = google_state(data)
        if g["pending"]:
            return render(request, "_google.html", google=g)
        return Response(status_code=204, headers={"HX-Refresh": "true"})

    # -- main page ------------------------------------------------------------------------
    @app.get("/", response_class=HTMLResponse)
    def index(request: Request, open: str = ""):
        s = require(request, fresh=True)
        detail = None
        if open and not s.running:
            detail = {"key": open, "rows": s.pile_rows(open)[:ROWS_PER_LOAD], "total": len(s.pile_rows(open)), "offset": 0}
        return render(request, "index.html", detail=detail, rep=s.usage() if s.running else None, **board_ctx(s))

    @app.get("/review")
    def review_redirect():
        return RedirectResponse("/?open=review", status_code=303)

    @app.get("/summary")
    def summary_redirect():
        return RedirectResponse("/", status_code=303)

    @app.get("/board", response_class=HTMLResponse)
    def board(request: Request):
        s = require(request)
        if s.running:
            return Response(status_code=204, headers={"HX-Refresh": "true"})
        return render(request, "_board.html", **board_ctx(s))

    @app.get("/pile", response_class=HTMLResponse)
    def pile(request: Request, key: str = "", offset: int = 0):
        """The expanded list for one pile. offset>0 returns only the next rows (for 'show more')."""
        s = require(request)
        rows = s.pile_rows(key) if key else []
        chunk = rows[offset: offset + ROWS_PER_LOAD]
        tpl = "_rows.html" if offset else "_detail.html"
        return render(request, tpl, s=s, detail={"key": key, "rows": chunk, "total": len(rows), "offset": offset})

    @app.get("/status", response_class=HTMLResponse)
    def status(request: Request, was: int = 0):
        """Polled every second while a run is active. When the run ends the page reloads itself."""
        s = require(request)
        if was and not s.running:
            return Response(status_code=204, headers={"HX-Refresh": "true"})
        prev = getattr(s, "_shown_pct", 0)     # the bar animates from the last value shown to the current one
        s._shown_pct = s.pct
        # animate the scanner image and the newest result only when they actually changed since the last poll
        cur = s.current.id if s.current else None
        top = s.recent[0].item.id if s.recent else None
        fresh_cur, fresh_top = cur != getattr(s, "_shown_cur", None), top != getattr(s, "_shown_top", None)
        s._shown_cur, s._shown_top = cur, top
        return render(request, "_status.html", s=s, prev=min(prev, s.pct), fresh_cur=fresh_cur, fresh_top=fresh_top, rep=s.usage())

    @app.post("/scan")
    def rescan(request: Request):
        require(request).scan(force=False)
        return back(request)

    @app.post("/create-inbox")
    def create_inbox(request: Request):
        s = require(request)
        s.sorter.storage.ensure_folder([s.cfg.inbox])
        s.event(f"created the {s.cfg.inbox} folder")
        s.scan()
        return back(request)

    @app.post("/classify")
    def classify(request: Request, redo: str = Form("")):
        s = require(request)
        s.resort() if redo else s.start()
        return back(request)

    @app.post("/pause")
    def pause(request: Request):
        require(request).pause()
        return back(request)

    @app.get("/triage")
    def triage(request: Request, key: str = "review"):
        """Everything the one-by-one viewer needs for a pile: images in order, suggestions, the category list."""
        s = require(request)
        rows = s.pile_rows(key) if key != "filed" else []
        items = []
        for r in rows:
            it, p = r["item"], r.get("pred")
            v = p.verdict if p else None
            nofit = bool(v and v.reason.startswith("no category fits"))
            items.append({
                "id": it.id, "name": it.name, "video": it.is_video,
                "thumb": f"/thumb/{quote(it.id)}", "view": f"/view/{quote(it.id)}",
                "guess": v.category if (v and v.category in s.cfg.all_categories and v.category != s.cfg.review_category) else None,
                "sure": round(v.confidence, 3) if v else None, "confident": bool(p and p.confident),
                "why": _explain(p) if (p and not p.confident) else (f"looks like {v.category.split(SEP)[-1]}" if v else "not sorted yet"),
                "scores": {} if (not v or nofit) else {k: round(x, 3) for k, x in (v.scores or {}).items()},
            })
        title = {"unsorted": "Unsorted", "review": "Needs review"}.get(key, key.split(SEP)[-1])
        cats = [{"key": k, "leaf": k.split(SEP)[-1], "path": SEP.join(k.split(SEP)[:-1])} for k in s.cfg.categories]
        vcats = [{"key": k, "leaf": k.split(SEP)[-1], "path": SEP.join(k.split(SEP)[:-1])} for k in (s.cfg.video_categories or {})]
        return {"key": key, "title": title, "items": items, "categories": cats, "video_categories": vcats,
                "examples": bool(s.cfg.is_embedding_provider)}

    @app.get("/view/{item_id:path}")
    def view(request: Request, item_id: str):
        s = require(request)
        item = s.items.get(item_id) or s.known.get(item_id)
        if not item:
            raise HTTPException(404)
        try:
            data = s.view(item)
        except Exception:  # noqa: BLE001 - fall back to the thumbnail (videos, unreadable files)
            try:
                data = s.thumb(item)
            except Exception:  # noqa: BLE001
                raise HTTPException(404)
        return Response(data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})

    @app.get("/history", response_class=HTMLResponse)
    def history(request: Request):
        s = require(request, fresh=True)
        runs = metering.load_runs(s.sorter.storage)
        return render(request, "history.html", s=s, moves=list(reversed(s.sorter.manifest.active_moves()))[:300],
                      runs=list(reversed(runs))[:100], tot=metering.totals(runs))

    @app.get("/thumb/{item_id:path}")
    def thumb(request: Request, item_id: str):
        s = require(request)
        item = s.items.get(item_id) or s.known.get(item_id)
        if not item:
            raise HTTPException(404)
        try:
            data = s.thumb(item)
        except Exception:  # noqa: BLE001
            raise HTTPException(404)
        return Response(data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})

    # -- filing ------------------------------------------------------------------------------
    NEW = "__new__"

    def taxonomy(s: Session) -> Taxonomy | None:
        """The session's category list as an editable tree (None when the categories are inline in a JSON config)."""
        return Taxonomy(s.cfg.categories_store) if s.cfg.categories_store is not None else None

    def categories_changed() -> None:
        """After an edit: every session re-reads its list (cheap when unchanged), so all of them see the new one."""
        for x in list(sessions.values()):
            if not x.running:
                x.refresh_categories()

    def resolve_category(s: Session, category: str, new_category: str) -> tuple[str, bool]:
        """(category key, created?). Creates the category (top level) when the user picked "New category…"."""
        if category != NEW:
            if not category:
                raise HTTPException(400, "choose a category")
            return category, False
        name = (new_category or "").strip()
        if not name:
            raise HTTPException(400, "type a name for the new category")
        t = taxonomy(s)
        if t is None:
            raise HTTPException(400, "categories are inline in the JSON config; set categories_file to add them here")
        existing = next((k for k in t.keys() if k.lower() == name.lower() or k.split(SEP)[-1].lower() == name.lower()), None)
        if existing:
            return existing, False
        try:
            key = t.add(None, name)
        except ValueError as e:
            raise HTTPException(400, str(e))
        t.save()
        categories_changed()
        return key, True

    def _file(s: Session, p: Prediction, category: str) -> dict | None:
        item_id = p.item.id                      # Sorter.file replaces p.item with the moved item
        decided = "user" if category != p.verdict.category else "auto"
        rec = s.sorter.file(p, category=category, decided_by=decided)
        if rec is not None or category == s.cfg.review_category:
            s.forget(item_id)
        s.record_filed(p, rec)
        return rec

    def prediction_for(s: Session, item_id: str) -> Prediction:
        """The suggestion for an inbox image, or a blank one for images not sorted yet (filed by hand)."""
        p = s.preds.get(item_id)
        if p is None:
            item = s.items.get(item_id)
            if item is None:
                raise HTTPException(404, "this photo is no longer waiting to be sorted; look for new photos")
            p = Prediction(item, Verdict(s.cfg.review_category, 0.0, "filed by hand", method="user"), s.cfg.threshold, s.cfg.review_category)
        return p

    def rec_json(rec: dict | None) -> dict | None:
        return None if rec is None else {k: rec.get(k) for k in ("id", "orig_name", "new_name", "category")}

    @app.post("/file", response_class=HTMLResponse)
    def file_one(request: Request, item_id: str = Form(...), category: str = Form(""), new_category: str = Form(""), fmt: str = Form("")):
        s = require(request)
        prediction_for(s, item_id)                       # 404 early, before creating anything
        category, created = resolve_category(s, category, new_category)
        p = prediction_for(s, item_id)
        with s.lock:
            rec = _file(s, p, category)
        if rec:
            ex.assign(s.sorter.storage, s.cfg, rec.get("md5"), category)
            s.event(f"filed {rec['orig_name']} → {category} as {rec['new_name']}" + (" (your choice)" if rec["decided_by"] == "user" else ""))
        triggers = {"board-changed": True}
        if created:
            # the list refreshes so every dropdown has the new category; say what happened to this image
            triggers["categories-changed"] = True
            if rec:
                triggers["toast"] = f"✓ {rec['orig_name']} filed as {rec['new_name']} in the new category {category.split(SEP)[-1]}"
        if fmt == "json":
            return JSONResponse({"ok": True, "rec": rec_json(rec), "category": category, "created": created}, headers={"HX-Trigger": json.dumps(triggers)})
        return render(request, "_row_filed.html", headers={"HX-Trigger": json.dumps(triggers)}, rec=rec, p=p, category=category)

    @app.post("/file-all", response_class=HTMLResponse)
    def file_all(request: Request, cat: str = Form(""), dry: str = Form("0")):
        """File every labeled (confident) image, or only those predicted as `cat`."""
        s = require(request)
        with s.lock:
            preds = s.filtered(False, cat)
            ids = [p.item.id for p in preds]
            recs = s.sorter.file_all(preds, only_confident=True, dry_run=dry == "1")
            if dry != "1":
                by_orig = {r["item_id"]: r for r in recs}
                for p, item_id in zip(preds, ids):
                    s.forget(item_id)
                    s.record_filed(p, by_orig.get(item_id))
        if dry == "1":
            return render(request, "_toast.html", recs=recs, dry=True, cat=cat)
        where = f" into {cat.split(SEP)[-1]}" if cat else ""
        s.event(f"filed {len(recs)} image(s){where}")
        return render(request, "_toast.html", headers={"HX-Trigger": json.dumps({"board-changed": True, "detail-changed": True})},
                      recs=recs, dry=False, cat=cat)

    @app.post("/file-selected")
    def file_selected(request: Request, sel: list[str] = Form([]), bulk_category: str = Form("")):
        s = require(request)
        category = bulk_category.strip()
        n = 0
        with s.lock, s.sorter.manifest.batch():
            for item_id in sel:
                p = s.preds.get(item_id)
                if p and _file(s, p, category or p.target):
                    n += 1
        s.event(f"filed {n} selected image(s)" + (f" → {category}" if category else ""))
        return back(request)

    @app.post("/undo")
    def undo(request: Request, id: list[str] = Form([]), ts: str = Form(""), fmt: str = Form("")):
        """Undo moves by manifest id (one or many), or by timestamp, or the last one."""
        s = require(request)
        with s.lock:
            moves = s.sorter.manifest.active_moves()
            if id:
                wanted = set(id)
                recs = [r for r in moves if r.get("id") in wanted]
            elif ts:
                recs = [r for r in moves if r["ts"] == ts]
            else:
                recs = s.sorter.manifest.last_moves(1)
            with s.sorter.manifest.batch():
                for r in recs:
                    s.sorter.undo(r)
            undone = {r.get("id") for r in recs}
            s.filed = deque(((p, r) for p, r in s.filed if r.get("id") not in undone), maxlen=2000)
        if recs:
            s.event(f"undid {len(recs)} move(s)")
        s.scan()
        s.restore([r["item_id"] for r in recs])
        if fmt == "json":
            return JSONResponse({"ok": True, "undone": len(recs), "items": [r["item_id"] for r in recs]})
        if request.headers.get("hx-request"):
            return Response(status_code=204, headers={"HX-Refresh": "true"})
        return RedirectResponse(request.headers.get("referer") or "/history", status_code=303)

    # -- examples: <category>/_training_data, filled by upload, web search or "use as example" ---------
    def examples_panel(request: Request, s: Session, cat: str, msg: str = "", headers: dict | None = None, check: bool = False):
        from ..embedding_classifier import label_text

        if cat not in s.cfg.all_categories or cat == s.cfg.review_category:
            raise HTTPException(404, "unknown category")
        filed_all = [i for i in s.sorter.storage.list_folder(split_path(cat)) if not i.is_video]
        cap = int(s.cfg.embeddings.get("max_filed_examples_per_category", 50)) if s.cfg.embeddings.get("use_filed_as_examples", True) else 0
        filed = list(reversed(filed_all[-cap:])) if cap else []
        s.known.update({i.id: i for i in filed})
        items = s.examples(cat)
        doubts: dict = {}
        if check:
            try:
                s.ensure_classifier()
                doubts = ex.check(s.sorter.classifier, s.sorter.storage, s.cfg, cat, items + filed)
                n = len(items) + len(filed)
                msg = (f"checked {n} photo{'s' if n != 1 else ''}: " +
                       (f"{len(doubts)} look{'s' if len(doubts) == 1 else ''} like another category (marked); select and move them"
                        if doubts else "all look like " + cat.split(SEP)[-1]))
                s.event(f"checked {cat.split(SEP)[-1]}: {len(doubts)} doubtful")
            except ValueError as e:
                msg = str(e)
        return render(request, "_examples.html", headers=headers, s=s, cat=cat, items=items, msg=msg, filed=filed,
                      filed_total=len(filed_all), doubts=doubts, folder=SEP.join(split_path(cat)) + " › " + s.cfg.training_subfolder,
                      query=label_text(cat, s.cfg.categories.get(cat) or (s.cfg.video_categories or {}).get(cat, "")))

    def add_example(s: Session, cat: str, name: str, data: bytes, source: dict) -> tuple[Item | None, str]:
        return ex.add(s.sorter.storage, s.cfg, cat, name, data, source)

    def refile(s: Session, item: Item, category: str) -> dict | None:
        """Move an already filed photo to another category; its example copies follow (one picture, one category)."""
        with s.lock:
            rec = s.sorter.move(item, category)
        if rec:
            ex.assign(s.sorter.storage, s.cfg, rec.get("md5"), category)
            p = Prediction(Item(id=rec["new_id"], name=rec["new_name"], parent=rec["new_parent"]),
                           Verdict(category, 1.0, "moved by hand", method="user"), s.cfg.threshold, s.cfg.review_category)
            s.filed = deque(((q, r) for q, r in s.filed if (r.get("new_id") or r["item_id"]) != item.id), maxlen=2000)
            s.record_filed(p, rec)
        return rec

    def changed(s: Session, what: str) -> dict:
        s.mark_stale("examples")
        s.event(what)
        return {"HX-Trigger": "examples-changed"}

    @app.get("/examples", response_class=HTMLResponse)
    def examples(request: Request, cat: str, check: int = 0):
        return examples_panel(request, require(request), cat, check=bool(check))

    @app.get("/examples/count", response_class=HTMLResponse)
    def examples_count(request: Request, cat: str):
        n = len(require(request).examples(cat))
        return HTMLResponse(f'<b class="excount{" zero" if not n else ""}">{n}</b>')

    @app.post("/examples/upload", response_class=HTMLResponse)
    async def examples_upload(request: Request, cat: str = Form(...), files: list[UploadFile] = File(...)):
        s = require(request)
        added = dupes = moved = 0
        for f in files:
            data = await f.read()
            if data and (f.content_type or "").startswith("image/"):
                _, status = add_example(s, cat, f.filename or "upload.jpg", data, {"source": "upload"})
                added += status == "added"
                dupes += status == "duplicate"
                moved += status == "moved"
        msg = f"added {added} uploaded image{'s' if added != 1 else ''}" if added or moved else "nothing added: choose image files"
        if moved:
            msg += f", {moved} moved here from another category's examples"
        if dupes:
            msg += f" ({dupes} already {'were examples' if dupes != 1 else 'was an example'})"
        added += moved
        return examples_panel(request, s, cat, msg, headers=changed(s, f"{msg} as examples of {cat.split(SEP)[-1]}") if added else None)

    @app.get("/examples/search", response_class=HTMLResponse)
    def examples_search(request: Request, cat: str, q: str, page: int = 1):
        require(request)
        try:
            results, err = websearch.search_images(q, page=page), ""
        except websearch.SearchError as e:
            results, err = [], str(e)
        return render(request, "_search.html", cat=cat, q=q, page=page, results=results, err=err)

    @app.post("/examples/fetch", response_class=HTMLResponse)
    def examples_fetch(request: Request, cat: str = Form(...), url: str = Form(...), title: str = Form(""), id: str = Form(""),
                       thumb: str = Form(""), license: str = Form(""), creator: str = Form(""), landing: str = Form(""),
                       provider: str = Form("")):
        s = require(request)
        r = {"id": id, "title": title, "url": url, "license": license, "creator": creator, "landing": landing, "provider": provider}
        try:
            data, _ = websearch.fetch_image(url)
        except websearch.SearchError as e:
            return HTMLResponse(f'<div class="hit failed" title="{e}">✕ could not download</div>')
        _, status = add_example(s, cat, websearch.filename_for(r), data, {"source": "web", **r})
        headers = changed(s, f"added a web example to {cat.split(SEP)[-1]}") if status != "duplicate" else None
        return render(request, "_hit_added.html", headers=headers, r={**r, "thumb": thumb}, dupe=status == "duplicate")

    def picked(s: Session, cat: str, ids: list[str]) -> tuple[list[Item], list[Item]]:
        """Selected tiles of an examples panel: (examples, filed photos)."""
        want_ex = {i[3:] for i in ids if i.startswith("ex:")}
        want_f = {i[6:] for i in ids if i.startswith("filed:")}
        exs = [i for i in s.examples(cat) if i.id in want_ex]
        filed = [i for i in s.sorter.storage.list_folder(split_path(cat)) if i.id in want_f]
        return exs, filed

    @app.post("/examples/remove", response_class=HTMLResponse)
    def examples_remove(request: Request, cat: str = Form(...), ids: list[str] = Form([])):
        s = require(request)
        exs, _ = picked(s, cat, ids)
        for item in exs:
            s.sorter.storage.trash(item)
        n = len(exs)
        msg = f"removed {n} example{'s' if n != 1 else ''} (in the trash, not deleted)" if n else "select examples to remove (filed photos can only be moved)"
        return examples_panel(request, s, cat, msg, headers=changed(s, f"removed {n} example(s) from {cat.split(SEP)[-1]}") if n else None)

    @app.post("/examples/move", response_class=HTMLResponse)
    def examples_move(request: Request, cat: str = Form(...), ids: list[str] = Form([]), category: str = Form(""),
                      new_category: str = Form("")):
        s = require(request)
        if not category:
            return examples_panel(request, s, cat, "choose where to move the selected photos")
        exs, filed = picked(s, cat, ids)
        to, _ = resolve_category(s, category, new_category)
        if to == s.cfg.review_category and exs:
            return examples_panel(request, s, cat, "examples can only move to a category, not the review folder")
        for item in exs:
            ex.assign(s.sorter.storage, s.cfg, s.sorter.storage.md5_of(item), to)
        with s.sorter.manifest.batch():
            for item in filed:
                refile(s, item, to)
        n = len(exs) + len(filed)
        msg = f"moved {n} photo{'s' if n != 1 else ''} to {to.split(SEP)[-1]}" if n else "select photos first"
        return examples_panel(request, s, cat, msg, headers=changed(s, f"{msg} (from {cat.split(SEP)[-1]})") if n else None)

    @app.post("/refile", response_class=HTMLResponse)
    def refile_route(request: Request, rec: str = Form(...), category: str = Form(...), new_category: str = Form("")):
        """Move a photo filed earlier (Filed list, History) to another category."""
        s = require(request)
        r = next((x for x in s.sorter.manifest.active_moves() if x.get("id") == rec), None)
        if r is None:
            raise HTTPException(404, "that move is no longer current; refresh")
        to, _ = resolve_category(s, category, new_category)
        new = refile(s, Item(id=r.get("new_id") or r["item_id"], name=r["new_name"], parent=r["new_parent"]), to)
        if new:
            changed(s, f"moved {r['new_name']} to {to.split(SEP)[-1]} as {new['new_name']}")
        if request.headers.get("hx-request"):
            return render(request, "_toast.html", headers={"HX-Trigger": json.dumps({"board-changed": True, "detail-changed": True, "examples-changed": True})},
                          moved=new, cat=to, recs=None)
        return RedirectResponse(request.headers.get("referer") or "/history", status_code=303)

    @app.post("/examples/teach", response_class=HTMLResponse)
    def examples_teach(request: Request, item_id: str = Form(...), category: str = Form(...), new_category: str = Form(""),
                       fmt: str = Form("")):
        """Copy an inbox image into a category's examples; the image itself stays where it is."""
        s = require(request)
        item = s.items.get(item_id)
        if not item:
            raise HTTPException(404, "this photo is no longer waiting to be sorted; look for new photos")
        if category == s.cfg.review_category:
            raise HTTPException(400, "pick a category, not the review folder")
        category, _ = resolve_category(s, category, new_category)
        data = s.sorter.storage.read_bytes(item, prefer="original")
        _, status = add_example(s, category, item.name, data, {"source": "inbox"})
        if fmt == "json":
            if status != "duplicate":
                changed(s, f"{item.name} is now an example of {category.split(SEP)[-1]}")
            return JSONResponse({"ok": True, "status": status, "category": category})
        if status == "duplicate":
            return render(request, "_toast.html", taught=item.name, cat=category, dupe=True, recs=None)
        return render(request, "_toast.html", headers=changed(s, f"{item.name} is now an example of {category.split(SEP)[-1]}"),
                      taught=item.name, cat=category, recs=None)

    # -- categories (an outliner over CATEGORIES.md) -------------------------------------
    def tree_page(request: Request, s: Session, name: str, msg: str = "", err: str = "", flash: str = "", undo: bool = False):
        return render(request, name, s=s, t=taxonomy(s), msg=msg, err=err,
                      flash=flash, added=msg.startswith("added"), undo=undo and bool(s.undo_text))

    @app.get("/categories", response_class=HTMLResponse)
    def categories(request: Request, msg: str = "", err: str = ""):
        return tree_page(request, require(request, fresh=True), "categories.html", msg=msg, err=err)

    def _mutate(request: Request, fn, undoable: bool = False):
        """Apply fn(taxonomy) -> (message, key to highlight). HTMX gets the tree back; plain forms a redirect."""
        s = require(request)
        t = taxonomy(s)
        hx = bool(request.headers.get("hx-request"))
        if t is None:
            err = "categories are inline in the JSON config; set categories_file to edit them here"
            return tree_page(request, s, "_tree.html", err=err) if hx else RedirectResponse("/categories?" + urlencode({"err": err}), status_code=303)
        before = t.text()
        try:
            msg, flash = fn(t)
        except ValueError as e:
            return tree_page(request, s, "_tree.html", err=str(e)) if hx else RedirectResponse("/categories?" + urlencode({"err": str(e)}), status_code=303)
        if t.text() != before:
            s.undo_text = before
            t.save()
            categories_changed()
        if hx:
            return tree_page(request, s, "_tree.html", msg=msg, flash=flash, undo=undoable)
        return RedirectResponse("/categories?" + urlencode({"msg": msg}), status_code=303)

    @app.post("/categories/add")
    def cat_add(request: Request, parent: str = Form(""), name: str = Form(...), description: str = Form("")):
        def go(t):
            key = t.add(parent or None, name, description)
            return f"added {key.split(SEP)[-1]}" + (f" under {parent.split(SEP)[-1]}" if parent else ""), key
        return _mutate(request, go)

    @app.post("/categories/update")
    def cat_update(request: Request, key: str = Form(...), name: str = Form(...), description: str = Form("")):
        def go(t):
            new = t.update(key, name, description)
            return "saved", new
        return _mutate(request, go)

    @app.post("/categories/delete")
    def cat_delete(request: Request, key: str = Form(...)):
        def go(t):
            n = t.delete(key)
            extra = f" and {n - 1} sub-categor{'y' if n == 2 else 'ies'}" if n > 1 else ""
            return f"removed {key.split(SEP)[-1]}{extra}", ""
        return _mutate(request, go, undoable=True)

    @app.post("/categories/move")
    def cat_move(request: Request, key: str = Form(...), direction: int = Form(...)):
        def go(t):
            t.move(key, direction)
            return "", key
        return _mutate(request, go)

    @app.post("/categories/indent")
    def cat_indent(request: Request, key: str = Form(...)):
        def go(t):
            new = t.indent(key)
            return f"{key.split(SEP)[-1]} is now under {new.split(SEP)[-2]}", new
        return _mutate(request, go, undoable=True)

    @app.post("/categories/outdent")
    def cat_outdent(request: Request, key: str = Form(...)):
        def go(t):
            new = t.outdent(key)
            return f"moved {key.split(SEP)[-1]} up one level", new
        return _mutate(request, go, undoable=True)

    @app.post("/categories/undo")
    def cat_undo(request: Request):
        s = require(request)
        text, s.undo_text = s.undo_text, None
        if s.cfg.categories_store is None or not text:
            return tree_page(request, s, "_tree.html", err="nothing to undo")
        s.cfg.categories_store.write(text)
        categories_changed()
        return tree_page(request, s, "_tree.html", msg="undone")

    @app.get("/favicon.svg")
    def favicon():
        svg = (Path(__file__).parent / "templates" / "_logo.svg").read_text(encoding="utf-8")
        return Response(svg, media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=86400"})

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    return app


def app_from_env() -> FastAPI:
    """Entry point for `uvicorn photo_sorter.web.app:app_from_env --factory` (Cloud Run, VPS). Settings come from the
    environment (PHOTO_SORTER_PROVIDER, GOOGLE_CLOUD_PROJECT, ...); a config file only if PHOTO_SORTER_CONFIG names one."""
    path = os.environ.get("PHOTO_SORTER_CONFIG")
    return create_app(load_config(path) if path else base_config(hosted=is_hosted()))
