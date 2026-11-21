"""Command line: `spherescan` opens the app; the subcommands do the same without a window."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from . import __version__, estimate as est, footage, hardware
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
    for name in ("estimate", "run"):
        p = sub.add_parser(name)
        p.add_argument("paths", nargs="+", help="footage folder(s)")
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
    hw = hardware.detect()
    if a.cmd == "hardware":
        import json
        print(json.dumps(hw.to_dict(), indent=2))
        print("Spirula:", find_spirula() or "NOT FOUND")
        return 0
    caps = _captures(a)
    dur = sum(c.duration_s for c in caps)
    lenses = 10 if caps[0].kind == "gopro" else 2
    if a.cmd == "estimate":
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
