"""Tighten the transparent margin around the existing app art and rebuild ICO.

Run after replacing ``prtsbox/ui/assets/app.png`` with new transparent art.
The artwork is cropped without resampling; only the ICO size variants are
downsampled for Windows.  Pillow is a development dependency of this script,
not a runtime dependency of PRTSBox.
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image


ASSETS = Path(__file__).resolve().parent.parent / "prtsbox" / "ui" / "assets"
PNG = ASSETS / "app.png"
ICO = ASSETS / "app.ico"
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)
ART_FRACTION = 0.94


def main() -> None:
    art = Image.open(PNG).convert("RGBA")
    # Ignore nearly transparent export noise when finding the real silhouette.
    mask = art.getchannel("A").point(lambda alpha: 255 if alpha >= 8 else 0)
    bounds = mask.getbbox()
    if bounds is None:
        raise SystemExit("app.png has no visible artwork")

    left, top, right, bottom = bounds
    canvas_size = math.ceil(max(right - left, bottom - top) / ART_FRACTION)
    canvas = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    origin_x = round((left + right - canvas_size) / 2)
    origin_y = round((top + bottom - canvas_size) / 2)
    canvas.paste(art, (-origin_x, -origin_y))
    canvas.save(PNG)
    canvas.save(ICO, format="ICO", sizes=[(size, size) for size in ICON_SIZES])
    print(f"Saved {PNG.name} and {ICO.name}; artwork fills {ART_FRACTION:.0%} of canvas")


if __name__ == "__main__":
    main()
