#!/usr/bin/env python3
"""Dev utility: render the site's icons into src/gradmarket/web/static/.

Not part of the installed gradmarket package, and nothing at runtime calls
it — the files it writes are committed. Re-run only to change the mark:

    pip install pillow
    python scripts/make_icons.py

One mark at every size: a 5x7 dot-matrix capital G. It's drawn entirely as
rounded rectangles, so no font is involved.

    icon-16.png   tab
    icon-32.png   retina tab (requested for the same 16px slot)
    icon-180.png  apple-touch-icon
    icon-512.png  Open Graph image
    mark.svg      the nav's logo mark — vector, so crisp at any zoom

The PNGs and mark.svg are all drawn from the same DOT_G grid and geometry,
so they're the identical mark, not redrawings. Everything sits on #0b0c0f
with a 6/32 corner radius, on a 32-unit grid. PNGs are drawn at SUPERSAMPLE
times their output size, then downscaled, so edges are antialiased.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw

STATIC_DIR = Path(__file__).resolve().parent.parent / "src" / "gradmarket" / "web" / "static"

UNITS = 32
SUPERSAMPLE = 8
TILE = "#0b0c0f"
TILE_RADIUS = 6
LIT = "#f2f3f5"
UNLIT = "#2a2e34"
ACCENT = "#d99a2b"

# 5x7 cells. "#" lit, "." unlit — the unlit grid is part of the mark. "*" is
# lit in the accent: the cell at the end of the G's crossbar.
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

PNG_SIZES = (16, 32, 180, 512)


def dot_matrix_cells() -> list[tuple[float, float, str]]:
    """(x, y, colour) for each cell, on the 32-unit grid, centred."""
    width = (len(DOT_G[0]) - 1) * PITCH + CELL
    height = (len(DOT_G) - 1) * PITCH + CELL
    x0 = (UNITS - width) / 2
    y0 = (UNITS - height) / 2
    colours = {"#": LIT, ".": UNLIT, "*": ACCENT}
    return [
        (x0 + col * PITCH, y0 + row * PITCH, colours[cell])
        for row, line in enumerate(DOT_G)
        for col, cell in enumerate(line)
    ]


def mark_png(size: int) -> Image.Image:
    px = size * SUPERSAMPLE
    unit = px / UNITS
    image = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle([0, 0, px - 1, px - 1], radius=TILE_RADIUS * unit, fill=TILE)
    for x, y, colour in dot_matrix_cells():
        draw.rounded_rectangle(
            [x * unit, y * unit, (x + CELL) * unit, (y + CELL) * unit], radius=CELL_RADIUS * unit, fill=colour
        )
    return image.resize((size, size), Image.Resampling.LANCZOS)


def mark_svg() -> str:
    def n(value: float) -> str:
        return f"{value:.2f}".rstrip("0").rstrip(".")

    cells = "".join(
        f'<rect x="{n(x)}" y="{n(y)}" width="{n(CELL)}" height="{n(CELL)}" rx="{n(CELL_RADIUS)}" fill="{colour}"/>'
        for x, y, colour in dot_matrix_cells()
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {UNITS} {UNITS}">'
        f'<rect width="{UNITS}" height="{UNITS}" rx="{TILE_RADIUS}" fill="{TILE}"/>{cells}</svg>\n'
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=STATIC_DIR)
    args = parser.parse_args()

    for size in PNG_SIZES:
        path = args.out / f"icon-{size}.png"
        mark_png(size).save(path, optimize=True)
        print(f"wrote {path}")
    path = args.out / "mark.svg"
    path.write_text(mark_svg())
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
