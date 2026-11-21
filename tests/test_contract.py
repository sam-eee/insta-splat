"""Checks every Spirula flag this app uses still exists upstream.

Set SPIRULA_SRC to a checkout of spirula-studio. Skipped otherwise.
"""
import os
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from insta_splat import memory, recipe
from insta_splat.presets import DETAILS, FORMATS

SRC = os.environ.get("SPIRULA_SRC")


@unittest.skipUnless(SRC, "SPIRULA_SRC not set")
class Contract(unittest.TestCase):
    def text(self, rel):
        return (Path(SRC) / rel).read_text(encoding="utf-8", errors="replace")

    def flags(self, cmd):
        return [a for a in cmd if re.fullmatch(r"-{1,2}[a-z0-9][a-z0-9-]*", a)]

    def test_extract_flags(self):
        src = self.text("src/app/cli/sam_extract.cpp")
        for paired in (True, False):
            for kind in ("insta360", "gopro"):
                cmd = recipe.extract_cmd("s", "v", "o", kind=kind, paired=paired, max_frames=1, skip=1,
                                         decoder="auto", ffmpeg="f")
                for f in self.flags(cmd):
                    self.assertIn(f'"{f}"', src, f)
        self.assertIn("faces", self.text("docs/datasets.md"))

    def test_sfm_flags(self):
        src = self.text("src/app/cli/sfm_main.cpp") + self.text("src/sfm/README.md")
        cmd = recipe.sfm_cmd("s", "w", DETAILS[2], rig_members=["cam0", "cam1"], fisheye=True, frame_width=100)
        for f in self.flags(cmd):
            self.assertIn(f, src, f)
        self.assertIn("thin-prism-fisheye", src)
        self.assertIn("dual-fisheye=", src)

    def test_train_flags_and_quality_levels(self):
        cfg = self.text("src/config/TrainConfig.h")
        cmd = recipe.train_cmd("s", "w", "t", DETAILS[2], memory.plan_at(DETAILS[2], None, 5))
        for f in self.flags(cmd):
            self.assertIn(f.lstrip("-").replace("-", "_"), cfg, f)
        for d in DETAILS.values():
            self.assertIn(f'c.quality == "{d.train_quality}"', cfg)

    def test_mesh_flags_and_formats(self):
        src = self.text("src/app/cli/mesh_main.cpp") + self.text("src/i18n/catalog/Mesh.h")
        for fmt in FORMATS.values():
            cmd = recipe.mesh_cmd("s", "r", "w", "o", DETAILS[2], fmt)
            for f in self.flags(cmd):
                self.assertIn(f, src, f)
            self.assertIn(fmt.spirula_format.split("+")[0], src)


if __name__ == "__main__":
    unittest.main()
