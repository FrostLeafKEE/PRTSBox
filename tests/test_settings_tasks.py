import threading
import time
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication

from prtsbox.config import ConfigStore
from prtsbox.ui import settings_dialog as module


def pump(app, condition):
    deadline = time.monotonic() + 3
    while not condition() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)
    assert condition()


@pytest.fixture
def dialog(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(module.llama, "recommend_variant", lambda: module.llama.RUNTIME_VARIANTS[0])
    manager = SimpleNamespace(is_runtime_installed=lambda: False, is_server_running=lambda: False)
    window = module.SettingsDialog(ConfigStore(), manager)
    yield app, window
    if window._task is not None:
        window._task.cancel.set()
    pump(app, lambda: window._task_thread is None)
    window.reject()


def test_second_download_does_not_steal_active_task_state(dialog):
    app, window = dialog
    release = threading.Event()
    try:
        window._start_task(lambda cancel, progress: release.wait(2), kind="runtime", target=None, on_done=lambda: None)
        window._download_model(module.llama.MODELS[0].id)
        assert window._active_card is None
        assert not window._runtime_button.isEnabled()
    finally:
        release.set()


def test_close_cancels_task_without_blocking_ui_or_destroying_thread(dialog):
    app, window = dialog
    release = threading.Event()
    timer = threading.Timer(.5, release.set)
    window.show()
    app.processEvents()
    window._start_task(lambda cancel, progress: release.wait(2), kind="runtime", target=None, on_done=lambda: None)
    timer.start()
    try:
        started = time.monotonic()
        window.reject()
        assert time.monotonic() - started < .2
        assert window.isVisible()
        pump(app, lambda: window._task_thread is None and not window.isVisible())
    finally:
        release.set()
        timer.join()


def test_escape_saves_current_openai_edits(dialog):
    app, window = dialog
    window._model_name.setText("edited-model")
    window.reject()
    assert window._config.get("openai_model") == "edited-model"
