"""What a classification run used: which model (exact id and revision) ran where, tokens, compute, money.

Each embedder / LLM classifier owns a Meter: an identity card (`info`) plus usage counters. A run
snapshots the counters at the start and reports the difference, together with process CPU time,
wall time and peak memory. Prices are list prices used as estimates; override them in the config:

    "pricing": {"claude-haiku-4-5": {"input_per_mtok": 1, "output_per_mtok": 5},
                "multimodalembedding@001": {"per_image": 0.0001, "per_1k_text_chars": 0.0002}}
"""
from __future__ import annotations

import os
import platform
import subprocess
import sys
import time
from dataclasses import dataclass, field
from functools import lru_cache

# USD list prices used as defaults (checked 2026-09; always confirm on the provider's pricing page)
CLAUDE_PRICES = {  # $ per million tokens: (input, output)
    "claude-fable-5-1": (10.0, 50.0), "claude-opus-5-5": (4.0, 20.0), "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5-5": (2.0, 10.0), "claude-sonnet-5": (2.0, 10.0), "claude-haiku-4-5": (1.0, 5.0),
}
VERTEX_PRICES = {"per_image": 0.0001, "per_1k_text_chars": 0.0002}
# rough per-image token use for a 640px thumbnail plus the category list, for the "before you press Sort" estimate
LLM_TOKENS_PER_IMAGE = (1100, 60)

COUNTERS = ("images_embedded", "image_tokens", "texts_embedded", "text_tokens", "cache_hits", "model_secs",
            "api_calls", "input_tokens", "output_tokens", "api_images", "api_text_chars", "flops")


@dataclass
class Meter:
    info: dict
    c: dict = field(default_factory=lambda: dict.fromkeys(COUNTERS, 0))

    def add(self, **kw) -> None:
        for k, v in kw.items():
            self.c[k] = self.c.get(k, 0) + v

    def snapshot(self) -> dict:
        return dict(self.c)

    def since(self, snap: dict | None) -> dict:
        snap = snap or {}
        return {k: self.c.get(k, 0) - snap.get(k, 0) for k in self.c}

    def cost(self, u: dict) -> float | None:
        """USD for usage u, or None when no price is known."""
        p = self.info.get("pricing") or {}
        if not p:
            return 0.0 if self.info.get("local") else None
        total = 0.0
        if "input_per_mtok" in p:
            total += u.get("input_tokens", 0) / 1e6 * p["input_per_mtok"] + u.get("output_tokens", 0) / 1e6 * p.get("output_per_mtok", 0)
        if "per_image" in p:
            total += u.get("api_images", 0) * p["per_image"] + u.get("api_text_chars", 0) / 1000 * p.get("per_1k_text_chars", 0)
        return total


class Timer:
    """with Timer(meter): ...  adds the elapsed seconds to meter's model_secs."""

    def __init__(self, meter: Meter):
        self.meter = meter

    def __enter__(self):
        self.t = time.perf_counter()
        return self

    def __exit__(self, *exc):
        self.meter.add(model_secs=time.perf_counter() - self.t)


@lru_cache(maxsize=1)
def machine() -> str:
    """'Apple M1 Pro · 10 cores · 16 GB' (best effort)."""
    name = platform.processor() or platform.machine()
    try:
        if sys.platform == "darwin":
            name = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True, timeout=2).stdout.strip() or name
            mem = int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=2).stdout.strip() or 0)
        elif sys.platform.startswith("linux"):
            with open("/proc/cpuinfo", encoding="utf-8", errors="replace") as f:
                name = next((ln.split(":", 1)[1].strip() for ln in f if ln.startswith("model name")), name)
            mem = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        elif sys.platform == "win32":
            name, mem = _windows_machine(name)
        else:
            mem = 0
    except Exception:  # noqa: BLE001
        mem = 0
    parts = [name, f"{os.cpu_count()} cores"]
    if mem:
        parts.append(f"{mem / 2**30:.0f} GB RAM")
    return " · ".join(p for p in parts if p)


def _windows_machine(name: str) -> tuple[str, int]:
    """(processor name, bytes of RAM) on Windows."""
    import ctypes
    import winreg

    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
        name = str(winreg.QueryValueEx(k, "ProcessorNameString")[0]).strip() or name

    class MemoryStatus(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong)] + \
                   [(f, ctypes.c_ulonglong) for f in ("total", "avail", "total_page", "avail_page", "total_virtual",
                                                      "avail_virtual", "avail_extended")]

    st = MemoryStatus()
    st.dwLength = ctypes.sizeof(st)
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(MemoryStatus)]
    ok = kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
    return name, int(st.total) if ok else 0


def peak_rss() -> int:
    """Peak resident memory of this process in bytes (0 when unknown)."""
    try:
        import resource                     # macOS and Linux
    except ImportError:
        return _windows_peak_rss()
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(r if sys.platform == "darwin" else r * 1024)


def _windows_peak_rss() -> int:
    try:
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + \
                       [(f, ctypes.c_size_t) for f in ("PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                                                       "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
                                                       "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]

        c = Counters()
        c.cb = ctypes.sizeof(c)
        kernel32 = ctypes.WinDLL("kernel32")       # a private handle, so the types below don't leak to other code
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.GetCurrentProcess.argtypes = []
        kernel32.K32GetProcessMemoryInfo.restype = wintypes.BOOL
        kernel32.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        if not kernel32.K32GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(c), c.cb):
            return 0
        return int(c.PeakWorkingSetSize)
    except Exception:  # noqa: BLE001 - only used for the "what it used" report
        return 0


def pricing_for(model: str, overrides: dict | None, default: dict | None) -> dict | None:
    if overrides and model in overrides:
        return dict(overrides[model])
    return default


def claude_pricing(model: str, overrides: dict | None = None) -> dict | None:
    p = CLAUDE_PRICES.get(model)
    return pricing_for(model, overrides, {"input_per_mtok": p[0], "output_per_mtok": p[1]} if p else None)


def planned(cfg) -> dict:
    """What will run, before anything is loaded: for the estimate next to the Sort button."""
    prov, model = cfg.provider, cfg.model_name
    over = getattr(cfg, "pricing", None) or {}
    if prov == "siglip":
        return {"provider": "SigLIP 2", "model": model, "where": "on this computer", "local": True, "kind": "local"}
    if prov == "vertex":
        e = cfg.embeddings
        return {"provider": "Vertex AI", "model": model, "where": f"Google Cloud · {e.get('location', 'us-central1')}",
                "local": False, "kind": "vertex", "pricing": pricing_for(model, over, VERTEX_PRICES)}
    if prov == "claude":
        return {"provider": "Claude", "model": model, "where": "Anthropic API", "local": False, "kind": "llm",
                "pricing": claude_pricing(model, over)}
    return {"provider": prov.title(), "model": model, "where": "Google AI API", "local": False, "kind": "llm",
            "pricing": pricing_for(model, over, None)}


def estimate_cost(cfg, n_images: int) -> float | None:
    """Rough USD for sorting n images (0 for local models, None when no price is known)."""
    p = planned(cfg)
    if p["local"]:
        return 0.0
    pr = p.get("pricing")
    if not pr:
        return None
    if p["kind"] == "vertex":
        return n_images * pr.get("per_image", 0)
    i, o = LLM_TOKENS_PER_IMAGE
    return n_images * (i / 1e6 * pr.get("input_per_mtok", 0) + o / 1e6 * pr.get("output_per_mtok", 0))


def meters_of(classifier) -> list[Meter]:
    if classifier is None:
        return []
    f = getattr(classifier, "meters", None)
    return list(f()) if f else []


class RunMeter:
    """Usage of one run across all meters of a classifier, plus process CPU / wall / memory."""

    def __init__(self, classifier, fresh_load: bool = False):
        self.meters = meters_of(classifier)
        self.snaps = [None if fresh_load else m.snapshot() for m in self.meters]
        self.cpu0, self.t0 = time.process_time(), time.perf_counter()
        self.t_end: float | None = None
        self.cpu_end: float | None = None

    def stop(self) -> None:
        self.t_end, self.cpu_end = time.perf_counter(), time.process_time()

    def report(self) -> dict:
        models, total, known = [], 0.0, True
        for m, snap in zip(self.meters, self.snaps):
            u = m.since(snap)
            c = m.cost(u)
            if c is None:
                known = False
            else:
                total += c
            models.append({"info": m.info, "u": u, "cost": c})
        wall = (self.t_end or time.perf_counter()) - self.t0
        cpu = (self.cpu_end or time.process_time()) - self.cpu0
        agg = {k: sum(x["u"].get(k, 0) for x in models) for k in COUNTERS}
        return {"models": models, "wall": wall, "cpu": cpu, "peak_rss": peak_rss(), "cost": total if known else None,
                "cost_partial": total, "u": agg, "machine": machine()}


def fmt_num(n: float) -> str:
    n = float(n)
    for unit, div in (("T", 1e12), ("G", 1e9), ("M", 1e6), ("k", 1e3)):
        if abs(n) >= div:
            return f"{n / div:.1f}{unit}"
    return f"{n:.0f}"


def fmt_usd(x: float | None) -> str:
    if x is None:
        return "price not set"
    if x == 0:
        return "$0.00"
    return f"${x:.4f}" if x < 0.01 else f"${x:.2f}"


def summary_lines(rep: dict) -> list[str]:
    """Plain-text receipt for the CLI."""
    out = []
    for m in rep["models"]:
        i, u = m["info"], m["u"]
        out.append(f"model   {i.get('model')}" + (f" @ {i['version']}" if i.get("version") else "") + f"  ({i.get('provider', '')})")
        out.append(f"where   {i.get('where', '')}")
        if i.get("weights"):
            out.append(f"weights {i['weights']} ({i.get('disk', 0) / 2**30:.1f} GB on disk, from {i.get('source', '')})")
        bits = []
        if u.get("images_embedded"):
            bits.append(f"{u['images_embedded']} image(s) computed")
        if u.get("cache_hits"):
            bits.append(f"{u['cache_hits']} from cache")
        if u.get("image_tokens") or u.get("text_tokens"):
            bits.append(f"{fmt_num(u['image_tokens'])} image tokens, {fmt_num(u['text_tokens'])} text tokens")
        if u.get("input_tokens") or u.get("output_tokens"):
            bits.append(f"{fmt_num(u['input_tokens'])} input / {fmt_num(u['output_tokens'])} output tokens")
        if u.get("api_calls"):
            bits.append(f"{u['api_calls']} API call(s)")
        if u.get("flops"):
            bits.append(f"≈{fmt_num(u['flops'])}FLOP")
        out.append("usage   " + (", ".join(bits) or "nothing"))
        out.append(f"cost    {fmt_usd(m['cost'])}" + ("  (local model: no API cost)" if i.get("local") else "  (estimate from list prices)"))
    out.append(f"compute {rep['wall']:.1f}s wall, {rep['cpu']:.1f}s CPU, peak memory {rep['peak_rss'] / 2**30:.1f} GB on {rep['machine']}")
    return out


RUNS_NAME = "runs.jsonl"


def run_record(rep: dict, images: int, labeled: int, review: int, load_secs: float = 0.0) -> dict:
    """A compact, JSON-safe line for .sorter/runs.jsonl (the spending log of this photo folder)."""
    from datetime import datetime, timezone

    m = rep["models"][0]["info"] if rep["models"] else {}
    return {
        "ts": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "provider": m.get("provider"), "model": m.get("model"), "version": m.get("version"), "where": m.get("where"),
        "models": [x["info"].get("model") for x in rep["models"]],
        "images": images, "labeled": labeled, "review": review,
        "computed": rep["u"].get("images_embedded", 0), "cached": rep["u"].get("cache_hits", 0),
        "image_tokens": rep["u"].get("image_tokens", 0), "text_tokens": rep["u"].get("text_tokens", 0),
        "input_tokens": rep["u"].get("input_tokens", 0), "output_tokens": rep["u"].get("output_tokens", 0),
        "api_calls": rep["u"].get("api_calls", 0), "flops": rep["u"].get("flops", 0),
        "wall_secs": round(rep["wall"], 2), "cpu_secs": round(rep["cpu"], 2), "model_secs": round(rep["u"].get("model_secs", 0), 2),
        "load_secs": round(load_secs, 2), "peak_rss": rep["peak_rss"], "cost_usd": rep["cost"], "machine": rep["machine"],
    }


def append_run(storage, record: dict) -> None:
    from .storage import append_jsonl

    append_jsonl(storage, RUNS_NAME, record)


def load_runs(storage) -> list[dict]:
    from .storage import read_jsonl

    return read_jsonl(storage, RUNS_NAME)


def totals(runs: list[dict]) -> dict:
    known = [r["cost_usd"] for r in runs if r.get("cost_usd") is not None]
    return {
        "runs": len(runs), "images": sum(r.get("images", 0) for r in runs), "computed": sum(r.get("computed", 0) for r in runs),
        "tokens": sum(r.get("image_tokens", 0) + r.get("text_tokens", 0) + r.get("input_tokens", 0) + r.get("output_tokens", 0) for r in runs),
        "flops": sum(r.get("flops", 0) for r in runs), "wall_secs": sum(r.get("wall_secs", 0) for r in runs),
        "cpu_secs": sum(r.get("cpu_secs", 0) for r in runs), "cost_usd": sum(known),
        "cost_unknown": any(r.get("cost_usd") is None for r in runs),
    }
