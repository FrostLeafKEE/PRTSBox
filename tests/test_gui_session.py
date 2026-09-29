"""GUI-level regression tests for the frame dispatch path.

Three defects conspired to make a session appear to hang on "正在加载模型":

1. ``MainWindow._tick`` dispatched work with
   ``QMetaObject.invokeMethod(..., Q_ARG(object, ...))``.  PySide6 cannot resolve
   the type name ``object``, so the call raised and no frame ever reached the
   worker.
2. ``PipelineWorker.process`` returned without emitting on a failed capture, so
   even a successful dispatch would have left the UI waiting forever.
3. Teardown only happened in ``closeEvent``, so ``app.quit()`` destroyed the
   widgets with the pipeline thread and the llama-server child still running,
   crashing on exit with 0xC0000409.

Each test below fails if its defect is reintroduced.  They need a QApplication,
which is created once per module and skipped when no display is available.
"""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import Q_ARG
from PySide6.QtWidgets import QApplication

from prtsbox import pipeline as pipeline_module
from prtsbox import win32 as win32_module
from prtsbox.config import ConfigStore
from prtsbox.models import OcrItem, WindowInfo
from prtsbox.pipeline import NOTE_CAPTURE_FAILED


class StubLlamaManager:
    """Enough of the manager for the main window to build and tear down."""

    def is_runtime_installed(self, _variant=None) -> bool:
        return True

    def is_server_running(self) -> bool:
        return False

    @property
    def running_model_id(self) -> str:
        return ""

    def stop(self) -> None:
        return None

    def shutdown(self) -> None:
        return None


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def patched_pipeline(monkeypatch: pytest.MonkeyPatch):
    """Stub out capture, OCR and translation so no model is needed."""

    class StubOcr:
        using_openvino = True

        def recognize(self, _frame):
            box = ((0.0, 0.0), (200.0, 0.0), (200.0, 14.0), (0.0, 14.0))
            return [OcrItem(box=box, text="Loading...", confidence=0.95)]

    class StubTranslator:
        def translate_batch(self, texts):
            return [f"译:{t}" for t in texts]

        def preflight(self) -> None:
            return None

    monkeypatch.setattr(
        pipeline_module, "capture_window", lambda _w: np.full((60, 120, 3), 240, np.uint8)
    )
    monkeypatch.setattr(pipeline_module, "OcrService", lambda **_k: StubOcr())
    monkeypatch.setattr(pipeline_module, "create_translator", lambda *_a, **_k: StubTranslator())
    monkeypatch.setattr(
        win32_module, "get_window_info",
        lambda hwnd: WindowInfo(hwnd=hwnd, title="stub", left=0, top=0, width=120, height=60),
    )
    return monkeypatch


def make_window(patched_pipeline, config=None):
    from prtsbox.ui.main_window import MainWindow

    config = config or ConfigStore()
    config.load()
    window = MainWindow(config, llama_manager=StubLlamaManager())  # type: ignore[arg-type]
    window.load_from_config()
    return window


def test_ui_language_switch_persists_and_preserves_translation_choices(
    qapp, patched_pipeline, tmp_path
) -> None:
    import re

    from PySide6.QtWidgets import QAbstractButton, QComboBox, QGroupBox, QLabel, QLineEdit, QScrollArea, QTabWidget, QWidget

    from prtsbox.ui.settings_dialog import SettingsDialog

    config = ConfigStore(tmp_path / "config.json")
    window = make_window(patched_pipeline, config)
    try:
        original_engine = window._engine_combo.currentData()
        original_target = window._target_combo.currentData()
        assert window._language_button.text() == "中 / EN"
        assert window.windowTitle() == "PRTSBox v1.01 · 实时窗口翻译"

        window.resize(400, 520)
        window.show()
        qapp.processEvents()
        window._language_button.click()
        qapp.processEvents()
        assert config.get("ui_language") == "en"
        assert ConfigStore(config.path).load()["ui_language"] == "en"
        assert window.windowTitle() == "PRTSBox v1.01 · Live Window Translation"
        assert any(group.title() == "Translation engine" for group in window.findChildren(QGroupBox))
        assert window._engine_combo.itemText(0) == "Local model (free, offline)"
        assert window._target_combo.itemText(window._target_combo.findData("en")) == "English"
        assert window._engine_combo.currentData() == original_engine
        assert window._target_combo.currentData() == original_target
        main_scroll = window.findChild(QScrollArea)
        assert main_scroll is not None
        assert main_scroll.horizontalScrollBar().maximum() == 0

        window._set_status("未识别到文字")
        assert window._status_label.text() == "No text detected"
        assert window._pet._main_window_action.text() == "Open main window"
        assert window._pet._translation_action.text() == "Start translation"

        main_untranslated = []
        for widget in window.findChildren(QWidget):
            if widget in (window._window_combo, window._language_button):
                continue
            if isinstance(widget, QGroupBox):
                values = [widget.title()]
            elif isinstance(widget, QComboBox):
                values = [widget.itemText(i) for i in range(widget.count())]
            elif isinstance(widget, (QLabel, QAbstractButton)):
                values = [widget.text()]
            else:
                values = []
            values.append(widget.toolTip())
            main_untranslated.extend(value for value in values if re.search(r"[\u4e00-\u9fff]", value))
        assert main_untranslated == []

        dialog = SettingsDialog(config, window._llama, window)
        try:
            tabs = dialog.findChild(QTabWidget)
            assert tabs is not None
            assert [tabs.tabText(i) for i in range(tabs.count())] == [
                "General", "Local model", "Translation provider", "AI Model API", "About"
            ]
            assert dialog._update_button.text() == "Check for updates"
            assert any(label.text() == "OCR backend" for label in dialog.findChildren(QLabel))
            assert dialog._theme_combo.itemText(0) == "Dark"
            assert dialog._cards[next(iter(dialog._cards))]._download.text() == "Download"
            untranslated = []
            for widget in dialog.findChildren(QWidget):
                if isinstance(widget, QGroupBox):
                    values = [widget.title()]
                elif isinstance(widget, QComboBox):
                    values = [widget.itemText(i) for i in range(widget.count())]
                elif isinstance(widget, (QLabel, QAbstractButton)):
                    values = [widget.text()]
                else:
                    values = []
                values.append(widget.toolTip())
                if isinstance(widget, QLineEdit):
                    values.append(widget.placeholderText())
                untranslated.extend(value for value in values if re.search(r"[\u4e00-\u9fff]", value))
            assert untranslated == []
        finally:
            dialog.reject()

        window._language_button.click()
        assert config.get("ui_language") == "zh"
        assert window.windowTitle() == "PRTSBox v1.01 · 实时窗口翻译"
        assert window._status_label.text() == "未识别到文字"
        assert "目标语言是中文" in window._skip_chinese_check.toolTip()
        assert window._pet._main_window_action.text() == "打开主窗口"
        assert window._engine_combo.currentData() == original_engine
        assert window._target_combo.currentData() == original_target
    finally:
        window.shutdown()


def test_saved_english_language_is_applied_on_startup(qapp, patched_pipeline, tmp_path) -> None:
    config = ConfigStore(tmp_path / "config.json")
    config.load()
    config.set("ui_language", "en")
    config.save()

    window = make_window(patched_pipeline, ConfigStore(config.path))
    try:
        assert window.windowTitle() == "PRTSBox v1.01 · Live Window Translation"
        assert window._language_button.isChecked()
        assert window._pet._close_action.text() == "Hide pet"
        assert window._source_combo.itemText(0) == "Auto detect"
    finally:
        window.shutdown()


def test_about_tab_checks_updates_without_blocking_ui(
    qapp, patched_pipeline, tmp_path, monkeypatch
) -> None:
    import time

    from PySide6.QtWidgets import QTabWidget

    from prtsbox import updates
    from prtsbox.ui.settings_dialog import SettingsDialog

    config = ConfigStore(tmp_path / "config.json")
    config.load()
    window = make_window(patched_pipeline, config)
    monkeypatch.setattr(
        updates, "check_latest_release",
        lambda: updates.UpdateResult("v1.02", True),
    )
    dialog = SettingsDialog(config, window._llama, window)
    try:
        tabs = dialog.findChild(QTabWidget)
        assert tabs is not None
        tabs.setCurrentIndex(4)
        dialog.show()
        qapp.processEvents()
        dialog._update_button.click()
        assert dialog._task_thread is not None
        deadline = time.monotonic() + 3
        while dialog._task_thread is not None and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.01)
        qapp.processEvents()
        assert dialog._task_thread is None
        assert dialog._update_status.text() == "发现新版本：v1.02"
        assert dialog._release_button.isVisibleTo(dialog)

        def unavailable():
            raise updates.ReleaseUnavailableError("private repository")

        monkeypatch.setattr(updates, "check_latest_release", unavailable)
        dialog._update_button.click()
        deadline = time.monotonic() + 3
        while dialog._task_thread is not None and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.01)
        qapp.processEvents()
        assert dialog._task_thread is None
        assert "仓库已公开" in dialog._update_status.text()
        assert not dialog._release_button.isVisibleTo(dialog)
    finally:
        dialog.reject()
        window.shutdown()


def select_target(window) -> None:
    """Give the window something to capture, without enumerating real windows."""
    window._selected_window = WindowInfo(
        hwnd=1, title="stub", left=0, top=0, width=120, height=60
    )


def pump(qapp, condition, attempts: int = 300) -> bool:
    """Run the event loop until ``condition`` holds, or give up.

    Waiting on the collected results alone is not enough: a plain callable
    connected to a cross-thread signal may be invoked immediately on the
    emitting thread, while the window's own slot is queued to the main thread.
    The condition has to describe the state that actually matters.
    """
    for _ in range(attempts):
        if condition():
            return True
        qapp.processEvents()
        QApplication.instance().thread().msleep(10)
    return condition()


class TestDispatchMechanism:
    def test_backend_reload_runs_only_on_pipeline_thread(self, qapp, patched_pipeline):
        from PySide6.QtCore import QThread
        created_on = []
        original = pipeline_module.OcrService

        def create(**kwargs):
            created_on.append(QThread.currentThread())
            return original(**kwargs)

        patched_pipeline.setattr(pipeline_module, "OcrService", create)
        window = make_window(patched_pipeline)
        try:
            assert pump(qapp, lambda: len(created_on) == 1)
            window._pipeline.set_ocr_backend("openvino")
            assert len(created_on) == 1
            select_target(window)
            window._running = True
            window._tick()
            assert pump(qapp, lambda: len(created_on) == 2 and not window._worker_busy)
            assert all(thread == window._thread for thread in created_on)
        finally:
            window.shutdown()

    def test_disappeared_picker_target_stops_and_clears_overlay(self, qapp, patched_pipeline):
        from prtsbox.ui import main_window
        window = make_window(patched_pipeline)
        try:
            select_target(window)
            window._running = True
            hidden = []
            patched_pipeline.setattr(window._overlay, "hide_overlay", lambda: hidden.append(True))
            patched_pipeline.setattr(main_window, "list_windows", lambda **kwargs: [])
            window.refresh_windows()
            assert not window._running
            assert window._selected_window is None
            assert hidden
        finally:
            window.shutdown()

    def test_tick_uses_current_window_geometry(self, qapp, patched_pipeline) -> None:
        window = make_window(patched_pipeline)
        try:
            select_target(window)
            moved = WindowInfo(hwnd=1, title="stub", left=80, top=40, width=200, height=100)
            patched_pipeline.setattr(win32_module, "get_window_info", lambda _hwnd: moved)
            dispatched = []
            window.dispatch_frame.connect(lambda target, _config: dispatched.append(target))
            window._running = True
            window._tick()
            assert dispatched == [moved]
        finally:
            window.shutdown()

    def test_q_arg_object_is_unusable(self, qapp) -> None:
        """Document why the dispatch uses a Signal instead of Q_ARG.

        If a future PySide6 release starts accepting ``object`` this test will
        fail, which is the prompt to reconsider - not a reason to switch back
        blindly, since the signal connection is strictly safer.
        """
        from PySide6.QtCore import QObject

        with pytest.raises(RuntimeError, match="QMetaType"):

            class Receiver(QObject):
                pass

            Q_ARG(object, {"k": 1})

    def test_tick_delivers_a_frame_to_the_worker(self, qapp, patched_pipeline) -> None:
        """The regression that mattered: a tick must actually reach the worker."""
        window = make_window(patched_pipeline)
        try:
            received: list = []
            window._pipeline.frame.connect(received.append)
            select_target(window)

            window._running = True
            window._tick()
            assert window._worker_busy, "派发后应标记忙碌"

            got = pump(qapp, lambda: bool(received) and not window._worker_busy)

            assert received, "帧没有送达 worker —— 派发链路又断了"
            assert not window._worker_busy, "收到帧后应清除忙碌标志"
            assert got
        finally:
            window.shutdown()

    def test_tick_does_nothing_when_not_running(self, qapp, patched_pipeline) -> None:
        window = make_window(patched_pipeline)
        try:
            window._running = False
            window._tick()
            assert not window._worker_busy
        finally:
            window.shutdown()


class TestStallRecovery:
    def test_stalled_worker_stops_the_session(self, qapp, patched_pipeline) -> None:
        """A frame that never returns must not leave the UI waiting silently."""
        import time

        from prtsbox.ui import main_window as module

        window = make_window(patched_pipeline)
        try:
            window._running = True
            window._worker_busy = True
            window._busy_since = time.monotonic() - module._WORKER_STALL_SECONDS - 1
            window._check_worker_stall()
            assert not window._running, "卡住后应停止会话"
            # Native work still owns the slot until its tagged reply arrives.
            assert window._worker_busy
            assert "未返回" in window._status_label.text()
        finally:
            window.shutdown()

    def test_recent_busy_is_left_alone(self, qapp, patched_pipeline) -> None:
        import time

        window = make_window(patched_pipeline)
        try:
            window._running = True
            window._worker_busy = True
            window._busy_since = time.monotonic()
            window._check_worker_stall()
            assert window._running, "刚开始处理就被判定卡死"
        finally:
            window.shutdown()


class TestShutdown:
    def test_shutdown_is_idempotent(self, qapp, patched_pipeline) -> None:
        window = make_window(patched_pipeline)
        window.shutdown()
        window.shutdown()  # must not raise or hang
        assert window._shutting_down

    def test_shutdown_stops_the_pipeline_thread(self, qapp, patched_pipeline) -> None:
        window = make_window(patched_pipeline)
        thread = window._thread
        assert thread.isRunning()
        window.shutdown()
        assert not thread.isRunning(), "退出后流水线线程必须已结束"


class TestFailedCaptureReports:
    def test_failed_capture_clears_busy_and_explains(
        self, qapp, patched_pipeline
    ) -> None:
        window = make_window(patched_pipeline)
        try:
            patched_pipeline.setattr(pipeline_module, "capture_window", lambda _w: None)
            received: list = []
            window._pipeline.frame.connect(received.append)

            select_target(window)
            window._running = True
            window._tick()
            pump(qapp, lambda: bool(received) and not window._worker_busy)

            assert received, "截图失败也必须回报"
            assert received[0].note == NOTE_CAPTURE_FAILED
            assert not window._worker_busy, "忙碌标志必须清除，否则流水线冻结"
            assert "截图失败" in window._status_label.text()
        finally:
            window.shutdown()


class TestOverlayStability:
    """Redrawing identical content is what makes the overlay visibly shimmer.

    OCR jitters by a pixel or two between frames even on a static screen, so the
    overlay has to distinguish "the text moved" from "the text was measured
    again".
    """

    @staticmethod
    def make_item(text: str, translation: str, left: float, top: float):
        from prtsbox.models import OcrItem

        box = ((left, top), (left + 200.0, top), (left + 200.0, top + 16.0), (left, top + 16.0))
        return OcrItem(box=box, text=text, confidence=0.95, translation=translation)

    @staticmethod
    def overlay():
        from prtsbox.overlay import TranslationOverlay

        return TranslationOverlay()

    def test_identical_content_does_not_repaint(self, qapp) -> None:
        from prtsbox.models import LayoutMode, WindowInfo

        overlay = self.overlay()
        try:
            target = WindowInfo(hwnd=1, title="t", left=0, top=0, width=800, height=600)
            items = [self.make_item("Loading...", "加载中…", 10.0, 20.0)]

            overlay.update_content(target, items, LayoutMode.BELOW, (800, 600))
            repaints = []
            overlay.update = lambda: repaints.append(1)  # type: ignore[method-assign]

            overlay.update_content(target, items, LayoutMode.BELOW, (800, 600))
            assert not repaints, "内容完全相同却重绘了"

            # A one-pixel drift is measurement noise, not a change.
            nudged = [self.make_item("Loading...", "加载中…", 10.0, 21.0)]
            overlay.update_content(target, nudged, LayoutMode.BELOW, (800, 600))
            assert not repaints, "1px 抖动不应触发重绘"
        finally:
            overlay.close()

    def test_real_change_repaints(self, qapp) -> None:
        from prtsbox.models import LayoutMode, WindowInfo

        overlay = self.overlay()
        try:
            target = WindowInfo(hwnd=1, title="t", left=0, top=0, width=800, height=600)
            overlay.update_content(
                target, [self.make_item("Loading...", "加载中…", 10.0, 20.0)],
                LayoutMode.BELOW, (800, 600),
            )
            repaints = []
            overlay.update = lambda: repaints.append(1)  # type: ignore[method-assign]

            changed = [self.make_item("Loading...", "正在加载…", 10.0, 20.0)]
            overlay.update_content(target, changed, LayoutMode.BELOW, (800, 600))
            assert repaints, "译文变了必须重绘"

            repaints.clear()
            moved = [self.make_item("Loading...", "加载中…", 10.0, 60.0)]
            overlay.update_content(target, moved, LayoutMode.BELOW, (800, 600))
            assert repaints, "位置明显变化必须重绘"

            repaints.clear()
            overlay.update_content(target, [], LayoutMode.BELOW, (800, 600))
            assert repaints, "清空必须重绘"
        finally:
            overlay.close()

    def test_setting_change_repaints(self, qapp) -> None:
        from prtsbox.models import LayoutMode, WindowInfo

        overlay = self.overlay()
        try:
            target = WindowInfo(hwnd=1, title="t", left=0, top=0, width=800, height=600)
            items = [self.make_item("Loading...", "加载中…", 10.0, 20.0)]
            overlay.update_content(target, items, LayoutMode.BELOW, (800, 600), font_size=14)
            repaints = []
            overlay.update = lambda: repaints.append(1)  # type: ignore[method-assign]

            overlay.update_content(target, items, LayoutMode.BELOW, (800, 600), font_size=20)
            assert repaints, "字号变化必须重绘"
        finally:
            overlay.close()


class TestOverlayCapturable:
    """Whether the overlay is visible to screenshot and capture software.

    The default is exclusion, so translations do not turn up in a screen share
    or a recording.  Opting in is the point of the checkbox, and the setting has
    to actually reach the Win32 display affinity - a checkbox that only wrote a
    JSON key would look like a broken feature.

    These build their own window against a temporary config file: ``make_window``
    uses the real ``data/config.json``, and a test must not silently rewrite the
    settings of whoever is running it.
    """

    @staticmethod
    def make_window(tmp_path, patched_pipeline):
        from prtsbox.ui.main_window import MainWindow

        config = ConfigStore(tmp_path / "config.json")
        config.load()
        window = MainWindow(config, llama_manager=StubLlamaManager())  # type: ignore[arg-type]
        window.load_from_config()
        return window

    @staticmethod
    def make_result():
        from prtsbox.models import OcrItem
        from prtsbox.pipeline import FrameResult

        box = ((0.0, 0.0), (200.0, 0.0), (200.0, 14.0), (0.0, 14.0))
        return FrameResult(
            items=[OcrItem(box=box, text="Loading...", confidence=0.95, translation="加载中…")],
            frame_size=(120, 60),
        )

    def test_default_is_hidden_from_capture(self, qapp, patched_pipeline, tmp_path) -> None:
        window = self.make_window(tmp_path, patched_pipeline)
        try:
            assert not window._capture_check.isChecked(), "默认必须是不被捕捉"
            assert window._config.get("overlay_capturable") is False
        finally:
            window.shutdown()

    def test_checkbox_drives_capture_affinity(
        self, qapp, patched_pipeline, monkeypatch, tmp_path
    ) -> None:
        from prtsbox.overlay import TranslationOverlay

        captured: list[bool] = []
        monkeypatch.setattr(
            TranslationOverlay,
            "attach",
            lambda self, target, **kwargs: captured.append(kwargs["hide_from_capture"]),
        )
        monkeypatch.setattr(
            "prtsbox.win32.get_window_info",
            lambda hwnd: WindowInfo(hwnd=hwnd, title="stub", left=0, top=0, width=120, height=60),
        )

        window = self.make_window(tmp_path, patched_pipeline)
        try:
            select_target(window)
            # A result must exist for the display options to redraw immediately;
            # this also proves the toggle takes effect without waiting a frame.
            window._last_result = self.make_result()

            window._capture_check.setChecked(True)
            assert captured and captured[-1] is False, "开启后译文层应可被捕捉"

            window._capture_check.setChecked(False)
            assert captured[-1] is True, "关闭后译文层应重新从捕捉中排除"
        finally:
            window.shutdown()

    def test_choice_is_persisted(self, qapp, patched_pipeline, tmp_path) -> None:
        window = self.make_window(tmp_path, patched_pipeline)
        try:
            window._capture_check.setChecked(True)
            assert window._config.get("overlay_capturable") is True
        finally:
            window.shutdown()

    def test_load_from_config_restores_the_checkbox(
        self, qapp, patched_pipeline, tmp_path
    ) -> None:
        window = self.make_window(tmp_path, patched_pipeline)
        try:
            window._config.set("overlay_capturable", True)
            window.load_from_config()
            assert window._capture_check.isChecked()
        finally:
            window.shutdown()
