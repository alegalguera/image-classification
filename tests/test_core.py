from pathlib import Path

import pytest

from photo_sorter.classifiers import Verdict
from photo_sorter.config import Config, load_config, split_path
from photo_sorter.items import Item
from photo_sorter.manifest import Manifest
from photo_sorter.naming import Counters, build_name, max_counter, safe_dirname, slugify
from photo_sorter.sorter import Sorter
from photo_sorter.storage import NoPreviewError
from photo_sorter.storage.local import LocalFolderStorage

from conftest import BLUE, GREEN, RED, make_image


def test_slug_and_names():
    assert slugify("APAL") == "apal"
    assert slugify("Sponge Disease/Bleaching") == "sponge_disease_bleaching"
    assert slugify("Staff and Interns") == "staff_and_interns"
    assert slugify("Photos - Fragging") == "fragging"
    assert slugify("Videos - Coral") == "coral"
    assert safe_dirname("Sponge Disease/Bleaching") == "Sponge Disease-Bleaching"
    assert build_name("coral", 7, ".JPG") == "coral_007.jpg"
    assert build_name("coral", 1234, ".heic") == "coral_1234.heic"


def test_counter_continues_from_existing():
    names = ["apal_001.jpg", "apal_007.heic", "APAL_003.jpg", "other_020.jpg", "apal_x.jpg", "notes.txt"]
    assert max_counter(names, "apal") == 7
    c = Counters()
    c.seed("f1", "apal", names)
    assert c.take("f1", "apal") == 8
    assert c.take("f1", "apal") == 9
    assert c.take("f1", "other") == 1  # unseeded slug starts at 1


class ConstClassifier:
    def __init__(self, category, confidence=0.9):
        self.category, self.confidence = category, confidence

    def classify(self, image, mime, categories, is_video):
        return Verdict(self.category, self.confidence, "const", scores={self.category: self.confidence}, method="const")


def write(root: Path, rel: str, data: bytes) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def test_local_storage_listing_excludes_meta_and_training(root):
    write(root, "Inbox/a.jpg", make_image(RED))
    write(root, "Inbox/.hidden.jpg", make_image(RED))
    write(root, "Inbox/notes.txt", b"x")
    write(root, "Inbox/pic.AVIF", make_image(RED))  # extension counts even if the bytes are a jpeg
    write(root, "Photos - Red/red_001.jpg", make_image(RED))
    write(root, "Photos - Red/_training_data/ex1.jpg", make_image(RED))
    write(root, ".sorter/manifest.jsonl", b"")
    st = LocalFolderStorage(root)
    assert [i.name for i in st.list_inbox()] == ["a.jpg", "pic.AVIF"]
    assert st.skipped == ["notes.txt"]
    assert [i.name for i in st.list_folder(["Photos - Red"])] == ["red_001.jpg"]
    assert [i.name for i in st.list_examples(["Photos - Red"])] == ["ex1.jpg"]
    assert st.folder_id(["Photos - Red"]) == "Photos - Red"
    assert st.folder_id(["Nope"]) is None


def test_local_thumb_and_video_preview(root):
    p = write(root, "Inbox/big.png", make_image(BLUE, size=(1600, 1200), fmt="PNG"))
    st = LocalFolderStorage(root)
    item = st.list_inbox()[0]
    from PIL import Image
    import io
    thumb = Image.open(io.BytesIO(st.read_bytes(item, "thumb")))
    assert max(thumb.size) == 640 and thumb.format == "JPEG"
    assert st.read_bytes(item, "original") == p.read_bytes()
    write(root, "Inbox/clip.mp4", b"\x00" * 10)
    vid = [i for i in st.list_inbox() if i.is_video][0]
    with pytest.raises(NoPreviewError):
        st.read_bytes(vid, "thumb")


def test_file_then_undo_roundtrip(root, cfg):
    write(root, "Inbox/IMG_1.JPG", make_image(RED))
    write(root, "Photos - Red/red_004.jpg", make_image(RED))  # existing numbering
    st = LocalFolderStorage(root)
    sorter = Sorter(cfg, st, ConstClassifier("Photos - Red"))
    preds = sorter.predict(sorter.scan())
    assert len(preds) == 1 and preds[0].confident
    planned = sorter.file(preds[0], dry_run=True)
    assert planned["new_name"] == "red_005.jpg" and planned["new_parent"] == "Photos - Red"
    assert (root / "Inbox/IMG_1.JPG").exists()  # dry run moved nothing
    rec = sorter.file(preds[0])
    assert (root / "Photos - Red/red_005.jpg").exists() and not (root / "Inbox/IMG_1.JPG").exists()
    assert rec["md5"] and rec["orig_name"] == "IMG_1.JPG" and rec["decided_by"] == "auto"
    # manifest persisted and reloadable
    m2 = Manifest(st)
    assert len(m2.active_moves()) == 1 and m2.filed_categories()[rec["md5"]] == "Photos - Red"
    # undo restores the exact original path and name
    sorter.undo_last(1)
    assert (root / "Inbox/IMG_1.JPG").exists() and not (root / "Photos - Red/red_005.jpg").exists()
    assert Manifest(st).active_moves() == []


def test_manifest_only_appends_and_survives_a_torn_line(root, cfg):
    for n in "abc":
        write(root, f"Inbox/{n}.jpg", make_image(RED))
    st = LocalFolderStorage(root)
    sorter = Sorter(cfg, st, ConstClassifier("Photos - Red"))
    preds = sorter.predict(sorter.scan())
    writes, append = [], st.append_meta
    st.write_meta = lambda *a: writes.append(a)                     # the manifest must never be rewritten whole
    st.append_meta = lambda name, data: writes.append(name) or append(name, data)
    sorter.file_all(preds)
    assert writes == ["manifest.jsonl"]                              # one write for the whole batch
    path = root / ".sorter/manifest.jsonl"
    assert len(path.read_text().splitlines()) == 3
    path.write_bytes(path.read_bytes() + b'{"op": "mo')            # a crash cut the last line short
    m = Manifest(st)
    assert len(m.active_moves()) == 3
    m.append({"op": "undo", "ref": m.active_moves()[0]["id"], "item_id": "x"})
    assert len(Manifest(st).active_moves()) == 2                     # the record after the torn line is intact


def test_review_folder_listed_once(root, cfg):
    write(root, "Review/a.jpg", make_image(BLUE))
    for n in "ab":
        write(root, f"Inbox/{n}.jpg", make_image(BLUE))
    st = LocalFolderStorage(root)
    sorter = Sorter(cfg, st, ConstClassifier("Photos - Blue", 0.01))
    listed, list_folder = [], st.list_folder
    st.list_folder = lambda parts: listed.append(parts) or list_folder(parts)
    recs = [sorter.file(p) for p in sorter.predict(sorter.scan())]
    assert [r["new_name"] for r in recs] == ["a_2.jpg", "b.jpg"] and listed == [["Review"]]
    sorter.undo_last(1)                                              # b.jpg leaves Review: its name is free again
    [p] = [p for p in sorter.predict(sorter.scan())]
    assert sorter.file(p)["new_name"] == "b.jpg" and listed == [["Review"]]


def test_nested_category_creates_folders_and_user_override(root, cfg):
    cfg.categories["Photos - Coral › Outplants › APAL"] = "elkhorn"
    write(root, "Inbox/x.heic", make_image(GREEN))
    st = LocalFolderStorage(root)
    sorter = Sorter(cfg, st, ConstClassifier("Photos - Green", 0.05))  # below threshold
    [pred] = sorter.predict(sorter.scan())
    assert not pred.confident and pred.target == "Review"
    rec = sorter.file(pred, category="Photos - Coral › Outplants › APAL", decided_by="user")
    assert (root / "Photos - Coral/Outplants/APAL/apal_001.heic").exists()
    assert rec["decided_by"] == "user"


def test_review_move_keeps_original_name_and_keep_mode(root, cfg):
    write(root, "Inbox/low.jpg", make_image(BLUE))
    st = LocalFolderStorage(root)
    sorter = Sorter(cfg, st, ConstClassifier("Photos - Blue", 0.01))
    [pred] = sorter.predict(sorter.scan())
    rec = sorter.file(pred)
    assert rec["new_name"] == "low.jpg" and (root / "Review/low.jpg").exists()
    cfg.review_action = "keep"
    write(root, "Inbox/low2.jpg", make_image(BLUE))
    [pred2] = sorter.predict(sorter.scan())
    assert sorter.file(pred2) is None and (root / "Inbox/low2.jpg").exists()


def test_config_json_source_and_defaults(tmp_path):
    (tmp_path / "CATEGORIES.md").write_text("# T\n\n- 📁 Photos - Coral\n  - ✅ APAL: elkhorn\n- Videos - Coral\n- KML (ignored)\n")
    (tmp_path / "c.json").write_text('{"categories_file": "CATEGORIES.md", "source": {"kind": "local", "root": "/tmp"}, "embeddings": {"knn_weight": 0.3}}')
    cfg = load_config(tmp_path / "c.json")
    assert cfg.provider == "siglip" and cfg.threshold == 0.15 and cfg.embeddings["knn_weight"] == 0.3
    assert cfg.embeddings["knn_k"] == 3 and cfg.source_kind == "local"
    assert split_path(next(iter(cfg.categories))) == ["Photos - Coral", "APAL"]
    assert cfg.video_categories == {"Videos - Coral": "Videos - Coral"}
    # legacy album key still works
    (tmp_path / "d.json").write_text('{"categories_file": "CATEGORIES.md", "album": "https://photos.icloud.com/shared/album/X/", "model": {"provider": "claude"}}')
    cfg2 = load_config(tmp_path / "d.json")
    assert cfg2.source_kind == "icloud" and cfg2.album.endswith("/X/") and cfg2.provider == "claude" and cfg2.threshold == 0.7


def test_refile_chain_and_one_picture_one_category(root, cfg):
    from photo_sorter import examples

    cfg.categories["Photos - Coral"] = "coral"
    data = make_image(RED)
    write(root, "Inbox/x.jpg", data)
    st = LocalFolderStorage(root)
    sorter = Sorter(cfg, st, ConstClassifier("Photos - Red"))
    [pred] = sorter.predict(sorter.scan())
    first = sorter.file(pred)                                         # wrongly filed into Red
    # the same picture was also taught as an example of Red
    ex, status = examples.add(st, cfg, "Photos - Red", "x.jpg", data, {"source": "test"})
    assert status == "added" and (root / "Photos - Red/_training_data/x.jpg").exists()
    assert examples.add(st, cfg, "Photos - Red", "again.jpg", data, {})[1] == "duplicate"
    # re-file the photo into Coral: recorded as a second move, example follows the picture
    moved = Item(id=first["new_id"], name=first["new_name"], parent=first["new_parent"])
    second = sorter.move(moved, "Photos - Coral")
    assert (root / "Photos - Coral/coral_001.jpg").exists() and not (root / "Photos - Red/red_001.jpg").exists()
    assert examples.assign(st, cfg, second["md5"], "Photos - Coral") == ["Photos - Red"]
    assert (root / "Photos - Coral/_training_data/x.jpg").exists() and not (root / "Photos - Red/_training_data/x.jpg").exists()
    # history shows only the latest step; undo walks back one step at a time
    m = Manifest(st)
    assert [r["category"] for r in m.active_moves()] == ["Photos - Coral"]
    sorter.undo_last(1)
    assert (root / "Photos - Red/red_001.jpg").exists()
    assert [r["category"] for r in Manifest(st).active_moves()] == ["Photos - Red"]
    # teaching the same picture to a third category moves the example there instead of copying it
    cfg.categories["Photos - Blue"] = "blue"
    assert examples.add(st, cfg, "Photos - Blue", "x.jpg", data, {})[1] == "moved"
    assert [c for c, _ in examples.copies(st, cfg, second["md5"])] == ["Photos - Blue"]
