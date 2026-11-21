"""Regenerate the README screenshots from the real page: `python scripts/screenshots.py`.

Uses invented footage and a stand-in computer (no personal data), the real page and server, and
headless Chrome/Edge. Writes docs/images/*.png.
"""

import os
import subprocess
import sys
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
os.environ["SPHERESCAN_CONFIG_DIR"] = tempfile.mkdtemp(prefix="spherescan-shots-")
os.environ.setdefault("SPHERESCAN_SPIRULA", sys.executable)          # only so the page does not say "not installed"

from helpers import mp4                                              # noqa: E402
from spherescan import hardware                                      # noqa: E402
from spherescan.server import App, make_handler                      # noqa: E402
from spherescan.window import find_app_browser                       # noqa: E402

OUT = ROOT / "docs" / "images"


class DemoApp(App):
    def _probe(self):
        self.hw = hardware.virtual("GeForce RTX 4060 Laptop", "Intel Core i7-12700H", gpu_decode=False)
        self._hw_ready.set()


SETUP_JS = """<script>(async()=>{const w=ms=>new Promise(r=>setTimeout(r,ms));
await w(1200);$('typed').value=%r;$('typedgo').click();await w(1500);
document.querySelector('#recs input').click();await w(1500);
$('lod').value=2;$('lod').dispatchEvent(new Event('input'));
document.querySelectorAll('#recs .meta').forEach((m,i)=>m.textContent=m.textContent.replace('0.0 GB',['6.3 GB','2.7 GB'][i]||'1.0 GB'));})()</script></body>"""


def shoot(url: str, name: str, height: int, budget: int = 9000) -> None:
    exe = find_app_browser() or sys.exit("Chrome, Edge or Chromium is needed to take screenshots.")
    out = OUT / name
    subprocess.run([exe, "--headless=new", "--no-sandbox", "--hide-scrollbars", f"--virtual-time-budget={budget}",
                    f"--window-size=860,{height}", f"--screenshot={out}", url],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=90)
    print("wrote", out)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.gettempdir()) / "SphereScan-demo" / "Insta360 trip"
    for lens in ("00", "10"):
        mp4(folder / f"VID_20260301_101500_{lens}_001.insv", 420)
        mp4(folder / f"VID_20260302_160000_{lens}_001.insv", 180)

    app = DemoApp()
    app._hw_ready.wait(5)
    base = make_handler(app)

    class Injecting(base):
        def _send(self, code, body, ctype):
            if ctype.startswith("text/html"):
                # An open live connection keeps a headless page "loading" forever; the screenshot needs none.
                body = body.replace(b'new EventSource("/api/live?token="+encodeURIComponent(TOKEN))', b"({close(){}})")
                if getattr(app, "inject", ""):
                    body = body.replace(b"</body>", app.inject.encode())
            super()._send(code, body, ctype)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Injecting)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"

    app.inject = SETUP_JS % str(folder)
    shoot(url, "setup.png", 1400)

    snap = {"state": "running", "stage": "train", "stage_label": "Building the 3D model", "stage_progress": 0.43,
            "overall": 0.48, "message": "", "result": None, "detail_note": "", "eta_s": 3900.0, "eta_text": "about 1 h 05 min",
            "desc": "Insta360 2026-03-01 10:15:00 · Standard · GLB", "elapsed": 3300, "commands": [], "health": "ok",
            "idle_s": 3.0, "notice": "", "retry": None, "log": ["step   6450/15000 ( 43%)  splats 612044"]}
    app.job = type("J", (), {"state": "running", "result": None, "ended": 0, "snapshot": lambda s: snap})()
    app.inject = ""
    shoot(url, "progress.png", 1600, budget=6000)
    httpd.shutdown()


if __name__ == "__main__":
    main()
