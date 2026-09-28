"""Probe our own test windows: capture, OCR, monitor placement and Z-order.

No user/game windows are captured. Pass an output directory for the JSON report.
"""
from pathlib import Path
import json
import os
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QFont, QPainter
from PySide6.QtWidgets import QApplication, QWidget

from prtsbox import win32
from prtsbox.capture import capture_window
from prtsbox.models import LayoutMode
from prtsbox.ocr import OcrService
from prtsbox.overlay import TranslationOverlay


class Sample(QWidget):
    angle = 0
    text = "Hello world"

    def __init__(self):
        super().__init__()
        self.setWindowTitle("PRTSBox audit sample")
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.resize(640, 360)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.GlobalColor.white)
        painter.setPen(Qt.GlobalColor.black)
        painter.setFont(QFont("Arial", 28))
        painter.translate(self.width() / 2, self.height() / 2)
        painter.rotate(self.angle)
        painter.drawText(-140, 0, self.text)
        painter.end()


def settle(app):
    for _ in range(5):
        app.processEvents()
        time.sleep(.03)


def main():
    output = Path(sys.argv[1]).resolve()
    output.mkdir(parents=True, exist_ok=True)
    os.chdir(output)
    win32.enable_dpi_awareness()
    app = QApplication([])
    sample, cover, overlay = Sample(), Sample(), TranslationOverlay()
    sample.show()
    settle(app)
    ocr = OcrService()
    report = {"monitors": [], "rotations": []}
    try:
        for screen in app.screens():
            sample.windowHandle().setScreen(screen)
            sample.move(screen.availableGeometry().topLeft() + QPoint(40, 40))
            settle(app)
            info = win32.get_window_info(int(sample.winId()))
            frame = capture_window(info)
            assert frame is not None and frame.shape[:2] == (info.height, info.width)
            items = ocr.recognize(frame)
            assert "hello" in " ".join(item.text for item in items).lower()
            overlay.attach(info)
            overlay.update_content(info, [item.with_translation("你好，世界") for item in items],
                                   LayoutMode.BELOW, (info.width, info.height))
            settle(app)
            actual = win32.window_rect(int(overlay.winId()))
            expected = (info.left, info.top, info.width, info.height)
            assert all(abs(a - b) <= 1 for a, b in zip(actual, expected)), (actual, expected)
            assert not win32.is_topmost(int(overlay.winId()))
            cover.show()
            cover.raise_()
            settle(app)
            overlay.sync_geometry(info)
            settle(app)
            predecessor = win32.user32.GetWindow(info.hwnd, 3)
            assert predecessor == int(overlay.winId())
            assert not win32.is_topmost(int(overlay.winId()))
            report["monitors"].append({"name": screen.name(), "scale": screen.devicePixelRatio(),
                                       "client": expected, "overlay": actual, "ocr": [i.text for i in items]})
            cover.hide()
            overlay.hide_overlay()
        for angle in (0, 15, 90, 180):
            sample.angle = angle
            sample.update()
            settle(app)
            frame = capture_window(win32.get_window_info(int(sample.winId())))
            items = ocr.recognize(frame)
            report["rotations"].append({"angle": angle, "text": [i.text for i in items]})
        sample.angle = 0
        sample.text = "Tomorrow we leave"
        sample.update()
        settle(app)
        items = ocr.recognize(capture_window(win32.get_window_info(int(sample.winId()))))
        assert "tomorrow" in " ".join(item.text for item in items).lower()
        report["page_change"] = [i.text for i in items]
    finally:
        overlay.close()
        cover.close()
        sample.close()
    (output / "desktop-audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True))


if __name__ == "__main__":
    main()
