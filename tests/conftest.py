import io
import hashlib
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from photo_sorter.config import Config


def make_image(color: tuple[int, int, int], size=(64, 48), fmt="JPEG") -> bytes:
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, fmt)
    return buf.getvalue()


RED, GREEN, BLUE = (220, 30, 30), (30, 200, 30), (30, 30, 220)


class FakeEmbedder:
    """Deterministic embeddings: images map to their mean colour, texts to a colour named in them."""

    slug = "fake"
    dim = 3
    COLORS = {"red": RED, "green": GREEN, "blue": BLUE}

    def embed_images(self, images: list[bytes]) -> np.ndarray:
        out = []
        for data in images:
            img = Image.open(io.BytesIO(data)).convert("RGB")
            arr = np.asarray(img, dtype=np.float32).reshape(-1, 3).mean(axis=0)
            out.append(arr / (np.linalg.norm(arr) + 1e-9))
        return np.stack(out)

    def embed_texts(self, texts: list[str]) -> np.ndarray:
        out = []
        for t in texts:
            t = t.lower()
            vec = np.ones(3, dtype=np.float32)
            for name, c in self.COLORS.items():
                if name in t:
                    vec = np.asarray(c, dtype=np.float32)
            out.append(vec / np.linalg.norm(vec))
        return np.stack(out)


@pytest.fixture
def fake_embedder():
    return FakeEmbedder()


@pytest.fixture
def categories():
    return {"Photos - Red": "a red thing", "Photos - Green": "a green thing", "Photos - Blue": "a blue thing"}


@pytest.fixture
def cfg(categories):
    c = Config(categories=categories, provider="siglip", source_kind="local", source_root=".", min_confidence=0.1)
    c.validate()
    return c


@pytest.fixture
def root(tmp_path: Path):
    (tmp_path / "Inbox").mkdir()
    return tmp_path


def md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()
