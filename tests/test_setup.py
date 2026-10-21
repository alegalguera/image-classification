"""First run without a config file: pick the folder in the browser; the folder keeps its own settings and categories.
Laptop (any local folder) and hosted (Cloud Run: Google sign-in, Drive links only)."""
import base64
import json

import pytest
from fastapi.testclient import TestClient

from photo_sorter import cli, config
from photo_sorter.config import base_config, source_from
from photo_sorter.web.app import CookieJar, create_app

from conftest import BLUE, RED, make_image
from test_drive import FakeDrive


@pytest.fixture
def app_dir(tmp_path, monkeypatch):
    d = tmp_path / "app-home"
    monkeypatch.setattr(config, "APP_DIR", d)
    monkeypatch.delenv("PHOTO_SORTER_HOSTED", raising=False)
    monkeypatch.delenv("K_SERVICE", raising=False)
    return d


def test_source_from_and_base_config(app_dir, monkeypatch, tmp_path):
    assert source_from("https://drive.google.com/drive/folders/1AbC") == ("drive", "https://drive.google.com/drive/folders/1AbC")
    assert source_from("1AbCdEfGhIjKlMnOpQrStUv")[0] == "drive"
    assert source_from(str(tmp_path)) == ("local", str(tmp_path))
    assert source_from("https://www.icloud.com/sharedalbum/#X")[0] == "icloud"
    cfg = base_config()
    assert cfg.provider == "siglip" and cfg.source_root is None and cfg.categories_in_folder
    assert base_config(hosted=True).provider == "vertex"
    monkeypatch.setenv("PHOTO_SORTER_PROVIDER", "claude")
    assert base_config(hosted=True).provider == "claude"
    config.remember_folder(str(tmp_path))
    assert base_config().source_root == str(tmp_path) and base_config(hosted=True).source_root is None


def test_laptop_first_run_picks_folder_inbox_and_categories(app_dir, tmp_path):
    photos = tmp_path / "Lab photos"
    for sub in ("Coral/Bleaching", "Sponges/_training_data", "Review"):
        (photos / sub).mkdir(parents=True)
    (photos / "a.jpg").write_bytes(make_image(RED))
    (photos / "b.jpg").write_bytes(make_image(BLUE))
    c = TestClient(create_app(base_config()), follow_redirects=False)

    assert c.get("/").headers["location"] == "/login?next=/"
    assert c.get("/login").status_code == 303
    assert c.get("/").headers["location"] == "/setup"                    # no folder yet
    r = c.get("/setup")
    assert "Which folder has your photos?" in r.text and "Choose a folder" in r.text and "Google Drive" in r.text
    b = c.get("/setup/browse", params={"path": str(photos)})
    assert "📁 Coral" in b.text and "📁 Sponges" in b.text and "2 photos directly in it" in b.text
    assert "is+not+a+folder" in c.post("/setup/folder", data={"kind": "local", "path": str(tmp_path / "nope")}).headers["location"]

    r = c.post("/setup/folder", data={"kind": "local", "path": str(photos)})
    assert r.headers["location"] == "/setup/categories"                           # no inbox question: the folder itself is sorted
    assert json.loads((app_dir / "app.json").read_text())["folder"] == str(photos.resolve())
    assert json.loads((photos / ".sorter" / "settings.json").read_text()) == {"inbox": ""}
    # a separate folder for new photos stays available as a setting
    r = c.get("/setup/inbox")
    assert 'value="self" checked' in r.text and "2 photos are there now" in r.text
    r = c.post("/setup/inbox", data={"mode": "sub"})
    assert r.headers["location"] == "/setup/categories" and (photos / "Inbox").is_dir()
    assert json.loads((photos / ".sorter" / "settings.json").read_text()) == {"inbox": "Inbox"}

    r = c.get("/setup/categories")
    assert "Bleaching" in r.text and "Sponges" in r.text and "Use these 2" in r.text
    assert "Review" not in r.text.split("tree-preview")[1].split("</div>")[0] and "_training_data" not in r.text
    r = c.post("/setup/categories", data={"mode": "folders"})
    assert r.headers["location"] == "/"
    assert "- Coral\n  - Bleaching\n- Sponges\n" in (photos / "CATEGORIES.md").read_text()
    page = c.get("/")
    assert page.status_code == 200 and 'href="/setup"' in page.text and "Categories<b>2</b>" in page.text

    # another browser, and the app after a restart, open the remembered folder straight away
    other = TestClient(c.app, follow_redirects=False)
    other.get("/login")
    assert other.get("/").status_code == 200
    again = TestClient(create_app(base_config()), follow_redirects=False)
    again.get("/login")
    assert "Categories<b>2</b>" in again.get("/").text


def test_folder_brings_its_own_settings_and_category_list(app_dir, tmp_path, monkeypatch, capsys):
    folder = tmp_path / "shared"
    (folder / ".sorter").mkdir(parents=True)
    (folder / "CATEGORIES.md").write_text("# Categories\n\n- Red: red things\n- Green\n")
    (folder / ".sorter" / "settings.json").write_text('{"inbox": "", "min_confidence": 0.3}')
    (folder / "x.jpg").write_bytes(make_image(RED))
    monkeypatch.chdir(tmp_path)                       # no categories.json here

    # command line: --source is all it needs
    assert cli.main(["--source", str(folder), "scan"]) == 0
    assert "x.jpg" in capsys.readouterr().out
    cfg = base_config()
    cfg.source_kind, cfg.source_root = "local", str(folder)
    cli.open_storage_for(cfg)
    assert cfg.threshold == 0.3 and cfg.inbox == "" and list(cfg.categories) == ["Red", "Green"]

    # web: edits go to the folder's CATEGORIES.md, and edits made elsewhere show up on the next page load
    config.remember_folder(str(folder))
    c = TestClient(create_app(base_config()), follow_redirects=False)
    c.get("/login")
    assert "Categories<b>2</b>" in c.get("/").text
    c.post("/categories/add", data={"parent": "", "name": "Blue", "description": "blue things"})
    assert "- Blue: blue things" in (folder / "CATEGORIES.md").read_text()
    with (folder / "CATEGORIES.md").open("a") as f:
        f.write("- Teal\n")
    assert "Categories<b>4</b>" in c.get("/").text


class FakeFlow:
    made: list = []

    def __init__(self, redirect, state=None, code_verifier=None):
        self.redirect, self.state, self.code_verifier = redirect, state, code_verifier or "verifier-123"
        self.got_code = None
        FakeFlow.made.append(self)

    def authorization_url(self, **kw):
        return "https://accounts.google.com/o/oauth2/auth?fake=1", self.state

    def fetch_token(self, code):
        self.got_code = code

        class Creds:
            def to_json(self):
                return json.dumps({"token": "secret-token", "refresh_token": "r"})

        self.credentials = Creds()


def test_hosted_sign_in_then_paste_a_drive_link(app_dir, monkeypatch):
    from photo_sorter.storage import drive_auth
    from photo_sorter.storage.drive import GoogleDriveStorage

    monkeypatch.setenv("PHOTO_SORTER_HOSTED", "1")
    monkeypatch.setenv("PHOTO_SORTER_SECRET", "s3cret")
    FakeFlow.made = []
    monkeypatch.setattr(drive_auth, "web_flow", lambda redirect, state=None, code_verifier=None: FakeFlow(redirect, state, code_verifier))
    monkeypatch.setattr(drive_auth, "credentials_from_json", lambda token: object())
    monkeypatch.setattr(drive_auth, "has_client", lambda: True)
    drive = FakeDrive()
    drive.add_file("p1", "IMG_1.jpg", "root", make_image(RED), md5="m1")
    monkeypatch.setattr(GoogleDriveStorage, "open", classmethod(
        lambda cls, url, credentials, inbox="Inbox", training_subfolder="_training_data": cls(drive, "root", credentials, inbox, training_subfolder)))

    c = TestClient(create_app(base_config(hosted=True)), follow_redirects=False)
    assert c.get("/").headers["location"] == "/login?next=/"
    assert "Continue with Google" in c.get("/login").text
    r = c.get("/auth/start")
    assert r.headers["location"].startswith("https://accounts.google.com/")
    first = FakeFlow.made[0]
    r = c.get("/auth/callback", params={"code": "the-code", "state": first.state})
    assert r.status_code == 303
    second = FakeFlow.made[1]
    assert second.code_verifier == "verifier-123" and second.got_code == "the-code"   # PKCE verifier survived the redirect
    raw = c.cookies.get("ps_session")
    assert b"secret-token" not in base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))   # encrypted, not just signed
    assert json.loads(CookieJar("s3cret").loads(raw)["token"])["token"] == "secret-token"

    assert c.get("/").headers["location"] == "/setup"
    r = c.get("/setup")
    assert "Paste the folder" in r.text and "A folder on this computer" not in r.text and "Sign out" in r.text
    assert "only+Google+Drive" in c.post("/setup/folder", data={"kind": "local", "path": "/tmp"}).headers["location"]
    assert c.get("/setup/browse").status_code == 404

    r = c.post("/setup/folder", data={"kind": "drive", "link": "https://drive.google.com/drive/folders/FOLDERid_12345?usp=sharing"})
    assert r.headers["location"] == "/setup/categories"
    assert c.post("/setup/categories", data={"mode": "empty"}).headers["location"] == "/categories"
    c.post("/categories/add", data={"parent": "", "name": "Coral", "description": "hard coral"})
    names = {f["name"]: f for f in drive.store.values()}
    assert "- Coral: hard coral" in names["CATEGORIES.md"]["_data"].decode() and names["CATEGORIES.md"]["parents"] == ["root"]
    assert json.loads(names["settings.json"]["_data"]) == {"inbox": ""}
    page = c.get("/")
    assert page.status_code == 200 and "1 unsorted image" in page.text and "Sign out" in page.text
    assert not (app_dir / "app.json").exists()                                      # the server remembers nothing


def test_ready_made_lists(app_dir, tmp_path):
    from photo_sorter.taxonomy import presets

    lists = {p["key"]: p for p in presets()}
    reef = lists["coral-reef-restoration"]
    assert reef["title"] == "Coral reef restoration" and reef["leaves"] > 50 and "Coral and sponge" in reef["about"]
    assert lists["reef-basics"]["leaves"] == 7

    photos = tmp_path / "Reef photos"
    (photos / "Inbox").mkdir(parents=True)
    config.remember_folder(str(photos))
    c = TestClient(create_app(base_config()), follow_redirects=False)
    c.get("/login")
    page = c.get("/setup/categories")
    assert "Coral reef restoration" in page.text and "Use this list" in page.text and "See the list" in page.text
    r = c.post("/setup/categories", data={"mode": "preset:coral-reef-restoration"})
    assert r.headers["location"] == "/"
    text = (photos / "CATEGORIES.md").read_text()
    assert "    - APAL: Acropora palmata" in text and "## Notes" in text
    assert f"Categories<b>{reef['leaves']}</b>" in c.get("/").text
    # a list that exists is never replaced from here
    assert c.get("/setup/categories").headers["location"] == "/categories"
    assert "already+has+categories" in c.post("/setup/categories", data={"mode": "preset:reef-basics"}).headers["location"]
    assert "APAL" in (photos / "CATEGORIES.md").read_text()


def test_folder_itself_is_sorted_and_guide_says_where(app_dir, tmp_path):
    folder = tmp_path / "Field trip"
    folder.mkdir()
    (folder / "a.jpg").write_bytes(make_image(RED))
    c = TestClient(create_app(base_config()), follow_redirects=False)
    c.get("/login")
    c.post("/setup/folder", data={"kind": "local", "path": str(folder)})
    c.post("/setup/categories", data={"mode": "preset:reef-basics"})
    page = c.get("/")
    assert "1 unsorted image" in page.text                          # the photo at the top of the folder
    (folder / "a.jpg").unlink()
    c.post("/scan")
    page = c.get("/")
    assert "Put new photos in the Field trip folder" in page.text and "Use a separate folder for new photos" in page.text


def test_setup_recommends_drive_for_desktop_without_a_sign_in_file(app_dir, monkeypatch):
    from photo_sorter.storage import drive_auth

    monkeypatch.setattr(drive_auth, "has_client", lambda: False)
    c = TestClient(create_app(base_config()), follow_redirects=False)
    c.get("/login")
    r = c.get("/setup")
    assert "Photos in Google Drive?" in r.text and "Get Google Drive for Desktop" in r.text
    assert "client_secret" not in r.text and "Paste a Google Drive link" not in r.text
    monkeypatch.setattr(drive_auth, "has_client", lambda: True)
    assert "Paste a Google Drive link" in c.get("/setup").text
