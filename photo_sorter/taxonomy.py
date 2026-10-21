"""The category tree behind CATEGORIES.md, with create / rename / describe / delete operations.

The file format is deliberately tiny: a nested bullet list, one category per line, an optional
description after a colon. Indentation makes sub-categories; a category with children acts as a
folder and photos are filed into its leaves.

    # Categories
    - Coral: hard corals on the reef
      - Bleaching: pale or white coral
    - Sponge
    - People: staff, interns, visitors

Everything from the first `## ` heading on is kept verbatim when the file is rewritten, so notes
and a `## Settings` block survive edits made from the web UI. Legend markers (✅ ⚠️ ❓ ➕ 📁) from
older files are tolerated and dropped on save.

The file lives either at the top of the photo folder (FolderCategories, local or Google Drive, shared
by everyone who opens the folder) or wherever a config file's categories_file points (FileCategories).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

SEP = " › "
MARKERS = re.compile(r"[✅⚠️❓➕📁🗂️]|‍|️")
BULLET = re.compile(r"^(\s*)[-*]\s+(.*)$")
HEADER = "# Categories\n\nOne category per line; indent to make sub-categories; text after the colon describes what the photos look like.\n\n"
FILE_NAME = "CATEGORIES.md"


class FileCategories:
    """CATEGORIES.md on this computer (named by a config file)."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()
        self.label = str(self.path)

    def read(self) -> str:
        return self.path.read_text(encoding="utf-8") if self.path.exists() else ""

    def write(self, text: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(self.path)


class FolderCategories:
    """CATEGORIES.md at the top of the photo folder (a local folder or Google Drive)."""

    def __init__(self, storage):
        self.storage = storage
        self.label = f"{storage.root_label} › {FILE_NAME}"

    def read(self) -> str | None:
        raw = self.storage.read_root_file(FILE_NAME)
        return raw.decode("utf-8") if raw is not None else None

    def write(self, text: str) -> None:
        self.storage.write_root_file(FILE_NAME, text.encode("utf-8"))


@dataclass
class Node:
    name: str
    description: str = ""
    children: list["Node"] = field(default_factory=list)

    @property
    def is_leaf(self) -> bool:
        return not self.children


def _clean(label: str) -> str:
    label = MARKERS.sub("", label)
    label = re.sub(r"\s{2,}.*$", "", label.strip())  # drop trailing notes separated by 2+ spaces
    return label.strip(" -:")


def parse(text: str) -> tuple[list[Node], str]:
    """-> (top-level nodes, tail). The tail is the text from the first '## ' heading on (kept as is)."""
    lines = text.splitlines()
    body, tail = lines, ""
    for i, ln in enumerate(lines):
        if re.match(r"^##\s", ln):
            body, tail = lines[:i], "\n".join(lines[i:]).rstrip("\n") + "\n"
            break
    rows: list[tuple[int, Node]] = []
    for ln in body:
        m = BULLET.match(ln)
        if not m or "(ignored" in m.group(2).lower():
            continue
        label = _clean(m.group(2))
        if not label:
            continue
        name, desc = label, ""
        mm = re.match(r"^([^:]+?)\s*:\s*(.+)$", label)
        if mm:
            name, desc = mm.group(1).strip(), mm.group(2).strip()
        rows.append((len(m.group(1).replace("\t", "    ")), Node(name, desc)))
    roots: list[Node] = []
    stack: list[tuple[int, Node]] = []
    for indent, node in rows:
        while stack and stack[-1][0] >= indent:
            stack.pop()
        (stack[-1][1].children if stack else roots).append(node)
        stack.append((indent, node))
    return roots, tail


def dump(roots: list[Node], tail: str = "") -> str:
    out = [HEADER.rstrip("\n"), ""]

    def walk(nodes: list[Node], depth: int) -> None:
        for n in nodes:
            line = "  " * depth + "- " + n.name
            if n.description:
                line += ": " + n.description
            out.append(line)
            walk(n.children, depth + 1)

    walk(roots, 0)
    text = "\n".join(out).rstrip("\n") + "\n"
    if tail.strip():
        text += "\n" + tail
    return text


def leaves(roots: list[Node]) -> tuple[dict[str, str], dict[str, str]]:
    """(photo categories, video categories) keyed by SEP-joined path, as config.py expects."""
    photo: dict[str, str] = {}
    video: dict[str, str] = {}

    def walk(nodes: list[Node], path: list[str]) -> None:
        for n in nodes:
            p = path + [n.name]
            if n.children:
                walk(n.children, p)
            else:
                target = video if p[0].lower().startswith("video") else photo
                target[SEP.join(p)] = n.description or n.name

    walk(roots, [])
    return photo, video


def from_paths(paths: list[list[str]]) -> list[Node]:
    """Folder paths (['Coral', 'Bleaching'], ...) -> a category tree."""
    roots: list[Node] = []
    for parts in paths:
        siblings = roots
        for part in parts:
            node = next((n for n in siblings if n.name == part), None)
            if node is None:
                node = Node(part)
                siblings.append(node)
            siblings = node.children
    return roots


def tree_rows(roots: list[Node]) -> list[tuple[str, int, Node]]:
    """(key, depth, node) in display order."""
    out = []

    def walk(nodes, path, depth):
        for n in nodes:
            p = path + [n.name]
            out.append((SEP.join(p), depth, n))
            walk(n.children, p, depth + 1)

    walk(roots, [], 0)
    return out


PRESETS_DIR = Path(__file__).parent / "presets"


def presets() -> list[dict]:
    """Ready-made category lists shipped with the app (presets/*.md), offered on the setup page. Each file is a normal
    CATEGORIES.md whose first heading is its title and whose first paragraph says what it covers."""
    out = []
    for p in sorted(PRESETS_DIR.glob("*.md")):
        text = p.read_text(encoding="utf-8")
        lines = [ln.strip() for ln in text.splitlines()]
        roots, tail = parse(text)
        rows = tree_rows(roots)
        out.append({"key": p.stem, "text": dump(roots, tail), "rows": rows,
                    "title": next((ln[2:] for ln in lines if ln.startswith("# ")), p.stem),
                    "about": next((ln for ln in lines if ln and not ln.startswith(("#", "-", "*"))), ""),
                    "leaves": sum(1 for _, _, n in rows if n.is_leaf)})
    return out


class Taxonomy:
    def __init__(self, store):
        """store: FileCategories / FolderCategories, or a path to a Markdown file."""
        self.store = store if hasattr(store, "read") else FileCategories(store)
        self.roots, self.tail = parse(self.store.read() or "")

    # -- queries -----------------------------------------------------------
    def find(self, key: str) -> tuple[Node | None, list[Node]]:
        """Node for a SEP path and the sibling list that contains it."""
        parts = [p.strip() for p in key.split(SEP) if p.strip()]
        siblings, node = self.roots, None
        for part in parts:
            node = next((n for n in siblings if n.name == part), None)
            if node is None:
                return None, siblings
            if part != parts[-1]:
                siblings = node.children
        return node, siblings

    def keys(self) -> list[str]:
        out: list[str] = []

        def walk(nodes, path):
            for n in nodes:
                p = path + [n.name]
                out.append(SEP.join(p))
                walk(n.children, p)

        walk(self.roots, [])
        return out

    def rows(self) -> list[tuple[str, int, Node]]:
        """(key, depth, node) in display order."""
        return tree_rows(self.roots)

    # -- mutations ---------------------------------------------------------
    @staticmethod
    def _check_name(name: str) -> str:
        name = re.sub(r"\s+", " ", name.strip())
        if not name:
            raise ValueError("the name is empty")
        if ":" in name or SEP.strip() in name or name.startswith(("-", "*", "#")):
            raise ValueError("names cannot contain ':' or '›' or start with -, * or #")
        return name

    def add(self, parent_key: str | None, name: str, description: str = "") -> str:
        name = self._check_name(name)
        if parent_key:
            parent, _ = self.find(parent_key)
            if parent is None:
                raise ValueError(f"parent {parent_key!r} not found")
            siblings = parent.children
        else:
            siblings = self.roots
        if any(n.name.lower() == name.lower() for n in siblings):
            raise ValueError(f"{name!r} already exists here")
        siblings.append(Node(name, description.strip()))
        return SEP.join(([parent_key] if parent_key else []) + [name])

    def update(self, key: str, name: str | None = None, description: str | None = None) -> str:
        node, siblings = self.find(key)
        if node is None:
            raise ValueError(f"{key!r} not found")
        if name is not None:
            name = self._check_name(name)
            if any(n is not node and n.name.lower() == name.lower() for n in siblings):
                raise ValueError(f"{name!r} already exists here")
            node.name = name
        if description is not None:
            node.description = description.strip()
        parts = key.split(SEP)[:-1] + [node.name]
        return SEP.join(parts)

    def delete(self, key: str) -> int:
        """Remove a category (and its sub-categories). Returns how many nodes were removed."""
        node, siblings = self.find(key)
        if node is None:
            raise ValueError(f"{key!r} not found")
        siblings.remove(node)
        return 1 + sum(1 for _ in _iter(node.children))

    def move(self, key: str, direction: int) -> None:
        node, siblings = self.find(key)
        if node is None:
            return
        i = siblings.index(node)
        j = max(0, min(len(siblings) - 1, i + direction))
        siblings[i], siblings[j] = siblings[j], siblings[i]

    def indent(self, key: str) -> str:
        """Make a category a sub-category of the one above it. Returns its new key."""
        node, siblings = self.find(key)
        if node is None:
            raise ValueError(f"{key!r} not found")
        i = siblings.index(node)
        if i == 0:
            raise ValueError(f"nothing above {node.name!r} to put it under")
        prev = siblings[i - 1]
        if any(n.name.lower() == node.name.lower() for n in prev.children):
            raise ValueError(f"{prev.name!r} already has a {node.name!r}")
        siblings.remove(node)
        prev.children.append(node)
        return SEP.join(key.split(SEP)[:-1] + [prev.name, node.name])

    def outdent(self, key: str) -> str:
        """Move a sub-category up one level, right after its parent. Returns its new key."""
        parts = key.split(SEP)
        if len(parts) < 2:
            raise ValueError(f"{parts[-1]!r} is already at the top level")
        node, siblings = self.find(key)
        parent, grand = self.find(SEP.join(parts[:-1]))
        if node is None or parent is None:
            raise ValueError(f"{key!r} not found")
        if any(n.name.lower() == node.name.lower() for n in grand):
            raise ValueError(f"there is already a {node.name!r} one level up")
        siblings.remove(node)
        grand.insert(grand.index(parent) + 1, node)
        return SEP.join(parts[:-2] + [node.name])

    def text(self) -> str:
        return dump(self.roots, self.tail)

    def save(self) -> None:
        self.store.write(self.text())


def _iter(nodes):
    for n in nodes:
        yield n
        yield from _iter(n.children)
