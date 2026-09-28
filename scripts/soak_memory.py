"""Memory soak test for the frame pipeline.

Runs the real capture and OCR path repeatedly and reports whether memory grows
without bound.  ``tracemalloc`` snapshots are diffed to name the allocation site
responsible, which is the only reliable way to tell a genuine leak from the
allocator holding onto freed arenas - both look like a rising number, but only
one of them is a bug worth chasing.

    .venv\\Scripts\\python.exe scripts\\soak_memory.py --frames 60
    .venv\\Scripts\\python.exe scripts\\soak_memory.py --frames 60 --translate
"""

from __future__ import annotations

import argparse
import ctypes
import gc
import logging
import sys
import time
import tracemalloc
from ctypes import wintypes
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from prtsbox import pipeline as pipeline_module
from prtsbox.models import WindowInfo
from prtsbox.pipeline import PipelineWorker


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


# Without explicit signatures ctypes marshals the HANDLE as a 32-bit int and the
# call silently fails, which is why this reported 0 MiB at first.
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


def memory_mb() -> tuple[float, float]:
    """(working set, private bytes) of this process, in MiB.

    Private bytes is the number that matters for a leak: the working set can
    shrink when the OS trims pages, so it can hide growth.
    """
    counters = _ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    handle = _kernel32.GetCurrentProcess()
    if not _psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
        raise OSError(ctypes.get_last_error(), "GetProcessMemoryInfo 失败")
    return (
        counters.WorkingSetSize / (1024 * 1024),
        counters.PagefileUsage / (1024 * 1024),
    )


class StubTranslator:
    """Answers instantly so the test isolates capture and OCR."""

    def translate_batch(self, texts: list[str]) -> list[str]:
        return [f"译:{text}" for text in texts]

    def preflight(self) -> None:
        return None


def synthetic_frame(width: int = 1920, height: int = 1080, variant: int = 0) -> np.ndarray:
    """A frame the size a real 1080p window would produce, with real text on it.

    The text has to be *legible*: an earlier version drew random noise, OCR
    correctly found nothing in it, and the pipeline returned before translation
    ever ran - so a "translation included" soak was silently measuring OCR only
    and the llama-server column stayed at zero.  Legible text also makes the
    recognised strings differ between frames, which is what exercises the
    cache-miss path.

    ``variant`` changes the wording so consecutive frames are genuinely
    different, which is what a live screen looks like.
    """
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (width, height), (246, 246, 248))
    draw = ImageDraw.Draw(image)
    index = variant % len(_SOAK_LINES)
    for row in range(6):
        text = _SOAK_LINES[(index + row) % len(_SOAK_LINES)]
        draw.text((40, 40 + row * 46), text, fill=(20, 20, 26))
    # PIL is RGB; the pipeline and OpenCV expect BGR.
    return np.array(image)[:, :, ::-1].copy()


# Sentences a screen translator would actually meet.  Rotating through them
# keeps every frame's OCR output distinct so the translation cache never hides
# the work being measured.
_SOAK_LINES = [
    "Loading...",
    "Are you sure you want to delete this file?",
    "The connection timed out. Retrying in 5 seconds.",
    "Enable hardware acceleration to reduce CPU usage.",
    "Damage: 1250  Critical Hit!",
    "Target window is not in the foreground; capture is paused.",
    "Press any key to continue.",
    "Saving your changes, please wait.",
    "Not enough memory to complete the operation.",
    "The server refused the connection.",
    "Update available: version 2.4.1",
    "Your session will expire in 10 minutes.",
]


def child_memory_mb(manager) -> float:
    """Private bytes of the llama-server child, in MiB (0 when none is running)."""
    if manager is None:
        return 0.0
    return manager.server_memory_mb()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frames", type=int, default=60)
    parser.add_argument("--translate", action="store_true", help="use the real local model")
    parser.add_argument("--interval", type=float, default=0.0, help="seconds between frames")
    parser.add_argument(
        "--backend",
        choices=("auto", "openvino", "onnxruntime"),
        default="auto",
        help="OCR inference backend; 'auto' is what the application uses by default",
    )
    parser.add_argument(
        "--fixed-frame",
        action="store_true",
        help="reuse one frame instead of generating a new one, to isolate content churn",
    )
    parser.add_argument(
        "--vary-mode",
        choices=("random", "shift"),
        default="random",
        help=(
            "random replaces the frame content each time (worst case, every OCR "
            "shape differs); shift keeps the layout and moves it a few pixels, "
            "which is what a real screen looks like"
        ),
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.ERROR)
    logger = logging.getLogger("soak")
    logger.setLevel(logging.INFO)

    frame = synthetic_frame()
    window = WindowInfo(hwnd=1, title="soak", left=0, top=0, width=frame.shape[1], height=frame.shape[0])
    state = {"variant": 0}

    def capture(_window):
        if args.fixed_frame:
            return frame
        if args.vary_mode == "shift":
            # Same layout, nudged horizontally.  Recognised text is identical,
            # so the detected box count and widths stay put - which is what a
            # real desktop looks like between frames.
            shift = state["variant"] % 9
            return np.roll(frame, shift, axis=1)
        return synthetic_frame(variant=state["variant"])

    pipeline_module.capture_window = capture

    if not args.translate:
        pipeline_module.create_translator = lambda *_a, **_k: StubTranslator()
        manager = None
    else:
        from prtsbox.llama import LlamaManager

        manager = LlamaManager()

    worker = PipelineWorker(manager, ocr_backend=args.backend)  # type: ignore[arg-type]
    # initialize() would emit on a thread that is not running here, so the
    # service is built directly to keep the measurement synchronous.
    worker._create_ocr()
    assert worker._ocr is not None
    print(f"OCR 后端：OpenVINO={worker._ocr.using_openvino}")
    print(f"帧尺寸：{frame.shape[1]}×{frame.shape[0]}　帧数：{args.frames}　"
          f"翻译：{'真实模型' if args.translate else '桩'}　"
          f"帧内容：{'固定' if args.fixed_frame else '每帧变化'}")

    config = {"engine": "local", "target_language": "zh-CN", "skip_chinese": False,
              "model_id": "", "credentials": {}}

    results: list = []
    failures: list = []
    worker.frame.connect(results.append)
    worker.failed.connect(failures.append)

    # Warm up: the first frames pay for lazy native initialisation and would be
    # mistaken for growth.
    for _ in range(3):
        worker.process(window, config)

    gc.collect()
    tracemalloc.start(25)
    baseline_snapshot = tracemalloc.take_snapshot()
    ws0, priv0 = memory_mb()
    child0 = child_memory_mb(manager) if args.translate else 0.0
    print(f"\n起始：工作集 {ws0:.0f} MiB　私有 {priv0:.0f} MiB"
          + (f"　llama-server {child0:.0f} MiB" if args.translate else ""))
    print()
    print(f"{'帧':>5}{'工作集':>10}{'私有':>10}{'对象数':>10}{'耗时':>9}"
          + (f"{'llama':>9}" if args.translate else ""))

    samples = [(0, ws0, priv0)]
    for index in range(1, args.frames + 1):
        state["variant"] = index
        started = time.perf_counter()
        worker.process(window, config)
        elapsed = time.perf_counter() - started
        if index % 5 == 0 or index == args.frames:
            ws, priv = memory_mb()
            samples.append((index, ws, priv))
            line = (
                f"{index:>5}{ws:>9.0f}M{priv:>9.0f}M{len(gc.get_objects()):>10}"
                f"{elapsed * 1000:>8.0f}ms"
            )
            if args.translate:
                line += f"{child_memory_mb(manager):>8.0f}M"
            print(line)
        if args.interval:
            time.sleep(args.interval)

    gc.collect()
    ws1, priv1 = memory_mb()
    final_snapshot = tracemalloc.take_snapshot()
    tracemalloc.stop()

    print(f"\n{'=' * 52}")
    print(f"工作集 {ws0:.0f} -> {ws1:.0f} MiB（{ws1 - ws0:+.0f}）")
    print(f"私有   {priv0:.0f} -> {priv1:.0f} MiB（{priv1 - priv0:+.0f}）")
    if args.translate:
        child1 = child_memory_mb(manager)
        print(f"llama  {child0:.0f} -> {child1:.0f} MiB（{child1 - child0:+.0f}）")
    print(f"帧数   {args.frames}，约 {(priv1 - priv0) / max(1, args.frames):.2f} MiB/帧")
    # A frame that errored emits nothing, so counting the replies is the only
    # way to know the workload actually ran - a soak over zero work would look
    # identical to a soak with no leak.
    print(f"回报   {len(results)} 帧，失败 {len(failures)} 次")
    if failures:
        print(f"       首次失败：{failures[0][:160]}")
    if len(results) < args.frames:
        print("       <-- 有帧没有回报，本次测量的负载不完整")

    # Compare the middle of the run against the end: growth that has stopped is
    # allocator retention, growth that continues is a leak.
    if len(samples) >= 3:
        mid_index, _mid_ws, mid_priv = samples[len(samples) // 2]
        print(
            f"\n后半程：第 {mid_index} 帧 {mid_priv:.0f} MiB -> "
            f"第 {args.frames} 帧 {priv1:.0f} MiB（{priv1 - mid_priv:+.0f}）"
        )

    print("\n保留最多的分配点：")
    diff = final_snapshot.compare_to(baseline_snapshot, "lineno")
    for stat in diff[:12]:
        if stat.size_diff > 0:
            print(f"  {stat.size_diff / 1024:>9.0f} KiB  {stat}")

    # Without this the llama-server child outlives the measurement.  It is
    # started lazily on the first translated frame and holds ~2 GiB, so leaking
    # one per run makes every later reading meaningless.
    if manager is not None:
        manager.stop()

    return 0


if __name__ == "__main__":
    sys.exit(main())
