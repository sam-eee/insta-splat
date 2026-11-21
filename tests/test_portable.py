"""Portable mode: a portable.txt next to the app keeps everything beside it and leaves the computer alone."""
import json
import os
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

import spherescan.util as u
from helpers import launcher, mp4
from spherescan import footage, hardware
from spherescan.pipeline import Job


class Portable(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.root = Path(self.t.name) / "stick"
        self.root.mkdir()
        self.saved = {k: os.environ.get(k) for k in ("SPHERESCAN_PORTABLE_ROOT", "SPHERESCAN_CONFIG_DIR", "HOME",
                                                     "APPDATA", "XDG_CONFIG_HOME", "LOCALAPPDATA", "XDG_CACHE_HOME")}
        os.environ["SPHERESCAN_PORTABLE_ROOT"] = str(self.root)
        os.environ.pop("SPHERESCAN_CONFIG_DIR", None)
        # the "computer's" normal folders, to prove nothing is written there
        self.home = Path(self.t.name) / "home"
        self.home.mkdir()
        for k in ("HOME", "APPDATA", "XDG_CONFIG_HOME", "LOCALAPPDATA", "XDG_CACHE_HOME"):
            os.environ[k] = str(self.home)
        if os.name == "nt":
            os.environ["USERPROFILE"] = str(self.home)
        self.addCleanup(self.restore)

    def restore(self):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        os.chmod(self.root, 0o755) if os.name != "nt" else None
        self.t.cleanup()

    def test_without_the_marker_it_is_a_normal_install(self):
        self.assertIsNone(u.portable_dir())
        self.assertNotIn(str(self.root), str(u.config_dir()))

    def test_marker_file_or_data_folder_turns_it_on(self):
        (self.root / "portable.txt").write_text("x")
        self.assertEqual(u.portable_dir(), self.root / "SphereScan-data")
        self.assertEqual(u.config_dir(), self.root / "SphereScan-data" / "settings")
        (self.root / "portable.txt").unlink()
        (self.root / "SphereScan-data").mkdir(exist_ok=True)
        self.assertIsNotNone(u.portable_dir())                    # the data folder alone is enough

    def test_the_window_profile_and_estimates_live_beside_the_app(self):
        (self.root / "portable.txt").write_text("x")
        from spherescan import window
        cmd = window.app_command("/x/chrome", "http://127.0.0.1:1/")
        self.assertTrue(any(c == f"--user-data-dir={self.root / 'SphereScan-data' / 'settings' / 'window'}" for c in cmd), cmd)

    def test_spirulas_own_settings_and_cache_are_redirected(self):
        self.assertNotIn("APPDATA", u.engine_env())               # normal install: untouched
        (self.root / "portable.txt").write_text("x")
        env = u.engine_env()
        data = self.root / "SphereScan-data" / "engine"
        self.assertEqual(Path(env["XDG_CONFIG_HOME"]), data / "config")
        self.assertEqual(Path(env["APPDATA"]), data / "config")
        self.assertEqual(Path(env["XDG_CACHE_HOME"]), data / "cache")
        self.assertEqual(Path(env["LOCALAPPDATA"]), data / "cache")

    def test_an_explicit_config_dir_still_wins(self):
        (self.root / "portable.txt").write_text("x")
        os.environ["SPHERESCAN_CONFIG_DIR"] = str(Path(self.t.name) / "elsewhere")
        self.assertEqual(u.config_dir(), Path(self.t.name) / "elsewhere")

    @unittest.skipIf(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0), "needs permissions that can be removed")
    def test_a_read_only_location_falls_back_instead_of_failing(self):
        (self.root / "portable.txt").write_text("x")
        os.chmod(self.root, stat.S_IREAD | stat.S_IEXEC)
        self.assertIsNone(u.portable_dir())
        self.assertNotIn(str(self.root), str(u.config_dir()))

    def test_where_the_app_lives(self):
        os.environ.pop("SPHERESCAN_PORTABLE_ROOT")
        old = (getattr(sys, "frozen", None), sys.executable)
        try:
            sys.frozen = True
            sys.executable = str(self.root / "SphereScan")
            self.assertEqual(u.portable_root(), self.root.resolve())
            if u.IS_MAC:
                app = self.root / "SphereScan.app" / "Contents" / "MacOS"
                app.mkdir(parents=True)
                sys.executable = str(app / "SphereScan")
                self.assertEqual(u.portable_root(), self.root.resolve())   # next to the .app, not inside it
        finally:
            sys.executable = old[1]
            if old[0] is None:
                del sys.frozen
            else:
                sys.frozen = old[0]

    def test_a_whole_run_leaves_nothing_in_the_users_folders(self):
        (self.root / "portable.txt").write_text("x")
        os.environ["SPHERESCAN_SPIRULA"] = launcher(Path(self.t.name))
        dump = Path(self.t.name) / "env.json"
        os.environ["FAKE_ENV_DUMP"] = str(dump)
        self.addCleanup(lambda: (os.environ.pop("SPHERESCAN_SPIRULA", None), os.environ.pop("FAKE_ENV_DUMP", None)))
        src = Path(self.t.name) / "footage"
        mp4(src / "IMG_x.insv", 30)
        g = hardware.Gpu("RTX 3060", "nvidia", 12.0, False, 1.0)
        hw = hardware.Hardware("Linux", 8, 16.0, [g], True, hardware.DecodePlan(None, None, True, ""), 12.0)
        job = Job(captures=footage.scan_folder(str(src)), detail_level=1, format_key="glb",
                  out_dir=str(Path(self.t.name) / "out"), hw=hw)
        job.start()
        for _ in range(300):
            if job.state != "running":
                break
            time.sleep(0.1)
        self.assertEqual(job.state, "done", job.message)
        data = self.root / "SphereScan-data"
        self.assertTrue((data / "settings" / "calibration.json").exists())      # what we learned stays with the app
        seen = json.loads(dump.read_text())                                      # and Spirula was pointed there too
        self.assertEqual(Path(seen["XDG_CONFIG_HOME"]), data / "engine" / "config")
        self.assertEqual(list(self.home.iterdir()), [], "nothing may be written to the user's own folders")


if __name__ == "__main__":
    unittest.main()
