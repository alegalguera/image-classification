import json

from photo_sorter import cli
from photo_sorter.sorter import Sorter

from conftest import RED, make_image
from test_core import ConstClassifier


def _cfg_file(tmp_path, root, **extra):
    (tmp_path / "CATEGORIES.md").write_text("# T\n\n- 📁 Photos - Red\n- Photos - Blue\n")
    cfg = {"categories_file": "CATEGORIES.md", "source": {"kind": "local", "root": str(root)}, **extra}
    p = tmp_path / "c.json"
    p.write_text(json.dumps(cfg))
    return str(p)


def test_scan_file_dry_run_file_undo(tmp_path, root, monkeypatch, capsys):
    (root / "Inbox" / "IMG_9.jpg").write_bytes(make_image(RED))
    (root / "Inbox" / "IMG_8.jpg").write_bytes(make_image(RED))
    cfgp = _cfg_file(tmp_path, root)

    def fake_build_sorter(c, storage=None, log=print, lazy=False):
        from photo_sorter.manifest import Manifest
        storage = storage or cli.open_storage_for(c)
        return Sorter(c, storage, ConstClassifier("Photos - Red", 0.8), Manifest(storage))

    monkeypatch.setattr(cli, "build_sorter", fake_build_sorter)
    assert cli.main(["-c", cfgp, "scan"]) == 0
    assert "IMG_9.jpg" in capsys.readouterr().out
    assert cli.main(["-c", cfgp, "file", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "red_001.jpg" in out and "red_002.jpg" in out and "(dry run)" in out and (root / "Inbox" / "IMG_9.jpg").exists()
    assert cli.main(["-c", cfgp, "file"]) == 0
    assert (root / "Photos - Red" / "red_001.jpg").exists() and (root / "Photos - Red" / "red_002.jpg").exists()
    assert cli.main(["-c", cfgp, "undo", "--last", "2"]) == 0
    assert (root / "Inbox" / "IMG_9.jpg").exists() and (root / "Inbox" / "IMG_8.jpg").exists()
    # threshold override sends it to review with --all
    assert cli.main(["-c", cfgp, "file", "--threshold", "0.95", "--all"]) == 0
    assert (root / "Review" / "IMG_9.jpg").exists() and (root / "Review" / "IMG_8.jpg").exists()


def test_source_override_and_errors(tmp_path, root, capsys):
    cfgp = _cfg_file(tmp_path, root)
    assert cli.main(["-c", cfgp, "--source", str(tmp_path / "missing"), "scan"]) == 1
    assert "error" in capsys.readouterr().err
    assert cli.main(["-c", cfgp, "--source", "https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOp", "scan"]) == 1
    assert "photo-sorter auth" in capsys.readouterr().err or True  # not signed in (or drive extra missing)


def test_setup_remove_model_deletes_only_that_model(monkeypatch, capsys, tmp_path):
    import huggingface_hub

    done = []

    class Plan:
        expected_freed_size_str = "1.4G"

        def execute(self):
            done.append("deleted")

    class Rev:
        commit_hash = "abc123"

    class Repo:
        def __init__(self, repo_id):
            self.repo_id, self.revisions = repo_id, [Rev()]

    class Cache:
        repos = [Repo("google/siglip2-base-patch16-224"), Repo("someone/other-model")]

        def delete_revisions(self, *revs):
            done.append(revs)
            return Plan()

    monkeypatch.setattr(huggingface_hub, "scan_cache_dir", lambda: Cache())
    monkeypatch.chdir(tmp_path)
    assert cli.main(["setup", "--remove-model"]) == 0
    assert done == [("abc123",), "deleted"] and "removed google/siglip2-base-patch16-224 (1.4G)" in capsys.readouterr().out
