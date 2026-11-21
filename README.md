# Insta Splat

Pick the folder with your 360 footage, choose a detail level, press **Go**. You get a textured 3D mesh.
It is a one-page front end for the unmodified [Spirula Studio](https://github.com/harry7557558/spirula-studio) (GPLv3).

## Using it

1. Run `python -m insta_splat` (or the packaged app). A page opens in your browser.
2. **Choose your footage folder.** Recordings are found and matched for you:
   - Insta360 `.insv`: the `_00_` and `_10_` lens files are paired, split chapters are joined, `LRV_` previews are ignored. A single `.insv` that holds both lenses also works.
   - GoPro MAX `.360` (chapters joined) and DJI `.osv`.
   - One model uses one camera type; other types are greyed out once you tick a recording.
3. **Move the detail slider (1-4).** The estimated time for *this* computer and *these* recordings is shown for every level before you start. Levels your graphics memory cannot handle are marked unavailable.
4. **Save as**: GLB by default (colour and fine detail preserved as a JPEG q95 texture inside one file). Also OBJ, glTF, PLY, STL, and lossless-texture GLB.
5. **Go.** Intermediate steps are hidden and cleaned up. Cancel works at any time.

Command line: `insta-splat run FOLDER -o OUT --detail 3`, `insta-splat estimate FOLDER`, `insta-splat hardware`, `--dry-run` to print the exact Spirula commands.

## Setup

Python 3.9+ (nothing else to install for the app). Put a Spirula Studio build in `vendor/` (or set `INSTA_SPLAT_SPIRULA`). Build with `-DSS_ENABLE_PATENTED=ON` if you want its built-in GPU video decoder. Optionally put `ffmpeg` in `vendor/` too.

## What it does about your hardware

- Finds GPUs (NVIDIA, AMD, Intel, Apple), graphics memory, Vulkan, RAM. Laptops with two GPUs get the faster one.
- Video decoding: Spirula's built-in decoder first; otherwise ffmpeg with the GPU decoder this machine really supports (checked by a test decode: cuda, d3d11va, vaapi, qsv, videotoolbox); otherwise CPU. If a decoder fails mid-run it falls back automatically.
- **Automatic memory saving** (`insta_splat/memory.py`): every level runs on small cards. If the model says a level will not fit your graphics memory, it steps down a fixed ladder: fewer detail points first, then smaller training pictures, then coarse-to-fine training. The page shows the trade (⚠) and the estimate already includes it. If Spirula still runs out of memory, the app retries one step lower by itself, up to 8 times. Only a card too small for any run is refused. The memory model is a first guess anchored on Spirula's '10M splats in 8 GB' claim and has not been measured on real cards.
- Time estimates start as a rough guess tuned to an RTX 3060 and are corrected after each run on your machine.

## Opening the result

GLB opens in Windows 3D Viewer / Paint 3D (where installed), Blender, and online viewers; Android's Scene Viewer and free viewer apps on iPhone and Android read it. **Gaps:** iOS's built-in Quick Look wants USDZ and Spirula cannot write it yet, and Microsoft is retiring some of its 3D apps. Adding a USDZ export is a worthwhile next step.

## Status: what is verified

Verified here: scanning/matching, estimates, hardware probing, the full pipeline and GUI flow **against a fake Spirula** that checks every flag we send, and every flag/format against Spirula's source (`tests/test_contract.py`).
**Not yet verified: a real run with a real Spirula binary and real footage.** Check these first, in `insta_splat/recipe.py`:
- whether your camera writes one file per lens (paired path, fixed frame spacing at an assumed 30 fps) or one two-track file (lockstep path);
- the `meshing` training preset with `--warp-to-pinhole 1` on fisheye footage (the GUI's 360 preset differs);
- the fisheye focal seed (0.27 x width) and the `dual-fisheye` rig on real frames;
- that `spirula mesh <run folder>` accepts the training output folder as written.
Use `--dry-run` and `--keep-work` to inspect.

See [docs/MAINTENANCE.md](docs/MAINTENANCE.md) for how upstream updates are handled.
