"""Drive a real translation session through the UI's own frame loop.

Every other end-to-end script calls ``PipelineWorker.process`` directly, which
skips the thread hand-off in ``MainWindow._tick``.  That hand-off was broken -
``Q_ARG(object, ...)`` raises in PySide6, so no frame ever reached the worker and
the session froze on its first status message - and the direct-call scripts could
not see it.  This one starts a session the way a user does and checks that frames
actually flow.

    .venv\\Scripts\\python.exe scripts\\e2e_session.py
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from prtsbox import pipeline as pipeline_module
from prtsbox.config import ConfigStore
from prtsbox.llama import LlamaManager
from prtsbox.models import OcrItem
from prtsbox.ui.main_window import MainWindow, bootstrap

LINES = ["Loading...", "Are you sure you want to delete this file?"]


class StubTranslator:
    """Marks each string so a translated frame is unmistakable."""

    def translate_batch(self, texts: list[str]) -> list[str]:
        return [f"译文:{text}" for text in texts]

    def preflight(self) -> None:
        return None


class StubOcr:
    using_openvino = True

    def recognize(self, _frame):
        return [
            OcrItem(
                box=((0.0, i * 20.0), (200.0, i * 20.0), (200.0, i * 20.0 + 14.0), (0.0, i * 20.0 + 14.0)),
                text=text,
                confidence=0.95,
            )
            for i, text in enumerate(LINES)
        ]


def main() -> int:
    logging.basicConfig(level=logging.ERROR)
    bootstrap()
    app = QApplication(sys.argv)

    # A capture that succeeds, so the frame path runs to completion.
    pipeline_module.capture_window = lambda _window: np.full((200, 400, 3), 240, np.uint8)

    # Replace the model and OCR with stubs: the question here is whether the
    # UI's dispatch path delivers frames, not whether the model translates well.
    #
    # Both patches must be in place *before* MainWindow is constructed, because
    # its pipeline thread starts immediately and OcrService is looked up as the
    # very first thing that thread does.
    pipeline_module.create_translator = lambda *_a, **_k: StubTranslator()
    pipeline_module.OcrService = lambda **_kwargs: StubOcr()

    config = ConfigStore()
    config.load()
    window = MainWindow(config, LlamaManager())
    window.load_from_config()
    window.refresh_windows()

    if window._selected_window is None:
        print("没有可用目标窗口，跳过")
        return 0

    results: list = []
    window._pipeline.frame.connect(results.append)

    window.start()
    QTimer.singleShot(50, window._on_prepare_finished)

    def report() -> None:
        window._timer.stop()
        print(f"派发帧数（worker_busy={window._worker_busy}）")
        print(f"收到帧数        {len(results)}")
        if results:
            first = results[0]
            print(f"首帧条目        {len(first.items)}")
            for item in first.items[:3]:
                print(f"  {item.text!r} -> {item.translation!r}")
            print(f"首帧备注        {first.note!r}")
        print(f"状态栏          {window._status_label.text()!r}")
        print(f"译文层绘制项    {window._overlay.item_count}")

        ok = bool(results) and any(item.translation for item in results[0].items)
        print()
        print("通过：帧经由 UI 派发链路送达并完成翻译" if ok else "失败：帧没有走通派发链路")
        window.stop()
        # Close the pipeline thread and llama child before exiting, otherwise
        # teardown happens with threads still live and the process crashes.
        window.shutdown()
        app.exit(0 if ok else 1)

    QTimer.singleShot(4000, report)
    code = app.exec()
    return code


if __name__ == "__main__":
    sys.exit(main())
