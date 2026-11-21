"""An offline library of graphics cards and CPUs, and how fast each is relative to a reference.

Why a library: the same run takes minutes on one card and hours on another, and the
page must say so before anything starts, without benchmarking and without internet.

The model. Training Gaussian splats is a mix of arithmetic (rasterising, optimising) and
memory traffic (sorting, reading the splats). So relative speed is

    speed = (TFLOPS / 12.7) ** 0.6  *  (bandwidth / 360) ** 0.4        (RTX 3060 = 1.0)

The exponents are a starting point fitted on one measured run; every finished run on a
machine corrects the estimate for that machine (see estimate.py). Specs are approximate
public figures (FP32 at boost clock). Add or fix entries in `gpus.json` / `cpus.json`
inside the SphereScan config folder; those override the built-in ones.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

from .util import app_root, config_dir

REF_TFLOPS = 12.7          # RTX 3060
REF_BANDWIDTH = 360.0      # GB/s
ALPHA = 0.6                # weight of arithmetic
BETA = 0.4                 # weight of memory bandwidth

_NOISE = {"nvidia", "geforce", "corporation", "amd", "radeon", "advanced", "micro", "devices", "inc", "ati",
          "r", "tm", "gpu", "controller", "vga", "compatible", "display", "3d", "series", "edition", "rev",
          "graphics" , "card", "with", "design", "mobile_"}
# Words that make two otherwise equal names different products: 4070 vs 4070 Ti vs 4070 Laptop.
_VARIANT = {"laptop", "ti", "super", "xt", "xtx", "gre", "pro", "max", "ultra"}


def tokens(name: str) -> frozenset:
    s = name.lower().replace("max-q", "maxq")
    toks = re.findall(r"[a-z0-9]+", s)
    toks = ["laptop" if t in ("mobile", "maxq") else t for t in toks]
    if "laptop" in toks:
        toks = [t for t in toks if t not in ("max", "q")]
    # "graphics" only identifies integrated parts, so keep it just when nothing else names the product
    out = [t for t in toks if t not in _NOISE]
    if not out:
        out = [t for t in toks if t != "gpu"]
    return frozenset(out)


def _load(builtin: str, user: str, key: str) -> list:
    rows = []
    for path in (config_dir() / user, app_root() / "spherescan" / "data" / builtin,
                 Path(__file__).parent / "data" / builtin):
        try:
            rows += json.loads(path.read_text(encoding="utf-8")).get(key, [])
        except (OSError, ValueError):
            continue
    seen, out = set(), []
    for r in rows:                     # user file first, so it wins
        if r.get("name") not in seen:
            seen.add(r["name"])
            out.append(r)
    return out


@lru_cache(maxsize=1)
def gpus() -> tuple:
    return tuple(_load("gpus.json", "gpus.json", "gpus"))


@lru_cache(maxsize=1)
def cpus() -> tuple:
    return tuple(_load("cpus.json", "cpus.json", "cpus"))


def reload() -> None:
    gpus.cache_clear(); cpus.cache_clear()


def _best(query: str, rows) -> Optional[dict]:
    q = tokens(query)
    qv = q & _VARIANT
    best, best_len = None, 0
    for r in rows:
        for alias in (r.get("match") or [r["name"]]):
            e = tokens(alias)
            if not e or not e <= q or (e & _VARIANT) != qv:
                continue
            if len(e) > best_len:
                best, best_len = r, len(e)
    return best


def find_gpu(name: str) -> Optional[dict]:
    return _best(name, gpus())


def find_cpu(name: str) -> Optional[dict]:
    return _best(name, cpus())


def gpu_speed(entry: dict) -> float:
    """Relative training speed (RTX 3060 = 1.0)."""
    return (entry["tflops"] / REF_TFLOPS) ** ALPHA * (entry["bandwidth_gbps"] / REF_BANDWIDTH) ** BETA


def cpu_score(name: Optional[str], threads: int) -> tuple:
    """(score, known). 1.0 = Ryzen 5 5600X. Unknown CPUs are guessed from their thread count."""
    e = find_cpu(name) if name else None
    if e:
        return float(e["score"]), True
    return max(0.2, min(4.0, threads / 12.0)), False


def search(text: str = "", limit: int = 60) -> list:
    t = text.lower()
    rows = [g for g in gpus() if t in g["name"].lower()]
    return sorted(rows, key=lambda g: gpu_speed(g))[:limit]
