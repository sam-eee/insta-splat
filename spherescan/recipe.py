"""Every Spirula command line this app issues, and nothing else does.

If Spirula renames a flag, this is the only file to change. Flags were read
from Spirula's source (src/app/cli/*.cpp, src/config/TrainConfig.h) at the
version in SPIRULA_VERSION; tests/test_contract.py checks them against a real
binary's --help when one is available.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from .memory import FULL_RES_PX, MemoryPlan
from .presets import Detail, MeshFormat

# Insta360 X-series lens: fx = fy ~ 0.27 * image width (Spirula's .insv preset
# uses 0.269). It only seeds the solver, which refines it.
FISHEYE_FOCAL_FACTOR = 0.27
# The 360-camera train preset warps fisheye views to pinhole; the mesh preset does not set it.
WARP_TO_PINHOLE = True


def extract_cmd(spirula: str, video: str, out: str, *, kind: str, paired: bool,
                max_frames: int, skip: int, decoder: str, ffmpeg: Optional[str]) -> list:
    cmd = [spirula, "sam", "extract", video, "--out", out, "--decoder", decoder]
    if ffmpeg:
        cmd += ["--ffmpeg", ffmpeg]
    if paired:
        # Two separate lens files: no lockstep decoding, so use the same fixed
        # spacing on both and match frames by name afterwards.
        cmd += ["--skip", str(skip)]
    else:
        cmd += ["--sync", "--adaptive", "--max-frames", str(max_frames)]
        if kind == "gopro":
            cmd += ["--360", "faces"]
    return cmd


def sfm_cmd(spirula: str, workspace: str, detail: Detail, *, rig_members: list,
            fisheye: bool, frame_width: Optional[int]) -> list:
    # images/ lives inside the workspace so sparse/ lands beside it, where the trainer looks.
    cmd = [spirula, "sfm", "auto", str(Path(workspace) / "images"), "-o", workspace,
           "--data-type", "video", "--quality", detail.sfm_quality]
    if len(rig_members) > 1:
        cmd += ["--rig", ("dual-fisheye=" if fisheye else "") + ",".join(rig_members)]
    if fisheye:
        cmd += ["--camera-model", "thin-prism-fisheye"]
        if frame_width:
            cmd += ["--focal", f"{FISHEYE_FOCAL_FACTOR * frame_width:.1f}"]
    return cmd


def train_cmd(spirula: str, workspace: str, train_dir: str, detail: Detail, plan: MemoryPlan) -> list:
    cmd = [spirula, "train", "meshing", "--data", workspace,
           "--quality", detail.train_quality,
           "--cap-max", str(plan.cap_max),          # explicit; wins over the quality level's own value
           "--output-dir-prefix", train_dir, "--output-dir-name", "run",
           "--disable-viewer", "1", "--keep-viewer-alive", "0"]
    if plan.max_resolution:
        # --train-resolution-divisor exists in every release; --max-resolution only on newer master.
        cmd += ["--train-resolution-divisor", f"{max(1.0, FULL_RES_PX / plan.max_resolution):.2f}"]
    if plan.progressive:
        cmd += ["--progressive-resolution", "1"]
    if WARP_TO_PINHOLE:
        cmd += ["--warp-to-pinhole", "1"]
    return cmd


def mesh_cmd(spirula: str, run_dir: str, workspace: str, out_base: str,
             detail: Detail, fmt: MeshFormat, progress_dir: Optional[str] = None) -> list:
    cmd = [spirula, "mesh", run_dir, "--data", workspace, "--output", out_base,
           "--format", fmt.spirula_format, "--color", fmt.color,
           "--texture-size", str(detail.texture_size)]
    if progress_dir:
        cmd += ["--progress-dir", progress_dir]      # Spirula writes its real work fraction here
    return cmd
