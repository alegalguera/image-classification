"""GoogleDriveStorage against an in-memory fake of the Drive v3 client surface we use."""
import pytest

from photo_sorter.items import Item
from photo_sorter.manifest import Manifest
from photo_sorter.sorter import Sorter
from photo_sorter.storage.drive import FOLDER, GoogleDriveStorage, folder_id_from

from conftest import RED, make_image
from test_core import ConstClassifier


class _Call:
    def __init__(self, fn):
        self.fn = fn

    def execute(self, num_retries=0):
        return self.fn()


class FakeFiles:
    def __init__(self, store: "FakeDrive"):
        self.s = store

    def get(self, fileId, fields=None, **kw):
        return _Call(lambda: dict(self.s.store[fileId]))

    def list(self, q="", fields=None, pageToken=None, orderBy=None, pageSize=1000, **kw):
        def run():
            import re
            parent = re.search(r"'([^']+)' in parents", q).group(1)
            hits = [f for f in self.s.store.values() if parent in f.get("parents", []) and not f.get("trashed")]
            if f"mimeType='{FOLDER}'" in q:
                hits = [f for f in hits if f["mimeType"] == FOLDER]
            elif "mimeType contains 'image/'" in q:
                hits = [f for f in hits if f["mimeType"].startswith(("image/", "video/"))]
            m = re.search(r"name='((?:[^'\\]|\\.)*)'", q)
            if m:
                hits = [f for f in hits if f["name"] == m.group(1).replace("\\'", "'")]
            hits.sort(key=lambda f: f["name"])
            page = int(pageToken or 0)
            chunk = hits[page * 2:(page + 1) * 2]  # tiny pages to exercise pagination
            resp = {"files": [dict(f) for f in chunk]}
            if (page + 1) * 2 < len(hits):
                resp["nextPageToken"] = str(page + 1)
            return resp
        return _Call(run)

    def create(self, body=None, fields=None, media_body=None, **kw):
        def run():
            fid = f"id{len(self.s.store) + 1}"
            mime = body.get("mimeType") or getattr(media_body, "_mimetype", None) or "application/octet-stream"
            f = {"id": fid, "trashed": False, **body, "mimeType": mime}
            if media_body is not None:
                f["_data"] = media_body._fd.getvalue()
            self.s.store[fid] = f
            return dict(f)          # like Drive, return the requested fields
        return _Call(run)

    def update(self, fileId, body=None, addParents=None, removeParents=None, media_body=None, fields=None, **kw):
        def run():
            f = self.s.store[fileId]
            if body:
                f.update(body)
            if addParents:
                f["parents"] = [p for p in f.get("parents", []) if p != removeParents] + [addParents]
            if media_body is not None:
                f["_data"] = media_body._fd.getvalue()
            return dict(f)
        return _Call(run)

    def get_media(self, fileId, **kw):
        return _Call(lambda: self.s.store[fileId]["_data"])


class FakeDrive:
    def __init__(self):
        self.store: dict[str, dict] = {}
        self.add_folder("root", "Lab Photos", parent=None)

    def add_folder(self, fid, name, parent="root"):
        self.store[fid] = {"id": fid, "name": name, "mimeType": FOLDER, "parents": [parent] if parent else [], "trashed": False}
        return fid

    def add_file(self, fid, name, parent, data=b"", mime="image/jpeg", md5=None):
        self.store[fid] = {"id": fid, "name": name, "mimeType": mime, "parents": [parent], "trashed": False,
                           "_data": data, "md5Checksum": md5, "size": str(len(data)), "createdTime": "2026-10-01T10:00:00Z"}
        return fid

    def files_api(self):
        return FakeFiles(self)

    # the googleapiclient "service" shape used by the backend
    def files(self):
        return self.files_api()

    def names_in(self, folder_id):
        return sorted(f["name"] for f in self.store.values() if folder_id in f.get("parents", []))


@pytest.fixture
def drive():
    d = FakeDrive()
    d.add_folder("inbox", "Inbox")
    d.add_folder("red", "Photos - Red")
    for i in range(5):  # 5 files -> 3 pages of 2
        d.add_file(f"r{i}", f"red_00{i + 1}.jpg", "red", make_image(RED), md5=f"m{i}")
    d.add_file("a", "IMG_1.JPG", "inbox", make_image(RED), md5="aaa")
    d.add_file("b", "clip.mov", "inbox", b"\x00", mime="video/quicktime", md5="bbb")
    return d


def test_folder_id_from():
    assert folder_id_from("https://drive.google.com/drive/folders/1AbC_def-9?usp=sharing") == "1AbC_def-9"
    assert folder_id_from("https://drive.google.com/open?id=1AbC_def-9") == "1AbC_def-9"
    assert folder_id_from("1AbC_def-9xyz") == "1AbC_def-9xyz"
    with pytest.raises(ValueError):
        folder_id_from("https://example.com/")


def test_listing_pagination_and_find_or_create(drive):
    st = GoogleDriveStorage(drive, "root")
    assert st.root_label == "Drive: Lab Photos"
    assert [i.name for i in st.list_inbox()] == ["IMG_1.JPG", "clip.mov"]
    assert len(st.list_folder(["Photos - Red"])) == 5  # across 3 pages
    assert st.folder_id(["Nope"]) is None
    fid = st.ensure_folder(["Photos - Coral", "Outplants", "APAL"])
    assert st.ensure_folder(["Photos - Coral", "Outplants", "APAL"]) == fid  # idempotent
    assert drive.names_in("root").count("Photos - Coral") == 1
    assert st.list_examples(["Photos - Red"]) == []


def test_file_and_undo_on_drive(drive, cfg):
    st = GoogleDriveStorage(drive, "root")
    sorter = Sorter(cfg, st, ConstClassifier("Photos - Red"))
    preds = sorter.predict(sorter.scan())
    photo = next(p for p in preds if not p.item.is_video)
    video = next(p for p in preds if p.item.is_video)
    assert video.verdict.category == "Review" and "no preview" in video.verdict.reason
    rec = sorter.file(photo)
    assert rec["new_name"] == "red_006.jpg" and drive.store["a"]["parents"] == ["red"] and drive.store["a"]["name"] == "red_006.jpg"
    assert rec["md5"] == "aaa"
    # manifest was written into .sorter inside Drive
    meta = st.folder_id([".sorter"])
    assert meta and "manifest.jsonl" in drive.names_in(meta)
    assert len(Manifest(st).active_moves()) == 1
    sorter.undo_last()
    assert drive.store["a"]["parents"] == ["inbox"] and drive.store["a"]["name"] == "IMG_1.JPG"


def test_md5_fallback_and_meta_roundtrip(drive):
    st = GoogleDriveStorage(drive, "root")
    drive.add_file("c", "nomd5.png", "inbox", make_image(RED), mime="image/png", md5=None)
    item = next(i for i in st.list_inbox() if i.name == "nomd5.png")
    assert len(st.md5_of(item)) == 32
    st.write_meta("x.bin", b"123")
    st.write_meta("x.bin", b"4567")  # update, not duplicate
    assert st.read_meta("x.bin") == b"4567"
    assert drive.names_in(st.folder_id([".sorter"])).count("x.bin") == 1
    st.append_meta("log.jsonl", b"a\n")
    st.append_meta("log.jsonl", b"b\n")
    assert st.read_meta("log.jsonl") == b"a\nb\n"


def test_one_listing_per_folder_and_no_duplicate_folders(drive):
    st = GoogleDriveStorage(drive, "root")
    listed, children = [], st._children_folders
    st._children_folders = lambda pid: listed.append(pid) or children(pid)
    for name in ["Photos - Red", "Inbox", "Nope", "Photos - Red"]:
        st.folder_id([name])
    assert listed == ["root"]                                   # was one listing per lookup
    drive.add_folder("late", "Nope")                            # someone else made it after we looked
    assert st.ensure_folder(["Nope"]) == "late" and drive.names_in("root").count("Nope") == 1


def test_drive_write_file_and_trash(drive):
    st = GoogleDriveStorage(drive, "root")
    a = st.write_file(["Photos - Red", "_training_data"], "ex.jpg", make_image(RED))
    b = st.write_file(["Photos - Red", "_training_data"], "ex.jpg", make_image(RED))
    assert a.name == "ex.jpg" and b.name == "ex_2.jpg"
    assert [i.name for i in st.list_examples(["Photos - Red"])] == ["ex.jpg", "ex_2.jpg"]
    st.trash(a)
    assert [i.name for i in st.list_examples(["Photos - Red"])] == ["ex_2.jpg"] and drive.store[a.id]["trashed"] is True


def test_root_files_and_folder_tree(drive):
    st = GoogleDriveStorage(drive, "root")
    assert st.read_root_file("CATEGORIES.md") is None
    st.write_root_file("CATEGORIES.md", b"- Coral\n")
    st.write_root_file("CATEGORIES.md", b"- Coral\n- Sponge\n")            # update, not a second file
    assert st.read_root_file("CATEGORIES.md") == b"- Coral\n- Sponge\n" and drive.names_in("root").count("CATEGORIES.md") == 1
    st.ensure_folder(["Photos - Red", "_training_data"])
    st.ensure_folder(["Photos - Coral", "Outplants"])
    assert st.folder_tree() == [["Inbox"], ["Photos - Coral"], ["Photos - Coral", "Outplants"], ["Photos - Red"]]
