"""Filesystem layout.

Everything the application writes stays under a single ``data`` directory next
to the app, so an install is portable and nothing leaks into
``%LOCALAPPDATA%``.  The downloaded llama.cpp runtime and the GGUF models live
there too, which keeps them out of the way of code updates.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_ENV_DATA_DIR = "PRTSBOX_DATA_DIR"


def app_dir() -> Path:
    """Root of the application: the repository when run from source."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    override = os.environ.get(_ENV_DATA_DIR, "").strip()
    root = Path(override).expanduser() if override else app_dir() / "data"
    root.mkdir(parents=True, exist_ok=True)
    return root


def models_dir() -> Path:
    path = data_dir() / "models"
    path.mkdir(parents=True, exist_ok=True)
    return path


def runtime_dir() -> Path:
    path = data_dir() / "runtime"
    path.mkdir(parents=True, exist_ok=True)
    return path


def logs_dir() -> Path:
    path = data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_path() -> Path:
    return data_dir() / "config.json"


def model_path(filename: str) -> Path:
    return models_dir() / filename


def runtime_root(variant: str) -> Path:
    """Directory a runtime archive is unpacked into."""
    return runtime_dir() / variant
