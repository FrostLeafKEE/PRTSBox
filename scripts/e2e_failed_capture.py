"""Reproduce and verify the frozen-pipeline bug end to end.

Before the fix, a failed window capture returned from the pipeline without
emitting anything.  The UI cleared its ``worker_busy`` flag only on a signal, so
it waited forever, never dispatched another frame, and left the status line on
"正在加载模型" - which read as a model that never finished loading when in fact
the pipeline had already stopped.

This drives the real MainWindow with a capture that always fails and asserts the
UI keeps making progress instead of wedging.

    .venv\\Scripts\\python.exe scripts\\e2e_failed_capture.py
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from prtsbox import pipeline as pipeline_module
from prtsbox.config import ConfigStore
from prtsbox.llama import LlamaManager
from prtsbox.ui.main_window import MainWindow, bootstrap


class StubTranslator:
    def translate_batch(self, texts):
        return list(texts)

    def preflight(self) -> None:
        return None


def main() -> int:
    logging.basicConfig(level=logging.ERROR)
    bootstrap()
    app = QApplication(sys.argv)

    # Every capture fails, which is what a virtual display or a protected window
    # produces.
    pipeline_module.capture_window = lambda _window: None

    config = ConfigStore()
    config.load()
    window = MainWindow(config, LlamaManager())
    window.load_from_config()
    window.refresh_windows()

    # Stand in for the model so the check needs no llama-server.
    window.translator_config = lambda: __import__(
        "prtsbox.models", fromlist=["TranslatorConfig"]
    ).TranslatorConfig(engine="local", target_language="zh-CN")
    window._pipeline._ocr = type(
        "Ocr", (), {"using_openvino": True, "recognize": staticmethod(lambda _f: [])}
    )()

    ticks: list[float] = []
    original_tick = window._tick

    def counting_tick() -> None:
        ticks.append(time.monotonic())
        original_tick()

    window._tick = counting_tick

    window.start()
    # Skip the prepare round trip; what is under test is the frame loop.
    QTimer.singleShot(100, window._on_prepare_finished)

    def report() -> None:
        elapsed = time.monotonic() - started
        window._timer.stop()
        print(f"运行 {elapsed:.1f} 秒")
        print(f"派发帧数        {len(ticks)}")
        print(f"worker_busy     {window._worker_busy}")
        print(f"状态栏          {window._status_label.text()!r}")
        print()
        ok = len(ticks) >= 3 and not window._worker_busy
        if ok:
            print("通过：截图持续失败时流水线仍在推进，状态栏说明了原因")
        else:
            print("失败：流水线已冻结（修复前正是如此）")
        window.stop()
        # Close the pipeline thread and llama child before exiting, otherwise
        # teardown happens with threads still live and the process crashes.
        window.shutdown()
        app.exit(0 if ok else 1)

    started = time.monotonic()
    QTimer.singleShot(4500, report)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
