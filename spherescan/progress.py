"""Turn Spirula's own output into real progress and a time-remaining estimate.

Only exact, known line formats count. (An earlier version matched any "NN%" and
read a memory warning's "97%" as progress.) Formats were taken from a real run:

  train   step    601/15000 (  4%)  splats 48554  [elapsed 00:03:56 | ETA 02:11:54]
  sfm     120/240   frame_0001.jpg   Features: 8123
  sfm     5000/21000 pairs matched
  train   - Projected peak: 6.23 +/- 1.40 GiB   free for training: 3.62 GiB   chance of running out: 97%.
  mesh    work fraction in <progress-dir>/memory.json
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_STEP = re.compile(r"\bstep\s+(\d+)/(\d+)\s*\(")
_TRAIN_ETA = re.compile(r"\|\s*ETA\s+(\d+):(\d{2}):(\d{2})")
_FEATURES = re.compile(r"\b(\d+)/(\d+)\s+.*\bFeatures:")
_PAIRS = re.compile(r"\b(\d+)/(\d+) pairs matched(?! for the focal)")
_MAPPING_DONE = re.compile(r"\bMapping:\s+\d|RESULT:")
_FORECAST = re.compile(r"Projected peak:\s*([\d.]+)\s*\+/-\s*([\d.]+)\s*GiB\s+free for training:\s*([\d.]+)\s*GiB"
                       r"\s+chance of running out:\s*(\d+)%")

# How the structure-from-motion stage splits: features, matching, then mapping.
SFM_FEATURES_END = 0.30
SFM_MATCHING_END = 0.60


@dataclass
class Forecast:
    peak_gib: float
    sigma_gib: float
    free_gib: float
    chance_oom: int


class StageParser:
    """Feeds on one stage's output lines; `fraction` only ever goes up."""

    def __init__(self, stage: str) -> None:
        self.stage = stage
        self.fraction = 0.0
        self.own_eta: Optional[float] = None       # seconds, when Spirula states one
        self.forecast: Optional[Forecast] = None
        self.mapping = False                        # sfm: features and matching are finished

    def _set(self, f: float) -> None:
        self.fraction = max(self.fraction, min(1.0, f))

    def feed(self, line: str) -> None:
        if self.stage == "train":
            m = _STEP.search(line)
            if m and int(m[2]) > 0:
                self._set(int(m[1]) / int(m[2]))
                e = _TRAIN_ETA.search(line)
                self.own_eta = int(e[1]) * 3600 + int(e[2]) * 60 + int(e[3]) if e else None
                return
            f = _FORECAST.search(line)
            if f:
                self.forecast = Forecast(float(f[1]), float(f[2]), float(f[3]), int(f[4]))
        elif self.stage == "sfm":
            m = _FEATURES.search(line)
            if m and int(m[2]) > 0:
                self._set(SFM_FEATURES_END * int(m[1]) / int(m[2]))
                return
            m = _PAIRS.search(line)
            if m and int(m[2]) > 0:
                self._set(SFM_FEATURES_END + (SFM_MATCHING_END - SFM_FEATURES_END) * int(m[1]) / int(m[2]))
                if int(m[1]) >= int(m[2]):
                    self.mapping = True
                return
            if _MAPPING_DONE.search(line):
                self._set(1.0)

    def finish(self) -> None:
        self.fraction = 1.0


def read_mesh_progress(progress_dir: Path) -> Optional[float]:
    """Mesh work fraction (0..1) from the file `spirula mesh --progress-dir` rewrites twice a second."""
    try:
        w = float(json.loads((progress_dir / "memory.json").read_text()).get("work", -1))
    except (OSError, ValueError, TypeError):
        return None
    return w if 0.0 <= w <= 1.0 else None


def mapping_time_fraction(stage_elapsed: float, in_mapping_for: float, expected_stage_s: float) -> float:
    """Mapping prints no running count, so move through its share of the bar by time, never past 97%."""
    share = 1.0 - SFM_MATCHING_END
    expected = max(expected_stage_s * share, 30.0)
    return SFM_MATCHING_END + share * min(0.97, in_mapping_for / expected)


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def eta_seconds(order: list, predicted: dict, current: str, fraction: float, elapsed_total: float,
                stage_elapsed: float, own_eta: Optional[float] = None) -> Optional[float]:
    """Seconds left for the whole run. Rough by nature: later stages scale with how this machine has run so far."""
    i = order.index(current)
    done = sum(predicted[s] for s in order[:i]) + fraction * predicted[current]
    ratio = clamp(elapsed_total / done, 0.3, 8.0) if done > 30 and elapsed_total > 30 else 1.0
    if own_eta is not None:
        here = own_eta
    elif fraction >= 0.03 and stage_elapsed > 20:
        here = stage_elapsed * (1 - fraction) / fraction
    else:
        here = predicted[current] * ratio * (1 - fraction)
    later = sum(predicted[s] for s in order[i + 1:]) * ratio
    return max(0.0, here + later)


def human_left(seconds: Optional[float]) -> str:
    if seconds is None:
        return ""
    m = int(round(seconds / 60))
    if m < 1:
        return "under a minute"
    if m < 60:
        return f"about {m} min"
    h, mm = divmod(m, 60)
    return f"about {h} h {mm:02d} min"
