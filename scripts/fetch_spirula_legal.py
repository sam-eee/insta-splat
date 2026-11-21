"""Fetch the licence files of a Spirula Studio release for bundling, and optionally its source.

  python scripts/fetch_spirula_legal.py v2026.11.12 vendor/licenses/spirula-studio --save-source dist/

GPLv3 requires that anyone who gets the Spirula binary from us also gets its licence
and can get its Corresponding Source. This copies the licence files into the app and
saves the exact source archive of the pinned tag so it can be attached to the release.
"""

import re
import sys
import tarfile
import urllib.request
from pathlib import Path

REPO = "harry7557558/spirula-studio"
KEEP = re.compile(r"^[^/]+/(LICENSE|LICENSES/[^/]+)$")


def extract_licenses(archive: Path, out: Path) -> list:
    out.mkdir(parents=True, exist_ok=True)
    written = []
    with tarfile.open(archive) as tf:
        for m in tf.getmembers():
            if m.isfile() and KEEP.match(m.name):
                dest = out / m.name.split("/", 1)[1]
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(tf.extractfile(m).read())
                written.append(str(dest))
    if not any(Path(w).name == "LICENSE" for w in written):
        raise SystemExit("Spirula's LICENSE was not found in the source archive; refusing to continue.")
    return written


def main() -> None:
    tag, out = sys.argv[1], Path(sys.argv[2])
    save = Path(sys.argv[sys.argv.index("--save-source") + 1]) if "--save-source" in sys.argv else None
    archive = (save or out.parent) / f"spirula-studio-{tag}-source.tar.gz"
    archive.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(f"https://github.com/{REPO}/archive/refs/tags/{tag}.tar.gz", archive)
    for f in extract_licenses(archive, out):
        print("licence:", f)
    if not save:
        archive.unlink()
    else:
        print("source:", archive)


if __name__ == "__main__":
    main()
