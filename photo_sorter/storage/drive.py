"""Google Drive backend: files stay in Drive; we list, stream bytes, create folders and move+rename."""
from __future__ import annotations

import hashlib
import io
import re
import urllib.request
from datetime import datetime

from ..items import Item, guess_mime
from ..naming import safe_dirname, safe_filename, unique_name
from . import META_DIR, NoPreviewError

FOLDER = "application/vnd.google-apps.folder"
SHORTCUT = "application/vnd.google-apps.shortcut"
FILE_FIELDS = "id,name,mimeType,md5Checksum,size,createdTime,modifiedTime,thumbnailLink,parents,imageMediaMetadata(width,height,time),shortcutDetails"
LIST_FIELDS = f"nextPageToken,files({FILE_FIELDS})"
COMMON = {"supportsAllDrives": True}
LIST_COMMON = {**COMMON, "includeItemsFromAllDrives": True, "pageSize": 1000}


def folder_id_from(url_or_id: str) -> str:
    s = url_or_id.strip()
    m = re.search(r"/folders/([A-Za-z0-9_-]+)", s) or re.search(r"[?&]id=([A-Za-z0-9_-]+)", s)
    if m:
        return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{10,}", s):
        return s
    raise ValueError(f"not a Google Drive folder link or id: {url_or_id!r}")


def _q(s: str) -> str:
    return s.replace("\\", "\\\\").replace("'", "\\'")


def _item(f: dict) -> Item:
    created = f.get("createdTime")
    meta = f.get("imageMediaMetadata") or {}
    taken = meta.get("time")
    when = None
    try:
        if taken:
            when = datetime.strptime(taken, "%Y:%m:%d %H:%M:%S")
        elif created:
            when = datetime.fromisoformat(created.replace("Z", "+00:00"))
    except ValueError:
        pass
    mime = f.get("mimeType", "")
    return Item(
        id=f["id"], name=f["name"], mime=mime, size=int(f["size"]) if f.get("size") else None,
        is_video=mime.startswith("video/"), created=when, md5=f.get("md5Checksum"),
        parent=(f.get("parents") or [None])[0],
        extra={"thumbnailLink": f.get("thumbnailLink"), "width": meta.get("width"), "height": meta.get("height")},
    )


class GoogleDriveStorage:
    def __init__(self, service, root_id: str, credentials=None, inbox: str = "Inbox",
                 training_subfolder: str = "_training_data"):
        self.svc, self.root_id, self.creds = service, root_id, credentials
        self.inbox, self.training_subfolder = inbox or "", training_subfolder
        self._folders: dict[tuple[str, str], str] = {}   # (parent_id, name) -> id
        self._listed: set[str] = set()                    # parents whose subfolders are all in _folders
        self._file_ids: dict[tuple[str, str], str] = {}   # (parent_id, file name) -> id, for .sorter files and CATEGORIES.md
        info = self.svc.files().get(fileId=root_id, fields="id,name,mimeType,driveId", **COMMON).execute()
        if info.get("mimeType") != FOLDER:
            raise ValueError(f"{info.get('name')!r} is not a folder")
        self.root_label = f"Drive: {info['name']}"

    @classmethod
    def open(cls, url_or_id: str, credentials, inbox: str = "Inbox", training_subfolder: str = "_training_data"):
        try:
            from googleapiclient.discovery import build
        except ImportError as e:
            raise ImportError("Google Drive needs the drive extra: run `uv sync --extra drive`") from e
        if credentials is None:
            raise PermissionError("not signed in to Google: run `photo-sorter auth`")
        svc = build("drive", "v3", credentials=credentials, cache_discovery=False)
        return cls(svc, folder_id_from(url_or_id), credentials, inbox, training_subfolder)

    # -- listing -----------------------------------------------------------
    def _list(self, q: str) -> list[dict]:
        out, token = [], None
        while True:
            resp = self.svc.files().list(q=q, fields=LIST_FIELDS, pageToken=token, orderBy="name", **LIST_COMMON).execute(num_retries=3)
            out.extend(resp.get("files", []))
            token = resp.get("nextPageToken")
            if not token:
                return out

    def _children_folders(self, parent_id: str) -> list[dict]:
        return self._list(f"'{_q(parent_id)}' in parents and trashed=false and mimeType='{FOLDER}'")

    def _media(self, parent_id: str | None) -> list[Item]:
        if not parent_id:
            return []
        files = self._list(f"'{_q(parent_id)}' in parents and trashed=false and (mimeType contains 'image/' or mimeType contains 'video/')")
        return [_item(f) for f in files if f.get("mimeType") != SHORTCUT]

    def _child_folder(self, parent_id: str, name: str, fresh: bool = False) -> str | None:
        """One listing per parent folder serves every lookup under it (fresh=True lists again)."""
        if fresh or parent_id not in self._listed:
            for f in self._children_folders(parent_id):
                self._folders.setdefault((parent_id, f["name"]), f["id"])
            self._listed.add(parent_id)
        return self._folders.get((parent_id, name))

    def _resolve(self, parts: list[str], create: bool) -> str | None:
        pid = self.root_id
        for part in parts:
            name = safe_dirname(part)
            cid = self._child_folder(pid, name)
            if cid is None and create:
                cid = self._child_folder(pid, name, fresh=True)   # someone else may have made it since we looked
            if cid is None:
                if not create:
                    return None
                meta = {"name": name, "mimeType": FOLDER, "parents": [pid]}
                cid = self.svc.files().create(body=meta, fields="id", **COMMON).execute(num_retries=3)["id"]
                self._folders[(pid, name)] = cid
                self._listed.add(cid)                              # new, so no subfolders yet
            pid = cid
        return pid

    def _inbox_id(self) -> str | None:
        if self.inbox in ("", "."):
            return self.root_id
        return self._resolve([self.inbox], create=False)

    # -- Storage interface -------------------------------------------------
    def list_inbox(self) -> list[Item]:
        return self._media(self._inbox_id())

    def list_folder(self, path_parts: list[str]) -> list[Item]:
        return self._media(self._resolve(path_parts, create=False))

    def folder_id(self, path_parts: list[str]) -> str | None:
        return self._resolve(path_parts, create=False)

    def ensure_folder(self, path_parts: list[str]) -> str:
        return self._resolve(path_parts, create=True)

    def list_examples(self, path_parts: list[str]) -> list[Item]:
        return self._media(self._resolve(path_parts + [self.training_subfolder], create=False))

    def read_bytes(self, item: Item, prefer: str = "thumb") -> bytes:
        if prefer == "thumb":
            link = item.extra.get("thumbnailLink")
            if link:
                try:
                    return self._fetch(re.sub(r"=s\d+(-c)?$", "=s640", link))
                except Exception:  # noqa: BLE001 - expired/missing thumb: fall back to the original
                    pass
            if item.is_video:
                raise NoPreviewError("Drive has no poster frame for this video yet")
        return self.svc.files().get_media(fileId=item.id, **COMMON).execute(num_retries=3)

    def _fetch(self, url: str) -> bytes:
        headers = {}
        if self.creds is not None:
            if getattr(self.creds, "expired", False) and getattr(self.creds, "refresh_token", None):
                from google.auth.transport.requests import Request

                self.creds.refresh(Request())
            headers["Authorization"] = f"Bearer {self.creds.token}"
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read()

    def move_rename(self, item: Item, folder_id: str, new_name: str) -> Item:
        prev = item.parent or self.svc.files().get(fileId=item.id, fields="parents", **COMMON).execute().get("parents", [None])[0]
        kwargs = {"fileId": item.id, "body": {"name": new_name}, "addParents": folder_id, "fields": FILE_FIELDS, **COMMON}
        if prev and prev != folder_id:
            kwargs["removeParents"] = prev
        f = self.svc.files().update(**kwargs).execute(num_retries=5)
        moved = _item(f)
        moved.md5 = moved.md5 or item.md5
        return moved

    def _file_id(self, parent_id: str | None, name: str) -> str | None:
        if not parent_id:
            return None
        key = (parent_id, name)
        if key not in self._file_ids:
            hits = self._list(f"'{_q(parent_id)}' in parents and trashed=false and name='{_q(name)}'")
            if not hits:
                return None
            self._file_ids[key] = hits[0]["id"]
        return self._file_ids[key]

    def _read_file(self, parent_id: str | None, name: str) -> bytes | None:
        fid = self._file_id(parent_id, name)
        return self.svc.files().get_media(fileId=fid, **COMMON).execute(num_retries=3) if fid else None

    def _write_file(self, parent_id: str, name: str, data: bytes, mime: str) -> None:
        from googleapiclient.http import MediaIoBaseUpload

        media = MediaIoBaseUpload(io.BytesIO(data), mimetype=mime, resumable=False)
        fid = self._file_id(parent_id, name)
        if fid:
            self.svc.files().update(fileId=fid, media_body=media, **COMMON).execute(num_retries=3)
        else:
            body = {"name": name, "parents": [parent_id]}
            created = self.svc.files().create(body=body, media_body=media, fields="id", **COMMON).execute(num_retries=3)
            self._file_ids[(parent_id, name)] = created["id"]

    def read_meta(self, name: str) -> bytes | None:
        return self._read_file(self._resolve([META_DIR], create=False), name)

    def write_meta(self, name: str, data: bytes) -> None:
        self._write_file(self._resolve([META_DIR], create=True), name, data, "application/octet-stream")

    def read_root_file(self, name: str) -> bytes | None:
        return self._read_file(self.root_id, name)

    def write_root_file(self, name: str, data: bytes) -> None:
        self._write_file(self.root_id, name, data, "text/markdown")

    def folder_tree(self, max_depth: int = 3) -> list[list[str]]:
        out: list[list[str]] = []

        def walk(pid: str, parts: list[str]) -> None:
            if len(parts) >= max_depth:
                return
            for f in self._children_folders(pid):
                if f["name"].startswith(".") or f["name"] == self.training_subfolder:
                    continue
                self._folders.setdefault((pid, f["name"]), f["id"])
                out.append(parts + [f["name"]])
                walk(f["id"], parts + [f["name"]])

        walk(self.root_id, [])
        return out

    def append_meta(self, name: str, data: bytes) -> None:
        """Drive files cannot be appended to: download, add the lines, upload. Callers batch their appends."""
        self.write_meta(name, (self.read_meta(name) or b"") + data)

    def write_file(self, path_parts: list[str], name: str, data: bytes) -> Item:
        from googleapiclient.http import MediaIoBaseUpload

        fid = self.ensure_folder(path_parts)
        name = unique_name({i.name for i in self._media(fid)}, safe_filename(name))
        media = MediaIoBaseUpload(io.BytesIO(data), mimetype=guess_mime(name) or "application/octet-stream", resumable=False)
        f = self.svc.files().create(body={"name": name, "parents": [fid]}, media_body=media, fields=FILE_FIELDS, **COMMON).execute(num_retries=3)
        return _item(f)

    def trash(self, item: Item) -> None:
        """Drive's own trash: recoverable for 30 days."""
        self.svc.files().update(fileId=item.id, body={"trashed": True}, **COMMON).execute(num_retries=3)

    def md5_of(self, item: Item) -> str:
        if not item.md5:
            item.md5 = hashlib.md5(self.read_bytes(item, prefer="original")).hexdigest()
        return item.md5
