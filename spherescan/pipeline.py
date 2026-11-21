"""Footage -> mesh in one go: extract frames, solve cameras, train, mesh."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from . import estimate as est
from . import memory, recipe
from .footage import Capture, jpeg_size
from .hardware import Hardware, device_env
from .presets import DETAILS, FORMATS
from .util import IS_WINDOWS, engine_env, find_executable, popen_flags

WORK_MARKER = ".spherescan-work"


class Cancelled(Exception):
    pass


class StageError(Exception):
    pass


def find_spirula() -> Optional[str]:
    return find_executable("spirula", "SPHERESCAN_SPIRULA")


@dataclass
class Job:
    captures: list
    detail_level: int
    format_key: str
    out_dir: str
    hw: Hardware
    keep_work: bool = False
    dry_run: bool = False
    # live state (read by the GUI)
    state: str = "idle"                 # idle|running|done|failed|cancelled
    stage: str = ""
    stage_progress: float = 0.0
    overall: float = 0.0
    message: str = ""
    result: Optional[str] = None
    log: list = field(default_factory=list)
    commands: list = field(default_factory=list)
    detail_note: str = ""
    started: float = 0.0
    _proc: Optional[subprocess.Popen] = None
    _cancel: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # ---- public ---------------------------------------------------------
    def start(self) -> None:
        self.state = "running"
        self.started = time.time()
        threading.Thread(target=self._main, daemon=True).start()

    def cancel(self) -> None:
        self._cancel = True
        p = self._proc
        if p and p.poll() is None:
            _kill_tree(p)

    def snapshot(self) -> dict:
        with self._lock:
            return {"state": self.state, "stage": self.stage, "stage_label": est.STAGE_LABEL.get(self.stage, ""),
                    "stage_progress": self.stage_progress, "overall": self.overall, "message": self.message,
                    "result": self.result, "detail_note": self.detail_note, "log": self.log[-60:], "elapsed": time.time() - self.started if self.started else 0,
                    "commands": self.commands if self.dry_run else []}

    # ---- internals ------------------------------------------------------
    def _say(self, line: str) -> None:
        with self._lock:
            self.log.append(line)

    def _main(self) -> None:
        actual: dict = {}
        try:
            spirula = find_spirula()
            if not spirula and not self.dry_run:
                raise StageError("Spirula Studio was not found. Place it in the 'vendor' folder or set SPHERESCAN_SPIRULA.")
            spirula = spirula or "spirula"
            detail = DETAILS[self.detail_level]
            fmt = FORMATS[self.format_key]
            out_dir = Path(self.out_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            name = _safe_name(f"{self.captures[0].label}_detail{detail.level}" +
                              (f"_plus{len(self.captures) - 1}" if len(self.captures) > 1 else ""))
            work = out_dir / f".{name}_work"
            work.mkdir(exist_ok=True)
            (work / WORK_MARKER).write_text("created by SphereScan; safe to delete\n")

            duration = sum(c.duration_s for c in self.captures)
            lenses = 2 if self.captures[0].kind != "gopro" else 10
            mplan = memory.plan(detail, self.hw)
            self.detail_note = mplan.note()
            predicted = est.stage_seconds(detail, duration, lenses, self.hw, mplan)
            weights = est.estimate(detail, duration, lenses, self.hw)["stages"]
            total_w = sum(weights.values()) or 1.0

            def stage(s: str):
                self.stage, self.stage_progress = s, 0.0
                done = sum(weights[x] for x in est.STAGES[:est.STAGES.index(s)])
                self._base = (done / total_w, weights[s] / total_w, weights[s])

            t0 = time.time()
            stage("extract")
            members, width = self._extract(spirula, detail, work)
            actual["extract"] = time.time() - t0

            t0 = time.time(); stage("sfm")
            fisheye = self.captures[0].kind in ("insta360", "dji")
            self._run(recipe.sfm_cmd(spirula, str(work), detail, rig_members=members,
                                     fisheye=fisheye, frame_width=width))
            actual["sfm"] = time.time() - t0

            t0 = time.time(); stage("train")
            self._train(spirula, work, detail, mplan)
            actual["train"] = time.time() - t0

            t0 = time.time(); stage("mesh")
            run_dir = _find_run_dir(work / "train") if not self.dry_run else work / "train" / "run"
            out_base = out_dir / name
            self._run(recipe.mesh_cmd(spirula, str(run_dir), str(work), str(out_base), detail, fmt))
            actual["mesh"] = time.time() - t0

            result = _find_result(out_dir, name, fmt.extension) if not self.dry_run else out_base.with_suffix(fmt.extension)
            if not result:
                raise StageError("Spirula finished but no mesh file was written. See the details below.")
            if not self.dry_run:
                est.record_run(predicted, actual)
                if not self.keep_work:
                    _remove_work(work)
            with self._lock:
                self.result, self.state, self.overall, self.stage_progress = str(result), "done", 1.0, 1.0
        except Cancelled:
            self.state, self.message = "cancelled", "Cancelled."
        except StageError as e:
            self.state, self.message = "failed", str(e)
        except Exception as e:      # never leave the GUI waiting on a dead thread
            self.state, self.message = "failed", f"Unexpected error: {e}"

    # ---- train, shrinking the model if the card runs out of memory ---------
    def _train(self, spirula: str, work: Path, detail, mplan) -> None:
        step = mplan.step
        while True:
            p = memory.plan_at(detail, self.hw, step)
            shutil.rmtree(work / "train", ignore_errors=True)
            mark = len(self.log)
            rc = self._run(recipe.train_cmd(spirula, str(work), str(work / "train"), detail, p), check=False)
            if rc == 0:
                if step != mplan.step:
                    self.detail_note = p.note()
                return
            if memory.looks_like_oom(self.log[mark:]) and step < memory.MAX_STEP:
                step += 1
                self._say(f"Out of graphics memory. Retrying with less detail (step {step}).")
                self.stage_progress = 0.0
                continue
            tail = " | ".join(self.log[-4:])
            raise StageError(f"'{est.STAGE_LABEL['train']}' failed (exit code {rc}). {tail}")

    # ---- extract ----------------------------------------------------------
    def _extract(self, spirula: str, detail, work: Path) -> tuple:
        images = work / "images"
        images.mkdir(exist_ok=True)
        decode = self.hw.decode
        shim = _hw_shim(decode, work) if decode and decode.hwaccel else None
        total = sum(c.duration_s for c in self.captures) or 1.0
        frames_total = est.frames_for(detail, total, 2)
        jobs = []
        for ri, cap in enumerate(self.captures):
            share = max(8, round(frames_total * cap.duration_s / total))
            n_chapters = max(len(chain) for chain in cap.lens_files)
            for ci in range(n_chapters):
                jobs.append((ri, ci, cap, share // n_chapters or 1))
        files = sum(len(c.lens_files) for _, _, c, _ in jobs)
        done = 0
        for ri, ci, cap, frames in jobs:
            for lens, chain in enumerate(cap.lens_files):
                if ci >= len(chain):
                    continue
                self._check()
                tmp = work / "_tmp" / f"{ri}_{ci}_{lens}"
                shutil.rmtree(tmp, ignore_errors=True)
                tmp.mkdir(parents=True)
                chapter_s = cap.duration_s / max(1, len(chain))
                skip = max(1, round(chapter_s * 30 / max(frames, 1)))
                ok = False
                for decoder, ff in (("auto", shim or (decode.ffmpeg if decode else None)),
                                    ("ffmpeg", decode.ffmpeg if decode else None)):
                    rc = self._run(recipe.extract_cmd(spirula, chain[ci], str(tmp), kind=cap.kind,
                                                      paired=cap.paired, max_frames=frames, skip=skip,
                                                      decoder=decoder, ffmpeg=ff), check=False)
                    if rc == 0:
                        ok = True
                        break
                    self._say(f"Decoder '{decoder}' failed; trying the next option.")
                if not ok:
                    raise StageError("The video could not be read. Check that the file plays on this computer.")
                _collect(tmp, images, f"r{ri:02d}c{ci:02d}_", lens if cap.paired else None)
                done += 1
                self.stage_progress = done / max(files, 1)
                self._publish()
        if any(c.paired for c in self.captures):
            _match_pairs(images)
        shutil.rmtree(work / "_tmp", ignore_errors=True)
        members = sorted(p.name for p in images.iterdir() if p.is_dir() and re.fullmatch(r"cam\d+", p.name))
        if not self.dry_run:
            if not members or not any((images / m).iterdir() for m in members):
                raise StageError("No frames were extracted from the video.")
        width = None
        if members and not self.dry_run:
            first = next(iter(sorted((images / members[0]).glob("*.jpg"))), None)
            size = jpeg_size(str(first)) if first else None
            width = size[0] if size else None
        return members or ["cam0", "cam1"], width

    # ---- running Spirula ---------------------------------------------------
    def _check(self) -> None:
        if self._cancel:
            raise Cancelled()

    def _publish(self) -> None:
        base, span, _ = getattr(self, "_base", (0, 0, 0))
        with self._lock:
            self.overall = min(0.99, base + span * min(self.stage_progress, 1.0))

    def _run(self, cmd: list, check: bool = True) -> int:
        self._check()
        self._say("$ " + " ".join(_q(c) for c in cmd))
        self.commands.append(cmd)
        if self.dry_run:
            return 0
        env = {**os.environ, **device_env(self.hw), **engine_env()}
        t0 = time.time()
        expected = self._base[2]
        try:
            self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                          text=True, errors="replace", env=env, **popen_flags())
        except OSError as e:
            raise StageError(f"Could not start Spirula: {e}")
        parsed = 0.0
        for line in self._proc.stdout:                       # type: ignore[union-attr]
            line = line.rstrip()
            if line:
                self._say(line)
            m = re.search(r"(\d+(?:\.\d+)?)\s*%", line)
            n = re.search(r"\b(\d+)\s*/\s*(\d+)\b", line)
            if m and 0 <= float(m[1]) <= 100:
                parsed = max(parsed, float(m[1]) / 100)
            elif n and 0 < int(n[2]) and int(n[1]) <= int(n[2]):
                parsed = max(parsed, int(n[1]) / int(n[2]))
            timed = min(0.9, (time.time() - t0) / max(expected, 1.0))
            if self.stage != "extract":
                self.stage_progress = min(0.98, max(parsed, timed))
                self._publish()
        self._proc.stdout.close()                            # type: ignore[union-attr]
        rc = self._proc.wait()
        self._check()
        if rc != 0:
            lib = next((l for l in self.log[-20:] if "error while loading shared libraries" in l), None)
            if lib:
                raise StageError("A system library Spirula needs is missing: " + lib.split("libraries:")[-1].strip()
                                 + ". Install it with your package manager and try again.")
        if rc != 0 and check:
            tail = " | ".join(l for l in self.log[-4:])
            raise StageError(f"'{est.STAGE_LABEL[self.stage]}' failed (exit code {rc}). {tail}")
        return rc


# ---- helpers -----------------------------------------------------------------

def _q(s: str) -> str:
    return f'"{s}"' if " " in s else s


def _safe_name(s: str) -> str:
    return re.sub(r"[^\w.\-]+", "_", s).strip("_")[:80] or "capture"


def _kill_tree(p: subprocess.Popen) -> None:
    try:
        if IS_WINDOWS:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True)
        else:
            import signal
            os.killpg(os.getpgid(p.pid), signal.SIGTERM)
    except (OSError, ProcessLookupError):
        pass


def _hw_shim(decode, work: Path) -> Optional[str]:
    """A tiny launcher that adds -hwaccel to every ffmpeg call Spirula makes."""
    if not decode or not decode.hwaccel or not decode.ffmpeg:
        return None
    if IS_WINDOWS:
        p = work / "ffmpeg_hw.bat"
        p.write_text(f'@echo off\r\n"{decode.ffmpeg}" -hwaccel {decode.hwaccel} %*\r\n')
    else:
        p = work / "ffmpeg_hw.sh"
        p.write_text(f'#!/bin/sh\nexec "{decode.ffmpeg}" -hwaccel {decode.hwaccel} "$@"\n')
        p.chmod(0o755)
    return str(p)


def _collect(tmp: Path, images: Path, prefix: str, force_lens: Optional[int]) -> None:
    """Move extracted frames into images/cam<N>/ with unique names."""
    cams = sorted(p for p in tmp.iterdir() if p.is_dir() and re.fullmatch(r"cam\d+", p.name))
    groups = [(c.name, c) for c in cams] or [("cam0", tmp)]
    for cam_name, src in groups:
        name = f"cam{force_lens}" if force_lens is not None else cam_name
        dst = images / name
        dst.mkdir(exist_ok=True)
        for f in sorted(src.iterdir()):
            if f.is_file() and f.suffix.lower() in (".jpg", ".jpeg", ".png"):
                shutil.move(str(f), dst / (prefix + f.name))


def _match_pairs(images: Path) -> None:
    """Keep only frames that exist for both lenses; the rig needs matching stems."""
    a, b = images / "cam0", images / "cam1"
    if not a.is_dir() or not b.is_dir():
        return
    sa, sb = {p.name for p in a.iterdir()}, {p.name for p in b.iterdir()}
    for d, names in ((a, sa - sb), (b, sb - sa)):
        for n in names:
            (d / n).unlink(missing_ok=True)


def _find_run_dir(train: Path) -> Path:
    for p in sorted(train.rglob("config.json")):
        return p.parent
    raise StageError("Training finished but its results could not be found.")


def _find_result(out_dir: Path, name: str, ext: str) -> Optional[Path]:
    p = out_dir / (name + ext)
    if p.exists():
        return p
    hits = sorted(out_dir.glob(name + "*" + ext))
    return hits[0] if hits else None


def _remove_work(work: Path) -> None:
    # Only ever remove a folder this app created.
    if (work / WORK_MARKER).exists():
        shutil.rmtree(work, ignore_errors=True)
