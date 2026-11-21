"""Stands in for spirula: checks the flags the app sends and writes plausible output."""
import json
import struct
import sys
from pathlib import Path

a = sys.argv[1:]


def opt(name, default=None):
    return a[a.index(name) + 1] if name in a else default


def jpeg(w=1920, h=1920):
    sof = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, h, w, 1) + b"\x01\x11\x00"
    return b"\xff\xd8" + sof + b"\xff\xd9"


def need(*flags):
    for f in flags:
        if f not in a:
            print(f"fake spirula: missing {f}", file=sys.stderr)
            sys.exit(3)


if a[:2] == ["sam", "extract"]:
    out = Path(opt("--out"))
    n = 12
    if "--sync" in a:
        need("--adaptive", "--max-frames")
        dirs = [f"cam{i}" for i in range(10 if opt("--360") == "faces" else 2)]
    else:
        need("--skip")
        dirs = ["cam0"]
    for d in dirs:
        (out / d).mkdir(parents=True, exist_ok=True)
        for i in range(n):
            (out / d / f"{i:04d}.jpg").write_bytes(jpeg())
    print(f"extracted {n}/{n} frames")
elif a[:2] == ["sfm", "auto"]:
    need("--data-type", "--quality", "--rig", "-o")
    ws = Path(opt("-o"))
    (ws / "sparse" / "0").mkdir(parents=True)
    print("matching 50%")
    print("mapping 100%")
elif a[:2] == ["train", "meshing"]:
    need("--data", "--quality", "--disable-viewer")
    assert (Path(opt("--data")) / "sparse").is_dir(), "sfm output missing"
    import os
    limit = os.environ.get("FAKE_OOM_ABOVE")
    if limit and int(opt("--cap-max")) > int(limit):
        print("vkAllocateMemory failed: VK_ERROR_OUT_OF_DEVICE_MEMORY", file=sys.stderr)
        sys.exit(1)
    run = Path(opt("--output-dir-prefix")) / opt("--output-dir-name")
    run.mkdir(parents=True)
    (run / "config.json").write_text(json.dumps({"quality": opt("--quality"), "cap_max": opt("--cap-max")}))
    lim = os.environ.get("FAKE_FORECAST_ABOVE")
    if lim and int(opt("--cap-max")) > int(lim):         # as on the real run: a scary forecast, then it finishes fine
        print("Warning: training may run out of GPU memory.")
        print("- Projected peak: 6.23 +/- 1.40 GiB   free for training: 3.62 GiB   chance of running out: 97%.", flush=True)
    if os.environ.get("FAKE_WARN"):
        print("Warning: training may run out of GPU memory.", flush=True)
        import time
        time.sleep(1.2)                     # long enough for the page to see it
    if os.environ.get("FAKE_SLOW"):
        print("step      1/10000 (  0%)  splats 46242  [elapsed 00:00:01 | ETA --:--]", flush=True)
        import time
        time.sleep(30)                      # a long training run the test will cancel
    for i in (1, 5, 10):
        print(f"step {i * 1000:>6}/10000 ({i * 10:>3}%)  splats 46242  [elapsed 00:00:01 | ETA 00:00:0{10 - i}]", flush=True)
elif a[:1] == ["mesh"]:
    need("--format", "--color", "--output", "--texture-size")
    if opt("--progress-dir"):
        import json as _j
        Path(opt("--progress-dir")).mkdir(parents=True, exist_ok=True)
        (Path(opt("--progress-dir")) / "memory.json").write_text(_j.dumps({"phase": "mesh", "work": 0.5}))
        import time
        time.sleep(1.6)                     # long enough for the app to read it
    fmt = opt("--format").split("+")[0]
    Path(opt("--output") + "." + fmt).write_bytes(b"glTF")
    print("mesh written")
else:
    print("fake spirula: unknown command", a, file=sys.stderr)
    sys.exit(2)
