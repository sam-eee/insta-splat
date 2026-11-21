"""Command line: `spherescan` opens the app; the subcommands do the same without a window."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from . import __version__, estimate as est, footage, gpudb, hardware
from .pipeline import Job, find_spirula
from .presets import DEFAULT_FORMAT, DETAILS, FORMATS


def _captures(args) -> list:
    caps = []
    for p in args.paths:
        pp = Path(p)
        caps += footage.scan_folder(str(pp)) if pp.is_dir() else []
    if args.only:
        caps = [c for c in caps if args.only.lower() in c.label.lower() or args.only in c.id]
    if not caps:
        sys.exit("No 360 recordings found.")
    kind = caps[0].kind
    return [c for c in caps if c.kind == kind]


def _list_library(a) -> int:
    if a.cpus:
        print(f"{'CPU':<34}{'score (5600X = 1.0)':>20}")
        for c in sorted((c for c in gpudb.cpus() if a.query.lower() in c["name"].lower()), key=lambda c: c["score"]):
            print(f"{c['name']:<34}{c['score']:>20.2f}")
        return 0
    print(f"{'Card':<42}{'TFLOPS':>8}{'GB/s':>7}{'VRAM':>6}{'speed':>7}   level 2 on a {a.minutes:g} min clip")
    for g in gpudb.search(a.query, 500):
        hw = hardware.virtual(g["name"])
        e = est.estimate(__import__("spherescan.presets", fromlist=["DETAILS"]).DETAILS[2], a.minutes * 60, 2, hw)
        vram = f"{g['vram_gb']:g}" if g.get("vram_gb") else "shared"
        flag = "~" if g.get("approx") else " "
        print(f"{g['name']:<42}{g['tflops']:>8.1f}{g['bandwidth_gbps']:>7.0f}{vram:>6}{gpudb.gpu_speed(g):>7.2f}{flag}  {est.human(e['total_s'])}")
    print("~ = approximate figures. Add or fix cards in gpus.json in the SphereScan config folder.")
    return 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    import os
    for stream in ("stdout", "stderr"):          # a windowed (no-console) exe has no streams
        if getattr(sys, stream) is None:
            setattr(sys, stream, open(os.devnull, "w"))
    if argv[:1] == ["--pick-folder"]:
        from .server import tk_pick
        tk_pick()
        return 0
    ap = argparse.ArgumentParser(prog="spherescan", description=__doc__)
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("gui", help="open the app (default)")
    sub.add_parser("hardware", help="show what this computer can do")
    sub.add_parser("install-launcher", help="Linux: add SphereScan to the applications menu")
    sub.add_parser("remove-launcher", help="Linux: remove that menu entry")
    g = sub.add_parser("gpus", help="list the offline card library (or CPUs with --cpus)")
    g.add_argument("query", nargs="?", default="", help="filter by name")
    g.add_argument("--cpus", action="store_true")
    g.add_argument("--minutes", type=float, default=5.0, help="clip length for the time column")
    for name in ("estimate", "run"):
        p = sub.add_parser(name)
        p.add_argument("paths", nargs="+" if name == "run" else "*", help="footage folder")
        if name == "estimate":
            p.add_argument("--gpu", help="estimate for this card from the library instead of this computer")
            p.add_argument("--cpu", help="CPU to assume with --gpu")
            p.add_argument("--vram", type=float, help="override the card's memory in GB")
            p.add_argument("--minutes", type=float, help="clip length instead of a folder")
        p.add_argument("--only", help="only recordings whose name contains this text")
        p.add_argument("--detail", type=int, choices=sorted(DETAILS), default=2)
        if name == "run":
            p.add_argument("-o", "--out", required=True)
            p.add_argument("--format", choices=sorted(FORMATS), default=DEFAULT_FORMAT)
            p.add_argument("--keep-work", action="store_true")
            p.add_argument("--dry-run", action="store_true", help="print the commands and stop")
    a = ap.parse_args(argv)

    if a.cmd in (None, "gui"):
        from .server import serve
        serve()
        return 0
    if a.cmd in ("install-launcher", "remove-launcher"):
        from . import window
        try:
            if a.cmd == "install-launcher":
                print("Added:", window.install_launcher(), "\nSphereScan now appears in your applications menu.")
            else:
                print("Removed the launcher." if window.remove_launcher() else "There was no launcher to remove.")
        except RuntimeError as e:
            sys.exit(str(e))
        return 0
    if a.cmd == "gpus":
        return _list_library(a)
    if a.cmd == "estimate" and a.gpu:
        try:
            hw = hardware.virtual(a.gpu, a.cpu, a.vram)
        except ValueError as e:
            sys.exit(str(e))
        print(f"Card: {hw.best_gpu().library_name}  ({hw.usable_vram_gb:g} GB)   CPU: {hw.cpu_name}")
    else:
        hw = hardware.detect()
    if a.cmd == "hardware":
        import json
        print(json.dumps(hw.to_dict(), indent=2))
        print("Spirula:", find_spirula() or "NOT FOUND")
        return 0
    if a.cmd == "estimate" and a.minutes:
        caps, dur, lenses = [], a.minutes * 60, 2
    else:
        if not a.paths:
            sys.exit("Give a footage folder, or --minutes with --gpu.")
        caps = _captures(a)
        dur = sum(c.duration_s for c in caps)
        lenses = 10 if caps[0].kind == "gopro" else 2
    if a.cmd == "estimate":
        print(f"Clip length: {dur / 60:.1f} min" + ("" if hw.best_gpu() is None or hw.best_gpu().spec_known else "   (card not in the library: wider error)"))
        for r in est.all_levels(dur, lenses, hw):
            print(f"Level {r['level']} {r['name']:<14} {r['text']:<24}{'' if r['available'] else ' (not available: ' + r['why'] + ')'}")
        return 0
    ok, why = est.available(DETAILS[a.detail], hw)
    if not ok:
        sys.exit(why)
    job = Job(captures=caps, detail_level=a.detail, format_key=a.format, out_dir=a.out,
              hw=hw, keep_work=a.keep_work, dry_run=a.dry_run)
    print(f"{len(caps)} recording(s), {dur / 60:.1f} min. Estimated: "
          f"{est.human_range(*[est.estimate(DETAILS[a.detail], dur, lenses, hw)[k] for k in ('low_s', 'high_s')])}")
    job.start()
    last = ""
    while job.state == "running":
        s = job.snapshot()
        line = f"{s['stage_label']:<32}{s['overall'] * 100:5.1f}%"
        if line != last:
            print(line, flush=True)
            last = line
        time.sleep(1)
    s = job.snapshot()
    for c in s["commands"]:
        print(" ".join(c))
    print(s["result"] or s["message"])
    return 0 if job.state == "done" else 1


if __name__ == "__main__":
    sys.exit(main())
