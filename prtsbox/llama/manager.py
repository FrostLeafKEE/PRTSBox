"""Coordinates runtime installation, model downloads and the server process."""

from __future__ import annotations

import logging
import shutil
import threading
from collections.abc import Callable
from pathlib import Path

from .. import paths
from ..memory import process_private_mb
from . import catalog
from .catalog import (
    LocalModel,
    RuntimeVariant,
    default_model,
    find_model,
    find_variant,
    installed_variants,
    recommend_variant,
    resolve_variant,
)
from .download import DownloadCancelled, DownloadError, DownloadProgress, download_file
from .server import LlamaServer, LlamaServerError, install_runtime

ProgressCallback = Callable[[DownloadProgress], None]


class LlamaManager:
    """Single owner of everything under ``data/runtime`` and ``data/models``.

    Only one model is served at a time; switching models stops the running
    server first, because two models would not both fit in VRAM alongside each
    other and the request latency of a cold start is already handled by the
    warm-up step.
    """

    def __init__(self, source_preference: str = catalog.SOURCE_AUTO) -> None:
        self._server: LlamaServer | None = None
        self._server_model_id: str = ""
        self._lock = threading.RLock()
        self._closing = threading.Event()
        self._logger = logging.getLogger("prtsbox.llama")
        self._source = catalog.normalise_source(source_preference)

    @property
    def source_preference(self) -> str:
        return self._source

    def set_source_preference(self, preference: str) -> None:
        """Choose which hosts downloads may use; affects the next download."""
        chosen = catalog.normalise_source(preference)
        if chosen != self._source:
            self._logger.info("下载源切换为 %s", chosen)
        self._source = chosen

    # -- status ----------------------------------------------------------

    @property
    def running_model_id(self) -> str:
        """Id of the model the server currently holds, or "" when stopped."""
        server = self._server
        return self._server_model_id if server and server.is_running else ""

    @property
    def base_url(self) -> str:
        with self._lock:
            return self._server.base_url if self._server else ""

    def is_server_running(self) -> bool:
        # Status is polled by the GUI while ensure_server holds the lock for
        # model loading. A transient snapshot is preferable to freezing the UI.
        server = self._server
        return bool(server and server.is_running)

    def server_pid(self) -> int:
        """Process id of the running llama-server, or 0 when stopped."""
        server = self._server
        return server.pid if server else 0

    def server_memory_mb(self) -> float:
        """Resident private bytes of the llama-server child, in MiB."""
        return process_private_mb(self.server_pid())

    def server_log_path(self) -> Path | None:
        server = self._server
        return server.log_path if server else None

    def is_runtime_installed(self, variant: RuntimeVariant | None = None) -> bool:
        if variant is not None:
            return variant.is_installed()
        return bool(installed_variants())

    # -- installation ----------------------------------------------------

    def install_runtime(
        self,
        variant: RuntimeVariant,
        *,
        on_progress: ProgressCallback | None = None,
        cancel: threading.Event | None = None,
    ) -> None:
        """Download and unpack a llama.cpp runtime.

        Every mirror is offered to the downloader, which walks the list until
        one answers and verifies the result before use.  No size or digest is
        pinned for these archives - GitHub's release assets are not stable
        enough to hard-code them - so the integrity check is that the archive
        unpacks and yields a ``llama-server.exe``; see
        :func:`prtsbox.llama.server.install_runtime`.
        """
        archive = paths.runtime_dir() / variant.asset
        sources = variant.sources(self._source)
        for label, url in variant.sources_with_labels(self._source):
            self._logger.info("运行时下载源 %s：%s", label, url)
        download_file(
            sources,
            archive,
            expected_size=variant.size_bytes,
            on_progress=on_progress,
            cancel=cancel,
        )
        install_runtime(variant, archive)
        # The archive has served its purpose; keeping it would waste 30-300 MB.
        archive.unlink(missing_ok=True)

    def download_model(
        self,
        model: LocalModel,
        *,
        on_progress: ProgressCallback | None = None,
        cancel: threading.Event | None = None,
    ) -> Path:
        """Fetch a GGUF model, verifying its published SHA-256."""
        target = model.path
        # A leftover file of the wrong size (interrupted copy, manual drop)
        # must not be mistaken for a valid install.
        if target.exists() and not model.is_downloaded():
            self._logger.warning("已存在的模型文件大小不符，将重新下载：%s", target.name)
            target.unlink(missing_ok=True)
        return download_file(
            model.sources(self._source),
            target,
            expected_size=model.size_bytes,
            expected_sha256=model.sha256,
            on_progress=on_progress,
            cancel=cancel,
        )

    def delete_model(self, model: LocalModel) -> None:
        """Remove a downloaded model, stopping the server if it is in use.

        Models are independent: deleting one leaves the other fully usable, and
        the caller is expected to fall back to whatever is still installed.
        """
        with self._lock:
            if self._server_model_id == model.id:
                self.stop()
        try:
            model.path.unlink(missing_ok=True)
            self._logger.info("已删除模型：%s", model.filename)
        except OSError as exc:
            raise DownloadError(f"删除模型失败：{exc}") from exc
        # Clean up any partial file so a later download starts from zero.
        partial = model.path.with_name(model.path.name + ".part")
        partial.unlink(missing_ok=True)

    def uninstall_runtime(self, variant: RuntimeVariant) -> None:
        with self._lock:
            self.stop()
        shutil.rmtree(variant.root, ignore_errors=True)

    # -- serving ---------------------------------------------------------

    def ensure_server(self, model: LocalModel, timeout: float = 180.0) -> LlamaServer:
        """Start (or reuse) a server holding ``model``.

        Reuses the running process when it already serves the requested model,
        which is the common case: the pipeline calls this on every "start
        translating" but the model rarely changes.
        """
        with self._lock:
            if self._closing.is_set():
                raise LlamaServerError("程序正在退出")
            if self._server is not None and self._server.is_running:
                if self._server_model_id == model.id:
                    return self._server
                self._logger.info(
                    "切换模型 %s -> %s，重启 llama-server", self._server_model_id, model.id
                )
                self._stop_locked()

            if not model.is_downloaded():
                raise LlamaServerError(f"模型尚未下载：{model.name}")

            variant = self._pick_installed_variant()
            if variant is None:
                raise LlamaServerError("尚未安装本地推理运行时，请先在设置中下载")

            server = LlamaServer(variant, model.path)
            # Publish before loading so shutdown can stop a starting child
            # without waiting for this lock (startup may take minutes).
            self._server = server
            try:
                server.start(timeout=timeout, cancel=self._closing)
                if self._closing.is_set():
                    raise LlamaServerError("程序正在退出")
                server.warmup()
                if self._closing.is_set():
                    raise LlamaServerError("程序正在退出")
                self._server_model_id = model.id
                return server
            except Exception:
                server.stop()
                self._server = None
                self._server_model_id = ""
                raise

    def _pick_installed_variant(self) -> RuntimeVariant | None:
        """Prefer a variant the user asked for, falling back to anything installed."""
        installed = installed_variants()
        if not installed:
            return None
        preferred = recommend_variant()
        for variant in installed:
            if variant.id == preferred.id:
                return variant
        return installed[0]

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()

    @property
    def closing(self) -> bool:
        return self._closing.is_set()

    def shutdown(self) -> None:
        """Permanently close, including a child still loading under the lock."""
        self._closing.set()
        server = self._server
        if server is not None:
            server.stop()

    def _stop_locked(self) -> None:
        if self._server is not None:
            self._server.stop()
            self._server = None
        self._server_model_id = ""


def resolve_model(model_id: str) -> LocalModel:
    """Model for a config value, falling back to whatever is installed."""
    found = find_model(model_id)
    if found is not None and found.is_downloaded():
        return found
    return default_model()


__all__ = [
    "DownloadCancelled",
    "DownloadError",
    "LlamaManager",
    "LlamaServerError",
    "ProgressCallback",
    "find_variant",
    "resolve_model",
    "resolve_variant",
]
