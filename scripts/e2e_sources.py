"""Verify the download-source option: switching, probing, and real downloads.

Three things have to hold. Changing the source must reach the manager the
downloads actually use, the reachability probe must report each host correctly,
and a download must respect the selected source - a preference that silently
did nothing would be worse than no option at all.

    .venv\\Scripts\\python.exe scripts\\e2e_sources.py
    .venv\\Scripts\\python.exe scripts\\e2e_sources.py --download   # also fetch
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QTabWidget

from prtsbox import llama, paths
from prtsbox.config import ConfigStore
from prtsbox.llama import LlamaManager
from prtsbox.ui.main_window import MainWindow, bootstrap
from prtsbox.ui.settings_dialog import SettingsDialog

logging.basicConfig(level=logging.WARNING, format="%(levelname)-7s %(name)s: %(message)s")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--download", action="store_true", help="also fetch the runtime")
    args = parser.parse_args()

    bootstrap()
    app = QApplication(sys.argv)  # noqa: F841 - must outlive the widgets

    # -- probe every host, outside the UI ---------------------------------
    print("=== 各源可达性（1.8B 模型）")
    model = llama.find_model("hy-mt2-1.8b")
    assert model is not None
    for label, url in model.sources_with_labels(llama.SOURCE_AUTO):
        started = time.perf_counter()
        ok, detail = llama.probe_source(url)
        took = time.perf_counter() - started
        print(f"  {'✓' if ok else '✗'} {label:<18} {detail}　{took:.1f}s")

    print("\n=== 各源可达性（Vulkan 运行时）")
    variant = llama.find_variant("vulkan")
    assert variant is not None
    for label, url in variant.sources_with_labels(llama.SOURCE_AUTO):
        started = time.perf_counter()
        ok, detail = llama.probe_source(url)
        took = time.perf_counter() - started
        print(f"  {'✓' if ok else '✗'} {label:<18} {detail}　{took:.1f}s")

    # -- the option reaches the manager -----------------------------------
    print("\n=== 设置项 → 管理器")
    config = ConfigStore()
    config.load()
    manager = LlamaManager()
    window = MainWindow(config, manager)
    dialog = SettingsDialog(config, manager, window)
    tabs = dialog.findChild(QTabWidget)
    assert tabs is not None
    tabs.setCurrentIndex(1)
    dialog.show()
    for _ in range(8):
        QApplication.processEvents()

    expected = llama.normalise_source(config.get("download_source"))
    print(f"  对话框初值 {dialog._source_combo.currentData()}　配置 {expected}")
    assert dialog._source_combo.currentData() == expected

    failures = 0
    for value in llama.DOWNLOAD_SOURCES:
        index = dialog._source_combo.findData(value)
        dialog._source_combo.setCurrentIndex(index)
        for _ in range(4):
            QApplication.processEvents()
        matched = manager.source_preference == value and config.get("download_source") == value
        print(f"  切到 {value:<9} → 管理器 {manager.source_preference:<9} {'OK' if matched else '不一致！'}")
        if not matched:
            failures += 1

    # -- the probe button --------------------------------------------------
    print("\n=== 「测试可达性」按钮")
    dialog._source_combo.setCurrentIndex(dialog._source_combo.findData(llama.SOURCE_AUTO))
    done = {"value": False}

    def finish_probe() -> None:
        done["value"] = True

    original_done = dialog._source_status.text

    def watch() -> None:
        if dialog._source_test_button.isEnabled() and dialog._source_status.text() != original_done():
            finish_probe()

    probe_timer = QTimer()
    probe_timer.timeout.connect(watch)
    probe_timer.start(200)

    dialog._source_test_button.click()
    deadline = time.monotonic() + 40
    while not done["value"] and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.05)
    probe_timer.stop()

    text = dialog._source_status.text()
    print(f"  结果：{text}")
    if "✓" not in text:
        print("  <-- 没有源可达，或按钮没有回报")
        failures += 1

    # -- a real download honours the choice --------------------------------
    if args.download:
        print("\n=== 按「仅官方源」下载运行时")
        shutil.rmtree(variant.root, ignore_errors=True)
        (paths.runtime_dir() / variant.asset).unlink(missing_ok=True)
        manager.set_source_preference(llama.SOURCE_OFFICIAL)
        started = time.perf_counter()
        try:
            manager.install_runtime(variant)
            print(f"  成功，{time.perf_counter() - started:.0f}s，已安装={variant.is_installed()}")
        except Exception as exc:  # noqa: BLE001 - reported, not fatal here
            print(f"  失败：{exc}")
            failures += 1

    dialog.reject()
    window.shutdown()

    print("\n" + "=" * 52)
    print("通过" if not failures else f"发现 {failures} 处问题")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
