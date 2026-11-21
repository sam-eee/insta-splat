# Keeping up with Spirula

This repo is a wrapper, not a fork. Spirula changes daily; we only depend on its command line.

1. **One pinned version** in `SPIRULA_VERSION`. Upgrade deliberately.
2. **One file touches Spirula flags:** `insta_splat/recipe.py`. A renamed flag is a one-line fix.
3. **Weekly contract check** (`.github/workflows/upstream-check.yml`) clones Spirula master and runs `tests/test_contract.py`, which fails if a flag, quality level (`low/medium/high/ultra`) or mesh format we use disappears. A failure opens your attention before users are affected.
4. **Upgrade routine:** bump `SPIRULA_VERSION` -> run `SPIRULA_SRC=<checkout> python -m unittest discover -s tests` -> run the real-footage smoke test (a 10 s clip, `insta-splat run clip_folder -o out --detail 1`) -> ship.
5. **Missing option?** Send a small PR upstream rather than patching a private copy.
6. **Licence:** ship Spirula's licence and a source link with any bundle (GPLv3).
7. **Estimates** self-correct per machine (`calibration.json` in the config folder). Re-tune constants in `estimate.py` if many users report drift.
