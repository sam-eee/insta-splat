"""Open SphereScan in its own window, like a desktop app, instead of a browser tab.

Chrome, Edge, Brave and Chromium all have an "app mode": a plain window with no tabs or address
bar, and its own icon in the taskbar. It needs nothing installed beyond the browser the person
almost certainly has (Edge ships with Windows). A separate browser profile keeps it clean and
remembers the window size. With none of those browsers, the default browser is used instead.

When the window closes the page stops reporting in, and the app quits by itself (see server.py).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import webbrowser
from pathlib import Path
from typing import Optional

from .util import IS_LINUX, IS_MAC, IS_WINDOWS, config_dir, popen_flags

WM_CLASS = "SphereScan"


def _candidates() -> list:
    if IS_WINDOWS:
        roots = [os.environ.get(k, "") for k in ("ProgramFiles", "ProgramFiles(x86)", "LocalAppData")]
        rel = [r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe",
               r"BraveSoftware\Brave-Browser\Application\brave.exe", r"Chromium\Application\chrome.exe"]
        return [str(Path(r) / x) for x in rel for r in roots if r]
    if IS_MAC:
        apps = ["Google Chrome", "Microsoft Edge", "Brave Browser", "Chromium"]
        return [f"/Applications/{a}.app/Contents/MacOS/{a}" for a in apps] + \
               [str(Path.home() / f"Applications/{a}.app/Contents/MacOS/{a}") for a in apps]
    names = ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge",
             "microsoft-edge-stable", "brave-browser"]
    return [p for p in (shutil.which(n) for n in names) if p]


def find_app_browser() -> Optional[str]:
    override = os.environ.get("SPHERESCAN_BROWSER")
    if override and override.lower() == "default":
        return None
    if override and Path(override).exists():
        return override
    for c in _candidates():
        if c and Path(c).exists():
            return c
    return None


def app_command(exe: str, url: str) -> list:
    cmd = [exe, f"--app={url}", f"--user-data-dir={config_dir() / 'window'}", "--window-size=1000,920",
           "--no-first-run", "--no-default-browser-check", "--disable-features=Translate"]
    if IS_LINUX:
        cmd.append(f"--class={WM_CLASS}")        # groups the window under the launcher's icon
    return cmd


def open_window(url: str) -> str:
    """Open the app window. Returns how it was opened: 'app-window', 'browser' or 'none'."""
    if os.environ.get("SPHERESCAN_NO_WINDOW"):
        return "none"
    exe = find_app_browser()
    if exe:
        try:
            subprocess.Popen(app_command(exe, url), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             stdin=subprocess.DEVNULL, **popen_flags())
            return "app-window"
        except OSError:
            pass
    webbrowser.open(url)
    return "browser"


# ---- Linux: a launcher entry so SphereScan appears in the applications menu -------------------

def _desktop_path() -> Path:
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "applications" / "spherescan.desktop"


def launcher_text() -> str:
    import sys
    icon = Path(__file__).parent / "web" / "icon.svg"
    if getattr(sys, "frozen", False):
        exec_line, path = f'"{sys.executable}"', ""
    else:
        root = Path(__file__).resolve().parent.parent
        exec_line, path = f'"{sys.executable}" -m spherescan', f"Path={root}\n"
    return ("[Desktop Entry]\nType=Application\nName=SphereScan\n"
            "Comment=Turn 360 degree footage into a 3D model\n"
            f"Exec={exec_line}\n{path}Icon={icon}\nTerminal=false\n"
            f"Categories=Graphics;3DGraphics;\nStartupWMClass={WM_CLASS}\n")


def install_launcher() -> Path:
    if not IS_LINUX:
        raise RuntimeError("Launcher entries are for Linux. On Windows and macOS the downloaded app is the launcher.")
    from .util import portable_dir
    if portable_dir():
        raise RuntimeError("This copy is portable (portable.txt is next to it), so it does not add menu entries "
                           "that point at this folder. Delete portable.txt first if you want a menu entry.")
    p = _desktop_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(launcher_text(), encoding="utf-8")
    return p


def remove_launcher() -> bool:
    p = _desktop_path()
    if p.exists():
        p.unlink()
        return True
    return False
