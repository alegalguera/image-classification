"""Vertex AI multimodal embeddings (Google Cloud). No model on the machine; ~$0.0001 per image.

Auth is Application Default Credentials: on Cloud Run the service account; locally
`gcloud auth application-default login`. Needs the Vertex AI API enabled on the project.
"""
from __future__ import annotations

import os
import time

import numpy as np

from ..metering import VERTEX_PRICES, Meter, Timer
from . import normalize


class VertexEmbedder:
    logit_scale = None
    logit_bias = None

    def __init__(self, model_name: str = "multimodalembedding@001", project: str | None = None,
                 location: str = "us-central1", dimension: int = 512, pricing: dict | None = None):
        try:
            import vertexai
            from vertexai.vision_models import Image, MultiModalEmbeddingModel
        except ImportError as e:
            raise ImportError("Vertex AI needs google-cloud-aiplatform: run `uv sync --extra vertex`") from e
        project = project or os.environ.get("GOOGLE_CLOUD_PROJECT")
        if not project:
            raise ValueError("Vertex AI needs a project: set embeddings.project in the config or GOOGLE_CLOUD_PROJECT")
        vertexai.init(project=project, location=location)
        self._Image = Image
        self.model = MultiModalEmbeddingModel.from_pretrained(model_name)
        self.model_name, self.dim = model_name, dimension
        self.slug = f"vertex-{model_name.replace('@', '-')}-{dimension}"
        self.meter = Meter(info={
            "provider": "Google Cloud Vertex AI", "model": model_name, "version": model_name.split("@")[-1] if "@" in model_name else None,
            "where": f"Google Cloud · Vertex AI · {location} · project {project}", "local": False, "dim": dimension,
            "pricing": (pricing or {}).get(model_name) or dict(VERTEX_PRICES),
        })

    def meters(self):
        return [self.meter]

    def _call(self, **kwargs):
        delay = 1.0
        for attempt in range(5):
            try:
                return self.model.get_embeddings(dimension=self.dim, **kwargs)
            except Exception as e:  # noqa: BLE001 - google.api_core errors vary; retry transient ones
                name = type(e).__name__
                if attempt == 4 or name in ("PermissionDenied", "Unauthenticated", "InvalidArgument", "NotFound"):
                    raise
                time.sleep(delay)
                delay *= 2

    def embed_images(self, images: list[bytes]) -> np.ndarray:
        with Timer(self.meter):
            vecs = [self._call(image=self._Image(image_bytes=b)).image_embedding for b in images]
        self.meter.add(images_embedded=len(images), api_images=len(images), api_calls=len(images))
        return normalize(np.asarray(vecs, dtype=np.float32)) if vecs else np.zeros((0, self.dim), np.float32)

    def embed_texts(self, texts: list[str]) -> np.ndarray:
        with Timer(self.meter):
            vecs = [self._call(contextual_text=t[:1000]).text_embedding for t in texts]
        self.meter.add(texts_embedded=len(texts), api_text_chars=sum(len(t[:1000]) for t in texts), api_calls=len(texts))
        return normalize(np.asarray(vecs, dtype=np.float32)) if vecs else np.zeros((0, self.dim), np.float32)
