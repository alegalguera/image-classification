"""SigLIP 2 running locally with PyTorch (CPU or Apple MPS). Free and offline after the first download."""
from __future__ import annotations

import os
import re
from pathlib import Path

import numpy as np

# the SigLIP 2 config makes transformers print harmless token-id warnings on every load
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")

from ..metering import Meter, Timer, machine
from . import decode_image, normalize


def _tensor(x):
    """transformers >= 5 returns a ModelOutput from get_*_features; older versions a tensor."""
    if hasattr(x, "pooler_output") and x.pooler_output is not None:
        return x.pooler_output
    if hasattr(x, "last_hidden_state") and not hasattr(x, "shape"):
        return x.last_hidden_state
    return x[0] if isinstance(x, (tuple, list)) else x


def _hf_snapshot(model_name: str) -> tuple[str | None, str | None, int]:
    """(commit hash, weights folder with ~ for home, bytes on disk) of the locally cached model, best effort."""
    import os

    try:
        from huggingface_hub import try_to_load_from_cache

        cfg_path = try_to_load_from_cache(model_name, "config.json")
        if not isinstance(cfg_path, str):
            if os.path.isdir(model_name):                     # a local folder was given instead of a hub id
                size = sum(f.stat().st_size for f in Path(model_name).rglob("*") if f.is_file())
                return None, model_name, size
            return None, None, 0
        snap = Path(cfg_path).parent
        size = sum(f.resolve().stat().st_size for f in snap.rglob("*") if f.is_file() or f.is_symlink())
        home = str(Path.home())
        return snap.name, str(snap).replace(home, "~", 1), size
    except Exception:  # noqa: BLE001
        return None, None, 0


class SiglipEmbedder:
    def __init__(self, model_name: str = "google/siglip2-base-patch16-224", device: str | None = None, batch_size: int = 16):
        try:
            import torch
            from transformers import AutoModel, AutoProcessor
        except ImportError as e:
            raise ImportError("SigLIP needs torch and transformers: run `uv sync --extra siglip`") from e
        self.torch = torch
        self.model_name = model_name
        self.slug = re.sub(r"[^a-z0-9]+", "-", model_name.split("/")[-1].lower()).strip("-")
        self.batch_size = batch_size
        if device is None:
            device = "mps" if torch.backends.mps.is_available() else "cpu"
        self.device = device
        if device == "cpu":
            torch.set_num_threads(max(1, torch.get_num_threads()))
        self.processor = AutoProcessor.from_pretrained(model_name)
        self.model = AutoModel.from_pretrained(model_name).to(device).eval()
        self.dim = int(getattr(self.model.config, "projection_dim", None) or self.model.config.vision_config.hidden_size)
        self.logit_scale = float(self.model.logit_scale.exp().item())
        self.logit_bias = float(self.model.logit_bias.item()) if getattr(self.model, "logit_bias", None) is not None else 0.0
        vc = self.model.config.vision_config
        self.image_tokens = (int(vc.image_size) // int(vc.patch_size)) ** 2       # patches the ViT looks at per image
        n_vision = sum(p.numel() for p in self.model.vision_model.parameters())
        n_text = sum(p.numel() for p in self.model.text_model.parameters())
        n_all = sum(p.numel() for p in self.model.parameters())
        mem = sum(p.numel() * p.element_size() for p in self.model.parameters())
        self._flops_image = 2 * n_vision * (self.image_tokens + 1)                 # ≈ 2 x params x tokens (forward pass)
        self._flops_text_tok = 2 * n_text
        self.pad_id = getattr(getattr(self.processor, "tokenizer", None), "pad_token_id", None)
        import transformers

        commit, weights_dir, disk = _hf_snapshot(model_name)
        commit = commit or getattr(self.model.config, "_commit_hash", None)
        where_dev = {"mps": "Apple GPU (Metal)", "cuda": "NVIDIA GPU (CUDA)", "cpu": "CPU"}.get(device, device)
        self.meter = Meter(info={
            "provider": "SigLIP 2 (Google, open weights)", "model": model_name, "version": commit[:10] if commit else None,
            "where": f"this computer · {where_dev} · {machine()}", "local": True, "device": device,
            "params": n_all, "params_used": n_vision, "memory": mem, "dtype": str(next(self.model.parameters()).dtype).replace("torch.", ""),
            "image_tokens": self.image_tokens, "dim": self.dim,
            "runtime": f"torch {torch.__version__} · transformers {transformers.__version__}",
            "weights": weights_dir, "disk": disk, "source": f"huggingface.co/{model_name}",
        })

    def meters(self):
        return [self.meter]

    def embed_images(self, images: list[bytes]) -> np.ndarray:
        out = []
        n = len(images)
        if n:
            self.meter.add(images_embedded=n, image_tokens=n * self.image_tokens, flops=n * self._flops_image)
        with Timer(self.meter), self.torch.inference_mode():
            for i in range(0, len(images), self.batch_size):
                pil = [decode_image(b, max_side=512) for b in images[i:i + self.batch_size]]
                inputs = self.processor(images=pil, return_tensors="pt").to(self.device)
                feats = _tensor(self.model.get_image_features(**inputs))
                out.append(feats.float().cpu().numpy())
        return normalize(np.concatenate(out)) if out else np.zeros((0, self.dim), np.float32)

    def embed_texts(self, texts: list[str]) -> np.ndarray:
        if getattr(self.model, "text_model", None) is None:
            raise RuntimeError("text tower was released; create a new SiglipEmbedder to embed more texts")
        out = []
        with Timer(self.meter), self.torch.inference_mode():
            for i in range(0, len(texts), 64):
                inputs = self.processor(text=texts[i:i + 64], padding="max_length", max_length=64, truncation=True,
                                        return_tensors="pt").to(self.device)
                ids = inputs["input_ids"]
                real = int((ids != self.pad_id).sum()) if self.pad_id is not None else int(ids.numel())
                self.meter.add(texts_embedded=int(ids.shape[0]), text_tokens=real, flops=int(ids.numel()) * self._flops_text_tok)
                feats = _tensor(self.model.get_text_features(**inputs))
                out.append(feats.float().cpu().numpy())
        return normalize(np.concatenate(out)) if out else np.zeros((0, self.dim), np.float32)

    def release_text_tower(self) -> None:
        """Free ~1 GB once all category texts are embedded (they are cached on disk)."""
        if getattr(self.model, "text_model", None) is not None:
            self.model.text_model = None
            import gc

            gc.collect()
