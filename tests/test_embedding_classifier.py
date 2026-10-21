import numpy as np

from photo_sorter.embedding_classifier import EmbeddingClassifier, build_embedding_classifier, label_text
from photo_sorter.references import EmbeddingCache, ReferenceIndex
from photo_sorter.sorter import Sorter
from photo_sorter.storage.local import LocalFolderStorage

from conftest import BLUE, GREEN, RED, make_image, md5


def test_label_text():
    assert label_text("Photos - Coral › Outplants › APAL", "Acropora palmata (elkhorn coral)") == "Acropora palmata (elkhorn coral), outplants"  # 'coral' already in the text
    assert label_text("Photos - Fragging", "Photos - Fragging") == "fragging"
    assert label_text("Videos - Coral", "Videos - Coral") == "coral"
    assert label_text("Photos - Sponges › Sponge Nursery", "Sponge Nursery") == "sponge nursery, sponges"
    assert label_text("Turtle", "") == "turtle"


def test_zero_shot_picks_matching_label(fake_embedder, cfg, categories):
    clf = EmbeddingClassifier(fake_embedder, cfg, EmbeddingCache(None, "fake"))
    v = clf.classify(make_image(RED), "image/jpeg", categories, False)
    assert v.category == "Photos - Red" and v.confidence > 0 and v.method == "siglip"
    assert list(v.scores)[0] == "Photos - Red" and "2nd:" in v.reason


def test_references_override_text_and_cache_is_used(fake_embedder, cfg, categories):
    cache = EmbeddingCache(None, "fake")
    clf = EmbeddingClassifier(fake_embedder, cfg, cache)
    # a "blue" image, but the Red category has references that look exactly like it
    img = make_image(BLUE)
    vec = fake_embedder.embed_images([img])[0]
    for i in range(3):
        clf.refs.add("Photos - Red", vec, f"ref{i}")
    clf.knn_weight = 0.9
    v = clf.classify(img, "image/jpeg", categories, False, key="k1")
    assert v.category == "Photos - Red" and v.method == "siglip+knn"
    assert cache.get("k1") is not None
    # cache hit: a different image with the same key reuses the cached vector
    v2 = clf.classify(make_image(GREEN), "image/jpeg", categories, False, key="k1")
    assert v2.category == "Photos - Red"
    # leave-one-out excludes the item's own reference
    idx = ReferenceIndex()
    idx.add("Photos - Blue", vec, "self")
    assert idx.knn_scores(vec, exclude_key="self") == {}
    assert "Photos - Blue" in idx.knn_scores(vec)


def test_build_from_training_folders_and_manifest(root, cfg, fake_embedder):
    (root / "Photos - Green" / "_training_data").mkdir(parents=True)
    # training examples for Green are actually red-looking, so red inbox images should go to Green
    for i in range(2):
        (root / "Photos - Green" / "_training_data" / f"ex{i}.jpg").write_bytes(make_image((230, 40 + i, 40)))
    (root / "Inbox" / "a.jpg").write_bytes(make_image(RED))
    st = LocalFolderStorage(root)
    clf = build_embedding_classifier(cfg, st, embedder=fake_embedder)
    assert clf.refs.counts() == {"Photos - Green": 2}
    assert (root / ".sorter" / "embeddings-fake.npz").exists()
    cfg.embeddings["knn_weight"] = 0.9
    clf.knn_weight = 0.9
    sorter = Sorter(cfg, st, clf)
    [pred] = sorter.predict(sorter.scan())
    assert pred.verdict.category == "Photos - Green"
    # second build reuses the cache (no embed calls needed) and picks up the manifest after filing
    sorter.file(pred, decided_by="user")
    cache2 = EmbeddingCache(st, "fake")
    assert len(cache2.vectors) >= 2
    clf2 = build_embedding_classifier(cfg, st, embedder=fake_embedder)
    assert clf2.refs.counts()["Photos - Green"] == 3  # 2 in _training_data + 1 filed into the folder


def test_sorter_passes_md5_key(root, cfg, fake_embedder):
    data = make_image(BLUE)
    (root / "Inbox" / "b.jpg").write_bytes(data)
    st = LocalFolderStorage(root)
    clf = build_embedding_classifier(cfg, st, embedder=fake_embedder)
    sorter = Sorter(cfg, st, clf)
    [pred] = sorter.predict(sorter.scan())
    assert pred.verdict.category == "Photos - Blue"
    assert clf.cache.get(md5(data)) is not None
    # sorting again uses the cached analysis without reading the image
    reads, read = [], st.read_bytes
    st.read_bytes = lambda item, prefer="thumb": reads.append(item.id) or read(item, prefer)
    [again] = sorter.predict(sorter.scan())
    assert reads == [] and again.verdict.category == "Photos - Blue"


def test_examples_rebuilt_without_restart_and_folder_cap(root, cfg, fake_embedder):
    st = LocalFolderStorage(root)
    clf = build_embedding_classifier(cfg, st, embedder=fake_embedder)
    assert clf.refs.total == 0
    ex = root / "Photos - Blue" / "_training_data"
    ex.mkdir(parents=True)
    (ex / "e1.jpg").write_bytes(make_image(BLUE))
    for i in range(5):
        (root / "Photos - Blue" / f"blue_{i:03d}.jpg").write_bytes(make_image((30, 30, 200 + i)))
    assert clf.rebuild_refs(st) == 6                                  # 1 example + 5 filed photos
    cfg.embeddings["max_filed_examples_per_category"] = 2
    assert clf.rebuild_refs(st) == 3                                  # newest 2 filed photos only
    cfg.embeddings["use_filed_as_examples"] = False
    assert clf.rebuild_refs(st) == 1


def test_calibrated_example_cutoff_per_category(cfg):
    from photo_sorter.references import ReferenceIndex
    idx = ReferenceIndex()
    rng = np.random.default_rng(0)
    base = np.array([1.0, 0, 0])
    for i in range(5):                                   # tight cluster of examples
        v = base + rng.normal(0, 0.05, 3); idx.add("Tight", v / np.linalg.norm(v), f"t{i}")
    idx.add("Few", np.array([0, 1.0, 0]), "f0")          # fewer than 3 examples -> default cutoff
    idx.calibrate(0.85)
    assert idx.mid["Few"] == 0.85 and 0.9 < idx.mid["Tight"] <= 0.97


def test_check_flags_photos_that_belong_elsewhere(root, cfg, fake_embedder):
    from photo_sorter import examples
    st = LocalFolderStorage(root)
    red = root / "Photos - Red"
    red.mkdir()
    for i in range(4):
        (red / f"red_{i:03d}.jpg").write_bytes(make_image((220, 30 + i, 30)))
    (red / "red_009.jpg").write_bytes(make_image(BLUE))           # a blue photo filed into Red by mistake
    blue = root / "Photos - Blue" / "_training_data"
    blue.mkdir(parents=True)
    for i in range(3):
        (blue / f"b{i}.jpg").write_bytes(make_image((30, 30, 210 + i)))
    clf = build_embedding_classifier(cfg, st, embedder=fake_embedder)
    items = st.list_folder(["Photos - Red"])
    doubts = examples.check(clf, st, cfg, "Photos - Red", items)
    assert list(doubts) == ["Photos - Red/red_009.jpg"] and doubts["Photos - Red/red_009.jpg"][0] == "Photos - Blue"
