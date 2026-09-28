"""Render the UI at several window sizes for visual review.

The geometry checker proves no two controls overlap, but it cannot tell whether
the result still *looks* right - spacing, wrapping and scrollbar placement are
judgement calls.  These PNGs are the ground truth for that.

    .venv\\Scripts\\python.exe scripts\\preview_ui.py
    $env:QT_SCALE_FACTOR="1.25"; .venv\\Scripts\\python.exe scripts\\preview_ui.py
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import (
    QApplication,
    QTabWidget,
)

from prtsbox.config import ConfigStore
from prtsbox.llama import LlamaManager
from prtsbox.ui.main_window import MainWindow, bootstrap
from prtsbox.ui.settings_dialog import SettingsDialog

OUT = Path(__file__).resolve().parent

MAIN_SIZES = ((580, 720), (800, 700), (700, 600), (460, 520))
SETTINGS_SIZES = ((600, 620), (700, 560), (430, 440))


def settle(widget, passes: int = 10) -> None:
    layout = widget.layout()
    if layout is not None:
        layout.activate()
    for _ in range(passes):
        QApplication.processEvents()
    if layout is not None:
        layout.activate()


def shoot(widget, name: str) -> None:
    settle(widget)
    path = OUT / name
    widget.grab().save(str(path))
    print(f"已保存 {path}（{widget.width()}×{widget.height()}）")


def main() -> int:
    logging.basicConfig(level=logging.ERROR)
    bootstrap()
    app = QApplication(sys.argv)
    print(f"屏幕缩放 {app.primaryScreen().devicePixelRatio()}")

    config = ConfigStore()
    config.load()
    manager = LlamaManager()

    window = MainWindow(config, manager)
    window.load_from_config()
    window.show()
    window.refresh_windows()

    for width, height in MAIN_SIZES:
        window.resize(width, height)
        settle(window)
        shoot(window, f"preview_main_{width}x{height}.png")

    # An open combo popup: the list must not be clipped by the window edge.
    window.resize(580, 720)
    settle(window)
    combo = window._window_combo
    combo.showPopup()
    settle(window, 4)
    shoot(window, "preview_main_combo_open.png")
    combo.hidePopup()
    settle(window, 4)
    window.close()

    # Highlight state, to confirm the visual design is unchanged at full size.
    config.set("theme", "light")
    light = MainWindow(config, manager)
    light.load_from_config()
    light.show()
    light.refresh_windows()
    shoot(light, "preview_main_light.png")
    light._set_status("翻译运行中　·　12 条译文　·　截图 13 ms　OCR 158 ms　翻译 210 ms")
    shoot(light, "preview_main_light_status.png")
    light.close()
    config.set("theme", "dark")

    dialog = SettingsDialog(config, manager, window)
    dialog.show()
    tabs = dialog.findChild(QTabWidget)
    assert tabs is not None

    for index, name in ((0, "general"), (1, "local"), (2, "openai")):
        tabs.setCurrentIndex(index)
        settle(dialog)
        for width, height in SETTINGS_SIZES:
            dialog.resize(width, height)
            settle(dialog)
            shoot(dialog, f"preview_settings_{name}_{width}x{height}.png")

    # A populated download card, to check the progress state at a narrow width.
    tabs.setCurrentIndex(1)
    dialog.resize(430, 440)
    dialog._cards["hy-mt2-7b"].set_downloading(0.42, "下载中 42%　7.3 MB/s　剩余 96s")
    settle(dialog)
    shoot(dialog, "preview_settings_downloading_narrow.png")

    dialog.reject()
    return 0


if __name__ == "__main__":
    sys.exit(main())
