"""Find recordings in a folder and match the files that belong together.

Supported cameras:
  Insta360  .insv  -- VID_<date>_<time>_00_<n>.insv + VID_<date>_<time>_10_<n>.insv
                      (one file per lens, matched here) or one file holding both
                      lens tracks (kept as a single file).
                      Chapters share a timestamp and differ in <n>.
                      LRV_* preview files are ignored.
  GoPro MAX .360   -- GS<chapter><recording>.360 (GS010001 then GS020001 ...).
  DJI       .osv   -- one file.
"""

from __future__ import annotations

import os
import re
import struct
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

KIND_LABEL = {"insta360": "Insta360", "gopro": "GoPro MAX", "dji": "DJI 360"}
SUFFIXES = {".insv", ".360", ".osv"}

_INSV = re.compile(r"^(VID)_(\d{8}_\d{6})_(\d{2})_(\d{3})\.insv$", re.I)
_GOPRO = re.compile(r"^G[SX](\d{2})(\d{4})\.360$", re.I)


@dataclass
class Capture:
    id: str
    kind: str                       # insta360 | gopro | dji
    label: str
    when: str                       # ISO-ish date/time if known, else ""
    # lens_files: one list per lens file-set. Insta360 with separate lens files
    # has two sets (00 and 10); everything else has one. Each set is the chapters in order.
    lens_files: list = field(default_factory=list)
    duration_s: float = 0.0
    duration_exact: bool = True
    size_gb: float = 0.0
    notes: list = field(default_factory=list)

    @property
    def paired(self) -> bool:
        return len(self.lens_files) == 2

    @property
    def all_files(self) -> list:
        return [f for chain in self.lens_files for f in chain]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["paired"] = self.paired
        return d


def mp4_duration(path: str) -> Optional[float]:
    """Movie duration in seconds from the ISO-BMFF header, or None."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            pos = 0
            while pos + 8 <= size:
                f.seek(pos)
                hdr = f.read(16)
                if len(hdr) < 8:
                    return None
                sz, typ = struct.unpack(">I4s", hdr[:8])
                hl = 8
                if sz == 1 and len(hdr) >= 16:
                    sz, hl = struct.unpack(">Q", hdr[8:16])[0], 16
                elif sz == 0:
                    sz = size - pos
                if sz < hl:
                    return None
                if typ == b"moov":
                    return _mvhd(f, pos + hl, pos + sz)
                pos += sz
    except (OSError, struct.error):
        pass
    return None


def _mvhd(f, pos: int, end: int) -> Optional[float]:
    while pos + 8 <= end:
        f.seek(pos)
        hdr = f.read(8)
        if len(hdr) < 8:
            return None
        sz, typ = struct.unpack(">I4s", hdr)
        if sz < 8:
            return None
        if typ == b"mvhd":
            body = f.read(32)
            if len(body) < 32:
                return None
            if body[0] == 1:
                timescale, dur = struct.unpack(">IQ", body[20:32])
            else:
                timescale, dur = struct.unpack(">II", body[12:20])
            return dur / timescale if timescale else None
        pos += sz
    return None


def _measure(paths: list) -> tuple:
    total, exact, size = 0.0, True, 0
    for p in paths:
        d = mp4_duration(p)
        size += os.path.getsize(p)
        if d is None:
            exact = False
            d = os.path.getsize(p) * 8 / 120e6        # assume ~120 Mbit/s
        total += d
    return total, exact, size / 2**30


def _fmt_when(stamp: str) -> str:
    m = re.match(r"(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})", stamp)
    return f"{m[1]}-{m[2]}-{m[3]} {m[4]}:{m[5]}:{m[6]}" if m else ""


def scan_folder(folder: str) -> list:
    """All recordings directly inside `folder` and one level down (cards use DCIM/Camera01)."""
    root = Path(folder)
    files = []
    for dirpath, dirnames, names in os.walk(root):
        depth = len(Path(dirpath).relative_to(root).parts)
        if depth >= 3:
            dirnames[:] = []
        for n in names:
            if Path(n).suffix.lower() in SUFFIXES:
                files.append(Path(dirpath) / n)

    caps = []
    caps += _group_insta360([f for f in files if f.suffix.lower() == ".insv"])
    caps += _group_gopro([f for f in files if f.suffix.lower() == ".360"])
    caps += _group_dji([f for f in files if f.suffix.lower() == ".osv"])
    caps.sort(key=lambda c: (c.when or "~", c.label))
    return caps


def _group_insta360(files: list) -> list:
    # stamp -> lens code -> {chapter: path}
    groups: dict = {}
    singles = []
    for f in files:
        m = _INSV.match(f.name)
        if not m:
            if not f.name.upper().startswith("LRV_"):
                singles.append(f)
            continue
        if m[1].upper() != "VID":
            continue
        groups.setdefault((str(f.parent), m[2]), {}).setdefault(m[3], {})[int(m[4])] = str(f)

    caps = []
    for (_, stamp), lenses in groups.items():
        chains = [[lens[k] for k in sorted(lens)] for _, lens in sorted(lenses.items())]
        notes = []
        if len(chains) > 2:
            chains, notes = chains[:2], ["More than two lens files share this timestamp; using the first two."]
        if len(chains) == 2 and len(chains[0]) != len(chains[1]):
            notes.append("The two lenses have different numbers of file parts. Matching may be incomplete.")
        dur, exact, gb = _measure([p for c in chains for p in c])
        if len(chains) == 2:
            dur /= 2          # each lens file covers the whole recording once
        caps.append(Capture(f"insta360-{stamp}", "insta360", f"Insta360 {_fmt_when(stamp)}",
                            _fmt_when(stamp), chains, dur, exact, gb, notes))
    for f in singles:
        dur, exact, gb = _measure([str(f)])
        caps.append(Capture(f"insta360-{f.stem}", "insta360", f"Insta360 {f.name}", "",
                            [[str(f)]], dur, exact, gb))
    return caps


def _group_gopro(files: list) -> list:
    by_rec: dict = {}
    for f in files:
        m = _GOPRO.match(f.name)
        key = (str(f.parent), m[2] if m else f.stem)
        by_rec.setdefault(key, []).append((int(m[1]) if m else 0, str(f)))
    caps = []
    for (_, rec), parts in by_rec.items():
        chain = [p for _, p in sorted(parts)]
        dur, exact, gb = _measure(chain)
        mtime = os.path.getmtime(chain[0])
        import datetime
        when = datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
        caps.append(Capture(f"gopro-{rec}-{Path(chain[0]).parent.name}", "gopro",
                            f"GoPro MAX {rec}", when, [chain], dur, exact, gb))
    return caps


def _group_dji(files: list) -> list:
    caps = []
    for f in files:
        dur, exact, gb = _measure([str(f)])
        import datetime
        when = datetime.datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        caps.append(Capture(f"dji-{f.stem}", "dji", f"DJI {f.name}", when, [[str(f)]], dur, exact, gb))
    return caps


def jpeg_size(path: str) -> Optional[tuple]:
    """(width, height) of a JPEG from its SOF marker."""
    try:
        with open(path, "rb") as f:
            if f.read(2) != b"\xff\xd8":
                return None
            while True:
                b = f.read(1)
                while b and b != b"\xff":
                    b = f.read(1)
                m = f.read(1)
                while m == b"\xff":
                    m = f.read(1)
                if not m:
                    return None
                if m[0] in range(0xC0, 0xD0) and m[0] not in (0xC4, 0xC8, 0xCC):
                    f.read(3)
                    h, w = struct.unpack(">HH", f.read(4))
                    return w, h
                ln = struct.unpack(">H", f.read(2))[0]
                f.seek(ln - 2, 1)
    except (OSError, struct.error):
        return None
