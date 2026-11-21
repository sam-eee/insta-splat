import http.client
import struct
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from make_glb import glb
from spherescan.server import App, make_handler


class Model(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        self.app = App.__new__(App)
        self.app.token = "tok"
        self.app.job = None
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.app))
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.shutdown)

    def get(self, path, token="tok", host=None):
        c = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1])
        h = {"X-Token": token} if token else {}
        if host:
            h["Host"] = host
        c.request("GET", path, headers=h)
        r = c.getresponse()
        return r.status, r.read(), r.getheader("Content-Type")

    def job(self, name, data, state="done"):
        f = Path(self.t.name) / name
        f.write_bytes(data)
        self.app.job = type("J", (), {"state": state, "result": str(f)})()
        return data

    def test_fixture_is_a_valid_glb(self):
        data = glb()
        magic, version, length = struct.unpack("<III", data[:12])
        self.assertEqual((magic, version, length), (0x46546C67, 2, len(data)))

    def test_finished_glb_is_served_byte_for_byte(self):
        data = self.job("m.glb", glb())
        status, body, ctype = self.get("/api/model")
        self.assertEqual((status, body, ctype), (200, data, "model/gltf-binary"))

    def test_needs_the_page_token_and_a_local_host_name(self):
        self.job("m.glb", glb())
        self.assertNotEqual(self.get("/api/model", token=None)[0], 200)
        self.assertNotEqual(self.get("/api/model", token="wrong")[0], 200)
        self.assertEqual(self.get("/api/model", host="evil.example")[0], 403)

    def test_only_a_finished_glb_is_ever_served(self):
        self.assertEqual(self.get("/api/model")[0], 404)                     # no job
        self.job("m.glb", glb(), state="running")
        self.assertEqual(self.get("/api/model")[0], 404)                     # not finished
        self.job("m.ply", b"ply\n", state="done")
        self.assertEqual(self.get("/api/model")[0], 404)                     # other formats are not previewed

    def test_viewer_script_is_served(self):
        status, body, ctype = self.get("/viewer.js", token=None)
        self.assertEqual(status, 200)
        self.assertIn("javascript", ctype)
        self.assertIn(b"SphereViewer", body)


if __name__ == "__main__":
    unittest.main()
