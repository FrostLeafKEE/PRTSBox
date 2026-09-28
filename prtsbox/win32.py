"""Win32 integration: window enumeration, DPI, Z-order and the global hotkey.

Implemented with :mod:`ctypes` rather than pywin32 so the dependency list stays
short and the installed size stays small.
"""

from __future__ import annotations

import ctypes
import logging
import threading
from ctypes import wintypes

from .models import WindowInfo

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)

WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104
WM_KEYUP = 0x0101
WM_SYSKEYUP = 0x0105
VK_F8 = 0x77

GWL_STYLE = -16
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
WS_EX_APPWINDOW = 0x00040000
WS_EX_TOPMOST = 0x00000008

DWMWA_CLOAKED = 14
SWP_NOACTIVATE = 0x0010
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOOWNERZORDER = 0x0200
SWP_NOSENDCHANGING = 0x0400

# Sentinel for SetWindowPos: place the window at the top of the Z-order.
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
HWND_TOP = 0

WDA_NONE = 0x00000000
WDA_EXCLUDEFROMCAPTURE = 0x00000011

# SetWindowDisplayAffinity / capture exclusion needs Windows 10 2004+.
_WIN10_2004_BUILD = 19041


class _Rect(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


class _Point(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class _KeyboardHookData(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


_HOOKPROC = ctypes.CFUNCTYPE(
    ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, ctypes.POINTER(_KeyboardHookData)
)

user32.EnumWindows.argtypes = [ctypes.c_void_p, wintypes.LPARAM]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(_Rect)]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(_Rect)]
user32.GetWindowRect.restype = wintypes.BOOL
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(_Point)]
user32.GetForegroundWindow.restype = wintypes.HWND
user32.SetWindowPos.argtypes = [
    wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.c_int, wintypes.UINT,
]
user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]

_dwm_get_window_attribute = dwmapi.DwmGetWindowAttribute
_dwm_get_window_attribute.argtypes = [
    wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD
]


def enable_dpi_awareness() -> None:
    """Opt into per-monitor DPI v2 before any window exists.

    Without this the coordinates Win32 reports and the ones Qt draws with
    disagree on a scaled display, and every overlay lands in the wrong place.
    """
    try:
        # -4 == DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


def _is_cloaked(hwnd: int) -> bool:
    """UWP and suspended windows stay "visible" while showing nothing.

    Capturing one yields a black frame, so they are filtered out of the picker.
    """
    value = wintypes.DWORD()
    try:
        result = _dwm_get_window_attribute(
            wintypes.HWND(hwnd), DWMWA_CLOAKED, ctypes.byref(value), ctypes.sizeof(value)
        )
    except OSError:
        return False
    return result == 0 and value.value != 0


def _client_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    rect = _Rect()
    if not user32.GetClientRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
        return None
    origin = _Point(0, 0)
    if not user32.ClientToScreen(wintypes.HWND(hwnd), ctypes.byref(origin)):
        return None
    width = rect.right - rect.left
    height = rect.bottom - rect.top
    if width <= 0 or height <= 0:
        return None
    return origin.x, origin.y, width, height


def window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    """Whole-window rect in screen coordinates: (left, top, width, height).

    Distinct from the client rect: the difference between the two is the
    title bar and border, which is exactly the offset needed to crop a
    whole-window render down to the client area.
    """
    rect = _Rect()
    if not user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
        return None
    return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top


def get_window_info(hwnd: int) -> WindowInfo | None:
    """Current title and client area of a window, or ``None`` if it is gone."""
    if not hwnd or not user32.IsWindow(wintypes.HWND(hwnd)):
        return None
    length = user32.GetWindowTextLengthW(wintypes.HWND(hwnd))
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(wintypes.HWND(hwnd), buffer, length + 1)
    rect = _client_rect(hwnd)
    if rect is None:
        return None
    left, top, width, height = rect
    return WindowInfo(hwnd=hwnd, title=buffer.value, left=left, top=top, width=width, height=height)


def list_windows(exclude: set[int] | None = None) -> list[WindowInfo]:
    """Top-level windows a user could plausibly want to translate."""
    skip = exclude or set()
    found: list[WindowInfo] = []

    def visit(hwnd: int, _param: int) -> bool:
        if hwnd in skip:
            return True
        if not user32.IsWindowVisible(wintypes.HWND(hwnd)):
            return True
        if _is_cloaked(hwnd):
            return True
        length = user32.GetWindowTextLengthW(wintypes.HWND(hwnd))
        if length <= 0:
            return True
        style = user32.GetWindowLongW(wintypes.HWND(hwnd), GWL_EXSTYLE)
        # Tool windows are palette/utility shells, not something to translate.
        if style & WS_EX_TOOLWINDOW and not style & WS_EX_APPWINDOW:
            return True
        info = get_window_info(hwnd)
        if info is None or info.width < 80 or info.height < 60:
            return True
        found.append(info)
        return True

    callback = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)(visit)
    user32.EnumWindows(callback, 0)
    found.sort(key=lambda item: item.title.casefold())
    return found


def is_foreground(hwnd: int) -> bool:
    return int(user32.GetForegroundWindow() or 0) == hwnd


def is_topmost(hwnd: int) -> bool:
    return bool(user32.GetWindowLongW(wintypes.HWND(hwnd), GWL_EXSTYLE) & WS_EX_TOPMOST)


def place_overlay_above(overlay_hwnd: int, target: WindowInfo) -> bool:
    """Pin the overlay directly above the target, in position and in Z-order.

    Z-order needs care.  ``SetWindowPos``'s second argument names the window the
    positioned window should come *after*, so passing the target itself would put
    the overlay underneath it - invisible behind an opaque window.

    Keep the overlay in the target's topmost/non-topmost band. If it is
    already directly above the target, only move/resize it. Otherwise insert
    after the target's predecessor; at a band boundary use the band's top.

    ``SWP_NOACTIVATE`` keeps the overlay from stealing focus from the window
    being translated - without it the target would lose foreground on every
    sync and, for a game, minimise.
    """
    GW_HWNDPREV = 3
    user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetWindow.restype = wintypes.HWND

    target_topmost = is_topmost(target.hwnd)
    if not target_topmost and is_topmost(overlay_hwnd):
        user32.SetWindowPos(
            wintypes.HWND(overlay_hwnd), wintypes.HWND(HWND_NOTOPMOST), 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOOWNERZORDER,
        )
    flags = SWP_NOACTIVATE | SWP_NOOWNERZORDER | SWP_NOSENDCHANGING
    predecessor = user32.GetWindow(wintypes.HWND(target.hwnd), GW_HWNDPREV)
    if predecessor == overlay_hwnd:
        insert_after = wintypes.HWND(HWND_TOP)
        flags |= SWP_NOZORDER
    elif predecessor and (target_topmost or not is_topmost(predecessor)):
        insert_after = wintypes.HWND(predecessor)
    else:
        insert_after = wintypes.HWND(HWND_TOPMOST if target_topmost else HWND_TOP)

    return bool(
        user32.SetWindowPos(
            wintypes.HWND(overlay_hwnd),
            insert_after,
            target.left,
            target.top,
            target.width,
            target.height,
            flags,
        )
    )


def exclude_from_capture(hwnd: int) -> bool:
    """Hide a window from screen capture.

    Used so the translation overlay is never itself captured and re-translated
    into a feedback loop.
    """
    try:
        return bool(
            user32.SetWindowDisplayAffinity(wintypes.HWND(hwnd), WDA_EXCLUDEFROMCAPTURE)
        )
    except (AttributeError, OSError):
        return False


def include_in_capture(hwnd: int) -> bool:
    try:
        return bool(user32.SetWindowDisplayAffinity(wintypes.HWND(hwnd), WDA_NONE))
    except (AttributeError, OSError):
        return False


# -- global hotkey ------------------------------------------------------

_hook_handle = 0
_hook_proc = None
_hook_callback = None
_hotkey_down = False
_hook_lock = threading.Lock()


def _hook_entry(n_code, w_param, event):
    """Low-level keyboard hook.

    A ``WH_KEYBOARD_LL`` hook sees keys before the focused application does,
    which is what makes the hotkey work while a game holds exclusive input -
    a ``RegisterHotKey`` binding would never fire there.

    This runs on the thread that installed the hook and must return within the
    system timeout, so the callback is expected to marshal to the UI thread
    rather than do work inline.
    """
    global _hotkey_down
    if n_code == 0:
        try:
            if event.contents.vkCode == VK_F8:
                if w_param in (WM_KEYUP, WM_SYSKEYUP):
                    _hotkey_down = False
                elif w_param in (WM_KEYDOWN, WM_SYSKEYDOWN) and not _hotkey_down:
                    _hotkey_down = True
                    if _hook_callback is not None:
                        _hook_callback()
        except (ValueError, OSError):
            pass
    return user32.CallNextHookEx(0, n_code, w_param, ctypes.cast(event, ctypes.c_void_p))


def install_hotkey_hook(callback) -> bool:
    """Start listening for F8 globally.  Returns False if the hook was refused."""
    global _hook_handle, _hook_proc, _hook_callback
    with _hook_lock:
        if _hook_handle:
            _hook_callback = callback
            return True
        _hook_callback = callback
        # The CFUNCTYPE object must outlive the hook or the trampoline is
        # collected and Windows calls into freed memory.
        _hook_proc = _HOOKPROC(_hook_entry)
        user32.SetWindowsHookExW.restype = wintypes.HHOOK
        user32.SetWindowsHookExW.argtypes = [
            ctypes.c_int, _HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD
        ]
        _hook_handle = user32.SetWindowsHookExW(WH_KEYBOARD_LL, _hook_proc, None, 0)
        if not _hook_handle:
            logging.getLogger("prtsbox.hotkeys").warning(
                "F8 全局热键注册失败：%s", ctypes.get_last_error()
            )
            _hook_proc = None
            _hook_callback = None
            return False
        return True


def uninstall_hotkey_hook() -> None:
    global _hook_handle, _hook_proc, _hook_callback, _hotkey_down
    with _hook_lock:
        if _hook_handle:
            user32.UnhookWindowsHookEx(wintypes.HHOOK(_hook_handle))
        _hook_handle = 0
        _hook_proc = None
        _hook_callback = None
        _hotkey_down = False
