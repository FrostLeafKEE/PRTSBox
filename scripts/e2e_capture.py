"""Capture + OCR check against a real on-screen window.

Opens Notepad with known content, captures it through the same code path the
pipeline uses, saves the frame for visual inspection, and recognises it.

    .venv\\Scripts\\python.exe scripts\\e2e_capture.py
"""

from __future__ import annotations

import logging
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from prtsbox.capture import capture_window, is_probably_blank
from prtsbox.ocr import OcrService
from prtsbox.textproc import prepare
from prtsbox.win32 import (
    enable_dpi_awareness,
    list_windows,
)

TITLE = "PRTSBox Capture Test"
CONTENT = """Loading...
Are you sure you want to delete this file?
The connection timed out. Retrying in 5 seconds.
Enable hardware acceleration to reduce CPU usage.
Damage: 1250  Critical Hit!
Target window is not in the foreground; capture is paused.
"""


def launch_notepad() -> subprocess.Popen:
    """Open a real third-party window for capture.

    Windows 11 Notepad is tabbed and reuses an existing window, so the capture
    also contains the user's other tabs, menu bar and status bar.  That is
    realistic rather than a problem: it is exactly what a window-level capture
    of a real application looks like, and it exercises both the client-area
    crop and the noise filtering.
    """
    document = Path(__file__).resolve().parent / "_capture_sample.txt"
    document.write_text(CONTENT, encoding="utf-8")
    process = subprocess.Popen(
        ["notepad.exe", str(document)],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    time.sleep(2.5)
    return process


def find_window(title_part: str):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        for window in list_windows():
            if title_part.casefold() in window.title.casefold():
                return window
        time.sleep(0.4)
    return None


def main() -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)-7s %(name)s: %(message)s")
    enable_dpi_awareness()

    process = launch_notepad()
    try:
        window = find_window("_capture_sample")
        if window is None:
            print("没有找到记事本窗口")
            return 2
        print(f"目标窗口：{window.title!r}  {window.width}×{window.height} @ ({window.left},{window.top})")

        started = time.perf_counter()
        frame = capture_window(window)
        capture_ms = (time.perf_counter() - started) * 1000.0
        if frame is None:
            print("截图失败")
            return 3
        print(f"抓帧：{frame.shape[1]}×{frame.shape[0]}  {capture_ms:.0f} ms  全黑={is_probably_blank(frame)}")

        output = Path(__file__).resolve().parent / "capture_sample.png"
        import cv2

        cv2.imwrite(str(output), frame)
        print(f"已保存截图：{output}")

        service = OcrService(backend="onnxruntime")
        service.recognize(frame)
        started = time.perf_counter()
        items, timing = service.recognize_timed(frame)
        print(f"OCR：{len(items)} 行  {timing['ocr_ms']:.0f} ms（含预热后）")

        print("\n原始识别：")
        for item in items:
            left, top, _, _ = item.bounds
            print(f"  ({left:5.0f},{top:5.0f}) {item.confidence:.2f}  {item.text!r}")

        print("\n清洗+合并后（送入翻译的单元）：")
        for item in prepare(items):
            print(f"  {item.text!r}")

        # A second capture should be no slower; PrintWindow has no frame cache
        # to warm, so this also confirms timing is stable.
        times = []
        for _ in range(3):
            started = time.perf_counter()
            capture_window(window)
            times.append((time.perf_counter() - started) * 1000.0)
        print(f"\n连续抓帧：{min(times):.0f} / {sum(times)/len(times):.0f} ms（最快/平均）")
        return 0
    finally:
        process.terminate()


if __name__ == "__main__":
    sys.exit(main())
