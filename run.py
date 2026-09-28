"""PRTSBox entry point.

    .venv\\Scripts\\python.exe run.py

Packaged builds start here too, so this file is also where a start-up failure is
turned into something a user can act on - see :func:`_report_startup_failure`.
"""

from __future__ import annotations

import os
import sys
import time
import traceback
from pathlib import Path


def _report_startup_failure(exc: BaseException) -> None:
    """Show and record a failure that happened before the UI existed.

    A packaged build runs without a console, so an exception during import or
    window construction would otherwise look like "double-clicked, nothing
    happened".  The traceback goes to ``data/logs/crash.log`` next to the
    executable and, when Qt is usable, into a dialog - the friend testing a
    build can then send one file back instead of guessing.
    """
    details = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))

    log_path = None
    try:
        from prtsbox import paths

        crash_log = paths.logs_dir() / "crash.log"
        crash_log.write_text(details, encoding="utf-8")
        log_path = crash_log
    except Exception:  # noqa: BLE001, S110 - reporting must never raise in turn
        pass

    sys.stderr.write(details)
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox

        # Reuse the running instance when there is one, so the dialog does not
        # need a second event loop.
        app = QApplication.instance() or QApplication(sys.argv)
        message = QMessageBox()
        message.setIcon(QMessageBox.Icon.Critical)
        message.setWindowTitle("PRTSBox 启动失败")
        message.setText("程序启动时出错，无法继续。")
        message.setInformativeText(
            f"{type(exc).__name__}: {exc}\n\n"
            + (f"详细信息已写入：\n{log_path}" if log_path else "详细信息见命令行输出。")
        )
        message.setDetailedText(details)
        message.exec()
        del app
    except Exception:  # noqa: BLE001, S110 - a dialog is a nicety, not the report
        pass


def _run_selftest() -> int:
    """Check the parts of a build that only fail once packaged.

    A frozen build imports and draws its window easily; what breaks is the
    pieces that reach outside Python - fetching and unpacking a downloaded
    archive, and launching the bundled llama-server child.  Those are exactly
    what a friend testing a build cannot diagnose, so this exercises them and
    reports plainly.

    ``PRTSBOX_SELFTEST=1`` checks what is already installed.
    ``PRTSBOX_SELFTEST=download`` additionally fetches the llama.cpp runtime
    (33 MB) to prove the download and extraction path works; the multi-gigabyte
    model is left to the user.

    Results go to the log.  Only a genuine failure sets a non-zero exit code:
    "not downloaded yet" is a normal state for a fresh install, not a defect.
    """
    import logging
    import os as _os

    from prtsbox import llama, paths
    from prtsbox.llama import LlamaManager
    from prtsbox.models import TranslatorConfig
    from prtsbox.translate import create_translator

    logger = logging.getLogger("prtsbox.selftest")
    do_download = _os.environ.get("PRTSBOX_SELFTEST", "").lower() == "download"
    # name, state ("ok" | "pending" | "fail"), detail
    checks: list[tuple[str, str, str]] = []

    # 1. Bundled OCR models are present and loadable.
    try:
        from prtsbox.ocr import OcrService

        service = OcrService(backend="auto")
        checks.append(("OCR 初始化", "ok", f"OpenVINO={service.using_openvino}"))
    except Exception as exc:  # noqa: BLE001 - report, do not abort the run
        checks.append(("OCR 初始化", "fail", str(exc)))

    manager = LlamaManager()

    # 2. The downloader, when asked.  The runtime is small enough to fetch here;
    #    this covers the archive extraction that a model download also relies on.
    if do_download and not manager.is_runtime_installed():
        variant = llama.recommend_variant()
        try:
            started = time.perf_counter()
            manager.install_runtime(variant)
            checks.append(
                ("下载并解压运行时", "ok", f"{variant.name}，{time.perf_counter() - started:.0f}s")
            )
        except Exception as exc:  # noqa: BLE001 - report, do not abort the run
            checks.append(("下载并解压运行时", "fail", str(exc)))

    # 3. Runtime and model - present, or simply not fetched yet.
    variant_ready = manager.is_runtime_installed()
    checks.append(
        ("推理运行时", "ok" if variant_ready else "pending",
         "已安装" if variant_ready else "未安装（设置 → 本地模型 中下载）")
    )
    model = llama.default_model()
    model_ready = model.is_downloaded()
    checks.append(
        ("翻译模型", "ok" if model_ready else "pending",
         model.filename if model_ready else "未下载（设置 → 本地模型 中下载，约 1 GB）")
    )

    # 4. Launch the child process and translate - the real end-to-end path, and
    #    the one most likely to break in a frozen build.
    if variant_ready and model_ready:
        try:
            config = TranslatorConfig(engine="local", target_language="zh-CN", model_id=model.id)
            translator = create_translator(config, llama_manager=manager)
            translator.preflight()
            translations = translator.translate_batch(["Hello, world."])
            checks.append(("llama-server 子进程 + 翻译", "ok", repr(translations[0])))
        except Exception as exc:  # noqa: BLE001 - report, do not abort the run
            checks.append(("llama-server 子进程 + 翻译", "fail", str(exc)))
        finally:
            manager.stop()
    else:
        checks.append(("llama-server 子进程 + 翻译", "pending", "跳过（运行时或模型未就绪）"))

    logger.info("=" * 56)
    logger.info("PRTSBox 自检（数据目录 %s）", paths.data_dir())
    labels = {"ok": "通过", "pending": "待下载", "fail": "失败"}
    failed = 0
    for name, state, detail in checks:
        if state == "fail":
            failed += 1
        logger.info("%-4s %-26s %s", labels[state], name, detail)
    logger.info(
        "结论：%s", "存在失败项，请查看上面的信息" if failed else "构建可用"
    )
    logger.info("=" * 56)

    for handler in logging.getLogger().handlers:
        handler.flush()
    return 1 if failed else 0


def main() -> int:
    from prtsbox import logging_setup, paths

    # onnxocr caches its converted models under Path.cwd()/cache and the path is
    # not configurable.  Moving the working directory into the data folder keeps
    # that cache next to the rest of the application state instead of dropping it
    # wherever the app happened to be started from.
    os.chdir(paths.data_dir())

    logging_setup.setup()

    if os.environ.get("PRTSBOX_SELFTEST"):
        return _run_selftest()

    from prtsbox.ui.main_window import bootstrap

    bootstrap()

    if sys.platform == "win32":
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("PRTSBox.Desktop")

    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from prtsbox.config import ConfigStore
    from prtsbox.llama import LlamaManager
    from prtsbox.ui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("PRTSBox")
    app.setWindowIcon(QIcon(str(Path(__file__).resolve().parent / "prtsbox" / "ui" / "assets" / "app.ico")))
    # Deliberately no setApplicationDisplayName: Qt appends it to every window
    # title, which produced "PRTSBox · 实时窗口翻译 - PRTSBox".

    config = ConfigStore()
    config.load()
    manager = LlamaManager(source_preference=str(config.get("download_source") or "auto"))

    window = MainWindow(config, manager)
    window.load_from_config()
    window.refresh_windows()
    window.show()

    # Teardown must also run on paths that never deliver a close event, such as
    # app.quit() from the smoke test or a session logoff.
    app.aboutToQuit.connect(window.shutdown)

    # Smoke test used by CI and by hand: start, render, exit.
    if os.environ.get("PRTSBOX_SMOKE_TEST") == "1":
        from PySide6.QtCore import QTimer

        QTimer.singleShot(2500, app.quit)

    return app.exec()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException as error:  # noqa: BLE001 - last line of defence
        _report_startup_failure(error)
        sys.exit(1)
