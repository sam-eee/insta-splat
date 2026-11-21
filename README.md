# SphereScan

Pick the folder with your 360 footage, choose a detail level, press **Go**. You get a textured 3D mesh.
It is a one-page front end for the unmodified [Spirula Studio](https://github.com/harry7557558/spirula-studio) (GPLv3).

Licensed under the GNU GPLv3 (see `LICENSE`), the same licence as Spirula Studio.

*SphereScan is independent. It is not affiliated with or endorsed by Spirula Studio, Insta360, GoPro or DJI; those names are trademarks of their owners, used only to say which files it reads.*

## Source code and licences

Everything here is free software. Each download bundles an unmodified Spirula Studio release (currently `v2026.11.12`, see `SPIRULA_VERSION`) and is distributed under GPLv3. The source for both is available:
- SphereScan: this repository, at the release's tag.
- Spirula Studio: [the same release](https://github.com/harry7557558/spirula-studio/releases/tag/v2026.11.12), and a source archive is attached to every SphereScan release.
- Licences and the written source offer: `THIRD_PARTY.md`, the **Licences and source code** link at the bottom of the app, and `vendor/licenses/` inside the app.

## Using it

1. Download **SphereScan** for your system from the Releases page and double-click it (Windows: `SphereScan.exe`; macOS: `SphereScan.app`; Linux: `SphereScan`). Nothing else to install: Spirula Studio is inside. A page opens in your browser, and the app quits by itself when you close the page (a run in progress is never interrupted).
   Unsigned apps trigger a warning the first time: Windows SmartScreen "More info > Run anyway", macOS right-click > Open.
2. **Choose your footage folder.** Recordings are found and matched for you:
   - Insta360 `.insv`: the `_00_` and `_10_` lens files are paired, split chapters are joined, `LRV_` previews are ignored. A single `.insv` that holds both lenses also works.
   - GoPro MAX `.360` (chapters joined) and DJI `.osv`.
   - One model uses one camera type; other types are greyed out once you tick a recording.
3. **Move the detail slider (1-4).** The estimated time for *this* computer and *these* recordings is shown for every level before you start. Levels your graphics memory cannot handle are marked unavailable.
4. **Save as**: GLB by default (colour and fine detail preserved as a JPEG q95 texture inside one file). Also OBJ, glTF, PLY, STL, and lossless-texture GLB.
5. **Go.** Intermediate steps are hidden and cleaned up. Cancel works at any time.

Command line: `spherescan run FOLDER -o OUT --detail 3`, `spherescan estimate FOLDER`, `spherescan hardware`, `--dry-run` to print the exact Spirula commands.

## Building the single file yourself

`python build.py` makes the one-file app with PyInstaller (`pip install pyinstaller` first). Put Spirula under `vendor/` beforehand to bundle it; the GitHub workflow `release.yml` does that for Windows, macOS and Linux (it fetches the release named in `SPIRULA_VERSION`) and attaches the files to a release when you push a `v*` tag. PyInstaller cannot cross-compile, so each system is built on its own runner.
Running from source: `python -m spherescan` with a Spirula build in `vendor/` or `SPHERESCAN_SPIRULA` set.

## What it does about your hardware

- Finds GPUs (NVIDIA, AMD, Intel, Apple), graphics memory, Vulkan, RAM. Laptops with two GPUs get the faster one.
- Video decoding: Spirula's built-in decoder first; otherwise ffmpeg with the GPU decoder this machine really supports (checked by a test decode: cuda, d3d11va, vaapi, qsv, videotoolbox); otherwise CPU. If a decoder fails mid-run it falls back automatically.
- **Automatic memory saving** (`spherescan/memory.py`): every level runs on small cards. If the model says a level will not fit your graphics memory, it steps down a fixed ladder: fewer detail points first, then smaller training pictures, then coarse-to-fine training. The estimate already includes it. Spirula forecasts its own memory use at the start of training; if it expects to run out, the app stops that attempt within seconds and restarts one step lower (up to 8 times), and remembers the step that worked for your graphics card. An actual out-of-memory failure is retried the same way. Only a card too small for any run is refused. The memory model is a first guess anchored on Spirula's '10M splats in 8 GB' claim and has not been measured on real cards.
- Time estimates start as a rough guess tuned to an RTX 3060 and are corrected after each run on your machine.

## Opening the result

GLB opens in Windows 3D Viewer / Paint 3D (where installed), Blender, and online viewers; Android's Scene Viewer and free viewer apps on iPhone and Android read it. **Gaps:** iOS's built-in Quick Look wants USDZ and Spirula cannot write it yet, and Microsoft is retiring some of its 3D apps. Adding a USDZ export is a worthwhile next step.

## Status: what is verified

Verified here: scanning/matching, estimates, hardware probing, the full pipeline and GUI flow **against a fake Spirula** that checks every flag we send, and every flag/format against Spirula's source (`tests/test_contract.py`).
**Not yet verified: a real run with a real Spirula binary and real footage, and the PyInstaller builds themselves (the workflow has not been run).** The pinned release v2026.11.12 lacks `--max-resolution`, so memory saving uses `--train-resolution-divisor`. Also check that the release's Windows exe prints to a console pipe when called from the app. Check these first, in `spherescan/recipe.py`:
- whether your camera writes one file per lens (paired path, fixed frame spacing at an assumed 30 fps) or one two-track file (lockstep path);
- the `meshing` training preset with `--warp-to-pinhole 1` on fisheye footage (the GUI's 360 preset differs);
- the fisheye focal seed (0.27 x width) and the `dual-fisheye` rig on real frames;
- that `spirula mesh <run folder>` accepts the training output folder as written.
Use `--dry-run` and `--keep-work` to inspect.

See [docs/MAINTENANCE.md](docs/MAINTENANCE.md) for how upstream updates are handled.
