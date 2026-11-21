# Third-party software and source code

SphereScan is free software under the GNU GPL version 3 (`LICENSE`).
Source: https://github.com/sam-eee/spherescan (the release tag matching your download).

## Spirula Studio (bundled)

Packaged builds contain an **unmodified** copy of the official Spirula Studio release
named in `SPIRULA_VERSION`, run as a separate program. Spirula Studio is by harry7557558,
licensed under GPLv3; its licence and its third-party licences are in `vendor/licenses/`.

- Project: https://github.com/harry7557558/spirula-studio
- Official release we bundle: https://github.com/harry7557558/spirula-studio/releases/tag/v2026.11.12
- Complete corresponding source for that release (also attached to every SphereScan
  release as `spirula-studio-<version>-source.tar.gz`):
  https://github.com/harry7557558/spirula-studio/archive/refs/tags/v2026.11.12.tar.gz

**Written offer.** For at least three years after you receive a build, you may obtain the
complete source of the bundled Spirula Studio and of SphereScan from the links above or, if
they are unavailable, by opening an issue at https://github.com/sam-eee/spherescan/issues.

SphereScan is an independent front end. It is not made, affiliated with or endorsed by the Spirula Studio author, Insta360 (Arashi Vision), GoPro or DJI. Those names are trademarks of their owners and are used only to say which files the app can read.

## libglvnd (Linux builds only)

Linux builds include `libOpenGL.so.0` from Ubuntu's `libopengl0` package (libglvnd, MIT-style
licence; text in `vendor/licenses/libglvnd/`). Spirula's binary needs it, and many systems lack
it. It is used only as a fallback after the system's own copy. Source: https://gitlab.freedesktop.org/glvnd/libglvnd

## Things to know

- **Video codecs.** Spirula Studio's own notes say its built-in H.264/H.265 decoding carries
  third-party patent exposure, and that people who use it are responsible for complying with
  local patent law. This applies to the bundled build.
- **AI models** (for Spirula's masking feature, which SphereScan does not use) are not
  bundled; Spirula downloads them on request under their own licences.
- SphereScan is built with Python and PyInstaller (GPL with the bootloader exception).
