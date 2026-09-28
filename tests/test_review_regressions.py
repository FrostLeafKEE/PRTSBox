import ctypes
import logging
from types import SimpleNamespace

import numpy as np

from prtsbox import capture, win32
from prtsbox.models import WindowInfo
from prtsbox.ocr import OcrService


def test_capture_rechecks_geometry_after_target_moves(monkeypatch):
    old = WindowInfo(1, "test", 11, 21, 2, 2)
    fresh = WindowInfo(1, "test", 101, 201, 2, 2)
    pixels = np.arange(48, dtype=np.uint8).reshape(4, 4, 3)
    monkeypatch.setattr(capture.user32, "IsIconic", lambda _: False)
    monkeypatch.setattr(capture, "window_rect", lambda _: (100, 200, 4, 4))
    monkeypatch.setattr(capture, "get_window_info", lambda _: fresh, raising=False)
    monkeypatch.setattr(capture, "_print_window", lambda *args: pixels)
    result = capture.capture_window(old)
    assert result is not None
    np.testing.assert_array_equal(result, pixels[1:3, 1:3])


def test_ocr_boxes_are_not_rescaled_when_image_resize_is_skipped():
    service = OcrService.__new__(OcrService)
    service._logger = logging.getLogger("test")
    service._confidence_threshold = .5
    box = ((100, 20), (300, 20), (300, 40), (100, 40))
    service._engine = SimpleNamespace(ocr=lambda image, **kw: [[(box, ("Hello", .99))]])
    result = service.recognize(np.ones((500, 1250, 3), dtype=np.uint8))
    assert result[0].box == box


def test_holding_f8_only_toggles_once_until_release(monkeypatch):
    calls = []
    monkeypatch.setattr(win32, "_hook_callback", lambda: calls.append(1))
    monkeypatch.setattr(win32, "_hotkey_down", False, raising=False)
    monkeypatch.setattr(win32.user32, "CallNextHookEx", lambda *args: 0)
    event = ctypes.pointer(win32._KeyboardHookData(vkCode=win32.VK_F8))
    for message in [0x100, 0x100, 0x100, 0x101, 0x100, 0x101]:
        win32._hook_entry(0, message, event)
    assert len(calls) == 2


def test_overlay_never_makes_an_ordinary_target_topmost(monkeypatch):
    calls = []
    monkeypatch.setattr(win32, "is_topmost", lambda hwnd: False)
    monkeypatch.setattr(win32.user32, "GetWindow", lambda *args: None)
    monkeypatch.setattr(win32.user32, "SetWindowPos", lambda *args: calls.append(args) or True)
    win32.place_overlay_above(20, WindowInfo(10, "test", 0, 0, 800, 600))
    assert calls[-1][1].value != ctypes.c_void_p(-1).value


def test_overlay_does_not_insert_after_itself(monkeypatch):
    calls = []
    monkeypatch.setattr(win32, "is_topmost", lambda hwnd: False)
    monkeypatch.setattr(win32.user32, "GetWindow", lambda *args: 20)
    monkeypatch.setattr(win32.user32, "SetWindowPos", lambda *args: calls.append(args) or True)
    win32.place_overlay_above(20, WindowInfo(10, "test", 0, 0, 800, 600))
    assert calls[-1][-1] & 0x0004  # SWP_NOZORDER: it is already above the target.
