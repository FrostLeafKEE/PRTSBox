"""Exit regressions run in child processes, including uninterruptible work."""
from pathlib import Path
from types import SimpleNamespace
import os
import subprocess
import sys
import threading
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from prtsbox.llama import manager as manager_module
from prtsbox.llama.server import LlamaServer, LlamaServerError


def test_gui_status_reads_do_not_wait_for_model_loading_lock():
    manager = manager_module.LlamaManager()
    replied = threading.Event()

    def read():
        manager.is_server_running()
        manager.running_model_id
        manager.server_pid()
        manager.server_log_path()
        replied.set()

    with manager._lock:
        thread = threading.Thread(target=read)
        thread.start()
        responsive = replied.wait(.5)
    thread.join(2)
    assert responsive


def test_shutdown_interrupts_model_loading_without_waiting_for_manager_lock(monkeypatch):
    entered, released = threading.Event(), threading.Event()

    class Server:
        is_running = False

        def __init__(self, *args):
            pass

        def start(self, timeout, cancel):
            entered.set()
            released.wait(5)
            assert cancel.is_set()
            raise LlamaServerError("cancelled")

        def stop(self):
            released.set()

    monkeypatch.setattr(manager_module, "LlamaServer", Server)
    manager = manager_module.LlamaManager()
    monkeypatch.setattr(manager, "_pick_installed_variant", lambda: object())
    model = SimpleNamespace(id="test", name="test", path=Path("unused"), is_downloaded=lambda: True)
    errors = []

    def load():
        try:
            manager.ensure_server(model)
        except LlamaServerError as exc:
            errors.append(str(exc))

    loading = threading.Thread(target=load)
    closing = threading.Thread(target=manager.shutdown)
    loading.start()
    assert entered.wait(2)
    closing.start()
    closing.join(1)
    stopped_promptly = not closing.is_alive()
    released.set()
    loading.join(2)
    closing.join(2)
    assert stopped_promptly, "Shutdown waited on the model-loading lock"
    assert errors and not loading.is_alive()
    with pytest.raises(LlamaServerError, match="程序正在退出"):
        manager.ensure_server(model)


def test_cancelled_start_never_spawns_a_child(monkeypatch):
    cancel = threading.Event()
    cancel.set()
    server = LlamaServer(SimpleNamespace(), Path("unused"))
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("spawned after shutdown"))
    with pytest.raises(LlamaServerError, match="程序正在退出"):
        server.start(cancel=cancel)


def test_failed_warmup_does_not_orphan_child(monkeypatch):
    stopped = []

    class Server:
        def __init__(self, *a):
            pass

        def start(self, **kwargs):
            pass

        def warmup(self):
            raise ValueError("warmup failure")

        def stop(self):
            stopped.append(True)

    monkeypatch.setattr(manager_module, "LlamaServer", Server)
    manager = manager_module.LlamaManager()
    monkeypatch.setattr(manager, "_pick_installed_variant", lambda: object())
    model = SimpleNamespace(id="test", name="test", path=Path("unused"), is_downloaded=lambda: True)
    with pytest.raises(ValueError, match="warmup failure"):
        manager.ensure_server(model)
    assert stopped == [True]
    assert manager._server is None


def process_alive(pid):
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x00100000, False, pid)
    if not handle:
        return False
    try:
        return kernel.WaitForSingleObject(handle, 0) == 258
    finally:
        kernel.CloseHandle(handle)


@pytest.mark.parametrize("scenario", ["idle", "capture", "prepare", "translate", "download"])
def test_close_exits_process_and_owned_child(scenario, tmp_path):
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "PRTSBOX_DATA_DIR": str(tmp_path)}
    started = time.monotonic()
    try:
        result = subprocess.run([sys.executable, __file__, "--child", scenario, str(tmp_path)],
                                env=env, capture_output=True, timeout=12)
        assert result.returncode == 0, result.stderr.decode(errors="replace")
        assert time.monotonic() - started < 10
        assert (tmp_path / "config.json").is_file()
        assert (tmp_path / "child-stopped").is_file()
        assert not process_alive(int((tmp_path / "child.pid").read_text()))
    finally:
        # Only this test's explicitly recorded child, if a regression orphaned it.
        pid_file = tmp_path / "child.pid"
        if pid_file.exists() and process_alive(int(pid_file.read_text())):
            import signal
            os.kill(int(pid_file.read_text()), signal.SIGTERM)


def run_child(scenario, directory):
    from concurrent.futures import ThreadPoolExecutor
    import numpy as np
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QWidget
    from prtsbox import pipeline
    from prtsbox.config import ConfigStore
    from prtsbox.models import OcrItem, WindowInfo
    from prtsbox.ui import main_window

    entered = threading.Event()
    child = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(60)"],
                             creationflags=subprocess.CREATE_NO_WINDOW)
    (directory / "child.pid").write_text(str(child.pid))

    def blocked():
        entered.set()
        time.sleep(60)

    class Manager:
        running_model_id = ""

        def is_runtime_installed(self, *args):
            return True

        def is_server_running(self):
            return True

        def ensure_server(self, model):
            blocked()

        def shutdown(self):
            child.terminate()
            child.wait(timeout=2)
            (directory / "child-stopped").write_text("ok")

    class Translator:
        def preflight(self):
            pass

        def translate_batch(self, texts):
            with ThreadPoolExecutor(max_workers=1) as executor:
                executor.submit(blocked).result()
            return texts

    def capture(_):
        if scenario == "capture":
            blocked()
        return np.full((100, 200, 3), 150, np.uint8)

    line = OcrItem(((0, 0), (100, 0), (100, 20), (0, 20)), "Loading...", .99)
    pipeline.capture_window = capture
    pipeline.OcrService = lambda **kw: SimpleNamespace(using_openvino=False, recognize=lambda f: [line])
    pipeline.create_translator = lambda *a, **kw: Translator()
    main_window.create_translator = lambda *a, **kw: Translator()
    from prtsbox import win32
    win32.get_window_info = (
        lambda hwnd: WindowInfo(hwnd, "test", 0, 0, 200, 100)
    )
    main_window.llama.resolve_model = lambda _: SimpleNamespace(name="test")
    app = QApplication([])
    window = main_window.MainWindow(ConfigStore(), Manager())
    window._selected_window = WindowInfo(1, "test", 0, 0, 200, 100)
    # Another top-level window must not keep the app alive after main close.
    auxiliary = QWidget()
    auxiliary.show()
    window.show()
    app.aboutToQuit.connect(window.shutdown)

    def begin():
        if scenario == "idle":
            entered.set()
        elif scenario == "download":
            from prtsbox.ui.settings_dialog import SettingsDialog
            main_window.llama.recommend_variant = lambda: main_window.llama.RUNTIME_VARIANTS[0]
            dialog = SettingsDialog(window._config, window._llama, window)
            dialog._start_task(lambda cancel, progress: blocked(), kind="runtime", target=None, on_done=lambda: None)
        elif scenario == "prepare":
            window.start()
        else:
            window._running = True
            window._tick()

    def close_when_busy():
        if entered.is_set():
            timer.stop()
            window.close()

    QTimer.singleShot(150, begin)
    timer = QTimer()
    timer.timeout.connect(close_when_busy)
    timer.start(20)
    return app.exec()


if __name__ == "__main__":
    sys.exit(run_child(sys.argv[2], Path(sys.argv[3])))
