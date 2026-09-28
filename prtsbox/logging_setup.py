"""Logging setup: rotating file log plus console output."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from . import paths

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_MAX_BYTES = 2 * 1024 * 1024
_BACKUPS = 3


def setup(level: int = logging.INFO) -> logging.Logger:
    root = logging.getLogger()
    if root.handlers:
        return root
    root.setLevel(level)

    formatter = logging.Formatter(_FORMAT, datefmt="%H:%M:%S")

    try:
        handler = RotatingFileHandler(
            paths.logs_dir() / "prtsbox.log",
            maxBytes=_MAX_BYTES,
            backupCount=_BACKUPS,
            encoding="utf-8",
        )
        handler.setFormatter(formatter)
        root.addHandler(handler)
    except OSError:
        # An unwritable log directory must not stop the app from starting.
        pass

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(formatter)
    root.addHandler(console)

    # Third-party loggers are noisy at INFO and drown out the pipeline logs.
    for noisy in ("onnxocr", "openvino", "PIL"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return root
