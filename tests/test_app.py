import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from helpers import launcher, mp4
from insta_splat import estimate, footage, hardware, memory, recipe
from insta_splat.pipeline import Job
from insta_splat.presets import DETAILS, FORMATS


def fake_hw(vram=12.0, hwaccel=None):
    g = hardware.Gpu("NVIDIA GeForce RTX 3060", "nvidia", vram, False, 1.0)
    return hardware.Hardware("Linux", 8, 16.0, [g], True,
                             hardware.DecodePlan("/usr/bin/ffmpeg" if hwaccel else None, hwaccel, True, "test"), vram)


class Scan(unittest.TestCase):
    def test_insv_pairs_chapters_and_lrv(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            for lens in ("00", "10"):
                for ch in ("001", "002"):
                    mp4(d / f"VID_20250101_120000_{lens}_{ch}.insv", 60)
            mp4(d / "LRV_20250101_120000_01_001.insv", 60)      # preview: ignored
            mp4(d / "GS010042.360", 30)
            mp4(d / "GS020042.360", 30)
            mp4(d / "DJI_0001.OSV", 20)
            caps = {c.kind: c for c in footage.scan_folder(t)}
            ins = caps["insta360"]
            self.assertTrue(ins.paired)
            self.assertEqual([len(c) for c in ins.lens_files], [2, 2])
            self.assertAlmostEqual(ins.duration_s, 120, delta=1)     # not doubled by the pair
            self.assertEqual(len(footage.scan_folder(t)), 3)
            self.assertEqual(len(caps["gopro"].lens_files[0]), 2)
            self.assertAlmostEqual(caps["gopro"].duration_s, 60, delta=1)
            self.assertFalse(caps["dji"].paired)

    def test_single_two_track_insv_is_not_paired(self):
        with tempfile.TemporaryDirectory() as t:
            mp4(Path(t) / "IMG_something.insv", 10)
            (c,) = footage.scan_folder(t)
            self.assertFalse(c.paired)


class Estimates(unittest.TestCase):
    def test_monotonic_and_small_cards_still_allowed(self):
        hw = fake_hw(vram=4)
        lv = estimate.all_levels(300, 2, hw)
        totals = [x["total_s"] for x in lv]
        self.assertEqual(totals, sorted(totals))
        self.assertTrue(all(x["available"] for x in lv))
        self.assertEqual([x["reduced"] for x in lv], [False, False, True, True])
        self.assertIn("graphics memory", lv[3]["memory_note"])

    def test_card_too_small_is_refused(self):
        self.assertFalse(estimate.available(DETAILS[1], fake_hw(vram=1.0))[0])

    def test_faster_gpu_is_faster(self):
        slow, fast = fake_hw(), fake_hw()
        fast.gpus[0].speed = 3.0
        self.assertLess(estimate.estimate(DETAILS[3], 300, 2, fast)["total_s"],
                        estimate.estimate(DETAILS[3], 300, 2, slow)["total_s"])

    def test_gpu_decode_is_faster(self):
        a = estimate.stage_seconds(DETAILS[2], 600, 2, fake_hw())["extract"]
        b = estimate.stage_seconds(DETAILS[2], 600, 2, fake_hw(hwaccel="cuda"))["extract"]
        self.assertLess(b, a)

    def test_unknown_vram_assumes_a_modest_card(self):
        hw = fake_hw(); hw.usable_vram_gb = None
        lv = estimate.all_levels(60, 2, hw)
        self.assertTrue(all(x["available"] for x in lv))
        self.assertTrue(lv[3]["reduced"] and not lv[0]["reduced"])


class Memory(unittest.TestCase):
    def test_more_memory_never_means_less_detail(self):
        for d in DETAILS.values():
            prev = 0
            for v in (2.5, 3, 4, 6, 8, 12, 24):
                p = memory.plan(d, fake_hw(vram=v))
                self.assertGreaterEqual(p.splats_m, prev)
                prev = p.splats_m

    def test_full_detail_when_it_fits(self):
        p = memory.plan(DETAILS[4], fake_hw(vram=24))
        self.assertFalse(p.reduced)
        self.assertEqual(p.cap_max, 10_000_000)
        self.assertEqual(p.note(), "")

    def test_ladder_shrinks_and_ends_in_progressive_resolution(self):
        plans = [memory.plan_at(DETAILS[4], fake_hw(), s) for s in range(memory.MAX_STEP + 1)]
        self.assertEqual([p.splats_m for p in plans], sorted((p.splats_m for p in plans), reverse=True))
        self.assertEqual(plans[0].max_resolution, 0)
        self.assertTrue(plans[-1].progressive and plans[-1].max_resolution > 0)

    def test_reduced_plan_reaches_the_command(self):
        p = memory.plan(DETAILS[4], fake_hw(vram=4))
        cmd = recipe.train_cmd("s", "w", "t", DETAILS[4], p)
        self.assertEqual(cmd[cmd.index("--cap-max") + 1], str(p.cap_max))
        self.assertIn("--train-resolution-divisor", cmd)
        self.assertNotIn("--max-resolution", cmd)

    def test_slower_when_squeezed_but_cheaper_per_step(self):
        big = estimate.estimate(DETAILS[4], 300, 2, fake_hw(vram=24))["stages"]["train"]
        small = estimate.estimate(DETAILS[4], 300, 2, fake_hw(vram=4))["stages"]["train"]
        self.assertLess(small, big)   # far fewer splats outweighs the small-batch overhead

    def test_oom_detection(self):
        self.assertTrue(memory.looks_like_oom(["x", "VK_ERROR_OUT_OF_DEVICE_MEMORY"]))
        self.assertFalse(memory.looks_like_oom(["step 10/100", "no memory leaks found"]))


class Packaging(unittest.TestCase):
    def test_bundled_spirula_is_found_under_vendor_and_made_executable(self):
        import insta_splat.util as u
        with tempfile.TemporaryDirectory() as t:
            nested = Path(t) / "vendor" / "spirula" / "Spirula Studio.app" / "Contents" / "MacOS"
            nested.mkdir(parents=True)
            exe = nested / ("spirula.exe" if os.name == "nt" else "spirula")
            exe.write_text("x")
            if os.name != "nt":
                exe.chmod(0o644)
            old = os.environ.pop("INSTA_SPLAT_SPIRULA", None)
            u.app_root = lambda: Path(t)
            try:
                self.assertEqual(u.find_executable("spirula", "INSTA_SPLAT_SPIRULA"), str(exe))
                if os.name != "nt":
                    self.assertTrue(os.access(exe, os.X_OK))
            finally:
                u.app_root = lambda: Path(u.__file__).resolve().parent.parent
                if old is not None:
                    os.environ["INSTA_SPLAT_SPIRULA"] = old

    def test_app_quits_itself_after_the_page_closes_but_not_during_a_run(self):
        from insta_splat.server import App
        app = App.__new__(App)
        app.job, app.started = None, time.time() - 1000
        app.last_ping = app.started
        self.assertTrue(app.idle_for_quit())                  # never loaded
        app.last_ping = time.time()
        self.assertFalse(app.idle_for_quit())                 # page open
        app.last_ping = time.time() - 100
        self.assertTrue(app.idle_for_quit())                  # page closed
        app.job = type("J", (), {"state": "running"})()
        self.assertFalse(app.idle_for_quit())                 # but never mid-run

    def test_build_command_bundles_web_and_entry_point(self):
        import build
        cmd = " ".join(build.command())
        self.assertIn("insta_splat/web", cmd)
        self.assertTrue(cmd.endswith("run_app.py"))


class Recipe(unittest.TestCase):
    def test_default_format_is_glb_with_texture(self):
        f = FORMATS["glb"]
        cmd = recipe.mesh_cmd("s", "run", "ws", "out", DETAILS[2], f)
        self.assertIn("glb+jpg", cmd)
        self.assertEqual(cmd[cmd.index("--color") + 1], "texture")
        for k, v in FORMATS.items():
            if v.keeps_color:
                self.assertEqual(v.color, "texture", k)


class Pipeline(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.d = Path(self.t.name)
        os.environ["INSTA_SPLAT_SPIRULA"] = launcher(self.d)
        os.environ["XDG_CONFIG_HOME"] = str(self.d / "cfg")
        os.environ["APPDATA"] = str(self.d / "cfg")
        self.addCleanup(self.t.cleanup)

    def run_job(self, caps, **kw):
        job = Job(captures=caps, detail_level=1, format_key=kw.pop("fmt", "glb"), out_dir=str(self.d / "out"),
                  hw=fake_hw(hwaccel=kw.pop("hwaccel", None)), **kw)
        job.start()
        for _ in range(300):
            if job.state != "running":
                break
            time.sleep(0.1)
        return job

    def test_paired_insta360_end_to_end(self):
        src = self.d / "in"
        for lens in ("00", "10"):
            mp4(src / f"VID_20250101_120000_{lens}_001.insv", 30)
        job = self.run_job(footage.scan_folder(str(src)))
        self.assertEqual(job.state, "done", job.message)
        self.assertTrue(job.result.endswith(".glb"))
        self.assertTrue(Path(job.result).exists())
        self.assertFalse(list((self.d / "out").glob(".*_work")), "work folder should be cleaned up")
        self.assertTrue((estimate.config_dir() / "calibration.json").exists())

    def test_two_track_file_end_to_end_with_hw_decode_shim(self):
        src = self.d / "in"
        mp4(src / "IMG_x.insv", 30)
        job = self.run_job(footage.scan_folder(str(src)), hwaccel="cuda", keep_work=True)
        self.assertEqual(job.state, "done", job.message)
        self.assertTrue(list((self.d / "out").glob(".*_work/ffmpeg_hw.*")))

    def test_gopro_uses_all_views_as_rig(self):
        src = self.d / "in"
        mp4(src / "GS010001.360", 30)
        job = self.run_job(footage.scan_folder(str(src)), keep_work=True)
        self.assertEqual(job.state, "done", job.message)
        sfm = [c for c in job.commands if c[1:3] == ["sfm", "auto"]][0]
        self.assertEqual(sfm[sfm.index("--rig") + 1], ",".join(f"cam{i}" for i in range(10)))
        self.assertNotIn("--camera-model", sfm)

    def test_out_of_memory_retries_with_less_detail(self):
        src = self.d / "in"
        mp4(src / "IMG_x.insv", 30)
        os.environ["FAKE_OOM_ABOVE"] = "180000"           # level 1 asks for 200k, so it must shrink
        self.addCleanup(os.environ.pop, "FAKE_OOM_ABOVE", None)
        job = self.run_job(footage.scan_folder(str(src)), keep_work=True)
        self.assertEqual(job.state, "done", job.message)
        trains = [c for c in job.commands if c[1] == "train"]
        caps = [int(c[c.index("--cap-max") + 1]) for c in trains]
        self.assertGreater(len(caps), 1)
        self.assertEqual(caps, sorted(caps, reverse=True))
        self.assertLessEqual(caps[-1], 180000)
        self.assertIn("graphics memory", job.snapshot()["detail_note"])

    def test_real_failure_is_not_retried_forever(self):
        src = self.d / "in"
        mp4(src / "IMG_x.insv", 30)
        os.environ["FAKE_OOM_ABOVE"] = "0"
        self.addCleanup(os.environ.pop, "FAKE_OOM_ABOVE", None)
        job = self.run_job(footage.scan_folder(str(src)))
        self.assertEqual(job.state, "failed")
        self.assertEqual(len([c for c in job.commands if c[1] == "train"]), memory.MAX_STEP + 1)

    def test_dry_run_prints_commands_and_runs_nothing(self):
        src = self.d / "in"
        mp4(src / "IMG_x.insv", 30)
        os.environ.pop("INSTA_SPLAT_SPIRULA")
        job = self.run_job(footage.scan_folder(str(src)), dry_run=True)
        self.assertEqual(job.state, "done", job.message)
        self.assertEqual([c[1] for c in job.commands], ["sam", "sfm", "train", "mesh"])


if __name__ == "__main__":
    unittest.main()
