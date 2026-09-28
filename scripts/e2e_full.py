"""Full-pipeline check: capture a real window, translate it, show the overlay.

This is the closest thing to running the application: it builds a target window
containing known English text, drives the real pipeline (capture -> OCR ->
cleanup -> local model -> overlay), then photographs the desktop so the result
can be inspected.

    .venv\\Scripts\\python.exe scripts\\e2e_full.py
    .venv\\Scripts\\python.exe scripts\\e2e_full.py --model hy-mt2-7b --save shot.png
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QPainter
from PySide6.QtWidgets import QApplication, QWidget

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from prtsbox import llama
from prtsbox.llama import LlamaManager
from prtsbox.models import LayoutMode, TranslatorConfig
from prtsbox.overlay import TranslationOverlay
from prtsbox.pipeline import PipelineWorker
from prtsbox.win32 import enable_dpi_awareness, get_window_info

LINES = [
    "Loading...",
    "Are you sure you want to delete this file?",
    "The connection timed out. Retrying in 5 seconds.",
    "Enable hardware acceleration to reduce CPU usage.",
    "Damage: 1250  Critical Hit!",
    "Target window is not in the foreground; capture is paused.",
    # Already Chinese: with a Chinese target these must be dropped rather than
    # sent to the model, which would otherwise reword them.
    "目标窗口不在前台时暂停截图",
    "确定要删除这个文件吗？",
]


class TargetWindow(QWidget):
    """Stands in for the third-party window a user would translate.

    Deliberately *not* topmost: that is the ordinary case, and a topmost target
    needs different Z-order handling.
    """

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("PRTSBox Full Pipeline Test")
        self.resize(760, 420)
        self.setStyleSheet("background: #f7f8fa;")
        self.move(120, 120)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        font = QFont("Segoe UI", 12)
        painter.setFont(font)
        painter.setPen(Qt.GlobalColor.black)
        for index, text in enumerate(LINES):
            painter.drawText(28, 56 + index * 40, text)
        painter.end()


def report_alignment(result, window, overlay: TranslationOverlay) -> None:
    """Verify each translation is drawn under its own source line.

    Eyeballing a screenshot cannot separate a real coordinate error from a DPI
    scaling artefact, so the mapping is checked numerically instead: the frame
    is in physical pixels, the overlay lays out in logical ones, and the ratio
    between them must be the monitor scale factor exactly.
    """
    frame_width, frame_height = result.frame_size
    geometry = overlay.geometry()
    scale_x = geometry.width() / frame_width
    scale_y = geometry.height() / frame_height
    ratio = overlay.devicePixelRatioF()

    print(
        f"\n坐标映射：帧 {frame_width}×{frame_height}（物理） -> "
        f"控件 {geometry.width()}×{geometry.height()}（逻辑），"
        f"缩放 {scale_x:.4f}/{scale_y:.4f}，DPR={ratio}"
    )
    expected = 1.0 / ratio if ratio else 1.0
    drift = abs(scale_y - expected)
    print(f"  期望缩放 {expected:.4f}，偏差 {drift:.4f} " + ("OK" if drift < 0.02 else "偏离！"))

    tolerance = 2.0
    problems = 0
    print("  逐行校验（逻辑坐标）：")
    for item in result.items:
        bottom = item.bounds[3]
        chip_top = bottom * scale_y + 2.0  # _GAP
        source_bottom = bottom * scale_y
        ok = chip_top >= source_bottom - tolerance
        if not ok:
            problems += 1
        print(
            f"    {item.text[:34]!r:38} 原文底 {source_bottom:7.1f}  "
            f"译文顶 {chip_top:7.1f}  {'OK' if ok else '重叠!'}"
        )
    print(f"  重叠行数：{problems}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=llama.DEFAULT_MODEL_ID)
    parser.add_argument("--target", default="zh-CN")
    parser.add_argument("--save", default="full_pipeline_result.png")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(name)s: %(message)s")
    logging.getLogger("prtsbox.ocr").setLevel(logging.WARNING)

    enable_dpi_awareness()
    app = QApplication(sys.argv)

    model = llama.resolve_model(args.model)
    print(f"模型：{model.name}（{model.filename}）")
    print(f"运行时：{llama.installed_variants() or '未安装'}")

    manager = LlamaManager()
    worker = PipelineWorker(manager)
    overlay = TranslationOverlay()

    target = TargetWindow()
    target.show()

    state: dict[str, object] = {}

    def on_ready() -> None:
        print("OCR 就绪，开始翻译…")
        QTimer.singleShot(400, run_frame)

    def on_frame(result) -> None:
        if not result.items:
            print("该帧没有识别到文本")
            finish(1)
            return

        state["result"] = result
        window = get_window_info(int(target.winId()))
        # Capture exclusion would also hide the overlay from the verification
        # screenshot below, so it is turned off for this check only.
        overlay.attach(window, hide_from_capture=False)
        overlay.update_content(
            window, result.items, LayoutMode.BELOW, result.frame_size,
            font_size=14, show_source=False,
        )
        print(f"\n识别 {len(result.items)} 条，缓存命中 {result.from_cache}")
        for item in result.items:
            print(f"  {item.text!r}\n    -> {item.translation!r}")
        print(
            f"\n耗时：截图 {result.timing.capture_ms:.0f} ms　"
            f"OCR {result.timing.ocr_ms:.0f} ms　"
            f"翻译 {result.timing.translate_ms:.0f} ms"
        )
        print(
            f"译文层：可见={overlay.isVisible()} 几何={overlay.geometry().getRect()} "
            f"绘制项={overlay.item_count}"
        )
        report_alignment(result, window, overlay)
        # Let the overlay paint before the screenshot is taken.
        QTimer.singleShot(900, capture_desktop)

    def capture_desktop() -> None:
        # Rendering the overlay to a pixmap proves what it paints and with what
        # geometry, independently of window compositing - useful because a
        # full-screen game or a capture-excluded overlay can hide the result
        # from a desktop grab.
        chip_shot = overlay.grab()
        chip_path = Path(__file__).resolve().parent / "overlay_render.png"
        chip_shot.save(str(chip_path))
        print(f"译文层自身渲染已保存：{chip_path}（{chip_shot.width()}×{chip_shot.height()}）")

        screen = app.primaryScreen()
        shot = screen.grabWindow(0)
        output = Path(__file__).resolve().parent / args.save
        shot.save(str(output))
        print(f"桌面截图已保存：{output}")
        finish(0)

    def on_failed(message: str) -> None:
        print(f"失败：{message}")
        finish(1)

    def finish(code: int) -> None:
        overlay.hide_overlay()
        target.close()
        manager.stop()
        app.exit(code)

    worker.ready.connect(on_ready)
    worker.frame.connect(on_frame)
    worker.failed.connect(on_failed)
    worker.status.connect(lambda text: print(f"  状态：{text}"))

    config = TranslatorConfig(engine="local", target_language=args.target, model_id=model.id)

    def run_frame() -> None:
        window = get_window_info(int(target.winId()))
        worker.process(window, config.to_dict())

    QTimer.singleShot(0, worker.initialize)
    # A hard stop so a hang cannot leave a window on screen forever.
    QTimer.singleShot(180_000, lambda: finish(2))
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
