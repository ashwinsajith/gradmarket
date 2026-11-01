#!/usr/bin/env python3
"""Dev utility: render the site's icon PNGs into src/gradmarket/web/static/.

Not part of the installed gradmarket package, and nothing at runtime calls
it — the PNGs it writes are committed. Re-run only to change the marks:

    pip install pillow
    python scripts/make_icons.py --font /path/to/InterTight[wght].ttf

The font is Inter Tight (SIL OFL, github.com/google/fonts, ofl/intertight);
it's only needed for the solid mark and is never shipped.

Two marks, one identity, size-specific: the dot-matrix G is unreadable
below about 24px, so both tab sizes are the solid letterform — 16px, and
32px, which retina screens request for the same 16px tab slot. The
dot-matrix mark starts at 180.

    icon-16.png   solid G
    icon-32.png   solid G       (retina tabs)
    icon-180.png  dot-matrix G  (apple-touch-icon)
    icon-512.png  dot-matrix G  (Open Graph image)

Both sit on #0b0c0f with a 6/32 corner radius. Everything is drawn on a 32
unit grid at SUPERSAMPLE times the output size, then downscaled, so edges
are antialiased at every size.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

STATIC_DIR = Path(__file__).resolve().parent.parent / "src" / "gradmarket" / "web" / "static"

UNITS = 32
SUPERSAMPLE = 8
TILE = "#0b0c0f"
TILE_RADIUS = 6
LIT = "#f2f3f5"
UNLIT = "#2a2e34"
ACCENT = "#d99a2b"

# Dot-matrix: 5x7 cells. "#" lit, "." unlit — the unlit grid is part of the
# mark. "*" is lit in the accent: the free end of the G's crossbar.
DOT_G = [
    ".###.",
    "#...#",
    "#....",
    "#.*##",
    "#...#",
    "#...#",
    ".###.",
]
CELL = 3.1
PITCH = 4.0
CELL_RADIUS = 0.9

# Solid mark.
SOLID_WEIGHT = 600
SOLID_FONT_UNITS = 23  # font size on the 32 grid
# Bounding-box centring leaves a G looking low and left (its open right side
# and round bowl carry less weight than the box suggests). Nudge up/right.
OPTICAL_NUDGE = (0.6, -0.8)  # (right, up) in 32-grid units


def _canvas(size: int) -> tuple[Image.Image, ImageDraw.ImageDraw, float]:
    px = size * SUPERSAMPLE
    image = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    unit = px / UNITS
    draw.rounded_rectangle([0, 0, px - 1, px - 1], radius=TILE_RADIUS * unit, fill=TILE)
    return image, draw, unit


def _finish(image: Image.Image, size: int) -> Image.Image:
    return image.resize((size, size), Image.Resampling.LANCZOS)


def dot_matrix_mark(size: int) -> Image.Image:
    image, draw, unit = _canvas(size)
    width = (len(DOT_G[0]) - 1) * PITCH + CELL
    height = (len(DOT_G) - 1) * PITCH + CELL
    x0 = (UNITS - width) / 2
    y0 = (UNITS - height) / 2
    colours = {"#": LIT, ".": UNLIT, "*": ACCENT}
    for row, line in enumerate(DOT_G):
        for col, cell in enumerate(line):
            x = (x0 + col * PITCH) * unit
            y = (y0 + row * PITCH) * unit
            draw.rounded_rectangle(
                [x, y, x + CELL * unit, y + CELL * unit], radius=CELL_RADIUS * unit, fill=colours[cell]
            )
    return _finish(image, size)


def solid_mark(size: int, font_path: Path) -> Image.Image:
    image, draw, unit = _canvas(size)
    font = ImageFont.truetype(str(font_path), round(SOLID_FONT_UNITS * unit))
    font.set_variation_by_axes([SOLID_WEIGHT])

    g_box = font.getbbox("G")
    g_advance = font.getlength("G")
    dot_box = font.getbbox(".")
    # The full stop sits where the font sets it after a G: lower right.
    left = min(g_box[0], g_advance + dot_box[0])
    right = max(g_box[2], g_advance + dot_box[2])
    top, bottom = g_box[1], max(g_box[3], dot_box[3])

    nudge_x, nudge_up = OPTICAL_NUDGE
    origin_x = (image.width - (right - left)) / 2 - left + nudge_x * unit
    origin_y = (image.height - (bottom - top)) / 2 - top - nudge_up * unit

    draw.text((origin_x, origin_y), "G", font=font, fill=LIT)
    draw.text((origin_x + g_advance, origin_y), ".", font=font, fill=ACCENT)
    return _finish(image, size)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--font", type=Path, required=True, help="Inter Tight variable TTF (for the solid mark)")
    parser.add_argument("--out", type=Path, default=STATIC_DIR)
    args = parser.parse_args()

    outputs = {
        "icon-16.png": solid_mark(16, args.font),
        "icon-32.png": solid_mark(32, args.font),
        "icon-180.png": dot_matrix_mark(180),
        "icon-512.png": dot_matrix_mark(512),
    }
    for name, image in outputs.items():
        image.save(args.out / name, optimize=True)
        print(f"wrote {args.out / name}")


if __name__ == "__main__":
    main()
