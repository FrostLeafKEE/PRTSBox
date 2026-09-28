"""Frame acquisition for the target window.

The capture reads the window's *own* content via ``PrintWindow`` rather than
copying the screen region it occupies.  That choice is forced by the design:
the translation overlay sits on top of the target, so a screen-region grab would
photograph our own translations and feed them back into OCR.  Rendering the
window in isolation makes the overlay structurally invisible to capture,
without depending on capture-exclusion support.

``PW_RENDERFULLCONTENT`` (0x2) is what makes this work for composited and
DirectComposition windows; without it many modern applications come back blank.
"""

from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes

import numpy as np

from .models import WindowInfo
from .win32 import get_window_info, user32, window_rect

gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

PW_RENDERFULLCONTENT = 0x00000002
DIB_RGB_COLORS = 0
BI_RGB = 0

SRCCOPY = 0x00CC0020


class _BitmapInfoHeader(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class _BitmapInfo(ctypes.Structure):
    _fields_ = [("bmiHeader", _BitmapInfoHeader), ("bmiColors", wintypes.DWORD * 3)]


gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.GetDIBits.argtypes = [
    wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
    ctypes.c_void_p, ctypes.POINTER(_BitmapInfo), wintypes.UINT,
]
user32.GetWindowDC.argtypes = [wintypes.HWND]
user32.GetWindowDC.restype = wintypes.HDC
user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
user32.PrintWindow.restype = wintypes.BOOL
user32.IsIconic.argtypes = [wintypes.HWND]

_logger = logging.getLogger("prtsbox.capture")


def capture_window(window: WindowInfo) -> np.ndarray | None:
    """Return the window's client area as a BGR array, or ``None`` on failure."""
    if user32.IsIconic(wintypes.HWND(window.hwnd)):
        # A minimised window has no composited surface to render.
        return None

    fresh = get_window_info(window.hwnd)
    if fresh is None:
        return None
    window = fresh
    window_box = window_rect(window.hwnd)
    if window_box is None:
        return None
    win_left, win_top, win_width, win_height = window_box
    if win_width <= 0 or win_height <= 0:
        return None

    frame = _print_window(window.hwnd, win_width, win_height)
    if frame is None:
        return None

    # PrintWindow renders the whole window, frame included, while everything
    # downstream (and the overlay position) is expressed in client coordinates.
    offset_x = window.left - win_left
    offset_y = window.top - win_top
    height, width = frame.shape[:2]
    left = max(0, min(offset_x, width))
    top = max(0, min(offset_y, height))
    right = max(left, min(offset_x + window.width, width))
    bottom = max(top, min(offset_y + window.height, height))
    cropped = frame[top:bottom, left:right]
    if cropped.size == 0:
        return None
    return np.ascontiguousarray(cropped)


def _render_window(hwnd: int, memory_dc: int) -> bool:
    """Render the window into a device context.

    Retries without ``PW_RENDERFULLCONTENT``: the flag is what makes composited
    and DirectComposition windows render at all, but older or protected windows
    reject it, and a plain render still beats returning nothing.
    """
    if user32.PrintWindow(wintypes.HWND(hwnd), memory_dc, PW_RENDERFULLCONTENT):
        return True
    return bool(user32.PrintWindow(wintypes.HWND(hwnd), memory_dc, 0))


def _print_window(hwnd: int, width: int, height: int) -> np.ndarray | None:
    window_dc = user32.GetWindowDC(wintypes.HWND(hwnd))
    if not window_dc:
        return None
    memory_dc = 0
    bitmap = 0
    previous = 0
    try:
        memory_dc = gdi32.CreateCompatibleDC(window_dc)
        if not memory_dc:
            return None
        bitmap = gdi32.CreateCompatibleBitmap(window_dc, width, height)
        if not bitmap:
            return None
        previous = gdi32.SelectObject(memory_dc, bitmap)

        if not _render_window(hwnd, memory_dc):
            return None

        info = _BitmapInfo()
        info.bmiHeader.biSize = ctypes.sizeof(_BitmapInfoHeader)
        info.bmiHeader.biWidth = width
        # A negative height requests a top-down bitmap, matching array order.
        info.bmiHeader.biHeight = -height
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = BI_RGB

        buffer = ctypes.create_string_buffer(width * height * 4)
        # GetDIBits requires the bitmap to be deselected from its DC.
        gdi32.SelectObject(memory_dc, previous)
        previous = 0
        copied = gdi32.GetDIBits(
            memory_dc, bitmap, 0, height, buffer, ctypes.byref(info), DIB_RGB_COLORS
        )
        if copied != height:
            return None

        pixels = np.frombuffer(buffer, dtype=np.uint8).reshape(height, width, 4)
        # BGRA in memory; OpenCV and the OCR model both want BGR.
        return np.ascontiguousarray(pixels[:, :, :3])
    except OSError as exc:
        _logger.warning("窗口截图失败：%s", exc)
        return None
    finally:
        if previous:
            gdi32.SelectObject(memory_dc, previous)
        if bitmap:
            gdi32.DeleteObject(bitmap)
        if memory_dc:
            gdi32.DeleteDC(memory_dc)
        if window_dc:
            user32.ReleaseDC(wintypes.HWND(hwnd), window_dc)


def is_probably_blank(frame: np.ndarray) -> bool:
    """Detect the all-black frames that protected windows produce.

    A black frame is indistinguishable from "nothing to translate", so it is
    treated as a capture failure and reported to the UI.
    """
    if frame.size == 0:
        return True
    return bool(frame.max() < 8)
