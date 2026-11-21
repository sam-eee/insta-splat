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
from spherescan import estimate, footage, hardware, memory, recipe
from spherescan.pipeline import Job
from spherescan.presets import DETAILS, FORMATS


def fake_hw(vram=12.0, hwaccel=None):
    g = hardware.Gpu("NVIDIA GeForce RTX 3060", "nvidia", vram, False, 1.0)
    return hardware.Hardware("Linux", 8, 16.0, [g], True,
                             hardware.DecodePlan("/usr/bin/ffmpeg" if hwaccel else None, hwaccel, True, "test"), vram)


class OneRecording(unittest.TestCase):
    def test_server_refuses_more_than_one(self):
        from spherescan.server import App
        app = App.__new__(App)
        app.hw = fake_hw()
        app._hw_ready = __import__("threading").Event(); app._hw_ready.set()
        app.job = None
        with tempfile.TemporaryDirectory() as t:
            mp4(Path(t) / "GS010001.360", 10); mp4(Path(t) / "GS010002.360", 10)
            caps = footage.scan_folder(t)
            self.assertEqual(len(caps), 2)
            app.captures = {c.id: c for c in caps}
            ids = list(app.captures)
            self.assertIn("error", app.estimate(ids))
            self.assertIn("error", app.start({"ids": ids, "level": 1, "format": "glb", "out_dir": t}))
            self.assertEqual(len(app.estimate(ids[:1])["levels"]), 4)


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
        self.assertIn("graphics card", lv[3]["memory_note"])

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


class Library(unittest.TestCase):
    def setUp(self):
        from spherescan import gpudb
        self.db = gpudb
        self.t = tempfile.TemporaryDirectory()
        os.environ["XDG_CONFIG_HOME"] = self.t.name
        os.environ["APPDATA"] = self.t.name
        gpudb.reload()
        self.addCleanup(lambda: (self.t.cleanup(), gpudb.reload()))

    def test_names_from_every_platform_resolve_to_the_right_card(self):
        for query, want in [
            ("NVIDIA RTX A500 Laptop GPU", "RTX A500 Laptop"),
            ("NVIDIA GeForce RTX 4070 Laptop GPU", "GeForce RTX 4070 Laptop"),
            ("NVIDIA GeForce RTX 4070", "GeForce RTX 4070"),
            ("NVIDIA GeForce RTX 4070 Ti SUPER", "GeForce RTX 4070 Ti Super"),
            ("NVIDIA Corporation GA107M [GeForce RTX 3050 Mobile]", "GeForce RTX 3050 Laptop"),
            ("AMD Radeon RX 7900 XTX", "Radeon RX 7900 XTX"),
            ("AMD Radeon RX 7900 XT", "Radeon RX 7900 XT"),
            ("Intel(R) Graphics (MTL)", "Intel Arc Graphics (Meteor Lake)"),
            ("Apple M2 Pro", "Apple M2 Pro"),
            ("Apple M2", "Apple M2")]:
            e = self.db.find_gpu(query)
            self.assertEqual(e and e["name"], want, query)
        self.assertIsNone(self.db.find_gpu("Totally Unknown 9000"))

    def test_library_is_well_formed(self):
        names = [g["name"] for g in self.db.gpus()]
        self.assertEqual(len(names), len(set(names)))
        self.assertGreater(len(names), 100)
        for g in self.db.gpus():
            self.assertGreater(g["tflops"], 0, g["name"])
            self.assertGreater(g["bandwidth_gbps"], 0, g["name"])
            self.assertEqual(g["integrated"], g.get("vram_gb") is None, g["name"])
        for c in self.db.cpus():
            self.assertGreater(c["score"], 0)

    def test_faster_cards_score_higher_within_a_family(self):
        for chain in (["RTX 3060", "RTX 3070", "RTX 3080", "RTX 3090"], ["RTX 4060", "RTX 4070", "RTX 4080", "RTX 4090"],
                      ["RX 7600", "RX 7800 XT", "RX 7900 XTX"], ["Apple M2", "Apple M2 Pro", "Apple M2 Max"]):
            speeds = [self.db.gpu_speed(self.db.find_gpu(n)) for n in chain]
            self.assertEqual(speeds, sorted(speeds), chain)
        self.assertAlmostEqual(self.db.gpu_speed(self.db.find_gpu("RTX 3060")), 1.0, places=2)

    def test_user_entries_override_and_extend(self):
        (Path(self.t.name) / "spherescan").mkdir(exist_ok=True)
        import json
        from spherescan.util import config_dir
        (config_dir() / "gpus.json").write_text(json.dumps({"gpus": [
            {"name": "GeForce RTX 3060", "vendor": "nvidia", "tflops": 25.4, "bandwidth_gbps": 360, "vram_gb": 12, "integrated": False},
            {"name": "Mystery X1", "vendor": "other", "tflops": 8, "bandwidth_gbps": 200, "vram_gb": 8, "integrated": False}]}))
        self.db.reload()
        self.assertEqual(self.db.find_gpu("RTX 3060")["tflops"], 25.4)
        self.assertEqual(self.db.find_gpu("Mystery X1")["vram_gb"], 8)

    def test_estimates_scale_with_the_card_offline(self):
        def total(gpu, cpu=None):
            return estimate.estimate(DETAILS[2], 420, 2, hardware.virtual(gpu, cpu))["total_s"]
        self.assertLess(total("RTX 4090"), total("RTX 3060"))
        self.assertLess(total("RTX 3060"), total("RTX A500 Laptop"))
        self.assertGreater(total("RTX A500 Laptop") / total("RTX 4090"), 4)
        self.assertLess(total("RTX 4090", "Ryzen 9 7950X"), total("RTX 4090", "Intel Core i7-1165G7"))

    def test_anchored_on_a_real_measured_run(self):
        # Measured on an RTX A500 Laptop (4 GB): 240 images -> sfm 81 s, level-1 training ~2h20 for 15000 steps.
        hw = hardware.virtual("RTX A500 Laptop")
        st = estimate.stage_seconds(DETAILS[1], 120, 2, hw)
        self.assertAlmostEqual(st["sfm"], 81, delta=45)
        self.assertAlmostEqual(st["train"], 2 * 3600 + 20 * 60, delta=2400)

    def test_unknown_card_widens_the_range_and_virtual_refuses_it(self):
        hw = fake_hw(); hw.gpus[0].name = "Unknown"; hw.gpus[0].spec_known = False
        k = fake_hw(); k.gpus[0].spec_known = True
        known = estimate.estimate(DETAILS[2], 300, 2, k)
        guess = estimate.estimate(DETAILS[2], 300, 2, hw)
        self.assertGreater(guess["high_s"] / guess["low_s"], known["high_s"] / known["low_s"])
        with self.assertRaises(ValueError):
            hardware.virtual("Not A Real Card")

    def test_detected_cards_use_the_library(self):
        g = hardware._classify("NVIDIA RTX A500 Laptop GPU", None, 16.0)
        self.assertTrue(g.spec_known)
        self.assertEqual(g.vram_gb, 4)             # the OS could not say; the library can
        self.assertTrue(hardware._classify("Intel(R) Graphics (MTL)", None, 32.0).integrated)


class Progress(unittest.TestCase):
    TRAIN = "step    601/15000 (  4%)  splats 48554  [elapsed 00:03:56.109 | ETA 02:11:54.947]  rgb_loss=0.1231"
    FORECAST = "- Projected peak: 6.23 +/- 1.40 GiB   free for training: 3.62 GiB   chance of running out: 97%."

    def test_a_memory_warning_is_not_progress(self):
        from spherescan.progress import StageParser
        p = StageParser("train")
        p.feed(self.FORECAST)
        self.assertEqual(p.fraction, 0.0)
        self.assertEqual((p.forecast.chance_oom, p.forecast.free_gib), (97, 3.62))

    def test_train_step_lines_give_fraction_and_spirulas_eta(self):
        from spherescan.progress import StageParser
        p = StageParser("train")
        p.feed(self.TRAIN)
        self.assertAlmostEqual(p.fraction, 601 / 15000)
        self.assertEqual(p.own_eta, 2 * 3600 + 11 * 60 + 54)

    def test_sfm_lines_and_other_stages_ignore_stray_percentages(self):
        from spherescan.progress import StageParser
        s = StageParser("sfm")
        s.feed("RESULT-less line says 100% of the images")        # no exact format: ignored
        self.assertEqual(s.fraction, 0.0)
        s.feed("  120/240   frame_0001.jpg   Features: 8123")
        self.assertAlmostEqual(s.fraction, 0.15)
        s.feed("10500/21000 pairs matched")
        self.assertAlmostEqual(s.fraction, 0.45)
        s.feed("21000/21000 pairs matched")
        self.assertTrue(s.mapping)
        s.feed("[run]     Mapping: 00:01:08   Registered: 240/240 images   Points: 46242   Cameras: 2")
        self.assertEqual(s.fraction, 1.0)

    def test_eta_uses_spirulas_own_estimate_and_scales_later_stages(self):
        from spherescan.progress import eta_seconds
        order = ["extract", "sfm", "train", "mesh"]
        pred = {"extract": 60, "sfm": 300, "train": 1000, "mesh": 200}
        # train 10% in; machine is running 2x slower than predicted so far
        e = eta_seconds(order, pred, "train", 0.1, elapsed_total=2 * (60 + 300 + 100), stage_elapsed=200, own_eta=1800)
        self.assertAlmostEqual(e, 1800 + 200 * 2, delta=1)
        self.assertEqual(eta_seconds(order, pred, "mesh", 1.0, 1500, 10), 0.0)

    def test_mesh_progress_file(self):
        from spherescan.progress import read_mesh_progress
        with tempfile.TemporaryDirectory() as t:
            self.assertIsNone(read_mesh_progress(Path(t)))
            (Path(t) / "memory.json").write_text('{"phase": "x", "work": 0.42}')
            self.assertEqual(read_mesh_progress(Path(t)), 0.42)
            (Path(t) / "memory.json").write_text('{"work": -1}')
            self.assertIsNone(read_mesh_progress(Path(t)))


class Packaging(unittest.TestCase):
    def test_bundled_spirula_is_found_under_vendor_and_made_executable(self):
        import spherescan.util as u
        with tempfile.TemporaryDirectory() as t:
            nested = Path(t) / "vendor" / "spirula" / "Spirula Studio.app" / "Contents" / "MacOS"
            nested.mkdir(parents=True)
            exe = nested / ("spirula.exe" if os.name == "nt" else "spirula")
            exe.write_text("x")
            if os.name != "nt":
                exe.chmod(0o644)
            old = os.environ.pop("SPHERESCAN_SPIRULA", None)
            u.app_root = lambda: Path(t)
            try:
                self.assertEqual(u.find_executable("spirula", "SPHERESCAN_SPIRULA"), str(exe))
                if os.name != "nt":
                    self.assertTrue(os.access(exe, os.X_OK))
            finally:
                u.app_root = lambda: Path(u.__file__).resolve().parent.parent
                if old is not None:
                    os.environ["SPHERESCAN_SPIRULA"] = old

    def test_app_quits_itself_after_the_page_closes_but_not_during_a_run(self):
        from spherescan.server import App
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

    def test_result_stays_reachable_for_a_while_after_the_tab_was_closed(self):
        from spherescan.server import App
        app = App.__new__(App)
        app.started, app.last_ping = time.time() - 1000, time.time() - 500        # page closed long ago
        app.job = type("J", (), {"state": "done", "ended": time.time() - 60})()
        self.assertFalse(app.idle_for_quit())                                     # finished a minute ago: stay up
        app.job.ended = time.time() - 4000
        self.assertTrue(app.idle_for_quit())                                      # long finished: may quit

    def test_page_recovers_a_run_from_the_app_when_reloaded(self):
        html = (Path(__file__).parent.parent / "spherescan" / "web" / "index.html").read_text()
        self.assertIn("async function restore()", html)
        self.assertTrue(html.rstrip().endswith("</html>"))
        self.assertIn("restore();\n</script>", html)

    def test_build_command_bundles_web_and_entry_point(self):
        import build
        cmd = " ".join(build.command())
        self.assertIn("spherescan/web", cmd)
        self.assertTrue(cmd.endswith("run_app.py"))


class EngineEnv(unittest.TestCase):
    def test_bundled_libs_are_appended_not_prepended(self):
        import spherescan.util as u
        if not u.IS_LINUX:
            self.skipTest("Linux only")
        with tempfile.TemporaryDirectory() as t:
            (Path(t) / "vendor" / "libs").mkdir(parents=True)
            old_root, old_env = u.app_root, os.environ.get("LD_LIBRARY_PATH")
            u.app_root = lambda: Path(t)
            os.environ["LD_LIBRARY_PATH"] = "/system/libs"
            try:
                v = u.engine_env()["LD_LIBRARY_PATH"].split(os.pathsep)
            finally:
                u.app_root = old_root
                os.environ.pop("LD_LIBRARY_PATH") if old_env is None else os.environ.update(LD_LIBRARY_PATH=old_env)
            self.assertEqual(v[0], "/system/libs")
            self.assertTrue(v[-1].endswith("libs"))

    def test_missing_system_library_gets_a_plain_message(self):
        from spherescan.pipeline import Job
        src = Path(tempfile.mkdtemp())
        mp4(src / "IMG_x.insv", 30)
        stub = src / "spirula"
        stub.write_text("#!/bin/sh\necho 'spirula: error while loading shared libraries: libFoo.so.1: cannot open shared object file' >&2\nexit 127\n")
        stub.chmod(0o755)
        os.environ["SPHERESCAN_SPIRULA"] = str(stub)
        self.addCleanup(os.environ.pop, "SPHERESCAN_SPIRULA", None)
        job = Job(captures=footage.scan_folder(str(src)), detail_level=1, format_key="glb",
                  out_dir=str(src / "o"), hw=fake_hw())
        job.start()
        for _ in range(100):
            if job.state != "running":
                break
            time.sleep(0.1)
        self.assertEqual(job.state, "failed")
        self.assertIn("libFoo.so.1", job.message)
        self.assertIn("Install", job.message)


class Legal(unittest.TestCase):
    def test_licences_are_extracted_and_legal_page_lists_them(self):
        import io
        import tarfile
        sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
        import fetch_spirula_legal as f
        with tempfile.TemporaryDirectory() as t:
            tgz = Path(t) / "s.tar.gz"
            with tarfile.open(tgz, "w:gz") as tf:
                for name, data in (("spirula-1/LICENSE", b"GPL TEXT"), ("spirula-1/LICENSES/MIT-X.txt", b"MIT"),
                                   ("spirula-1/src/main.cpp", b"code")):
                    ti = tarfile.TarInfo(name); ti.size = len(data)
                    tf.addfile(ti, io.BytesIO(data))
            out = Path(t) / "vendor" / "licenses" / "spirula-studio"
            written = f.extract_licenses(tgz, out)
            self.assertEqual(len(written), 2)
            self.assertEqual((out / "LICENSE").read_text(), "GPL TEXT")
            self.assertFalse((out / "src").exists())
            (Path(t) / "THIRD_PARTY.md").write_text("notice")
            import spherescan.util as u
            from spherescan.server import legal_text
            old = u.app_root
            u.app_root = lambda: Path(t)
            try:
                text = legal_text()
            finally:
                u.app_root = old
            self.assertIn("notice", text)
            self.assertIn("GPL TEXT", text)

    def test_missing_licence_is_a_hard_error(self):
        import io
        import tarfile
        sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
        import fetch_spirula_legal as f
        with tempfile.TemporaryDirectory() as t:
            tgz = Path(t) / "s.tar.gz"
            with tarfile.open(tgz, "w:gz") as tf:
                ti = tarfile.TarInfo("x/README"); ti.size = 1
                tf.addfile(ti, io.BytesIO(b"."))
            with self.assertRaises(SystemExit):
                f.extract_licenses(tgz, Path(t) / "o")

    def test_page_and_bundle_carry_the_notices(self):
        root = Path(__file__).parent.parent
        self.assertIn("/legal", (root / "spherescan/web/index.html").read_text())
        notice = (root / "THIRD_PARTY.md").read_text()
        for needle in ("unmodified", "Written offer", "patent"):
            self.assertIn(needle, notice)
        self.assertIn("THIRD_PARTY.md", (root / "build.py").read_text())
        rel = (root / ".github/workflows/release.yml").read_text()
        for needle in ("fetch_spirula_legal.py", "source.tar.gz", "SHA256SUMS"):
            self.assertIn(needle, rel)


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
        os.environ["SPHERESCAN_SPIRULA"] = launcher(self.d)
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
        self.assertIn("graphics card", job.snapshot()["detail_note"])

    def test_real_failure_is_not_retried_forever(self):
        src = self.d / "in"
        mp4(src / "IMG_x.insv", 30)
        os.environ["FAKE_OOM_ABOVE"] = "0"
        self.addCleanup(os.environ.pop, "FAKE_OOM_ABOVE", None)
        job = self.run_job(footage.scan_folder(str(src)))
        self.assertEqual(job.state, "failed")
        self.assertEqual(len([c for c in job.commands if c[1] == "train"]), memory.MAX_STEP + 1)

    def test_spirulas_memory_forecast_triggers_an_early_restart_with_less(self):
        src = self.d / "in"
        mp4(src / "IMG_x.insv", 30)
        os.environ["FAKE_FORECAST_ABOVE"] = "180000"
        self.addCleanup(os.environ.pop, "FAKE_FORECAST_ABOVE", None)
        job = self.run_job(footage.scan_folder(str(src)), keep_work=True)
        self.assertEqual(job.state, "done", job.message)
        trains = [c for c in job.commands if c[1] == "train"]
        self.assertGreater(len(trains), 1)
        self.assertLessEqual(int(trains[-1][trains[-1].index("--cap-max") + 1]), 180000)
        self.assertTrue(any("expects to run out" in l for l in job.log))
        self.assertEqual(memory.learned_step(DETAILS[1], fake_hw()) > 0, True)   # remembered for next time

    def test_progress_comes_from_spirula_and_an_eta_is_given(self):
        src = self.d / "in"
        mp4(src / "IMG_x.insv", 30)
        job = Job(captures=footage.scan_folder(str(src)), detail_level=1, format_key="glb",
                  out_dir=str(self.d / "out"), hw=fake_hw())
        seen = []
        orig = job._publish
        def spy():
            orig()
            seen.append((job.stage, round(job.stage_progress, 2), job.eta_s))
        job._publish = spy
        job.start()
        for _ in range(300):
            if job.state != "running":
                break
            time.sleep(0.1)
        self.assertEqual(job.state, "done", job.message)
        train = [p for st, p, _ in seen if st == "train"]
        self.assertTrue(any(0.05 < p < 0.95 for p in train), train)      # real steps, not a jump to 90%
        self.assertEqual(train, sorted(train))
        self.assertTrue(any(e is not None for _, _, e in seen))
        mesh = [p for st, p, _ in seen if st == "mesh"]
        self.assertTrue(any(abs(p - 0.5) < 0.01 for p in mesh), mesh)    # read from Spirula's progress file

    def test_dry_run_prints_commands_and_runs_nothing(self):
        src = self.d / "in"
        mp4(src / "IMG_x.insv", 30)
        os.environ.pop("SPHERESCAN_SPIRULA")
        job = self.run_job(footage.scan_folder(str(src)), dry_run=True)
        self.assertEqual(job.state, "done", job.message)
        self.assertEqual([c[1] for c in job.commands], ["sam", "sfm", "train", "mesh"])


if __name__ == "__main__":
    unittest.main()
