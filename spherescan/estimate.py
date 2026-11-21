"""How long video -> mesh will take, from the footage and this computer.

The constants are a starting guess calibrated to an RTX 3060-class GPU. Every
finished run records how far off each stage was, and later estimates are
scaled by that, so the numbers get better on the machine that uses them.
"""

from __future__ import annotations

from typing import Optional

from . import memory
from .hardware import Hardware
from .presets import DETAILS, Detail
from .util import config_dir, load_json, save_json

STAGES = ("extract", "sfm", "train", "mesh")
STAGE_LABEL = {"extract": "Reading video", "sfm": "Working out camera positions",
               "train": "Building the 3D model", "mesh": "Making the mesh"}

_CAL = "calibration.json"


def frames_for(detail: Detail, duration_s: float, lenses: int) -> int:
    """Camera positions to keep. Capped so short clips are not padded out."""
    return max(10, min(detail.frames, int(duration_s * 2)))


def _speed(hw: Optional[Hardware]) -> float:
    g = hw.best_gpu() if hw else None
    return g.speed if g else 0.6


def stage_seconds(detail: Detail, duration_s: float, lenses: int, hw: Optional[Hardware],
                  plan: Optional[memory.MemoryPlan] = None) -> dict:
    speed = _speed(hw)
    plan = plan or memory.plan(detail, hw)
    frames = frames_for(detail, duration_s, lenses)
    images = frames * lenses

    hw_decode = bool(hw and hw.decode and hw.decode.hwaccel)
    cpu = (hw.cpu_score if hw else 1.0) or 1.0
    decode = duration_s * lenses * (0.12 if hw_decode else 0.75 / max(cpu, 0.2))      # real-time multiples; software decode is CPU-bound
    extract = decode + images * 0.08

    sfm_q = {"low": 0.6, "medium": 1.0, "high": 1.8}[detail.sfm_quality]
    sfm = 0.2 * (images ** 1.1) * sfm_q / max(speed, 0.1) ** 0.6

    its_per_s = 5.0 * speed / (1.0 + 0.25 * plan.splats_m)
    train = detail.iterations / max(its_per_s, 0.05) * plan.time_factor

    mesh = (45 + plan.splats_m * 25 + (detail.texture_size / 4096) ** 2 * 20) * (
        0.6 / max(speed, 0.1) ** 0.7 + 0.4 / max(cpu, 0.2) ** 0.8)
    return {"extract": extract, "sfm": sfm, "train": train, "mesh": mesh}


def calibration() -> dict:
    return load_json(config_dir() / _CAL, {})


def record_run(predicted: dict, actual: dict) -> None:
    """Fold a finished run's measured stage times into the correction ratios."""
    cal = calibration()
    for s in STAGES:
        if predicted.get(s, 0) > 5 and actual.get(s, 0) > 0:
            ratio = min(4.0, max(0.3, actual[s] / predicted[s]))
            old = cal.get(s, 1.0)
            cal[s] = round(0.6 * ratio + 0.4 * old if s in cal else ratio, 3)
    save_json(config_dir() / _CAL, cal)


def estimate(detail: Detail, duration_s: float, lenses: int, hw: Optional[Hardware]) -> dict:
    cal = calibration()
    raw = stage_seconds(detail, duration_s, lenses, hw, memory.plan(detail, hw))
    stages = {s: raw[s] * cal.get(s, 1.0) for s in STAGES}
    total = sum(stages.values())
    calibrated = bool(cal)
    g = hw.best_gpu() if hw else None
    spread = (0.85, 1.3) if calibrated else (0.7, 1.8) if (g and g.spec_known) else (0.55, 2.4)
    return {"level": detail.level, "stages": stages, "raw_stages": raw, "total_s": total,
            "low_s": total * spread[0], "high_s": total * spread[1], "calibrated": calibrated}


def available(detail: Detail, hw: Optional[Hardware]) -> tuple:
    """(ok, reason). Levels shrink to fit; only a card too small for any run is refused."""
    if not memory.can_run_at_all(hw):
        return False, "This computer's graphics memory is too small to build a 3D model."
    return True, ""


def human(seconds: float) -> str:
    m = int(round(seconds / 60.0))
    if m < 2:
        return "about a minute" if seconds < 90 else "about 2 minutes"
    if m < 90:
        return f"{5 * round(m / 5) if m >= 20 else m} min"
    h = seconds / 3600
    return f"{h:.1f} hours"


def human_range(low: float, high: float) -> str:
    a, b = human(low), human(high)
    return a if a == b else f"{a} to {b}"


def all_levels(duration_s: float, lenses: int, hw: Optional[Hardware]) -> list:
    out = []
    for d in DETAILS.values():
        e = estimate(d, duration_s, lenses, hw)
        ok, why = available(d, hw)
        mp = memory.plan(d, hw)
        out.append({**e, "reduced": mp.reduced, "memory_note": mp.note(), "kept_pct": round(mp.kept * 100),
                    "name": d.name, "blurb": d.blurb, "available": ok, "why": why,
                    "text": human_range(e["low_s"], e["high_s"]), "total_text": human(e["total_s"]),
                    "frames": frames_for(d, duration_s, lenses),
                    "stage_text": {s: human(v) for s, v in e["stages"].items()}})
    return out
