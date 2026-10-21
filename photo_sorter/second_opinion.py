"""Optional: ask a vision LLM (Claude or Gemini) only when the embedding classifier is unsure.

Config:  "second_opinion": {"provider": "claude", "model": "claude-haiku-4-5", "when_below": 0.15}
The LLM sees only the embedding classifier's top candidates, so the call is short and cheap.
"""
from __future__ import annotations

from .classifiers import ClassifyError, Verdict


class SecondOpinion:
    def __init__(self, primary, fallback, when_below: float, top_n: int = 5):
        self.primary, self.fallback, self.when_below, self.top_n = primary, fallback, when_below, top_n
        self.cache = getattr(primary, "cache", None)   # lets the sorter pass md5 keys through
        self.refs = getattr(primary, "refs", None)

    def classify(self, image, mime: str, categories: dict[str, str], is_video: bool, **kwargs) -> Verdict:
        v = self.primary.classify(image, mime, categories, is_video, **kwargs)
        if v.confidence >= self.when_below or not v.scores:
            return v
        shortlist = {c: categories[c] for c in list(v.scores)[: self.top_n] if c in categories}
        try:
            w = self.fallback.classify(image() if callable(image) else image, mime, shortlist, is_video)
        except ClassifyError as e:
            v.reason += f"; second opinion failed: {e}"
            return v
        except Exception as e:  # noqa: BLE001
            v.reason += f"; second opinion failed: {type(e).__name__}"
            return v
        if w.category == v.category:
            return Verdict(v.category, max(v.confidence, w.confidence), f"{v.reason}; LLM agrees: {w.reason}",
                           scores=v.scores, method=f"{v.method}+{self.fallback.__class__.__name__.replace('Classifier', '').lower()}")
        return Verdict(w.category, w.confidence, f"LLM overrode {v.category}: {w.reason}", scores=v.scores,
                       method=f"{v.method}->{self.fallback.__class__.__name__.replace('Classifier', '').lower()}")

    def rebuild_refs(self, storage, log=None) -> int:
        n = self.primary.rebuild_refs(storage, log=log)
        self.refs = self.primary.refs
        return n

    def meters(self):
        out = []
        for c in (self.primary, self.fallback):
            f = getattr(c, "meters", None)
            out += list(f()) if f else []
        return out

    def flush(self) -> None:
        f = getattr(self.primary, "flush", None)
        if f:
            f()

    def is_fatal(self, e: Exception) -> bool:
        return getattr(self.primary, "is_fatal", lambda e: False)(e)

    def describe_error(self, e: Exception) -> str:
        return getattr(self.primary, "describe_error", lambda e: f"{type(e).__name__}: {e}")(e)
