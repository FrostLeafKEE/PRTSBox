"""Resumable, verifiable downloads.

Model files are up to 4.3 GB and runtime archives come from GitHub, so a
single dropped connection must not throw away progress.  Downloads therefore
stream into ``<name>.part`` and are resumed with a Range request when the
server supports it, then SHA-256 verified before the file is moved into place.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import requests

_CHUNK = 1024 * 256
# Progress is reported to the UI thread; flooding it with per-chunk signals
# would cost more than the download itself.
_REPORT_INTERVAL = 0.15


class DownloadError(RuntimeError):
    pass


class DownloadCancelled(DownloadError):
    pass


@dataclass(frozen=True)
class DownloadProgress:
    downloaded: int
    total: int
    bytes_per_second: float

    @property
    def fraction(self) -> float:
        if self.total <= 0:
            return 0.0
        return min(1.0, self.downloaded / self.total)

    @property
    def eta_seconds(self) -> float:
        if self.bytes_per_second <= 0 or self.total <= 0:
            return 0.0
        return max(0.0, (self.total - self.downloaded) / self.bytes_per_second)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def _partial_path(dest: Path) -> Path:
    return dest.with_name(dest.name + ".part")


def _existing_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _seed_digest(path: Path) -> tuple[hashlib._Hash, int]:
    """Hash whatever a previous attempt already wrote, so a resumed download
    can still be verified end to end."""
    digest = hashlib.sha256()
    total = 0
    if not path.exists():
        return digest, 0
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(block)
            total += len(block)
    return digest, total


def _open_stream(
    url: str,
    offset: int,
    timeout: float,
) -> tuple[requests.Response, bool]:
    """Open ``url``, asking to resume at ``offset``.

    Returns the response and whether the server honoured the range.  A server
    that ignores Range replies 200 with the whole file, which means the partial
    data has to be discarded rather than appended to.
    """
    headers = {"Range": f"bytes={offset}-"} if offset > 0 else {}
    response = requests.get(
        url,
        stream=True,
        headers=headers,
        timeout=timeout,
        allow_redirects=True,
    )
    response.raise_for_status()
    if offset > 0 and response.status_code != 206:
        return response, False
    return response, True


def download_file(
    urls: Sequence[str],
    dest: Path,
    *,
    expected_size: int = 0,
    expected_sha256: str = "",
    on_progress: Callable[[DownloadProgress], None] | None = None,
    cancel: threading.Event | None = None,
    timeout: float = 30.0,
) -> Path:
    """Download the first reachable URL to ``dest``.

    Skips the work entirely when ``dest`` already matches the expected size and
    digest, which is what makes re-running an interrupted install cheap.
    """
    logger = logging.getLogger("prtsbox.download")
    dest.parent.mkdir(parents=True, exist_ok=True)

    already_complete = (
        dest.exists()
        and expected_size
        and _existing_size(dest) == expected_size
        and (not expected_sha256 or sha256_of(dest) == expected_sha256)
    )
    if already_complete:
        logger.info("已存在且校验通过，跳过下载：%s", dest.name)
        return dest

    partial = _partial_path(dest)
    last_error: Exception | None = None

    for url in urls:
        try:
            _download_one(
                url,
                partial,
                expected_size=expected_size,
                on_progress=on_progress,
                cancel=cancel,
                timeout=timeout,
                logger=logger,
            )
        except DownloadCancelled:
            raise
        except (requests.RequestException, OSError, DownloadError) as exc:
            last_error = exc
            logger.warning("下载源失败，尝试下一个：%s（%s）", url, exc)
            continue

        if expected_sha256:
            if on_progress is not None:
                on_progress(DownloadProgress(expected_size, expected_size, 0.0))
            actual = sha256_of(partial)
            if actual != expected_sha256:
                # A mismatching file is worse than no file: remove it so the
                # next attempt starts clean instead of resuming corrupt bytes.
                partial.unlink(missing_ok=True)
                last_error = DownloadError(
                    f"{dest.name} 校验失败：期望 {expected_sha256[:16]}…，实际 {actual[:16]}…"
                )
                logger.error("%s", last_error)
                continue

        os.replace(partial, dest)
        logger.info("下载完成：%s", dest.name)
        return dest

    if last_error is not None:
        raise DownloadError(f"下载 {dest.name} 失败：{last_error}") from last_error
    raise DownloadError(f"下载 {dest.name} 失败：没有可用的下载源")


def _download_one(
    url: str,
    partial: Path,
    *,
    expected_size: int,
    on_progress: Callable[[DownloadProgress], None] | None,
    cancel: threading.Event | None,
    timeout: float,
    logger: logging.Logger,
) -> None:
    offset = _existing_size(partial)
    if expected_size and offset > expected_size:
        # Left over from a different (or aborted mid-rewrite) build.
        partial.unlink(missing_ok=True)
        offset = 0

    response, resumed = _open_stream(url, offset, timeout)
    with response:
        if not resumed:
            offset = 0
        digest, written = _seed_digest(partial) if resumed else (hashlib.sha256(), 0)
        if not resumed:
            partial.unlink(missing_ok=True)

        total = expected_size
        if not total:
            length = response.headers.get("Content-Length")
            if length and length.isdigit():
                total = int(length) + offset

        mode = "ab" if resumed else "wb"
        started = time.monotonic()
        last_report = 0.0
        logger.info(
            "开始下载：%s（%s，已下载 %d 字节）", url, "续传" if resumed else "新下载", offset
        )
        with partial.open(mode) as handle:
            for chunk in response.iter_content(_CHUNK):
                if cancel is not None and cancel.is_set():
                    raise DownloadCancelled("下载已取消")
                if not chunk:
                    continue
                handle.write(chunk)
                digest.update(chunk)
                written += len(chunk)

                now = time.monotonic()
                if on_progress is not None and now - last_report >= _REPORT_INTERVAL:
                    last_report = now
                    elapsed = max(1e-6, now - started)
                    on_progress(
                        DownloadProgress(
                            downloaded=offset + written,
                            total=total,
                            bytes_per_second=written / elapsed,
                        )
                    )

        if expected_size and offset + written != expected_size:
            raise DownloadError(
                f"大小不符：期望 {expected_size} 字节，实际 {offset + written} 字节"
            )
        if on_progress is not None:
            elapsed = max(1e-6, time.monotonic() - started)
            on_progress(
                DownloadProgress(
                    downloaded=offset + written,
                    total=total or offset + written,
                    bytes_per_second=written / elapsed,
                )
            )
