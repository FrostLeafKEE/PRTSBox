"""UI smoke test: build every dialog and window without showing them.

Catches construction errors - a signal connected to a missing slot, a widget
referencing an attribute set later - that only appear when the UI is built.  The
unit tests cover the pipeline; nothing else covers the widgets.

    .venv\\Scripts\\python.exe scripts\\smoke_ui.py
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication

from prtsbox import llama
from prtsbox.config import ConfigStore
from prtsbox.llama import LlamaManager
from prtsbox.ui.main_window import MainWindow, bootstrap
from prtsbox.ui.settings_dialog import SettingsDialog


def main() -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)-7s %(name)s: %(message)s")
    bootstrap()
    # The reference must be kept: letting the QApplication be collected while
    # widgets still exist crashes inside Qt.
    app = QApplication(sys.argv)  # noqa: F841

    config = ConfigStore()
    config.load()
    manager = LlamaManager()

    window = MainWindow(config, manager)
    window.load_from_config()
    window.refresh_windows()
    print(f"主窗口：目标列表 {window._window_combo.count()} 项")
    print(f"  引擎：{window._engine_combo.currentText()}")
    print(f"  本地模型可选：{window._model_combo.count()} 项")
    print(f"  语言：{window._source_combo.currentText()} -> {window._target_combo.currentText()}")

    dialog = SettingsDialog(config, manager, window)
    print("设置对话框：已构造")
    print(f"  页签：{dialog.findChild(type(dialog._theme_combo).__mro__[3]) is not None}")
    cards = dialog._cards
    print(f"  模型卡片：{len(cards)} 个 -> {[c.model.name for c in cards.values()]}")
    # card is used below, so .items() is correct here despite the lint hint.
    for model_id, card in cards.items():  # noqa: PERF102
        model = llama.find_model(model_id)
        assert model is not None
        print(
            f"    {model.name}: 已下载={model.is_downloaded()} "
            f"显存={model.vram_label} 体积={model.size_gb:.2f} GB"
        )
    print(f"  运行时已安装：{manager.is_runtime_installed()}")

    # Exercise the state transitions that the buttons drive.
    active = str(config.get("local_model") or "")
    cards[llama.DEFAULT_MODEL_ID].refresh(active_id=active, busy=False)
    cards[llama.DEFAULT_MODEL_ID].set_downloading(0.42, "下载中 42%")
    print("  卡片状态切换：正常")

    dialog.reject()
    window.close()
    print("\n全部 UI 构造通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
