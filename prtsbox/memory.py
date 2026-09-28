"""Process memory reporting.

Used to bound the footprint of native inference code.  ``PagefileUsage`` (private
bytes) is the figure that matters rather than the working set: Windows trims
working-set pages under pressure, so that number can fall while the process is
in fact still holding the memory.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes


class _ProcessMemoryCounters(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


# Explicit signatures are required: without them ctypes marshals the process
# HANDLE as a 32-bit int, the call fails, and the function silently reports 0.
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_psapi = ctypes.WinDLL("psapi", use_last_error=True)
_kernel32.GetCurrentProcess.restype = wintypes.HANDLE
_kernel32.GetCurrentProcess.argtypes = []
_psapi.GetProcessMemoryInfo.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(_ProcessMemoryCounters),
    wintypes.DWORD,
]
_psapi.GetProcessMemoryInfo.restype = wintypes.BOOL


def private_mb() -> float:
    """Private bytes of this process in MiB, or 0.0 when unavailable."""
    counters = _ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    handle = _kernel32.GetCurrentProcess()
    if not _psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
        return 0.0
    return counters.PagefileUsage / (1024 * 1024)


def working_set_mb() -> float:
    """Working set of this process in MiB, or 0.0 when unavailable."""
    counters = _ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    handle = _kernel32.GetCurrentProcess()
    if not _psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
        return 0.0
    return counters.WorkingSetSize / (1024 * 1024)


# PROCESS_QUERY_LIMITED_INFORMATION is enough to read the memory counters and,
# unlike PROCESS_QUERY_INFORMATION, does not require elevation for a process
# this user already owns.
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.CloseHandle.restype = wintypes.BOOL


def process_private_mb(pid: int) -> float:
    """Private bytes of another process in MiB, or 0.0 when unavailable.

    Used to report the llama-server child's footprint.  Reading it directly
    avoids shelling out to PowerShell, which is slow, adds a dependency on
    command availability, and - as this was first written - can silently return
    nothing and leave a diagnostic column reading a misleading zero.
    """
    if pid <= 0:
        return 0.0
    handle = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return 0.0
    try:
        counters = _ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        if not _psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            return 0.0
        return counters.PagefileUsage / (1024 * 1024)
    finally:
        _kernel32.CloseHandle(handle)
