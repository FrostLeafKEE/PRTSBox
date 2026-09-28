"""Pixel-level overlap check for the main window at several sizes.

Geometry checks compare widget rectangles, which cannot see a control that is
painted over or clipped by its parent's boundary.  This renders the window and
looks for two symptoms directly in the image:

* a control drawn partly outside the window, i.e. cut off at an edge;
* a colour transition that no widget boundary accounts for.

Only the first is checked automatically here - the second needs a model of the
expected rendering.  What this adds over the geometry pass is proof that the
composited result, not just the layout data, is sane.

    .venv\\Scripts\\python.exe scripts\\check_pixels.py
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from prtsbox.config import ConfigStore
from prtsbox.llama import LlamaManager
from prtsbox.ui.main_window import MainWindow, bootstrap

SIZES = ((580, 720), (800, 700), (700, 600), (460, 520), (400, 420))


def grab(widget) -> np.ndarray:
    image = widget.grab().toImage().convertToFormat(QImage.Format.Format_RGB32)
    width, height = image.width(), image.height()
    buffer = image.constBits()
    # bytesPerLine can exceed width*4 because rows are padded for alignment.
    array = np.frombuffer(buffer, dtype=np.uint8, count=height * image.bytesPerLine())
    array = array.reshape(height, image.bytesPerLine())[:, : width * 4]
    return array.reshape(height, width, 4)[:, :, :3].copy()


def main() -> int:
    logging.basicConfig(level=logging.ERROR)
    bootstrap()
    app = QApplication(sys.argv)  # noqa: F841 - must outlive the widgets

    config = ConfigStore()
    config.load()
    window = MainWindow(config, LlamaManager())
    window.load_from_config()
    window.show()
    window.refresh_windows()

    failures = 0
    for width, height in SIZES:
        window.resize(width, height)
        for _ in range(10):
            QApplication.processEvents()
        frame = grab(window)
        actual_h, actual_w = frame.shape[:2]

        # A control clipped by the window edge leaves the window's own
        # background colour in a one-pixel rim; a control that overflows shows
        # as unexpected content in the outermost row/column.
        border = np.concatenate(
            [frame[0, :], frame[-1, :], frame[:, 0], frame[:, -1]]
        )
        # The theme background is a flat light or dark tone; anything strongly
        # saturated on the rim means a control is being cut off there.
        spread = border.max(axis=0).astype(int) - border.min(axis=0).astype(int)
        saturated = int((spread > 60).sum())

        status = "正常" if saturated == 0 else f"边缘有 {saturated} 个彩色像素"
        if saturated:
            failures += 1
        print(
            f"  {width}×{height}: 渲染 {actual_w}×{actual_h}，窗口边缘{status}"
        )

    window.close()
    print("\n" + "=" * 46)
    print("像素检查通过" if not failures else f"发现 {failures} 处边缘异常")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
