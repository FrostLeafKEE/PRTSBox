"""JSON configuration store.

The file is UTF-8 JSON and is meant to be hand editable.  Secrets are never
written in the clear: :meth:`ConfigStore.set_secret` encrypts with DPAPI and
stores base64 under a ``*_dpapi`` key, while :meth:`ConfigStore.get_secret`
transparently decrypts.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

from . import dpapi, paths

_SECRET_SUFFIX = "_dpapi"

DEFAULTS: dict[str, Any] = {
    "engine": "local",
    "source_language": "auto",
    "target_language": "zh-CN",
    "layout_mode": "below",
    "overlay_font_size": 14,
    "show_latency": False,
    "show_source_text": False,
    # The overlay is excluded from screen capture by default, so translations
    # stay out of screenshots, recordings and screen shares.  Users who want
    # them captured - to show the translation on a stream, for instance - opt
    # back in here.  Recognition reads the target's own render, so this can
    # never turn into a translation feedback loop.
    "overlay_capturable": False,
    "skip_chinese": True,
    # Restrict recognition to the bottom slice of the target window.  Text-heavy
    # games usually put the dialogue in a box at the bottom, and reading the
    # whole window would also pick up lettering on artwork and clothing.
    "region_bottom_only": False,
    "region_bottom_percent": 30,
    "theme": "dark",
    "ui_language": "zh",
    "local_model": "hy-mt2-1.8b",
    "local_runtime_variant": "auto",
    "ocr_backend": "auto",
    # Which hosts downloads may use.  Reachability varies so much between
    # networks that guessing is worse than letting the user decide.
    "download_source": "auto",
    "openai_base_url": "https://api.openai.com/v1",
    "openai_model": "gpt-4o-mini",
    "translation_platform": "azure",
    "azure_endpoint": "https://api.cognitive.microsofttranslator.com",
    "azure_region": "",
    "deepl_plan": "free",
    "baidu_app_id": "",
    "window_hwnd": 0,
    "window_title": "",
}

# Engines that no longer exist in this build.  A config carried over from a
# previous install must not leave the app pointing at a backend it cannot
# construct.
_RETIRED_ENGINES = {"baidu", "baidu_llm", "tencent"}

_ENGINE_ALIASES = {"local_model": "local", "llama": "local", "openai_compatible": "openai"}

# Bounds for the bottom-region slice.  Below 10% there is rarely room for a line
# of dialogue; 100% is the same as not restricting at all.
REGION_PERCENT_MIN = 10
REGION_PERCENT_MAX = 100


class ConfigStore:
    """Thread-safe, atomically persisted settings."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or paths.config_path()
        self._lock = threading.RLock()
        self._data: dict[str, Any] = dict(DEFAULTS)
        self._logger = logging.getLogger("prtsbox.config")

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> dict[str, Any]:
        with self._lock:
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                self._data = dict(DEFAULTS)
                return dict(self._data)
            except (OSError, ValueError) as exc:
                # A corrupt config must not stop the app from starting; fall
                # back to defaults and keep the bad file for the user to look at.
                self._logger.warning("配置文件读取失败，使用默认值：%s", exc)
                self._data = dict(DEFAULTS)
                return dict(self._data)

            if not isinstance(raw, dict):
                self._data = dict(DEFAULTS)
                return dict(self._data)

            merged = dict(DEFAULTS)
            merged.update(raw)
            merged["engine"] = self._normalise_engine(merged.get("engine"))
            merged["ui_language"] = "en" if merged.get("ui_language") == "en" else "zh"
            merged["layout_mode"] = str(merged.get("layout_mode", "below"))
            merged["overlay_font_size"] = _clamp_int(merged.get("overlay_font_size"), 9, 28, 14)
            merged["region_bottom_percent"] = _clamp_int(
                merged.get("region_bottom_percent"), REGION_PERCENT_MIN, REGION_PERCENT_MAX, 30
            )
            merged["download_source"] = _normalise_source(merged.get("download_source"))
            self._data = merged
            return dict(self._data)

    @staticmethod
    def _normalise_engine(value: object) -> str:
        engine = _ENGINE_ALIASES.get(str(value), str(value))
        return "local" if engine in _RETIRED_ENGINES or engine not in {"local", "openai", "platform"} else engine

    def save(self) -> None:
        with self._lock:
            payload = json.dumps(self._data, ensure_ascii=False, indent=2)
            temporary = self._path.with_suffix(".json.tmp")
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                # Write-then-replace so a crash mid-write cannot truncate the
                # live config.
                temporary.write_text(payload, encoding="utf-8")
                os.replace(temporary, self._path)
            except OSError as exc:
                self._logger.error("配置保存失败：%s", exc)

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._data.get(key, DEFAULTS.get(key, default))

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = value

    def update(self, **values: Any) -> None:
        with self._lock:
            self._data.update(values)

    def as_dict(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._data)

    # -- secrets ---------------------------------------------------------

    def set_secret(self, key: str, value: str) -> None:
        with self._lock:
            storage_key = f"{key}{_SECRET_SUFFIX}"
            if not value:
                self._data.pop(storage_key, None)
                return
            try:
                self._data[storage_key] = base64.b64encode(dpapi.protect(value)).decode("ascii")
            except OSError as exc:
                self._logger.error("凭据加密失败：%s", exc)

    def get_secret(self, key: str) -> str:
        with self._lock:
            stored = self._data.get(f"{key}{_SECRET_SUFFIX}")
        if not isinstance(stored, str) or not stored:
            return ""
        try:
            return dpapi.unprotect(base64.b64decode(stored))
        except (ValueError, OSError):
            return ""

    def has_secret(self, key: str) -> bool:
        with self._lock:
            return bool(self._data.get(f"{key}{_SECRET_SUFFIX}"))


def _clamp_int(value: object, low: int, high: int, fallback: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    return max(low, min(high, number))


def _normalise_source(value: object) -> str:
    """Coerce the download source into a known value.

    Imported lazily to keep the config module free of the llama package, which
    pulls in far more than a settings file should need.
    """
    from .llama.catalog import normalise_source

    return normalise_source(value)
