import numpy as np

from photo_sorter.embedding_classifier import EmbeddingClassifier
from photo_sorter.references import EmbeddingCache

from conftest import FakeEmbedder, RED, make_image


class ScaledFake(FakeEmbedder):
    """Like FakeEmbedder but with SigLIP-style logit scale/bias so sigmoid fit applies."""
    logit_scale = 20.0
    logit_bias = -12.0   # cos=1 -> sigmoid(8)=~1 ; cos~0.5 -> sigmoid(-2)=0.12 ; cos~0.3 -> sigmoid(-6)=0.002


def test_no_fit_goes_to_review_but_refs_can_rescue(cfg):
    cats = {"Photos - Red": "a red thing", "Photos - Green": "a green thing"}
    clf = EmbeddingClassifier(ScaledFake(), cfg, EmbeddingCache(None, "fake"))
    # a blue image: neither label fits -> confidence 0, guess kept for the dropdown
    v = clf.classify(make_image((20, 20, 230)), "image/jpeg", cats, False)
    assert v.confidence == 0.0 and v.reason.startswith("no category fits") and v.category in cats
    # a red image fits "red" -> confident, with the fit shown
    v = clf.classify(make_image(RED), "image/jpeg", cats, False)
    assert v.confidence > 0.5 and "fit" in v.reason
    # the same blue image, but Green has reference images that look exactly like it -> rescued by kNN
    blue = make_image((20, 20, 230))
    vec = clf.embedder.embed_images([blue])[0]
    for i in range(3):
        clf.refs.add("Photos - Green", vec, f"r{i}")
    v = clf.classify(blue, "image/jpeg", cats, False)
    assert v.category == "Photos - Green" and v.confidence > 0.3
