"""Small helpers shared by every module."""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Optional, Sequence

IS_WINDOWS = platform.system() == "Windows"
IS_MAC = platform.system() == "Darwin"
IS_LINUX = platform.system() == "Linux"


def popen_flags() -> dict:
    """Keyword arguments that keep child processes from flashing a console."""
    if IS_WINDOWS:
        return {"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def run(cmd: Sequence[str], timeout: float = 15.0) -> tuple[int, str]:
    """Run a probe command. Never raises; (-1, "") means it could not run."""
    try:
        p = subprocess.run(
            list(cmd), capture_output=True, text=True, timeout=timeout,
            errors="replace", **({"creationflags": subprocess.CREATE_NO_WINDOW} if IS_WINDOWS else {}),
        )
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except (OSError, subprocess.SubprocessError):
        return -1, ""


def config_dir() -> Path:
    if IS_WINDOWS:
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif IS_MAC:
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    d = base / "insta-splat"
    d.mkdir(parents=True, exist_ok=True)
    return d


def app_root() -> Path:
    """The folder holding vendor/ (bundled spirula and ffmpeg)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def save_json(path: Path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def find_executable(name: str, env_var: Optional[str] = None) -> Optional[str]:
    """env override, then vendor/, then PATH."""
    import shutil
    if env_var and os.environ.get(env_var):
        p = os.environ[env_var]
        return p if Path(p).exists() else None
    exe = name + (".exe" if IS_WINDOWS else "")
    for cand in (app_root() / "vendor" / exe, app_root() / "vendor" / name / exe):
        if cand.exists():
            return str(cand)
    return shutil.which(name)
