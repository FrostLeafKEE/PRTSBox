"""Verify the bottom-region option and overlay stability together.

The region option has to do two things at once: recognise only the bottom slice
*and* still place every translation over the right line, because the overlay
maps captured pixels onto the window.  Getting the crop right but the offset
wrong would silently misplace every chip.

The stability half of the check confirms that a static screen stops repainting,
which is the flicker the option and the cache together are meant to remove.

    .venv\\Scripts\\python.exe scripts\\e2e_region.py
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QPainter
from PySide6.QtWidgets import QApplication, QWidget

from prtsbox.llama import LlamaManager
from prtsbox.models import LayoutMode, WindowInfo
from prtsbox.overlay import TranslationOverlay
from prtsbox.pipeline import PipelineWorker
from prtsbox.win32 import enable_dpi_awareness, get_window_info

logging.basicConfig(level=logging.WARNING, format="%(levelname)-7s %(name)s: %(message)s")

# A tall window: dialogue at the bottom, artwork lettering at the top.
TOP_LINES = ["GUILD OF HEROES", "EST. 1892"]
BOTTOM_LINES = [
    "Loading...",
    "Are you sure you want to delete this file?",
    "The connection timed out. Retrying in 5 seconds.",
]


class TextWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("PRTSBox Region Test")
        self.resize(700, 900)
        self.setStyleSheet("background: #1b1f2a;")
        self.move(150, 80)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setPen(Qt.GlobalColor.white)
        painter.setFont(QFont("Segoe UI", 26, QFont.Weight.Bold))
        for index, text in enumerate(TOP_LINES):
            painter.drawText(40, 90 + index * 60, text)
        painter.setFont(QFont("Segoe UI", 13))
        # Dialogue sits in the bottom third.
        for index, text in enumerate(BOTTOM_LINES):
            painter.drawText(40, self.height() - 220 + index * 40, text)
        painter.end()


def translate_with(worker: PipelineWorker, window: WindowInfo, config: dict):
    results: list = []
    failures: list = []
    worker.frame.connect(results.append)
    worker.failed.connect(failures.append)
    worker.process(window, config)
    worker.frame.disconnect(results.append)
    worker.failed.disconnect(failures.append)
    if failures:
        raise RuntimeError(failures[0])
    return results[0] if results else None


def main() -> int:
    enable_dpi_awareness()
    app = QApplication(sys.argv)  # noqa: F841 - must outlive the widgets

    target = TextWindow()
    target.show()
    for _ in range(12):
        QApplication.processEvents()
    window = get_window_info(int(target.winId()))
    if window is None:
        print("无法获取测试窗口")
        return 1
    print(f"测试窗口 {window.width}×{window.height}\n")

    worker = PipelineWorker(LlamaManager())
    worker._create_ocr()
    assert worker._ocr is not None
    print(f"OCR 后端：OpenVINO={worker._ocr.using_openvino}")
    print(f"窗口顶部文字：{TOP_LINES}")
    print(f"窗口底部文字：{BOTTOM_LINES}\n")

    base = {"engine": "local", "target_language": "zh-CN", "skip_chinese": False,
            "model_id": "", "credentials": {}}

    def run(label: str, region: dict) -> None:
        config = {**base, **region}
        started = time.perf_counter()
        result = translate_with(worker, window, config)
        elapsed = (time.perf_counter() - started) * 1000.0
        if result is None:
            print(f"{label}: 无回报")
            return
        texts = [item.text for item in result.items]
        bottom_hits = sum(1 for line in BOTTOM_LINES
                          if any(line[:14] in text for text in texts))
        top_hits = sum(1 for line in TOP_LINES
                       if any(line[:8] in text for text in texts))
        print(f"{label}")
        print(f"  OCR {result.timing.ocr_ms:.0f} ms  整体 {elapsed:.0f} ms  识别 {len(texts)} 行")
        print(f"  命中底部对话 {bottom_hits}/{len(BOTTOM_LINES)}　命中顶部美术字 {top_hits}/{len(TOP_LINES)}")
        for text in texts[:6]:
            print(f"    {text!r}")
        print()

    run("① 全窗口识别", {"region_bottom_only": False})
    run("② 仅下方 30%", {"region_bottom_only": True, "region_bottom_percent": 30})
    run("③ 仅下方 50%", {"region_bottom_only": True, "region_bottom_percent": 50})

    # -- overlay stability -------------------------------------------------
    print("=" * 52)
    print("译文层稳定性（同一画面重复送入 30 次）")
    overlay = TranslationOverlay()
    repaints = {"count": 0}
    original_update = overlay.update
    overlay.update = lambda: (repaints.__setitem__("count", repaints["count"] + 1),
                              original_update())[1]  # type: ignore[method-assign]

    config = {**base, "region_bottom_only": True, "region_bottom_percent": 30}
    first = translate_with(worker, window, config)
    assert first is not None
    overlay.attach(window, hide_from_capture=False)
    overlay.update_content(window, first.items, LayoutMode.BELOW, first.frame_size, font_size=14)
    baseline = repaints["count"]

    # Identical frames.
    for _ in range(30):
        overlay.update_content(window, first.items, LayoutMode.BELOW, first.frame_size, font_size=14)
    identical_repaints = repaints["count"] - baseline

    # Frames whose boxes wobble by a pixel, as real OCR produces.
    from dataclasses import replace

    wobbled = [
        replace(item, box=tuple((x + 1.0, y + (1.0 if i % 2 else -1.0)) for i, (x, y) in enumerate(item.box)))
        for item in first.items
    ]
    before_wobble = repaints["count"]
    for _ in range(30):
        overlay.update_content(window, wobbled, LayoutMode.BELOW, first.frame_size, font_size=14)
    wobble_repaints = repaints["count"] - before_wobble

    print(f"完全相同内容重绘次数：{identical_repaints}/30")
    print(f"1px 抖动内容重绘次数：{wobble_repaints}/30")

    overlay.hide_overlay()
    target.close()

    ok = identical_repaints == 0 and wobble_repaints == 0
    print()
    print("通过：静态画面不再重绘，抖动已消除" if ok else "失败：仍在重复重绘")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
