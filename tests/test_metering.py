import json
from types import SimpleNamespace

import pytest

from photo_sorter import metering
from photo_sorter.config import Config
from photo_sorter.embedding_classifier import EmbeddingClassifier
from photo_sorter.references import EmbeddingCache

from conftest import FakeEmbedder, RED, make_image


def test_cost_and_estimates():
    m = metering.Meter(info={"model": "claude-haiku-4-5", "pricing": metering.claude_pricing("claude-haiku-4-5")})
    m.add(input_tokens=1_000_000, output_tokens=100_000)
    assert m.cost(m.c) == pytest.approx(1.0 + 0.5)
    assert metering.Meter(info={"local": True}).cost({}) == 0.0
    assert metering.Meter(info={"model": "x"}).cost({}) is None            # no price known
    v = metering.Meter(info={"pricing": metering.VERTEX_PRICES})
    assert v.cost({"api_images": 1000, "api_text_chars": 2000}) == pytest.approx(0.1 + 0.0004)
    cats = {"A": "a"}
    assert metering.estimate_cost(Config(categories=cats, provider="siglip"), 500) == 0.0
    assert metering.estimate_cost(Config(categories=cats, provider="vertex"), 500) == pytest.approx(0.05)
    est = metering.estimate_cost(Config(categories=cats, provider="claude", model="claude-opus-5-5"), 100)
    assert est == pytest.approx(100 * (1100 * 4 + 60 * 20) / 1e6)
    over = Config(categories=cats, provider="gemini", pricing={"gemini-2.5-flash-lite": {"input_per_mtok": 0.1, "output_per_mtok": 0.4}})
    assert metering.estimate_cost(over, 10) > 0
    assert metering.estimate_cost(Config(categories=cats, provider="gemini"), 10) is None
    assert metering.fmt_usd(0) == "$0.00" and metering.fmt_usd(0.0011) == "$0.0011" and metering.fmt_num(2_400_000) == "2.4M"


class MeteredFake(FakeEmbedder):
    def __init__(self):
        self.meter = metering.Meter(info={"provider": "fake", "model": "fake-1", "local": True})

    def meters(self):
        return [self.meter]

    def embed_images(self, images):
        self.meter.add(images_embedded=len(images), image_tokens=196 * len(images), flops=1e9 * len(images))
        return super().embed_images(images)


def test_run_meter_counts_computed_and_cached(cfg, categories):
    emb = MeteredFake()
    clf = EmbeddingClassifier(emb, cfg, EmbeddingCache(None, "fake"))
    run = metering.RunMeter(clf)
    clf.classify(make_image(RED), "image/jpeg", categories, False, key="k1")
    clf.classify(make_image(RED), "image/jpeg", categories, False, key="k1")   # second time: cache hit
    run.stop()
    rep = run.report()
    assert rep["u"]["images_embedded"] == 1 and rep["u"]["cache_hits"] == 1 and rep["u"]["image_tokens"] == 196
    assert rep["cost"] == 0.0 and rep["wall"] >= 0 and rep["peak_rss"] > 0 and rep["machine"]
    lines = "\n".join(metering.summary_lines(rep))
    assert "fake-1" in lines and "1 from cache" in lines and "no API cost" in lines


def test_claude_usage_from_response(monkeypatch):
    anthropic = pytest.importorskip("anthropic")
    from photo_sorter.classifiers import ClaudeClassifier

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    clf = ClaudeClassifier("claude-haiku-4-5")
    resp = SimpleNamespace(stop_reason="end_turn", model="claude-haiku-4-5", stop_details=None,
                           usage=SimpleNamespace(input_tokens=1200, output_tokens=40, cache_read_input_tokens=0),
                           content=[SimpleNamespace(type="text", text=json.dumps({"category": "A", "confidence": 0.9, "reason": "r"}))])
    clf.client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: resp)))
    v = clf.classify(make_image(RED), "image/jpeg", {"A": "a", "B": "b"}, False)
    assert v.category == "A"
    u = clf.meter.c
    assert u["input_tokens"] == 1200 and u["output_tokens"] == 40 and u["api_calls"] == 1
    assert clf.meter.info["version"] == "claude-haiku-4-5"
    assert clf.meter.cost(u) == pytest.approx(1200 / 1e6 * 1 + 40 / 1e6 * 5)


def test_runs_log_roundtrip(root):
    from photo_sorter.storage.local import LocalFolderStorage

    st = LocalFolderStorage(root)
    rep = {"models": [{"info": {"model": "m", "provider": "p", "where": "here"}, "u": {}, "cost": 0.0}],
           "u": {"images_embedded": 3, "cache_hits": 1, "image_tokens": 588}, "wall": 1.0, "cpu": 2.0,
           "peak_rss": 10, "cost": 0.0, "machine": "x"}
    metering.append_run(st, metering.run_record(rep, 4, 3, 1))
    metering.append_run(st, metering.run_record(dict(rep, cost=None), 2, 2, 0))
    runs = metering.load_runs(st)
    assert len(runs) == 2 and runs[0]["computed"] == 3 and runs[0]["cached"] == 1
    t = metering.totals(runs)
    assert t["runs"] == 2 and t["images"] == 6 and t["cost_unknown"] is True
