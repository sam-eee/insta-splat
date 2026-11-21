"""Fit a detail level into the graphics memory this computer has.

Two layers, because a memory model is only ever a guess:
  1. plan(): before the run, pick the gentlest step on a fixed ladder that the
     model says fits, so most runs never fail.
  2. The pipeline retries one step further down the same ladder if Spirula
     still reports it ran out of memory (see pipeline.py).

Each step on the ladder keeps what matters most for a mesh for longest: first
the splat budget shrinks, then training images are capped in size, and from
there training starts at low resolution and grows (progressive resolution).
Nothing else is touched, so the result is the same kind of model with less
fine detail, not a different kind.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .hardware import Hardware
from .presets import Detail

MAX_STEP = 8
MIN_SPLATS_M = 0.15
SPLAT_DECAY = 0.75                  # each step keeps 75% of the previous splat budget
RES_CAPS = {2: 2000, 3: 1600, 4: 1400, 5: 1200, 6: 1000, 7: 900, 8: 800}   # long edge, px
FULL_RES_PX = 2900                  # a typical extracted fisheye/face frame

# Rough memory model, anchored on Spirula's claim of 10M splats in 8 GB.
# Meshing trains without view-dependent colour, so splats are cheaper than that claim.
BASE_GB = 1.2
PER_MSPLAT_GB = 0.55
IMAGES_GB_AT_FULL = 1.3
HEADROOM_GB = 0.5                   # the desktop and driver keep some
UNKNOWN_VRAM_GB = 4.0               # assume modest when the card's memory could not be read

_OOM = re.compile(r"out[ _]of[ _](device[ _]|host[ _])?memory|failed to allocate|allocation failed|"
                  r"cannot allocate|insufficient (gpu |device )?memory|\boom\b|vkallocatememory", re.I)


def looks_like_oom(lines: list) -> bool:
    return any(_OOM.search(l) for l in lines)


@dataclass(frozen=True)
class MemoryPlan:
    step: int
    splats_m: float
    max_resolution: int             # 0 = no cap
    progressive: bool
    needed_gb: float
    budget_gb: Optional[float]
    kept: float                     # fraction of this level's normal splat budget
    fits: bool

    @property
    def reduced(self) -> bool:
        return self.step > 0

    @property
    def cap_max(self) -> int:
        return int(self.splats_m * 1e6)

    @property
    def time_factor(self) -> float:
        """Extra time from working in smaller batches to save memory."""
        return 1.0 + 0.04 * self.step

    def note(self) -> str:
        if not self.reduced:
            return ""
        bits = [f"about {self.kept * 100:.0f}% of this level's usual detail points"]
        if self.max_resolution:
            bits.append(f"training pictures limited to {self.max_resolution} px")
        if self.progressive:
            bits.append("starting coarse and sharpening")
        tail = "" if self.fits else " It may still run out of memory; the app will retry with less automatically."
        return "To fit your graphics memory this uses " + ", ".join(bits) + "." + tail


def need_gb(splats_m: float, max_res: int) -> float:
    res = min(max_res or FULL_RES_PX, FULL_RES_PX)
    return BASE_GB + PER_MSPLAT_GB * splats_m + IMAGES_GB_AT_FULL * (res / FULL_RES_PX) ** 2


def budget_gb(hw: Optional[Hardware]) -> float:
    vram = hw.usable_vram_gb if hw and hw.usable_vram_gb else UNKNOWN_VRAM_GB
    return max(0.0, vram - HEADROOM_GB)


def plan_at(detail: Detail, hw: Optional[Hardware], step: int) -> MemoryPlan:
    step = max(0, min(MAX_STEP, step))
    splats = detail.splats if step == 0 else max(MIN_SPLATS_M, detail.splats * SPLAT_DECAY ** step)
    res = RES_CAPS.get(step, 0)
    budget = budget_gb(hw)
    need = need_gb(splats, res)
    return MemoryPlan(step, round(splats, 3), res, step >= 4, round(need, 2),
                      round(budget, 1), splats / detail.splats, need <= budget)


def plan(detail: Detail, hw: Optional[Hardware]) -> MemoryPlan:
    """The gentlest step that is expected to fit, or the smallest one if none does."""
    for s in range(MAX_STEP + 1):
        p = plan_at(detail, hw, s)
        if p.fits:
            return p
    return plan_at(detail, hw, MAX_STEP)


def can_run_at_all(hw: Optional[Hardware]) -> bool:
    smallest = need_gb(MIN_SPLATS_M, RES_CAPS[MAX_STEP])
    return budget_gb(hw) >= smallest
