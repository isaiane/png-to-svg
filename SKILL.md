---
name: png-to-svg
description: Convert raster images into editable path-only SVGs with a local browser UI or command-line workflow. Use when the user wants to vectorize PNG, JPG, WebP, or BMP images, tune color counts, remove backgrounds, simplify paths, or export editable SVG artwork.
metadata:
  short-description: Local PNG/JPG/WebP/BMP to editable SVG converter
---

# PNG to SVG

Use this skill when the user wants local, private raster-to-SVG vectorization.

## Capabilities

- Convert PNG, JPG, WebP, or BMP files to path-only SVG.
- Use color mode with 2 to 64 colors.
- Use black-and-white mode with threshold, inversion, and foreground color.
- Preserve, auto-remove, or explicitly remove a background color.
- Tune blur, simplification, minimum area, output precision, and maximum trace size.
- Run a browser UI for upload, preview, palette inspection, and download.

## Setup

From this skill directory:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

On Windows, use `.venv\Scripts\python.exe` instead of `.venv/bin/python`.

## Browser UI

Start the local app:

```bash
.venv/bin/python scripts/web_app.py --open
```

If port `8765` is busy, pass another port:

```bash
.venv/bin/python scripts/web_app.py --port 8766
```

## Command Line

Basic color vectorization:

```bash
.venv/bin/python scripts/png_to_svg.py input.png output.svg --colors 8 --simplify 1 --min-area 4
```

Auto-remove the inferred background:

```bash
.venv/bin/python scripts/png_to_svg.py input.png output.svg --colors 8 --background auto --bg-tolerance 24
```

Black-and-white vectorization:

```bash
.venv/bin/python scripts/png_to_svg.py logo.png logo.svg --mode bw --threshold 150 --simplify 0.6
```

Fixed palette:

```bash
.venv/bin/python scripts/png_to_svg.py input.png output.svg --palette '#102A43,#2CB1BC,#F0B429,#FFFFFF'
```

## Notes

- The output SVG contains paths only and does not embed the source raster image.
- The browser UI sends files only to the local server.
- For large or noisy images, reduce `--max-dimension`, increase `--min-area`, or increase `--simplify` to reduce SVG complexity.
