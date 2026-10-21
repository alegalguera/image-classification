import json
import re

import pytest
from fastapi.testclient import TestClient

from photo_sorter import cli
from photo_sorter.config import load_config
from photo_sorter.sorter import Sorter
from photo_sorter.web.app import create_app

from conftest import GREEN, RED, make_image
from test_core import ConstClassifier


def rows(html: str, kind: str) -> int:
    return len(re.findall(r'<tr class="%s"[ >]' % kind, html))


def build(root, tmp_path, monkeypatch, n=70, cats="- Photos - Red: red things\n- Photos - Green\n- Photos - Blue\n"):
    monkeypatch.setenv("PHOTO_SORTER_SYNC", "1")
    (tmp_path / "CATEGORIES.md").write_text("# Categories\n\n" + cats)
    cfgp = tmp_path / "c.json"
    cfgp.write_text(json.dumps({"categories_file": "CATEGORIES.md", "source": {"kind": "local", "root": str(root)}, "min_confidence": 0.1}))
    for i in range(n):
        (root / "Inbox" / f"img_{i:03d}.jpg").write_bytes(make_image(RED if i % 2 else GREEN))

    seen: dict = {}

    from photo_sorter import metering

    class Clf:
        meter = metering.Meter(info={"provider": "Fake", "model": "fake-model", "version": "abc1234", "where": "this computer · CPU", "local": True})

        def meters(self):
            return [self.meter]

        def classify(self, image, mime, categories, is_video):
            self.meter.add(images_embedded=1, image_tokens=196, model_secs=0.01, flops=3.4e10)
            seen["cats"] = list(categories)
            import io

            from PIL import Image

            from photo_sorter.classifiers import Verdict
            r, g, b = Image.open(io.BytesIO(image)).convert("RGB").resize((1, 1)).getpixel((0, 0))
            cat = "Photos - Red" if r > g else "Photos - Green"
            conf = 0.9 if cat in categories else 0.0
            cat = cat if cat in categories else next(iter(categories), "Review")
            if cat == "Photos - Green":
                conf = 0.05  # green is "unsure"
            return Verdict(cat, conf, "fake reason", scores={cat: conf, "Photos - Blue": 0.01})

    built = {"n": 0, "seen": seen}

    def fake_build_sorter(c, storage=None, log=print, lazy=False):
        from photo_sorter.manifest import Manifest
        storage = storage or cli.open_storage_for(c)
        return Sorter(c, storage, None if lazy else Clf(), Manifest(storage))

    def fake_build_classifier(c, storage, manifest=None, log=print):
        built["n"] += 1
        return Clf()

    monkeypatch.setattr(cli, "build_sorter", fake_build_sorter)
    monkeypatch.setattr(cli, "build_classifier", fake_build_classifier)
    c = TestClient(create_app(load_config(cfgp)), follow_redirects=False)
    assert c.get("/login").status_code == 303
    return c, built, tmp_path


@pytest.fixture
def setup(root, tmp_path, monkeypatch):
    c, built, tmp = build(root, tmp_path, monkeypatch)
    return c, root, tmp, built


def test_first_view_is_a_pile_and_nothing_runs_until_sort(setup):
    c, root, _, built = setup
    r = c.get("/")
    assert r.status_code == 200 and "70 unsorted images" in r.text and "Sort 70 images" in r.text
    assert 'class="pile big"' in r.text and r.text.count('<img src="/thumb/') >= 4
    assert 'id="status"' not in r.text and "pbar" not in r.text.split("<main>")[1]   # no progress bar when idle
    assert built["n"] == 0                                                          # model not loaded
    # clicking the pile lists every unsorted image, 100 at a time
    d = c.get("/pile?key=unsorted")
    assert rows(d.text, "unsorted") == 70 and "Show more" not in d.text and 'data-triage="unsorted"' in d.text
    assert c.post("/classify").status_code == 303 and built["n"] == 1
    r = c.get("/")
    assert "Sorted 70 images" in r.text and "35 ready to file · 35 need your decision" in r.text
    assert 'data-pile="review"' in r.text and 'data-pile="Photos - Red"' in r.text and "unsorted image" not in r.text
    c.post("/scan"); c.post("/classify")
    assert built["n"] == 1                                                          # loaded once per session


def test_status_panel_only_while_running_and_refresh_when_done(setup):
    c, root, _, _ = setup
    assert c.get("/status?was=1").headers.get("hx-refresh") == "true"   # idle -> tell the page to reload
    r = c.get("/status")
    assert 'id="status"' in r.text and "Looking at images" in r.text


def test_piles_lists_and_file_one_with_correction(setup):
    c, root, _, _ = setup
    c.post("/classify")
    d = c.get("/pile?key=review")
    assert rows(d.text, "review") == 35 and 'class="chip"' in d.text and "Needs review" in d.text
    item_id = re.search(r'name="item_id" value="([^"]+)"', d.text).group(1)
    r = c.post("/file", data={"item_id": item_id, "category": "Photos - Blue"})
    assert r.status_code == 200 and "blue_001.jpg" in r.text and "done-row" in r.text
    assert "board-changed" in r.headers.get("hx-trigger", "")
    d = c.get("/pile?key=Photos - Red")
    assert rows(d.text, "cat") == 35 and "File all 35 into Photos - Red" in d.text


def test_file_pile_toast_undo_and_filed_pile(setup):
    c, root, _, _ = setup
    c.post("/classify")
    r = c.post("/file-all", data={"cat": "Photos - Red"})
    assert "Filed 35 images" in r.text and (root / "Photos - Red" / "red_035.jpg").exists()
    assert "board-changed" in r.headers["hx-trigger"] and "detail-changed" in r.headers["hx-trigger"]
    ids = json.loads(re.search(r"hx-vals='([^']+)'", r.text).group(1))["id"]
    assert len(ids) == 35
    b = c.get("/board").text
    assert 'data-pile="filed"' in b and 'data-pile="Photos - Red"' not in b
    f = c.get("/pile?key=filed")
    assert rows(f.text, "filed") == 35 and "red_001.jpg" in f.text
    u = c.post("/undo", data={"id": ids[:2]}, headers={"HX-Request": "true"})
    assert u.status_code == 204 and u.headers["hx-refresh"] == "true"
    assert len(list((root / "Photos - Red").glob("*.jpg"))) == 33
    page = c.get("/").text                                               # undone images go back to their pile, suggestion intact
    assert "unsorted image" not in page and re.search(r'data-pile="Photos - Red">.*?class="badge">2<', page, re.S)


def test_show_more_pagination(root, tmp_path, monkeypatch):
    c, _, _ = build(root, tmp_path, monkeypatch, n=230)
    d = c.get("/pile?key=unsorted")
    assert rows(d.text, "unsorted") == 100 and "Show more (130 left)" in d.text
    more = c.get("/pile?key=unsorted&offset=100")
    assert rows(more.text, "unsorted") == 100 and "Show more (30 left)" in more.text and 'id="detail"' not in more.text


def test_empty_inbox_guide(root, tmp_path, monkeypatch):
    c, _, _ = build(root, tmp_path, monkeypatch, n=0)
    (root / "Inbox" / "notes.txt").write_text("x")
    c.post("/scan")
    r = c.get("/")
    assert "No new photos to sort." in r.text and str(root / "Inbox") in r.text and "Copy path" in r.text
    assert "notes.txt" in r.text and "Sort" in r.text and 'class="pile big"' not in r.text
    import shutil
    shutil.rmtree(root / "Inbox")
    c.post("/scan")
    assert "does not exist yet" in c.get("/").text
    c.post("/create-inbox")
    assert (root / "Inbox").is_dir()


def test_history_and_single_undo(setup):
    c, root, _, _ = setup
    c.post("/classify")
    c.post("/file-selected", data={"sel": ["Inbox/img_001.jpg", "Inbox/img_003.jpg"], "bulk_category": ""})
    h = c.get("/history")
    assert h.text.count(">auto<") == 2 and "Activity this session" in h.text and "filed 2 selected" in h.text
    rid = re.search(r'name="id" value="([^"]+)"', h.text).group(1)
    assert c.post("/undo", data={"id": rid}).status_code == 303
    assert len(list((root / "Inbox").glob("*.jpg"))) == 69


def test_categories_crud_and_predictions_cleared(setup):
    c, root, tmp_path, _ = setup
    c.post("/classify")
    r = c.get("/categories")
    assert r.status_code == 200 and "Photos - Red" in r.text and "red things" in r.text
    assert "added" in c.post("/categories/add", data={"parent": "", "name": "Divers", "description": "people underwater"}).headers["location"]
    assert "added" in c.post("/categories/add", data={"parent": "Divers", "name": "Training"}).headers["location"]
    assert "saved" in c.post("/categories/update", data={"key": "Photos - Green", "name": "Photos - Verde", "description": "green"}).headers["location"]
    assert "removed" in c.post("/categories/delete", data={"key": "Photos - Blue"}).headers["location"]
    text = (tmp_path / "CATEGORIES.md").read_text()
    assert "- Divers: people underwater\n  - Training" in text and "Photos - Verde: green" in text and "Blue" not in text
    assert c.post("/categories/add", data={"parent": "", "name": "divers"}).headers["location"].startswith("/categories?err=")
    page = c.get("/")
    # Green was renamed and Blue removed: their suggestions go back to the inbox, Red keeps its 35 and is flagged stale
    assert "35 unsorted images" in page.text and 'data-pile="Photos - Red"' in page.text and "Your categories changed" in page.text


def test_no_categories_blocks_sort(root, tmp_path, monkeypatch):
    c, _, _ = build(root, tmp_path, monkeypatch, n=1, cats="")
    r = c.get("/")
    assert "No categories yet" in r.text and re.search(r'<button class="go big"\s+disabled', r.text)


def test_logo_and_favicon(setup):
    c, root, _, _ = setup
    r = c.get("/")
    assert 'class="logo-svg"' in r.text and 'class="lg-label"' in r.text and 'rel="icon"' in r.text
    f = c.get("/favicon.svg")
    assert f.status_code == 200 and f.headers["content-type"].startswith("image/svg+xml") and "<svg" in f.text


def test_redirects_and_thumb(setup):
    c, root, _, _ = setup
    assert c.get("/review").headers["location"] == "/?open=review"
    assert c.get("/summary").headers["location"] == "/"
    r = c.get("/?open=unsorted")
    assert rows(r.text, "unsorted") == 70
    item_id = re.search(r'/thumb/([^"]+)"', r.text).group(1)
    t = c.get(f"/thumb/{item_id}")
    assert t.status_code == 200 and t.content[:2] == b"\xff\xd8"


def test_category_outliner_htmx(setup):
    c, root, tmp_path, _ = setup
    hx = {"HX-Request": "true"}
    page = c.get("/categories")
    assert 'id="tree"' in page.text and "+ add a category" in page.text and 'class="tb add"' in page.text
    r = c.post("/categories/add", data={"parent": "", "name": "Coral", "description": "hard coral"}, headers=hx)
    assert r.status_code == 200 and "✓ added Coral" in r.text and r.text.count('class="node') == 4 and "data-refocus" in r.text
    r = c.post("/categories/add", data={"parent": "Coral", "name": "Bleaching"}, headers=hx)
    assert "added Bleaching under Coral" in r.text and 'class="node folder"' in r.text
    r = c.post("/categories/indent", data={"key": "Photos - Green"}, headers=hx)
    assert "now under Photos - Red" in r.text and "↶ Undo" in r.text
    assert "- Photos - Red: red things\n  - Photos - Green" in (tmp_path / "CATEGORIES.md").read_text()
    r = c.post("/categories/undo", headers=hx)
    assert "undone" in r.text and "- Photos - Red: red things\n- Photos - Green" in (tmp_path / "CATEGORIES.md").read_text()
    r = c.post("/categories/delete", data={"key": "Coral"}, headers=hx)
    assert "removed Coral and 1 sub-category" in r.text and "Coral" not in (tmp_path / "CATEGORIES.md").read_text()
    c.post("/categories/undo", headers=hx)
    assert "- Coral: hard coral\n  - Bleaching" in (tmp_path / "CATEGORIES.md").read_text()
    r = c.post("/categories/update", data={"key": "Coral › Bleaching", "name": "Pale", "description": "white coral"}, headers=hx)
    assert "✓ saved" in r.text and "  - Pale: white coral" in (tmp_path / "CATEGORIES.md").read_text()
    r = c.post("/categories/add", data={"parent": "", "name": "coral"}, headers=hx)
    assert "✕" in r.text and "already exists" in r.text


def test_categories_edited_on_disk_are_picked_up_without_restart(setup):
    c, root, tmp_path, built = setup
    c.post("/classify")
    assert built["seen"]["cats"] == ["Photos - Red", "Photos - Green", "Photos - Blue"]
    with (tmp_path / "CATEGORIES.md").open("a") as f:
        f.write("- Photos - Teal: teal things\n")
    r = c.get("/")
    assert "Your categories changed" in r.text and "Re-sort all" in r.text and 'data-pile="review"' in r.text
    c.post("/classify", data={"redo": "1"})
    assert "Photos - Teal" in built["seen"]["cats"]
    assert "Your categories changed" not in c.get("/").text


def test_usage_receipt_hero_and_spending_log(setup):
    c, root, _, _ = setup
    r = c.get("/")
    assert "$0</b> API cost" in r.text and "LOCAL" in r.text                       # estimate before sorting
    c.post("/classify")
    r = c.get("/")
    assert "What it used:" in r.text and "fake-model" in r.text and "@abc1234" in r.text and "70 computed" in r.text
    assert "≈2.4TFLOP" in r.text and "13.7k tokens" in r.text and "$0.00" in r.text
    assert (root / ".sorter" / "runs.jsonl").exists()
    h = c.get("/history")
    assert "Spending in this folder" in h.text and "fake-model" in h.text and "70 computed" in h.text


def test_examples_panel_upload_remove_and_count(setup):
    c, root, _, _ = setup
    cat = "Photos - Red"
    p = c.get("/examples", params={"cat": cat})
    assert p.status_code == 200 and "What teaches Photos - Red" in p.text and "No examples yet" in p.text
    assert "also finds land tortoises" in p.text and "teaches only one category" in p.text      # the hints
    assert 'name="q" value="red things"' in p.text                                  # search prefilled with what the model reads
    assert c.get("/examples/count", params={"cat": cat}).text.count(">0<") == 1
    files = [("files", ("a.jpg", make_image(RED), "image/jpeg")), ("files", ("b.png", make_image(GREEN, fmt="PNG"), "image/png")),
             ("files", ("notes.txt", b"x", "text/plain"))]
    r = c.post("/examples/upload", data={"cat": cat}, files=files)
    assert "added 2 uploaded images" in r.text and r.headers["hx-trigger"] == "examples-changed"
    folder = root / "Photos - Red" / "_training_data"
    assert sorted(x.name for x in folder.iterdir()) == ["a.jpg", "b.png"]
    r = c.post("/examples/upload", data={"cat": cat}, files=[("files", ("a.jpg", make_image(RED), "image/jpeg"))])
    assert "already was an example" in r.text and not (folder / "a_2.jpg").exists()   # identical content is skipped
    r = c.post("/examples/upload", data={"cat": cat}, files=[("files", ("a.jpg", make_image((200, 10, 10)), "image/jpeg"))])
    assert (folder / "a_2.jpg").exists()                                              # same name, new picture: never overwrites
    assert ">3<" in c.get("/examples/count", params={"cat": cat}).text
    item_id = re.search(r'value="ex:([^"]+)"', c.get("/examples", params={"cat": cat}).text).group(1)
    r = c.post("/examples/remove", data={"cat": cat, "ids": [f"ex:{item_id}"]})
    assert "in the trash" in r.text and len(list(folder.iterdir())) == 2 and any((root / ".sorter" / "trash").iterdir())


def test_examples_web_search_and_fetch(setup, monkeypatch):
    c, root, _, _ = setup
    from photo_sorter import websearch
    hits = [{"id": f"id{i}", "title": f"Red thing {i}", "thumb": f"https://t/{i}", "url": f"https://u/{i}", "license": "CC BY 2.0",
             "creator": "someone", "landing": f"https://l/{i}", "provider": "flickr"} for i in range(20)]
    monkeypatch.setattr(websearch, "search_images", lambda q, page=1, page_size=20: hits if page == 1 else hits[:3])
    monkeypatch.setattr(websearch, "fetch_image", lambda url: (make_image(RED), ".jpg"))
    r = c.get("/examples/search", params={"cat": "Photos - Red", "q": "red things"})
    assert r.text.count('class="hit"') == 20 and "More results" in r.text and "CC BY 2.0" in r.text
    more = c.get("/examples/search", params={"cat": "Photos - Red", "q": "red things", "page": 2})
    assert more.text.count('class="hit"') == 3 and 'class="hits"' not in more.text
    vals = json.loads(re.search(r"hx-vals='([^']+)'", r.text).group(1))
    a = c.post("/examples/fetch", data=vals)
    assert "✓ added" in a.text and a.headers["hx-trigger"] == "examples-changed" and 'src="https://t/0"' in a.text
    again = c.post("/examples/fetch", data=vals)                                     # same picture twice: skipped
    assert "already an example" in again.text and "hx-trigger" not in again.headers
    assert len(list((root / "Photos - Red" / "_training_data").iterdir())) == 1
    assert (root / "Photos - Red" / "_training_data" / "red_thing_0_id0.jpg").exists()
    log = (root / ".sorter" / "examples.jsonl").read_text()
    assert '"license": "CC BY 2.0"' in log and '"landing": "https://l/0"' in log
    monkeypatch.setattr(websearch, "search_images", lambda *a, **k: (_ for _ in ()).throw(websearch.SearchError("offline")))
    assert "✕ offline" in c.get("/examples/search", params={"cat": "Photos - Red", "q": "x"}).text


def test_teach_from_review_and_new_category_from_a_row(setup):
    c, root, tmp_path, _ = setup
    c.post("/classify")
    d = c.get("/pile?key=review")
    assert "＋ New category…" in d.text and "▶ One by one" in d.text and 'hx-trigger="change[target.value!==' in d.text
    ids = re.findall(r'name="item_id" value="([^"]+)"', d.text)
    t = c.post("/examples/teach", data={"item_id": ids[0], "category": "Photos - Blue"})
    assert "is now an example of" in t.text and "Re-sort all" in t.text
    assert (root / "Photos - Blue" / "_training_data" / ids[0].split("/")[-1]).exists()
    assert (root / ids[0]).exists()                                                   # the image itself stays in the inbox
    assert "Your examples changed" in c.get("/").text
    # file a review image into a brand-new category typed in the row
    r = c.post("/file", data={"item_id": ids[1], "category": "__new__", "new_category": "Sea Turtle"})
    assert r.status_code == 200 and "sea_turtle_001.jpg" in r.text
    assert "in the new category Sea Turtle" in json.loads(r.headers["hx-trigger"])["toast"]
    assert "- Sea Turtle" in (tmp_path / "CATEGORIES.md").read_text() and (root / "Sea Turtle" / "sea_turtle_001.jpg").exists()
    # and teach another one into it, reusing the existing category (case-insensitive)
    t = c.post("/examples/teach", data={"item_id": ids[2], "category": "__new__", "new_category": "sea turtle"})
    assert "Sea Turtle" in t.text and (tmp_path / "CATEGORIES.md").read_text().count("Sea Turtle") == 1
    assert c.post("/file", data={"item_id": ids[3], "category": "__new__", "new_category": ""}).status_code == 400


def test_tree_has_example_buttons_for_leaves_only(setup):
    c, root, _, _ = setup
    c.post("/categories/add", data={"parent": "Photos - Red", "name": "Dark"}, headers={"HX-Request": "true"})
    page = c.get("/categories").text
    assert page.count('class="tb exb"') == 3                                          # Dark, Green, Blue (Red is now a folder)
    assert "/examples/count?cat=Photos%20-%20Red%20%E2%80%BA%20Dark" in page
    # the count must swap into itself, not inherit the button's target (the examples panel)
    assert page.count('hx-trigger="load, examples-changed from:body" hx-target="this"') == 3


def test_no_session_redirects_back_and_fragments_never_get_a_page(root, tmp_path, monkeypatch):
    c, _, _ = build(root, tmp_path, monkeypatch, n=1)
    fresh = TestClient(c.app, follow_redirects=False)
    r = fresh.get("/categories?x=1")
    assert r.status_code == 307 and r.headers["location"] == "/login?next=/categories%3Fx%3D1"
    r = fresh.get("/login", params={"next": "/categories"})
    assert r.status_code == 303 and r.headers["location"] == "/categories" and "ps_session" in r.cookies
    assert TestClient(c.app, follow_redirects=False).get("/login", params={"next": "//evil.example"}).headers["location"] == "/"
    frag = TestClient(c.app, follow_redirects=False).get("/examples/count", params={"cat": "Photos - Red"},
                                                         headers={"HX-Request": "true", "HX-Current-URL": "http://x/categories"})
    assert frag.status_code == 401 and frag.headers["hx-redirect"] == "/login?next=/categories"


def test_fix_a_wrong_photo_that_was_also_an_example(setup):
    """Filed into Red by mistake AND added as an example of Red: one Move fixes both, and Undo walks back."""
    c, root, _, _ = setup
    c.post("/classify")
    hx = {"HX-Request": "true"}
    rec = c.post("/file", data={"item_id": "Inbox/img_000.jpg", "category": "Photos - Red"})      # a green image, filed into Red
    assert (root / "Photos - Red" / "red_001.jpg").exists()
    c.post("/examples/upload", data={"cat": "Photos - Red"},
           files=[("files", ("copy.jpg", (root / "Photos - Red" / "red_001.jpg").read_bytes(), "image/jpeg"))])
    assert (root / "Photos - Red" / "_training_data" / "copy.jpg").exists()
    panel = c.get("/examples", params={"cat": "Photos - Red"}).text
    assert 'value="filed:Photos - Red/red_001.jpg"' in panel and "these teach too" in panel
    r = c.post("/examples/move", data={"cat": "Photos - Red", "ids": ["filed:Photos - Red/red_001.jpg"], "category": "Photos - Green"})
    assert "moved 1 photo to Photos - Green" in r.text
    assert (root / "Photos - Green" / "green_001.jpg").exists() and not (root / "Photos - Red" / "red_001.jpg").exists()
    # the example copy followed the picture: one picture teaches one category
    assert not (root / "Photos - Red" / "_training_data" / "copy.jpg").exists()
    assert (root / "Photos - Green" / "_training_data" / "copy.jpg").exists()
    hist = c.get("/history").text
    moves_table = hist.split("<h2", 2)[2]
    assert moves_table.count('<tr><td class="muted">') == 1 and "green_001.jpg" in moves_table   # only the latest step
    rid = re.search(r'name="rec" value="([^"]+)"', hist).group(1)
    # and move it once more from History, then undo back one step
    r = c.post("/refile", data={"rec": rid, "category": "Photos - Blue"})
    assert r.status_code == 303 and (root / "Photos - Blue" / "blue_001.jpg").exists()
    assert (root / "Photos - Blue" / "_training_data" / "copy.jpg").exists()
    c.post("/undo", data={"id": re.search(r'name="id" value="([^"]+)"', c.get("/history").text).group(1)})
    assert (root / "Photos - Green" / "green_001.jpg").exists() and not (root / "Photos - Blue" / "blue_001.jpg").exists()


def test_move_from_filed_list_and_examples_to_new_category(setup):
    c, root, tmp_path, _ = setup
    c.post("/classify")
    c.post("/file-all", data={"cat": "Photos - Red"})
    f = c.get("/pile?key=filed").text
    assert "move to…" in f and 'hx-post="/refile"' in f
    rid = re.search(r'name="rec" value="([^"]+)"', f).group(1)
    t = c.post("/refile", data={"rec": rid, "category": "__new__", "new_category": "Crimson"}, headers={"HX-Request": "true"})
    assert "Moved to <b>Crimson</b>" in t.text and "examples-changed" in t.headers["hx-trigger"]
    assert "- Crimson" in (tmp_path / "CATEGORIES.md").read_text() and (root / "Crimson" / "crimson_001.jpg").exists()
    # examples can move between categories but never into the review folder
    c.post("/examples/upload", data={"cat": "Photos - Blue"}, files=[("files", ("e.jpg", make_image(RED), "image/jpeg"))])
    r = c.post("/examples/move", data={"cat": "Photos - Blue", "ids": ["ex:Photos - Blue/_training_data/e.jpg"], "category": "Review"})
    assert "not the review folder" in r.text
    r = c.post("/examples/move", data={"cat": "Photos - Blue", "ids": ["ex:Photos - Blue/_training_data/e.jpg"], "category": "Crimson"})
    assert "moved 1 photo to Crimson" in r.text and (root / "Crimson" / "_training_data" / "e.jpg").exists()


def test_check_needs_an_embedding_model(setup):
    c, root, _, _ = setup
    c.post("/examples/upload", data={"cat": "Photos - Red"}, files=[("files", ("e.jpg", make_image(RED), "image/jpeg"))])
    r = c.get("/examples", params={"cat": "Photos - Red", "check": 1})
    assert "checking needs an embedding model" in r.text           # the fake classifier here is not embedding-based


def test_one_by_one_viewer_flow(setup):
    c, root, tmp_path, _ = setup
    # unsorted images can be filed by hand, before any sorting
    t = c.get("/triage", params={"key": "unsorted"}).json()
    assert t["title"] == "Unsorted" and len(t["items"]) == 70 and [x["key"] for x in t["categories"]] == ["Photos - Red", "Photos - Green", "Photos - Blue"]
    first = t["items"][0]
    assert first["why"] == "not sorted yet" and first["guess"] is None and first["view"].startswith("/view/")
    v = c.get(first["view"])
    assert v.status_code == 200 and v.headers["content-type"] == "image/jpeg"
    j = c.post("/file", data={"item_id": first["id"], "category": "Photos - Blue", "fmt": "json"}).json()
    assert j["ok"] and j["rec"]["new_name"] == "blue_001.jpg" and j["created"] is False
    # after sorting, the review pile: suggestions, plain reason, numbered categories
    c.post("/classify")
    t = c.get("/triage", params={"key": "review"}).json()
    it = t["items"][0]
    assert it["guess"] == "Photos - Green" and it["confident"] is False and "Photos - Green" in it["scores"]
    # create a category from the viewer and file into it; JSON says it was created
    j = c.post("/file", data={"item_id": it["id"], "category": "__new__", "new_category": "Lime", "fmt": "json"})
    assert j.json()["created"] is True and "categories-changed" in j.headers["hx-trigger"]
    assert [x["key"] for x in c.get("/triage", params={"key": "review"}).json()["categories"]][-1] == "Lime"
    # typing an existing name (any case, leaf name) reuses the category instead of failing
    it2 = c.get("/triage", params={"key": "review"}).json()["items"][0]
    j = c.post("/file", data={"item_id": it2["id"], "category": "__new__", "new_category": "lime", "fmt": "json"}).json()
    assert j["created"] is False and j["category"] == "Lime"
    # undo puts the image back with its suggestion, in the same pile
    u = c.post("/undo", data={"id": j["rec"]["id"], "fmt": "json"}).json()
    assert u["undone"] == 1 and any(x["id"] == it2["id"] for x in c.get("/triage", params={"key": "review"}).json()["items"])
    # errors are explicit
    r = c.post("/file", data={"item_id": it2["id"], "category": "__new__", "new_category": "", "fmt": "json"})
    assert r.status_code == 400 and r.json()["detail"] == "type a name for the new category"
    assert c.post("/file", data={"item_id": it2["id"], "category": "", "fmt": "json"}).json()["detail"] == "choose a category"
