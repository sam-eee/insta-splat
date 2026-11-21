import os
import stat
import struct
import sys
from pathlib import Path

FAKE = Path(__file__).parent / "fake_spirula.py"


def mp4(path: Path, seconds: float, timescale: int = 1000) -> None:
    """A minimal ISO-BMFF file: ftyp + moov(mvhd) + a little mdat."""
    mvhd = struct.pack(">I4sBBBB", 108, b"mvhd", 0, 0, 0, 0) + struct.pack(">IIII", 0, 0, timescale, int(seconds * timescale)) + b"\0" * 80
    mvhd = struct.pack(">I", len(mvhd)) + mvhd[4:]
    moov = struct.pack(">I4s", 8 + len(mvhd), b"moov") + mvhd
    ftyp = struct.pack(">I4s4sI", 16, b"ftyp", b"isom", 0)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(ftyp + moov + struct.pack(">I4s", 16, b"mdat") + b"\0" * 8)


def launcher(dirpath: Path) -> str:
    """An executable that behaves like spirula, for the pipeline tests."""
    if os.name == "nt":
        p = dirpath / "spirula.bat"
        p.write_text(f'@"{sys.executable}" "{FAKE}" %*\r\n')
    else:
        p = dirpath / "spirula"
        p.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" "$@"\n')
        p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)
