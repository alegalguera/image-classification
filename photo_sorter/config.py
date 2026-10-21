"""Load the classification spec from a JSON or Markdown file.

Two Markdown layouts are understood:

1. Flat: `## Category` headings, each followed by a description paragraph
   (see categories.example.md). A `# Videos` H1 starts the video-only section.

2. Tree (CATEGORIES.md): a nested bullet list of folders and albums, e.g.

       - 📁 Photos - Coral
         - 📁 Outplants
           - ✅ APAL: Acropora palmata (elkhorn coral)
       - ✅ Photos - Fragging
       - Videos - Coral  ❓ sub-albums unknown

   Leaves become categories keyed by their full path, joined with " › "
   (e.g. "Photos - Coral › Outplants › APAL"). Legend markers (✅ ⚠️ ❓ ➕ 📁)
   and trailing notes are stripped, lines marked "(ignored" are skipped, and
   top-level entries whose name starts with "Videos" become video categories.
   An optional `## Settings` section may hold `- key: value` lines
   (album, provider, model, effort, min_confidence, review_category,
   output_folder, output_size).

A JSON config can hold the settings and point at the Markdown taxonomy with
`"categories_file": "CATEGORIES.md"`.

Without a config file, a photo folder carries its own settings: `CATEGORIES.md` at its top and
`.sorter/settings.json` (inbox, min_confidence, review folder, naming), so everyone who opens the
folder (on a laptop or the hosted app) sorts with the same list. The app-level choices (which model)
come from the environment; on a laptop the folder picked last time is remembered in APP_DIR/app.json.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_MODELS = {
    "siglip": "google/siglip2-base-patch16-224",
    "vertex": "multimodalembedding@001",
    "claude": "claude-opus-5-5",
    "gemini": "gemini-2.5-flash-lite",
}
EMBEDDING_PROVIDERS = ("siglip", "vertex")
DEFAULT_THRESHOLD = {"siglip": 0.15, "vertex": 0.15, "claude": 0.7, "gemini": 0.7}
DEFAULT_EMBEDDINGS = {
    "knn_weight": 0.6, "knn_k": 3, "knn_temperature": 0.03, "text_temperature": 0.05,
    "knn_midpoint": 0.85, "knn_softness": 0.03,   # similarity to examples that counts as a match (categories with <3 examples;
                                                  # with 3+ it is calibrated from how alike the examples are)
    "min_fit": 0.02,   # SigLIP: the best label's own match probability must reach this, else -> Review ("no category fits")
    "use_filed_as_examples": True, "max_filed_examples_per_category": 50,
    "prompt_templates": ["This is a photo of {label}."],
    "project": None, "location": "us-central1", "dimension": 512,
}
SEP = " › "
APP_DIR = Path(os.environ.get("PHOTO_SORTER_HOME", Path.home() / ".config" / "photo-sorter"))
FOLDER_SETTINGS = "settings.json"      # in <folder>/.sorter/
MARKERS = re.compile(r"[✅⚠️❓➕📁🗂️]|‍|️")
SETTING_KEYS = {"album", "provider", "model", "effort", "min_confidence", "review_category", "output_folder", "output_size",
                "source_kind", "source_root", "inbox", "training_subfolder", "review_action", "naming_pattern"}


@dataclass
class Config:
    categories: dict[str, str]
    video_categories: dict[str, str] | None = None
    provider: str = "siglip"
    model: str | None = None
    effort: str = "low"
    min_confidence: float | None = None          # None -> DEFAULT_THRESHOLD[provider]
    review_category: str = "Review"
    review_action: str = "move"                  # move | keep (below-threshold items)
    output_folder: str = "Sorted Photos"
    output_size: str = "original"
    source_kind: str = "icloud"                  # icloud | local | drive
    source_root: str | None = None               # album URL, folder path, or Drive folder URL/id
    inbox: str = "Inbox"
    training_subfolder: str = "_training_data"
    naming_pattern: str = "{slug}_{n:03d}{ext}"
    embeddings: dict = field(default_factory=lambda: dict(DEFAULT_EMBEDDINGS))
    second_opinion: dict | None = None
    pricing: dict = field(default_factory=dict)      # optional price overrides per model id (see metering.py)
    path: Path | None = field(default=None, repr=False)
    categories_store: object | None = field(default=None, repr=False)   # where CATEGORIES.md is read and saved
    categories_in_folder: bool = False   # True: use the photo folder's own CATEGORIES.md (see apply_folder)

    @property
    def categories_path(self) -> Path | None:
        """The Markdown file on this computer, when the categories come from a config's categories_file."""
        return getattr(self.categories_store, "path", None)

    @property
    def album(self) -> str | None:
        """Legacy alias: the iCloud shared-album URL."""
        return self.source_root if self.source_kind == "icloud" else None

    @property
    def threshold(self) -> float:
        return self.min_confidence if self.min_confidence is not None else DEFAULT_THRESHOLD[self.provider]

    @property
    def is_embedding_provider(self) -> bool:
        return self.provider in EMBEDDING_PROVIDERS

    def categories_for(self, is_video: bool) -> dict[str, str]:
        if is_video and self.video_categories:
            return self.video_categories
        return self.categories

    @property
    def model_name(self) -> str:
        return self.model or DEFAULT_MODELS[self.provider]

    @property
    def all_categories(self) -> set[str]:
        return set(self.categories) | set(self.video_categories or {}) | {self.review_category}

    def validate(self) -> None:
        if self.provider not in DEFAULT_MODELS:
            raise ValueError(f"{self.path}: provider must be one of {sorted(DEFAULT_MODELS)}")
        if self.output_size not in ("thumb", "medium", "original"):
            raise ValueError(f"{self.path}: output size must be thumb, medium or original")
        if not 0 <= self.threshold <= 1:
            raise ValueError(f"{self.path}: min_confidence must be between 0 and 1")
        if self.review_action not in ("move", "keep"):
            raise ValueError(f"{self.path}: review_action must be move or keep")
        if self.source_kind not in ("icloud", "local", "drive"):
            raise ValueError(f"{self.path}: source.kind must be icloud, local or drive")
        if self.min_confidence is not None and not 0 <= self.min_confidence <= 1:
            raise ValueError(f"{self.path}: min_confidence must be between 0 and 1")
        for name in self.all_categories:
            if not name or name.strip() != name or any(not part.strip() for part in name.split(SEP)):
                raise ValueError(f"{self.path}: bad category name {name!r}")


def split_path(category: str) -> list[str]:
    """'Photos - Coral › Outplants › APAL' -> ['Photos - Coral', 'Outplants', 'APAL']."""
    return [p.strip() for p in category.split(SEP) if p.strip()]


def load_config(path: str | Path) -> Config:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"config not found: {p}")
    if p.suffix.lower() == ".json":
        cfg = _from_json(json.loads(p.read_text()), p.parent)
    elif p.suffix.lower() in (".md", ".markdown"):
        from .taxonomy import FileCategories

        cfg = _from_markdown(p.read_text())
        cfg.categories_store = FileCategories(p)
    else:
        raise ValueError(f"config must be .json or .md, got {p.suffix}")
    cfg.path = p
    cfg.validate()
    return cfg


def _from_json(d: dict, base: Path) -> Config:
    model = d.get("model") or {}
    out = d.get("output") or {}
    from .taxonomy import FileCategories

    cats = dict(d.get("categories") or {})
    vids = dict(d["video_categories"]) if d.get("video_categories") else None
    store = None
    if d.get("categories_file"):
        store = FileCategories((base / d["categories_file"]).expanduser())
        cats, vids = parse_categories(store.read())
    emb = {**DEFAULT_EMBEDDINGS, **(d.get("embeddings") or {})}
    # provider/model: "embeddings" wins for siglip/vertex, "model" block for claude/gemini (legacy)
    provider = emb.get("provider") or model.get("provider") or "siglip"
    model_name = emb.get("model") if provider in EMBEDDING_PROVIDERS else model.get("name")
    src = d.get("source") or {}
    kind = src.get("kind") or ("icloud" if d.get("album") else "local")
    root = src.get("root") or d.get("album")
    naming = d.get("naming") or {}
    mc = d.get("min_confidence")
    cfg = Config(
        categories=cats,
        video_categories=vids,
        provider=provider,
        model=model_name,
        effort=model.get("effort", "low"),
        min_confidence=float(mc) if mc is not None else None,
        review_category=d.get("review_category", "Review"),
        review_action=d.get("review_action", "move"),
        output_folder=out.get("folder", "Sorted Photos"),
        output_size=out.get("size", "original"),
        source_kind=kind,
        source_root=root,
        inbox=src.get("inbox", "Inbox"),
        training_subfolder=d.get("training_subfolder", "_training_data"),
        naming_pattern=naming.get("pattern", "{slug}_{n:03d}{ext}"),
        embeddings=emb,
        second_opinion=d.get("second_opinion"),
        pricing=dict(d.get("pricing") or {}),
    )
    cfg.categories_store = store
    cfg.categories_in_folder = store is None and not cats    # no list in the config: the folder brings its own
    return cfg


def parse_categories(text: str) -> tuple[dict[str, str], dict[str, str] | None]:
    """CATEGORIES.md text -> (photo categories, video categories or None)."""
    md = _from_markdown(text)
    return md.categories, md.video_categories


def source_from(text: str) -> tuple[str, str]:
    """A folder path, a Google Drive folder link or id, or an iCloud album link -> (source kind, root)."""
    s = text.strip()
    if "icloud.com" in s:
        return "icloud", s
    if "drive.google.com" in s or (len(s) > 20 and not re.search(r"[/\\]", s) and not Path(s).expanduser().exists()):
        return "drive", s
    return "local", str(Path(s).expanduser())


def remembered_folder() -> str | None:
    """The photo folder picked last time on this computer (written by the web app's setup page)."""
    try:
        return json.loads((APP_DIR / "app.json").read_text()).get("folder") or None
    except (OSError, ValueError, AttributeError):
        return None


def remember_folder(folder: str) -> None:
    p = APP_DIR / "app.json"
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError):
        data = {}
    data["folder"] = folder
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2) + "\n")


def base_config(hosted: bool = False) -> Config:
    """Settings when there is no config file: the model from the environment (Vertex AI when hosted, SigLIP on a
    laptop) and, on a laptop, the folder picked last time. Categories and the rest come from the folder."""
    provider = os.environ.get("PHOTO_SORTER_PROVIDER") or ("vertex" if hosted else "siglip")
    cfg = Config(categories={}, provider=provider, model=os.environ.get("PHOTO_SORTER_MODEL") or None,
                 source_kind="local", categories_in_folder=True)
    folder = None if hosted else remembered_folder()
    if folder:
        cfg.source_kind, cfg.source_root = source_from(folder)
    cfg.validate()
    return cfg


def apply_folder(cfg: Config, storage) -> str | None:
    """Use the photo folder's own settings (.sorter/settings.json) and, unless the config names its own list, its
    CATEGORIES.md. Mutates cfg (and the storage's inbox), returns the categories text that was read."""
    from .taxonomy import FolderCategories

    raw = storage.read_meta(FOLDER_SETTINGS)
    if raw:
        try:
            d = json.loads(raw)
        except ValueError as e:
            raise ValueError(f".sorter/{FOLDER_SETTINGS} in the photo folder is not valid JSON: {e}") from e
        if "inbox" in d:
            cfg.inbox = str(d["inbox"] or "")
        if d.get("min_confidence") is not None:
            cfg.min_confidence = float(d["min_confidence"])
        for k in ("review_category", "review_action", "training_subfolder"):
            if d.get(k):
                setattr(cfg, k, str(d[k]))
        if (d.get("naming") or {}).get("pattern"):
            cfg.naming_pattern = d["naming"]["pattern"]
    storage.inbox, storage.training_subfolder = cfg.inbox, cfg.training_subfolder
    if cfg.categories_in_folder:
        cfg.categories_store = FolderCategories(storage)
    text = cfg.categories_store.read() if cfg.categories_store is not None else None
    if text is not None:
        cfg.categories, cfg.video_categories = parse_categories(text)
    cfg.validate()
    return text


def folder_settings(storage) -> dict | None:
    raw = storage.read_meta(FOLDER_SETTINGS)
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


def save_folder_settings(storage, **changes) -> None:
    """Merge changes into the folder's .sorter/settings.json."""
    data = {**(folder_settings(storage) or {}), **changes}
    storage.write_meta(FOLDER_SETTINGS, (json.dumps(data, indent=2) + "\n").encode("utf-8"))


def _from_markdown(text: str) -> Config:
    lines = text.splitlines()
    settings = _settings_section(lines)
    body = _before_first_h2(lines)
    if any(re.match(r"^\s*[-*]\s", ln) for ln in body):
        photo, video = _parse_tree(body)
    else:
        photo, video = _parse_flat(lines)
    cfg = Config(categories=photo, video_categories=video or None)
    for k, v in settings.items():
        if k == "min_confidence":
            cfg.min_confidence = float(v)
        elif k == "album":
            cfg.source_kind, cfg.source_root = "icloud", v
        else:
            setattr(cfg, k, v)
    return cfg


def _before_first_h2(lines: list[str]) -> list[str]:
    out = []
    for ln in lines:
        if re.match(r"^##\s", ln):
            break
        out.append(ln)
    return out


def _settings_section(lines: list[str]) -> dict[str, str]:
    found: dict[str, str] = {}
    active = False
    for ln in lines:
        if re.match(r"^#{1,6}\s", ln):
            active = bool(re.match(r"^##\s+settings\s*$", ln, re.I))
            continue
        m = re.match(r"^\s*[-*]?\s*([a-z_]+)\s*:\s*(.+?)\s*$", ln, re.I)
        if active and m and m.group(1).lower() in SETTING_KEYS:
            found[m.group(1).lower()] = m.group(2).strip("`\"' ")
    return found


def _clean(label: str) -> str:
    label = MARKERS.sub("", label)
    label = re.sub(r"\s{2,}.*$", "", label.strip())  # drop trailing notes separated by 2+ spaces
    return label.strip(" -:")


def _parse_tree(lines: list[str]) -> tuple[dict[str, str], dict[str, str]]:
    from . import taxonomy

    roots, _ = taxonomy.parse("\n".join(lines))
    return taxonomy.leaves(roots)


def _parse_flat(lines: list[str]) -> tuple[dict[str, str], dict[str, str]]:
    photo: dict[str, str] = {}
    video: dict[str, str] = {}
    target = photo
    current: str | None = None
    for line in lines:
        h1 = re.match(r"^#\s+(.*)", line)
        h2 = re.match(r"^##\s+(.*)", line)
        if h1:
            target = video if h1.group(1).strip().lower().startswith("video") else photo
            current = None
        elif h2:
            current = h2.group(1).strip()
            if current.lower() == "settings":
                current = None
                continue
            target[current] = ""
        elif current is not None and line.strip():
            target[current] = (target[current] + " " + line.strip()).strip()
    return photo, video
