"""llama.cpp runtime installation and ``llama-server`` process management.

The server runs as a child process.  Two details matter more than they look:

* The child is assigned to a Windows Job Object with ``KILL_ON_JOB_CLOSE``.
  Without it a crash of the GUI would leave ``llama-server`` alive holding
  several gigabytes of VRAM, and the next launch would fail with an
  out-of-memory error that looks like a bug in this application.
* Readiness is established by polling ``/health`` rather than by sleeping,
  because loading a model takes 1.2 s for the 1.8B and 4.2 s for the 7B and
  varies with disk speed.
"""

from __future__ import annotations

import ctypes
import logging
import shutil
import socket
import subprocess
import threading
import time
import zipfile
from ctypes import wintypes
from pathlib import Path

import requests

from .catalog import RuntimeVariant

_HEALTH_POLL_INTERVAL = 0.25
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_PROCESS_TERMINATE = 0x0001
_PROCESS_SET_QUOTA = 0x0100


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _JobBasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _JobExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JobBasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class LlamaServerError(RuntimeError):
    pass


def _kernel32():
    return ctypes.WinDLL("kernel32", use_last_error=True)


def _bind_child_to_job(process: subprocess.Popen) -> int:
    """Tie the child's lifetime to this process.

    Returns the job handle (kept open for the process lifetime) or 0 when the
    platform does not support it.
    """
    try:
        kernel32 = _kernel32()
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return 0

        info = _JobExtendedLimitInformation()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE

        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        if not kernel32.SetInformationJobObject(
            job,
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(info),
            ctypes.sizeof(info),
        ):
            kernel32.CloseHandle(job)
            return 0

        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        handle = kernel32.OpenProcess(
            _PROCESS_TERMINATE | _PROCESS_SET_QUOTA, False, process.pid
        )
        if not handle:
            kernel32.CloseHandle(job)
            return 0

        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        assigned = kernel32.AssignProcessToJobObject(job, handle)
        kernel32.CloseHandle(handle)
        if not assigned:
            kernel32.CloseHandle(job)
            return 0
        return int(job)
    except (OSError, AttributeError):
        return 0


def install_runtime(variant: RuntimeVariant, archive: Path) -> None:
    """Unpack a downloaded runtime archive into its own directory."""
    logger = logging.getLogger("prtsbox.llama")
    target = variant.root
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(target)

    # Some archives nest everything under a single folder; flatten so
    # ``llama-server.exe`` is always found relative to the variant root.
    if not variant.server_path.is_file():
        candidates = list(target.rglob("llama-server.exe"))
        if len(candidates) == 1:
            nested = candidates[0].parent
            if nested != target:
                for item in nested.iterdir():
                    shutil.move(str(item), str(target / item.name))
                shutil.rmtree(nested, ignore_errors=True)

    if not variant.server_path.is_file():
        raise LlamaServerError(f"运行时解压后未找到 llama-server.exe：{target}")
    logger.info("运行时安装完成：%s", target)


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class LlamaServer:
    """A single ``llama-server`` child process serving one model."""

    def __init__(
        self,
        variant: RuntimeVariant,
        model_path: Path,
        *,
        # 4096 across 4 slots leaves 1024 tokens per request, which is several
        # times what a screen translation needs (a prompt of ~100 tokens plus a
        # short answer).  Measured against the alternatives on the 1.8B model:
        # 8192 costs 2045 MiB resident, 4096 costs 1787 MiB, 2048 costs 1658 MiB,
        # and all three answer a frame in 383-405 ms.  The saving is modest
        # because most of that footprint is the model weights - 1592 MiB remains
        # even at a 1024 context - but 4096 gives back 258 MiB for free.
        context_size: int = 4096,
        parallel: int = 4,
        gpu_layers: int = 99,
        threads: int | None = None,
    ) -> None:
        self.variant = variant
        self.model_path = model_path
        self.context_size = context_size
        self.parallel = parallel
        self.gpu_layers = gpu_layers
        self.threads = threads
        self.port = 0
        self._process: subprocess.Popen | None = None
        self._job = 0
        self._log_handle = None
        self._lock = threading.RLock()
        self._logger = logging.getLogger("prtsbox.llama")

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def pid(self) -> int:
        """Process id of the child, or 0 when it is not running.

        Exposed so diagnostics can report the child's memory; task managers
        hide it among the other python processes.
        """
        process = self._process
        return process.pid if process is not None and process.poll() is None else 0

    @property
    def is_running(self) -> bool:
        process = self._process
        return process is not None and process.poll() is None

    def command(self) -> list[str]:
        command = [
            str(self.variant.server_path),
            "-m", str(self.model_path),
            "--host", "127.0.0.1",
            "--port", str(self.port),
            "-ngl", str(self.gpu_layers),
            "-c", str(self.context_size),
            "-np", str(self.parallel),
        ]
        if self.threads:
            command += ["-t", str(self.threads)]
        return command

    def start(self, timeout: float = 180.0, cancel: threading.Event | None = None) -> None:
        with self._lock:
            if cancel is not None and cancel.is_set():
                raise LlamaServerError("程序正在退出")
            if self.is_running:
                return
            if not self.variant.server_path.is_file():
                raise LlamaServerError(f"运行时未安装：{self.variant.name}")
            if not self.model_path.is_file():
                raise LlamaServerError(f"模型文件不存在：{self.model_path}")

            self.port = find_free_port()
            log_path = self.model_path.parent.parent / "logs" / "llama-server.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            # The server is chatty; its output is only needed when something
            # goes wrong, so it goes to a file rather than a pipe that nobody
            # drains (a full pipe would block the child).
            self._log_handle = log_path.open("w", encoding="utf-8", errors="replace")

            creation = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            self._logger.info("启动 llama-server：%s", " ".join(self.command()))
            self._process = subprocess.Popen(
                self.command(),
                stdout=self._log_handle,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                creationflags=creation,
                cwd=str(self.variant.root),
            )
            self._job = _bind_child_to_job(self._process)
            if not self._job:
                self._logger.warning("未能将 llama-server 绑定到 Job Object，异常退出时可能残留进程")

        self._wait_ready(timeout, cancel)

    def _wait_ready(self, timeout: float, cancel: threading.Event | None = None) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if cancel is not None and cancel.is_set():
                self.stop()
                raise LlamaServerError("程序正在退出")
            process = self._process
            if process is None or process.poll() is not None:
                code = process.returncode if process else None
                raise LlamaServerError(
                    f"llama-server 启动失败（退出码 {code}）。详见 {self.log_path}"
                )
            try:
                response = requests.get(f"{self.base_url}/health", timeout=2)
                if response.status_code == 200:
                    self._logger.info("llama-server 就绪：%s", self.base_url)
                    return
            except requests.RequestException:
                pass
            time.sleep(_HEALTH_POLL_INTERVAL)
        self.stop()
        raise LlamaServerError(f"llama-server 在 {timeout:.0f} 秒内未就绪")

    @property
    def log_path(self) -> Path:
        return self.model_path.parent.parent / "logs" / "llama-server.log"

    def warmup(self, timeout: float = 60.0) -> None:
        """Force the first inference so the user's first frame is not the one
        paying for graph setup and KV allocation."""
        try:
            requests.post(
                f"{self.base_url}/v1/chat/completions",
                json={
                    "messages": [{"role": "user", "content": "hi"}],
                    "max_tokens": 1,
                    "temperature": 0,
                },
                timeout=timeout,
            )
        except requests.RequestException as exc:
            self._logger.warning("预热失败：%s", exc)

    def stop(self) -> None:
        with self._lock:
            process = self._process
            self._process = None
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    # A wedged server must not keep its VRAM reservation.
                    process.kill()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        self._logger.error("llama-server 无法结束：pid=%s", process.pid)
            if self._job:
                try:
                    _kernel32().CloseHandle(wintypes.HANDLE(self._job))
                except OSError:
                    pass
                self._job = 0
            if self._log_handle is not None:
                try:
                    self._log_handle.close()
                except OSError:
                    pass
                self._log_handle = None
