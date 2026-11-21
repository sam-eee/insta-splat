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


# macOS has neither a parent-death signal nor job objects, so the engine is started under a tiny shell
# supervisor: it runs Spirula, and kills the whole process group if SphereScan disappears (even by SIGKILL).
_MAC_SUPERVISOR = r'''
parent="$1"; shift
"$@" &
child=$!
( while kill -0 "$parent" 2>/dev/null; do sleep 1; done; kill -9 -$$ 2>/dev/null ) &
watcher=$!
wait "$child"; rc=$?
kill "$watcher" 2>/dev/null
exit $rc
'''

_JOB = None


def _windows_job():
    """One job object for SphereScan's lifetime. Windows kills everything in it when SphereScan exits, however it exits."""
    global _JOB
    if _JOB is not None:
        return _JOB or None
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)

        class IO(ctypes.Structure):
            _fields_ = [(n, ctypes.c_ulonglong) for n in ("a", "b", "c", "d", "e", "f")]

        class BASIC(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

        class EXT(ctypes.Structure):
            _fields_ = [("Basic", BASIC), ("Io", IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        k32.CreateJobObjectW.restype = wintypes.HANDLE
        job = k32.CreateJobObjectW(None, None)
        info = EXT()
        info.Basic.LimitFlags = 0x2000            # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not job or not k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            _JOB = False
            return None
        _JOB = (k32, job)
        return _JOB
    except Exception:
        _JOB = False
        return None


def spawn_engine(cmd, **kwargs):
    """Start Spirula so that it cannot outlive SphereScan, whichever way SphereScan ends."""
    if IS_WINDOWS:
        p = subprocess.Popen(cmd, **popen_flags(), **kwargs)
        j = _windows_job()
        if j:
            try:
                j[0].AssignProcessToJobObject(j[1], int(p._handle))
            except Exception:
                pass
        return p
    if IS_MAC:
        return subprocess.Popen(["/bin/sh", "-c", _MAC_SUPERVISOR, "sh", str(os.getpid()), *map(str, cmd)],
                                **popen_flags(), **kwargs)
    return subprocess.Popen(cmd, **child_flags(), **kwargs)


def child_flags() -> dict:
    """Flags for the engine (Spirula) on Linux: stopped by the kernel if SphereScan dies abruptly."""
    f = popen_flags()
    if IS_LINUX:
        def die_with_parent():
            try:
                import ctypes, signal
                ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGTERM)    # PR_SET_PDEATHSIG
            except Exception:
                pass
        f["preexec_fn"] = die_with_parent
    return f


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


PORTABLE_MARKER = "portable.txt"
PORTABLE_DATA = "SphereScan-data"


def portable_root() -> Path:
    """The folder that holds SphereScan itself: next to the .exe / binary / .app, or the project folder."""
    override = os.environ.get("SPHERESCAN_PORTABLE_ROOT")      # for tests and for running from source
    if override:
        return Path(override)
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable).resolve()
        if IS_MAC:
            for parent in exe.parents:
                if parent.suffix == ".app":
                    return parent.parent                        # next to SphereScan.app, not inside it
        return exe.parent
    return Path(__file__).resolve().parent.parent


def portable_dir() -> Optional[Path]:
    """Where everything SphereScan keeps lives, when it is run as a portable app; otherwise None.

    Portable means a `portable.txt` file (or a `SphereScan-data` folder) sits next to the app. If that
    place cannot be written to (a read-only drive, Program Files, a quarantined macOS app), we quietly
    fall back to the normal per-user folders rather than fail.
    """
    root = portable_root()
    data = root / PORTABLE_DATA
    if not ((root / PORTABLE_MARKER).exists() or data.is_dir()):
        return None
    try:
        data.mkdir(parents=True, exist_ok=True)
        probe = data / ".write-test"
        probe.write_text("x")
        probe.unlink()
        return data
    except OSError:
        return None


def config_dir() -> Path:
    override = os.environ.get("SPHERESCAN_CONFIG_DIR")      # isolated tests and unusual setups
    if override:
        d = Path(override)
        d.mkdir(parents=True, exist_ok=True)
        return d
    portable = portable_dir()
    if portable:
        d = portable / "settings"
        d.mkdir(parents=True, exist_ok=True)
        return d
    if IS_WINDOWS:
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif IS_MAC:
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    d = base / "spherescan"
    d.mkdir(parents=True, exist_ok=True)
    return d


def app_root() -> Path:
    """Where bundled files live: PyInstaller's unpack folder, else the project folder."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
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
    """env override, then anything bundled under vendor/, then PATH."""
    import shutil
    if env_var and os.environ.get(env_var):
        p = os.environ[env_var]
        return p if Path(p).exists() else None
    exe = name + (".exe" if IS_WINDOWS else "")
    vendor = app_root() / "vendor"
    if vendor.is_dir():
        for cand in sorted(vendor.rglob(exe), key=lambda c: len(c.parts)):
            if cand.is_file():
                if not IS_WINDOWS and not os.access(cand, os.X_OK):
                    try:
                        cand.chmod(cand.stat().st_mode | 0o111)    # data files can lose their exec bit
                    except OSError:
                        continue
                return str(cand)
    return shutil.which(name)


def engine_env() -> dict:
    """Environment for Spirula: bundled helper libraries are a fallback, never an override; in portable
    mode its settings and caches go beside the app too."""
    env = {}
    libs = app_root() / "vendor" / "libs"
    if IS_LINUX and libs.is_dir():
        cur = os.environ.get("LD_LIBRARY_PATH", "")
        env["LD_LIBRARY_PATH"] = (cur + os.pathsep if cur else "") + str(libs)
    portable = portable_dir()
    if portable:
        cfg, cache = portable / "engine" / "config", portable / "engine" / "cache"
        for d in (cfg, cache):
            d.mkdir(parents=True, exist_ok=True)
        # Spirula reads APPDATA/LOCALAPPDATA on Windows and XDG_CONFIG_HOME/XDG_CACHE_HOME elsewhere.
        env.update({"APPDATA": str(cfg), "XDG_CONFIG_HOME": str(cfg),
                    "LOCALAPPDATA": str(cache), "XDG_CACHE_HOME": str(cache)})
    return env
