"""Windows DPAPI wrappers used to encrypt stored API keys.

The configuration file is plain JSON and is meant to be readable and hand
editable, so anything secret is encrypted with the user's Windows credentials
before it is written.  A stolen config file is then useless on another machine
and useless to another user account on the same machine.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

_CRYPTPROTECT_UI_FORBIDDEN = 0x01


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


_crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_crypt32.CryptProtectData.argtypes = [
    ctypes.POINTER(_DataBlob),
    wintypes.LPCWSTR,
    ctypes.POINTER(_DataBlob),
    ctypes.c_void_p,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(_DataBlob),
]
_crypt32.CryptProtectData.restype = wintypes.BOOL
_crypt32.CryptUnprotectData.argtypes = [
    ctypes.POINTER(_DataBlob),
    ctypes.POINTER(wintypes.LPWSTR),
    ctypes.POINTER(_DataBlob),
    ctypes.c_void_p,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(_DataBlob),
]
_crypt32.CryptUnprotectData.restype = wintypes.BOOL
_kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
_kernel32.LocalFree.restype = wintypes.HLOCAL

# Ties the ciphertext to this application: another program cannot decrypt a
# config it happens to read without also knowing this string.
_ENTROPY = b"PRTSBox.credentials.v1"


def _to_blob(data: bytes) -> _DataBlob:
    buffer = ctypes.create_string_buffer(data, len(data))
    return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))


def _from_blob(blob: _DataBlob) -> bytes:
    try:
        return ctypes.string_at(blob.pbData, blob.cbData)
    finally:
        _kernel32.LocalFree(blob.pbData)


def protect(plaintext: str) -> bytes:
    """Encrypt a string for the current Windows user."""
    if not plaintext:
        return b""
    blob_in = _to_blob(plaintext.encode("utf-8"))
    blob_entropy = _to_blob(_ENTROPY)
    blob_out = _DataBlob()
    if not _crypt32.CryptProtectData(
        ctypes.byref(blob_in),
        "PRTSBox",
        ctypes.byref(blob_entropy),
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(blob_out),
    ):
        raise OSError(ctypes.get_last_error(), "CryptProtectData 失败")
    return _from_blob(blob_out)


def unprotect(ciphertext: bytes) -> str:
    """Decrypt a string produced by :func:`protect`.

    Returns an empty string when the blob cannot be decrypted - a config copied
    from another machine or user account should degrade to "no key configured"
    rather than crash the application.
    """
    if not ciphertext:
        return ""
    blob_in = _to_blob(ciphertext)
    blob_entropy = _to_blob(_ENTROPY)
    blob_out = _DataBlob()
    if not _crypt32.CryptUnprotectData(
        ctypes.byref(blob_in),
        None,
        ctypes.byref(blob_entropy),
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(blob_out),
    ):
        return ""
    try:
        return _from_blob(blob_out).decode("utf-8", errors="replace")
    except (OSError, ValueError):
        return ""
