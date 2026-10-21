"""Vision classifiers. Each returns a Verdict for one image given the category map."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass

from .metering import Meter, claude_pricing, pricing_for


@dataclass
class Verdict:
    category: str
    confidence: float
    reason: str
    scores: dict[str, float] | None = None   # top candidates -> blended score (embedding providers)
    method: str = ""                          # e.g. "siglip+knn", "claude"


def _prompt(categories: dict[str, str], is_video: bool) -> str:
    kind = "video (you are seeing its poster frame)" if is_video else "photo"
    lines = "\n".join(f"- {name}: {desc}" for name, desc in categories.items())
    return (
        f"Classify this {kind} into exactly one of these categories. Names that contain ' › ' are "
        f"folder paths (parent › child); the parents tell you the context of the leaf.\n\n{lines}\n\n"
        "Return the category name exactly as written, a confidence between 0 and 1 "
        "(how sure you are that this is the right category), and a one-sentence reason."
    )


def _schema(categories: dict[str, str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "category": {"type": "string", "enum": list(categories)},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "reason": {"type": "string"},
        },
        "required": ["category", "confidence", "reason"],
        "additionalProperties": False,
    }


def _parse(text: str, categories: dict[str, str]) -> Verdict:
    d = json.loads(text)
    cat = d["category"]
    if cat not in categories:
        raise ValueError(f"model returned unknown category {cat!r}")
    return Verdict(cat, max(0.0, min(1.0, float(d["confidence"]))), str(d.get("reason", "")).strip())


class ClaudeClassifier:
    """Anthropic Messages API with a JSON-schema constrained output."""

    def __init__(self, model: str, effort: str = "low", pricing: dict | None = None):
        import anthropic  # lazy so the gemini path doesn't need it

        self.anthropic = anthropic
        self.client = anthropic.Anthropic(max_retries=4)
        self.model = model
        self.effort = effort
        self.meter = Meter(info={"provider": "Claude (Anthropic)", "model": model, "version": None,
                                 "where": "Anthropic API (cloud)", "local": False, "effort": effort,
                                 "pricing": claude_pricing(model, pricing)})

    def meters(self):
        return [self.meter]

    def classify(self, image: bytes, mime: str, categories: dict[str, str], is_video: bool) -> Verdict:
        import base64

        t0 = time.perf_counter()
        resp = self.client.beta.messages.create(
            model=self.model,
            max_tokens=512,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": _schema(categories)}},
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": mime,
                                                 "data": base64.standard_b64encode(image).decode()}},
                    {"type": "text", "text": _prompt(categories, is_video)},
                ],
            }],
        )
        u = getattr(resp, "usage", None)
        self.meter.add(api_calls=1, images_embedded=1, model_secs=time.perf_counter() - t0,
                       input_tokens=int(getattr(u, "input_tokens", 0) or 0) + int(getattr(u, "cache_read_input_tokens", 0) or 0),
                       output_tokens=int(getattr(u, "output_tokens", 0) or 0))
        if getattr(resp, "model", None):
            self.meter.info["version"] = resp.model          # the exact model that answered (fallbacks included)
        if resp.stop_reason == "refusal":
            details = getattr(resp, "stop_details", None)
            why = getattr(details, "explanation", None) or getattr(details, "category", None) or "refusal"
            raise ClassifyError(f"model declined: {why}")
        if resp.stop_reason == "max_tokens":
            raise ClassifyError("response truncated (max_tokens)")
        text = next((b.text for b in resp.content if b.type == "text"), "")
        return _parse(text, categories)

    def is_fatal(self, e: Exception) -> bool:
        """Errors that will hit every item; stop instead of writing 84 error rows."""
        a = self.anthropic
        return isinstance(e, (a.AuthenticationError, a.PermissionDeniedError, a.NotFoundError)) or (
            isinstance(e, TypeError) and "authentication method" in str(e))

    def describe_error(self, e: Exception) -> str:
        a = self.anthropic
        if isinstance(e, a.AuthenticationError) or (isinstance(e, TypeError) and "authentication method" in str(e)):
            return "no valid Anthropic credentials. Set ANTHROPIC_API_KEY (see .env.example)."
        if isinstance(e, a.NotFoundError):
            return f"model not found: {self.model!r}"
        if isinstance(e, a.RateLimitError):
            return "rate limited (retries exhausted)"
        if isinstance(e, a.APIStatusError):
            return f"API error {e.status_code}: {e.message}"
        if isinstance(e, a.APIConnectionError):
            return f"network error: {e}"
        return f"{type(e).__name__}: {e}"


class GeminiClassifier:
    """google-genai with response_schema (same approach as the original coral_sorter script)."""

    def __init__(self, model: str, effort: str = "low", pricing: dict | None = None):
        try:
            from google import genai
            from google.genai import types
        except ImportError as e:
            raise ImportError("google-genai is not installed: run `uv sync --extra gemini`") from e

        self.genai, self.types = genai, types
        self.client = genai.Client()
        self.model = model
        self.meter = Meter(info={"provider": "Gemini (Google AI)", "model": model, "version": None,
                                 "where": "Google AI API (cloud)", "local": False, "pricing": pricing_for(model, pricing, None)})

    def meters(self):
        return [self.meter]

    def classify(self, image: bytes, mime: str, categories: dict[str, str], is_video: bool) -> Verdict:
        t0 = time.perf_counter()
        r = self.client.models.generate_content(
            model=self.model,
            contents=[self.types.Part.from_bytes(data=image, mime_type=mime), _prompt(categories, is_video)],
            config=self.types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=_schema(categories),
            ),
        )
        um = getattr(r, "usage_metadata", None)
        self.meter.add(api_calls=1, images_embedded=1, model_secs=time.perf_counter() - t0,
                       input_tokens=int(getattr(um, "prompt_token_count", 0) or 0),
                       output_tokens=int(getattr(um, "candidates_token_count", 0) or 0))
        if getattr(r, "model_version", None):
            self.meter.info["version"] = r.model_version
        return _parse(r.text, categories)

    def is_fatal(self, e: Exception) -> bool:
        msg = str(e).lower()
        return "api key" in msg or "permission" in msg or "not found" in msg

    def describe_error(self, e: Exception) -> str:
        return f"{type(e).__name__}: {e}"


class ClassifyError(RuntimeError):
    pass


def make_classifier(provider: str, model: str, effort: str = "low", pricing: dict | None = None, **kwargs):
    """LLM classifiers only. Embedding providers are built by embedding_classifier.build_embedding_classifier."""
    if provider == "claude":
        return ClaudeClassifier(model, effort, pricing)
    if provider == "gemini":
        return GeminiClassifier(model, effort, pricing)
    raise ValueError(f"unknown LLM provider {provider!r}")
