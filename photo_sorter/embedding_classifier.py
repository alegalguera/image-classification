"""Zero-shot (text) + few-shot (reference images) classifier on top of an Embedder."""
from __future__ import annotations

import hashlib

import numpy as np

from .classifiers import ClassifyError, Verdict
from .config import Config, split_path
from .naming import strip_media_prefix
from .references import EmbeddingCache, ReferenceIndex, text_key


def label_text(category: str, description: str) -> str:
    """'Photos - Coral › Outplants › APAL', 'Acropora palmata (elkhorn coral)' -> 'Acropora palmata (elkhorn coral), outplants, coral'."""
    parts = split_path(category)
    leaf = strip_media_prefix(parts[-1])
    desc = (description or "").strip()
    # a bare name reads better to the model in lower case ("a photo of turtle", not "of Turtle"); descriptions stay as typed
    main = desc if desc and desc != parts[-1] else leaf.lower()
    context = [strip_media_prefix(p).lower() for p in reversed(parts[:-1])]
    return ", ".join([main] + [c for c in context if c and c.lower() not in main.lower()])


def softmax(z: np.ndarray) -> np.ndarray:
    z = z - z.max()
    e = np.exp(z)
    return e / e.sum()


class EmbeddingClassifier:
    def __init__(self, embedder, cfg: Config, cache: EmbeddingCache, refs: ReferenceIndex | None = None):
        self.embedder, self.cfg, self.cache = embedder, cfg, cache
        self.refs = refs or ReferenceIndex()
        self._text: dict[str, tuple[list[str], np.ndarray]] = {}   # categories-hash -> (names, matrix)
        e = cfg.embeddings
        self.templates = list(e.get("prompt_templates") or ["This is a photo of {label}."])
        self.knn_weight = float(e.get("knn_weight", 0.6))
        self.knn_k = int(e.get("knn_k", 3))
        self.knn_tau = float(e.get("knn_temperature", 0.03))
        self.text_tau = float(e.get("text_temperature", 0.05))
        self.knn_midpoint = float(e.get("knn_midpoint", 0.85))
        self.knn_softness = float(e.get("knn_softness", 0.05))
        self.min_fit = float(e.get("min_fit", 0.02))

    def rebuild_refs(self, storage, log=None) -> int:
        """Re-read the examples (cheap: embeddings are cached). Called before every sort."""
        self.refs = ReferenceIndex.build(self.cfg, storage, self.embedder, self.cache, log=log)
        return self.refs.total

    def meters(self):
        f = getattr(self.embedder, "meters", None)
        return list(f()) if f else []

    def _count(self, **kw) -> None:
        for m in self.meters()[:1]:
            m.add(**kw)

    @property
    def method(self) -> str:
        base = self.cfg.provider
        return f"{base}+knn" if self.refs.total else base

    # -- text side ---------------------------------------------------------
    def text_matrix(self, categories: dict[str, str]) -> tuple[list[str], np.ndarray]:
        h = hashlib.sha1(repr(sorted(categories.items())).encode()).hexdigest()
        if h in self._text:
            return self._text[h]
        names = list(categories)
        rows, missing, missing_texts = [None] * len(names), [], []
        for i, name in enumerate(names):
            texts = [t.format(label=label_text(name, categories[name])) for t in self.templates]
            keys = [text_key(t) for t in texts]
            cached = [self.cache.get(k) for k in keys]
            if all(c is not None for c in cached):
                rows[i] = np.mean(cached, axis=0)
            else:
                for t, k in zip(texts, keys):
                    missing.append((i, k))
                    missing_texts.append(t)
        if missing_texts:
            vecs = self.embedder.embed_texts(missing_texts)
            per_row: dict[int, list[np.ndarray]] = {}
            for (i, k), v in zip(missing, vecs):
                self.cache.put(k, v)
                per_row.setdefault(i, []).append(v)
            for i, vs in per_row.items():
                rows[i] = np.mean(vs, axis=0)
            self.cache.save()
        mat = np.stack(rows).astype(np.float32)
        mat /= np.linalg.norm(mat, axis=1, keepdims=True) + 1e-9
        self._text[h] = (names, mat)
        return names, mat

    # -- scoring -----------------------------------------------------------
    def scores_for(self, x: np.ndarray, categories: dict[str, str], exclude_key: str | None = None):
        names, tmat = self.text_matrix(categories)
        cos = tmat @ x
        scale = getattr(self.embedder, "logit_scale", None)
        bias = getattr(self.embedder, "logit_bias", None) or 0.0
        if scale:
            logits = scale * cos + bias
            s_text = softmax(logits)
            sig_max = float((1 / (1 + np.exp(-logits))).max())
        else:
            s_text = softmax(cos / self.text_tau)
            sig_max = float(cos.max())
        knn_raw = {c: v for c, v in self.refs.knn_scores(x, self.knn_k, exclude_key).items() if c in categories}
        p_in = 0.0
        if knn_raw:
            pos = {c: i for i, c in enumerate(names)}
            idx = [pos[c] for c in knn_raw]
            sims = np.array(list(knn_raw.values()))
            sk = softmax(sims / self.knn_tau)
            if len(idx) == len(names):
                p_in = 1.0
            else:
                # how strongly the image matches *some* category's examples, each judged against its own calibration
                mids = np.array([self.refs.mid.get(c, self.knn_midpoint) for c in knn_raw])
                p_in = float((1 / (1 + np.exp(-(sims - mids) / self.knn_softness))).max())
            # mixture: with probability p_in the image matches some category's examples (distribution sk over those
            # categories); otherwise the examples say nothing and we fall back to the text judgement over all categories
            s_knn = (1 - p_in) * s_text
            s_knn[idx] += p_in * sk
            s = (1 - self.knn_weight) * s_text + self.knn_weight * s_knn
        else:
            s_knn = None
            s = s_text
        return names, s, s_text, s_knn, sig_max, p_in

    def classify_vector(self, x: np.ndarray, categories: dict[str, str], exclude_key: str | None = None) -> Verdict:
        names, s, s_text, s_knn, sig_max, p_in = self.scores_for(x, categories, exclude_key)
        order = np.argsort(-s)
        top, second = int(order[0]), int(order[1]) if len(order) > 1 else None
        margin = float(s[top] - (s[second] if second is not None else 0.0))
        bits = [f"text {s_text[top]:.2f}"]
        if s_knn is not None:
            bits.append(f"knn {s_knn[top]:.2f}")
        if second is not None:
            bits.append(f"2nd: {names[second]} {s[second]:.2f}")
        # Absolute fit: the margin only says which category is *more* likely. With SigLIP the best
        # label's own sigmoid probability tells whether the image matches it at all. A strong match
        # to reference images (p_in) counts as fit too.
        has_sigmoid = bool(getattr(self.embedder, "logit_scale", None))
        if has_sigmoid and self.min_fit > 0 and sig_max < self.min_fit and p_in < 0.5:
            bits.insert(0, f"no category fits (best match {sig_max:.1%}, need {self.min_fit:.0%})")
            margin = 0.0
        elif has_sigmoid:
            bits.append(f"fit {sig_max:.0%}")
        return Verdict(
            category=names[top], confidence=margin, reason="; ".join(bits),
            scores={names[i]: float(s[i]) for i in order[:5]},
            method=self.method if s_knn is not None else self.cfg.provider,
        )

    def classify(self, image, mime: str, categories: dict[str, str], is_video: bool, key: str | None = None) -> Verdict:
        """image: bytes, or a function returning them (called only when the cache misses)."""
        if not categories:
            raise ClassifyError("no categories")
        vec = self.cache.get(key) if key else None
        if vec is not None:
            self._count(cache_hits=1)
        if vec is None:
            data = image() if callable(image) else image
            try:
                vec = self.embedder.embed_images([data])[0]
            except Exception as e:  # noqa: BLE001
                raise ClassifyError(f"embedding failed: {type(e).__name__}: {e}") from e
            if key:
                self.cache.put(key, vec)
        return self.classify_vector(vec, categories)

    def flush(self) -> None:
        self.cache.save()

    def is_fatal(self, e: Exception) -> bool:
        return isinstance(e, (ImportError, PermissionError))

    def describe_error(self, e: Exception) -> str:
        return f"{type(e).__name__}: {e}"


def build_embedding_classifier(cfg: Config, storage, embedder=None, log=None) -> EmbeddingClassifier:
    from .embedders import make_embedder

    embedder = embedder or make_embedder(cfg)
    cache = EmbeddingCache(storage, embedder.slug)
    clf = EmbeddingClassifier(embedder, cfg, cache)
    if cfg.categories:
        clf.text_matrix(cfg.categories)
    if cfg.video_categories:
        clf.text_matrix(cfg.video_categories)
    clf.rebuild_refs(storage, log=log)
    # the text tower stays loaded: the web UI can add categories at any time
    return clf
