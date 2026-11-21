"""Local web server behind the one-page GUI. Listens on 127.0.0.1 only."""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

from . import estimate as est
from . import footage, hardware
from .footage import Capture
from .pipeline import Job, find_spirula
from .presets import DEFAULT_FORMAT, DETAILS, FORMATS
from .util import IS_MAC, IS_WINDOWS, popen_flags, run

WEB = Path(__file__).parent / "web"


class App:
    def __init__(self) -> None:
        self.token = secrets.token_urlsafe(16)
        self.hw: Optional[hardware.Hardware] = None
        self.captures: dict = {}
        self.folder = ""
        self.job: Optional[Job] = None
        self.last_ping = time.time()
        self.started = time.time()
        self._hw_ready = threading.Event()
        threading.Thread(target=self._probe, daemon=True).start()

    def _probe(self) -> None:
        self.hw = hardware.detect(verify_decode=True)
        self._hw_ready.set()

    # ---- API handlers ---------------------------------------------------
    def hardware(self) -> dict:
        self._hw_ready.wait(60)
        if not self.hw:
            return {"ready": False}
        hw = self.hw
        g = hw.best_gpu()
        parts = [g.name if g else "No graphics card found"]
        if g and g.vram_gb:
            parts.append(f"{g.vram_gb:g} GB")
        spirula = find_spirula()
        problems = list(hw.problems)
        if not spirula:
            problems.append("Spirula Studio is not installed next to this app (see README).")
        return {"ready": True, "summary": " · ".join(parts),
                "decode": hw.decode.reason if hw.decode else "", "notes": hw.notes,
                "problems": problems, "details": hw.to_dict()}

    def scan(self, folder: str) -> dict:
        p = Path(folder).expanduser()
        if not p.is_dir():
            return {"error": "That folder does not exist."}
        caps = footage.scan_folder(str(p))
        self.folder = str(p)
        self.captures = {c.id: c for c in caps}
        return {"folder": self.folder, "captures": [c.to_dict() for c in caps],
                "default_out": str(p / "Insta Splat 3D models")}

    def _selected(self, ids: list) -> list:
        return [self.captures[i] for i in ids if i in self.captures]

    def estimate(self, ids: list) -> dict:
        caps = self._selected(ids)
        if not caps:
            return {"levels": []}
        kinds = {c.kind for c in caps}
        if len(kinds) > 1:
            return {"levels": [], "error": "Pick recordings from one camera type at a time."}
        dur = sum(c.duration_s for c in caps)
        lenses = 10 if caps[0].kind == "gopro" else 2
        self._hw_ready.wait(60)
        return {"levels": est.all_levels(dur, lenses, self.hw), "duration_s": dur,
                "approximate": any(not c.duration_exact for c in caps)}

    def start(self, body: dict) -> dict:
        if self.job and self.job.state == "running":
            return {"error": "A run is already in progress."}
        caps = self._selected(body.get("ids", []))
        level = int(body.get("level", 2))
        fmt = body.get("format", DEFAULT_FORMAT)
        if not caps or level not in DETAILS or fmt not in FORMATS:
            return {"error": "Choose at least one recording first."}
        if len({c.kind for c in caps}) > 1:
            return {"error": "Pick recordings from one camera type at a time."}
        ok, why = est.available(DETAILS[level], self.hw)
        if not ok:
            return {"error": why}
        out = body.get("out_dir") or str(Path(self.folder) / "Insta Splat 3D models")
        self.job = Job(captures=caps, detail_level=level, format_key=fmt, out_dir=out,
                       hw=self.hw, keep_work=bool(body.get("keep_work")))
        self.job.start()
        return {"ok": True}

    def ping(self) -> dict:
        self.last_ping = time.time()
        return {"ok": True}

    def idle_for_quit(self, grace: float = 25.0, first_load: float = 90.0) -> bool:
        """True when the page was closed and nothing is running, so the app can exit by itself."""
        if self.job and self.job.state == "running":
            return False
        if self.last_ping == self.started:           # the page has never loaded
            return time.time() - self.started > first_load
        return time.time() - self.last_ping > grace

    def status(self) -> dict:
        return self.job.snapshot() if self.job else {"state": "idle"}

    def cancel(self) -> dict:
        if self.job:
            self.job.cancel()
        return {"ok": True}

    def reveal(self, what: str) -> dict:
        if not self.job:
            return {}
        target = Path(self.job.result) if (what == "file" and self.job.result) else Path(self.job.out_dir)
        folder = target.parent if target.is_file() else target
        if IS_WINDOWS and target.is_file():
            subprocess.Popen(["explorer", "/select,", str(target)])
        elif IS_WINDOWS:
            os.startfile(str(folder))                                    # type: ignore[attr-defined]
        else:
            subprocess.Popen(["open" if IS_MAC else "xdg-open", str(folder)])
        return {"ok": True}

    def pick(self, kind: str) -> dict:
        path = pick_folder()
        return {"path": path} if path else {"cancelled": True}


def pick_folder() -> str:
    """A native folder chooser, run out-of-process so it never blocks the server."""
    if IS_MAC:
        rc, out = run(["osascript", "-e", 'POSIX path of (choose folder with prompt "Choose your footage folder")'], 600)
        return out.strip() if rc == 0 else ""
    if IS_WINDOWS:
        ps = ("Add-Type -AssemblyName System.Windows.Forms; $d=New-Object System.Windows.Forms.FolderBrowserDialog;"
              "$d.Description='Choose the folder with your footage'; $f=New-Object System.Windows.Forms.Form;"
              "$f.TopMost=$true; if($d.ShowDialog($f) -eq 'OK'){Write-Output $d.SelectedPath}")
        rc, out = run(["powershell", "-NoProfile", "-STA", "-Command", ps], 600)
        return out.strip().splitlines()[-1] if rc == 0 and out.strip() else ""
    cmd = [sys.executable, "--pick-folder"] if getattr(sys, "frozen", False) else \
          [sys.executable, "-m", "insta_splat", "--pick-folder"]
    rc, out = run(cmd, 600)
    if rc == 0 and out.strip():
        return out.strip().splitlines()[-1]
    for exe, args in (("zenity", ["--file-selection", "--directory"]), ("kdialog", ["--getexistingdirectory"])):
        import shutil
        if shutil.which(exe):
            rc, out = run([exe, *args], 600)
            return out.strip() if rc == 0 else ""
    return ""


def tk_pick() -> None:
    """Entry for `--pick-folder`: print the chosen folder."""
    try:
        import tkinter
        from tkinter import filedialog
        root = tkinter.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        print(filedialog.askdirectory(title="Choose the folder with your footage") or "")
    except Exception:
        sys.exit(1)


def make_handler(app: App):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet
            pass

        def _ok_host(self) -> bool:
            host = (self.headers.get("Host") or "").split(":")[0]
            return host in ("127.0.0.1", "localhost")      # blocks DNS-rebinding

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code: int = 200) -> None:
            self._send(code, json.dumps(obj).encode(), "application/json")

        def do_GET(self):
            if not self._ok_host():
                return self._json({"error": "bad host"}, 403)
            path = self.path.split("?")[0]
            if path in ("/", "/index.html"):
                fmts = json.dumps([{"key": f.key, "label": f.label, "hint": f.hint} for f in FORMATS.values()])
                html = (WEB / "index.html").read_text(encoding="utf-8").replace("__TOKEN__", app.token).replace("__FORMATS__", fmts)
                return self._send(200, html.encode(), "text/html; charset=utf-8")
            if path.startswith("/api/") and self.headers.get("X-Token") == app.token:
                if path == "/api/hardware":
                    return self._json(app.hardware())
                if path == "/api/status":
                    return self._json(app.status())
                if path == "/api/ping":
                    return self._json(app.ping())
            self._json({"error": "not found"}, 404)

        def do_POST(self):
            if not self._ok_host() or self.headers.get("X-Token") != app.token:
                return self._json({"error": "forbidden"}, 403)
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return self._json({"error": "bad request"}, 400)
            route = {"/api/pick": lambda: app.pick(body.get("kind", "folder")),
                     "/api/scan": lambda: app.scan(body.get("folder", "")),
                     "/api/estimate": lambda: app.estimate(body.get("ids", [])),
                     "/api/start": lambda: app.start(body),
                     "/api/cancel": app.cancel,
                     "/api/reveal": lambda: app.reveal(body.get("what", "folder"))}.get(self.path)
            if not route:
                return self._json({"error": "not found"}, 404)
            try:
                self._json(route())
            except Exception as e:
                self._json({"error": f"Something went wrong: {e}"}, 500)
    return H


def serve(open_browser: bool = True) -> None:
    app = App()
    httpd = ThreadingHTTPServer(("127.0.0.1", int(os.environ.get("INSTA_SPLAT_PORT", "0"))), make_handler(app))
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    print(f"Insta Splat is running at {url}  (it quits when you close the page)")
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    def watchdog():
        while True:
            time.sleep(3)
            if app.idle_for_quit():
                httpd.shutdown()
                return
    threading.Thread(target=watchdog, daemon=True).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
