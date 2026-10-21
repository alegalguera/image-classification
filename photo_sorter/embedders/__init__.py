"""Image/text embedding backends. Both return L2-normalised float32 arrays of shape [N, dim]."""
from __future__ import annotations

import io
from typing import Protocol

import numpy as np


class Embedder(Protocol):
    slug: str              # identifies the embedding space; caches are kept per slug
    dim: int
    logit_scale: float | None   # SigLIP's learned scale/bias for zero-shot logits; None for others
    logit_bias: float | None

    def embed_images(self, images: list[bytes]) -> np.ndarray: ...
    def embed_texts(self, texts: list[str]) -> np.ndarray: ...


def normalize(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=np.float32)
    return x / (np.linalg.norm(x, axis=-1, keepdims=True) + 1e-9)


def decode_image(data: bytes, max_side: int | None = None):
    """Bytes -> RGB PIL image, honouring EXIF rotation; HEIC when pillow-heif is installed."""
    from PIL import Image, ImageOps

    try:
        import pillow_heif

        pillow_heif.register_heif_opener()
    except ImportError:
        pass
    img = Image.open(io.BytesIO(data))
    if max_side:
        img.draft("RGB", (max_side * 2, max_side * 2))
    img = ImageOps.exif_transpose(img).convert("RGB")
    if max_side:
        img.thumbnail((max_side, max_side))
    return img


def make_embedder(cfg) -> Embedder:
    if cfg.provider == "siglip":
        from .siglip import SiglipEmbedder

        return SiglipEmbedder(cfg.model_name)
    if cfg.provider == "vertex":
        from .vertex import VertexEmbedder

        e = cfg.embeddings
        return VertexEmbedder(cfg.model_name, project=e.get("project"), location=e.get("location", "us-central1"),
                              dimension=int(e.get("dimension", 512)), pricing=getattr(cfg, "pricing", None))
    raise ValueError(f"{cfg.provider!r} is not an embedding provider")
