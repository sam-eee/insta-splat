"""Build the single-file app with PyInstaller: `python build.py`.

Put Spirula Studio under vendor/ first (the release workflow does this) and it is
bundled inside, so the finished file needs nothing installed. PyInstaller cannot
cross-compile: run this on each operating system you want a build for.

  Windows -> dist/SphereScan.exe        (one file, no console window)
  Linux   -> dist/SphereScan            (one file)
  macOS   -> dist/SphereScan.app        (an app; zip or dmg it to share)
"""

import argparse
import os
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
SEP = os.pathsep


def command(onefile_mac: bool = False) -> list:
    system = platform.system()
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--name", "SphereScan",
           "--add-data", f"{ROOT / 'spherescan' / 'web'}{SEP}spherescan/web",
           "--add-data", f"{ROOT / 'LICENSE'}{SEP}.",
           "--add-data", f"{ROOT / 'THIRD_PARTY.md'}{SEP}.",
           "--add-data", f"{ROOT / 'SPIRULA_VERSION'}{SEP}."]
    if (ROOT / "vendor").is_dir():
        cmd += ["--add-data", f"{ROOT / 'vendor'}{SEP}vendor"]
    if system == "Darwin":
        cmd += ["--windowed"] + (["--onefile"] if onefile_mac else [])
    elif system == "Windows":
        cmd += ["--onefile", "--noconsole"]
    else:
        cmd += ["--onefile"]
    cmd.append(str(ROOT / "run_app.py"))
    return cmd


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--print", action="store_true", help="show the command and stop")
    a = ap.parse_args()
    c = command()
    if a.print:
        print(" ".join(c))
        sys.exit(0)
    if not (ROOT / "vendor").is_dir():
        print("Note: no vendor/ folder, so Spirula will NOT be bundled in this build.")
    sys.exit(subprocess.call(c, cwd=ROOT))
