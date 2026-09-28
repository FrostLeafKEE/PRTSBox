"""The per-frame pipeline: capture, recognise, clean, translate.

Runs on a worker thread so the UI thread only ever paints.  A frame is dropped
rather than queued when the worker is still busy, because a stale translation
arriving late is worse than a slightly lower refresh rate.

One in-flight frame at a time is also what keeps CPU and GPU use predictable:
OCR and the language model are both throughput-bound, so overlapping frames
would increase latency for every one of them without improving the refresh rate.
"""

from __future__ import annotations

import logging
import re
import time
from difflib import SequenceMatcher
from collections import OrderedDict
from dataclasses import dataclass, field, replace

import numpy as np

from PySide6.QtCore import QObject, QThread, Signal, Slot

from . import ocr as ocr_module
from .capture import capture_window, is_probably_blank
from .llama import LlamaManager
from .memory import private_mb
from .models import (
    CaptureTiming,
    OcrItem,
    TranslatorConfig,
    WindowInfo,
    is_chinese_language,
)
from .ocr import OcrService
from .textproc import is_already_chinese, prepare
from .translate import TranslationError, create_translator

# Bounded so a long session cannot grow without limit.  Keys are whole OCR
# strings, so an unbounded dict would accumulate every line ever seen on
# screen; 2000 entries covers far more than one screen's worth of text.
_CACHE_CAPACITY = 2000

# How often the footprint is sampled.  A frame is ~1 s apart, so this is a
# check roughly every half minute - often enough to react, rare enough that the
# cost of querying the process counters is irrelevant.
_MEMORY_CHECK_INTERVAL_FRAMES = 30

# Growth tolerated before the OCR service is rebuilt.  Comfortably above the
# steady-state footprint of the models (a few hundred MiB) so ordinary
# fluctuation never triggers it.
_OCR_MEMORY_ALLOWANCE_MB = 600.0


class TranslationCache:
    """Least-recently-used cache keyed by engine, language pair and source text."""

    def __init__(self, capacity: int = _CACHE_CAPACITY) -> None:
        self._capacity = capacity
        self._entries: OrderedDict[tuple[str, ...], str] = OrderedDict()

    def get(self, key: tuple[str, ...]) -> str | None:
        if key not in self._entries:
            return None
        self._entries.move_to_end(key)
        return self._entries[key]

    def put(self, key: tuple[str, ...], value: str) -> None:
        self._entries[key] = value
        self._entries.move_to_end(key)
        while len(self._entries) > self._capacity:
            self._entries.popitem(last=False)

    def clear(self) -> None:
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)


@dataclass
class FrameResult:
    """The outcome of one frame.

    Every call to :meth:`PipelineWorker.process` must produce exactly one of
    these, including the frames that yield nothing.  The UI clears its
    ``worker_busy`` flag on this signal and will not dispatch another frame
    until it does, so an early ``return`` without emitting wedges the whole
    pipeline permanently - which is what happened when a capture failed.

    ``note`` explains why a frame produced no translations; empty means the
    frame succeeded and ``items`` is authoritative.
    """

    items: list[OcrItem] = field(default_factory=list)
    frame_size: tuple[int, int] = (0, 0)
    timing: CaptureTiming = field(default_factory=CaptureTiming)
    from_cache: int = 0
    note: str = ""
    request_config: dict | None = None
    target_hwnd: int = 0
    error: bool = False


# Reasons a frame can produce nothing.  Kept as constants so the UI and the
# tests refer to the same strings.
NOTE_CAPTURE_FAILED = "截图失败：目标窗口可能已最小化，或该窗口拒绝被渲染"
NOTE_CAPTURE_BLANK = "截图全黑：目标窗口可能受保护（如 DRM 视频、独占全屏游戏）"
NOTE_NO_TEXT = "未识别到文字"
NOTE_ALL_SKIPPED = "识别到的文字已是中文，无需翻译"
NOTE_REGION_TOO_SMALL = "指定的识别区域太小，没有可识别的画面"


def _region_fraction(raw_config: dict) -> float:
    """How much of the window height to recognise, as a fraction in (0, 1]."""
    try:
        percent = float(raw_config.get("region_bottom_percent", 30))
    except (TypeError, ValueError):
        percent = 30.0
    return max(0.05, min(1.0, percent / 100.0))


def _shift_item(item: OcrItem, offset_y: float) -> OcrItem:
    """Move a recognised box down by ``offset_y``.

    OCR ran on a cropped frame, so its coordinates are relative to that crop.
    Shifting them back into whole-window space here means line merging, the
    overlay and every diagnostic share one coordinate system, instead of each
    having to remember the crop.
    """
    return replace(item, box=tuple((x, y + offset_y) for x, y in item.box))


_WHITESPACE = re.compile(r"\s+")
# Whitespace OCR sometimes inserts before punctuation, which no human would
# type: "Loading ..." for "Loading...".  Removing it is safe - no legitimate
# text has a space there - and catches a wobble that collapsing runs does not.
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([,.;:!?%)\]，。！？；：）】」》])")


def _cache_key_text(text: str) -> str:
    """Normalise an OCR string before using it as a cache key.

    OCR is not perfectly stable between frames: it may insert a stray space, or
    segment a line slightly differently.  An exact-match key then misses, the
    string is sent for translation again, a differently worded answer arrives a
    moment later, and the overlay visibly flickers even though the text on
    screen never changed.

    Only spacing is normalised.  Anything more - punctuation, case, digits -
    would start treating genuinely different lines as the same, and a wrong
    translation is worse than an occasional extra request.
    """
    collapsed = _WHITESPACE.sub(" ", text).strip()
    return _SPACE_BEFORE_PUNCT.sub(r"\1", collapsed)


class PipelineWorker(QObject):
    ready = Signal()
    failed = Signal(str)
    status = Signal(str)
    frame = Signal(object)

    def __init__(
        self,
        llama_manager: LlamaManager,
        ocr_backend: str = ocr_module.BACKEND_AUTO,
    ) -> None:
        super().__init__()
        self._llama = llama_manager
        self._logger = logging.getLogger("prtsbox.pipeline")
        self._ocr: OcrService | None = None
        self._ocr_backend = ocr_backend
        self._requested_ocr_backend = ocr_backend
        self._cache = TranslationCache()
        self._frames_since_memory_check = 0
        # Footprint when the OCR service was created; growth past the allowance
        # means native code is holding on to memory that will not come back.
        self._ocr_baseline_mb = 0.0
        self._recycle_count = 0
        self._reset_pending = False
        self._previous_frame = None
        self._previous_result: FrameResult | None = None
        self._frame_context = None
        self._stable_items: list[OcrItem] | None = None
        self._candidate_text: str | None = None
        self._candidate_frames = 0

    def set_ocr_backend(self, backend: str) -> None:
        """Request a backend change; only the worker may rebuild the models."""
        self._requested_ocr_backend = backend

    @Slot()
    def initialize(self) -> None:
        """Load the OCR models.  Runs once, off the UI thread."""
        try:
            self._create_ocr()
            self.ready.emit()
        except Exception as exc:
            self._logger.exception("OCR 初始化失败")
            self.failed.emit(f"OCR 初始化失败：{exc}")

    def _create_ocr(self) -> None:
        """Build the OCR service, falling back to ONNX Runtime if needed.

        The ``except`` is deliberately broad: an OpenVINO failure surfaces as
        anything from ImportError to a native RuntimeError raised inside the
        plugin loader, and having no OCR at all is far worse than slow OCR.
        """
        try:
            self._ocr = OcrService(backend=self._ocr_backend)
        except Exception as exc:
            if self._ocr_backend != ocr_module.BACKEND_ONNXRUNTIME:
                self._logger.warning("OCR 后端 %s 初始化失败，回退 ONNX Runtime：%s", self._ocr_backend, exc)
                self._status("所选 OCR 后端不可用，已回退 ONNX Runtime")
                self._ocr = OcrService(backend=ocr_module.BACKEND_ONNXRUNTIME)
            else:
                raise
        self._ocr_baseline_mb = private_mb()
        self._frames_since_memory_check = 0
        self._logger.info(
            "OCR 就绪（OpenVINO=%s，基线内存 %.0f MiB）",
            self._ocr.using_openvino,
            self._ocr_baseline_mb,
        )

    def _check_memory(self) -> None:
        """Recycle the OCR service if its footprint runs away.

        A safety net rather than the primary defence: the default backend does
        not grow, but native inference code is outside this project's control
        and a screen translator runs for hours.  Rebuilding costs ~0.7 s, which
        is a far better outcome than unbounded growth.
        """
        self._frames_since_memory_check += 1
        if self._frames_since_memory_check < _MEMORY_CHECK_INTERVAL_FRAMES:
            return
        self._frames_since_memory_check = 0
        if not self._ocr_baseline_mb:
            return

        current = private_mb()
        growth = current - self._ocr_baseline_mb
        if growth < _OCR_MEMORY_ALLOWANCE_MB:
            return

        self._recycle_count += 1
        self._logger.warning(
            "OCR 内存增长 %.0f MiB（%.0f -> %.0f），第 %d 次重建服务",
            growth,
            self._ocr_baseline_mb,
            current,
            self._recycle_count,
        )
        self._status("OCR 内存占用过高，已重建识别服务")
        try:
            self._create_ocr()
        except Exception:
            self._logger.exception("重建 OCR 服务失败")
            self._ocr_baseline_mb = 0.0

    def _status(self, message: str) -> None:
        self.status.emit(message)

    def invalidate_cache(self) -> None:
        # UI callers only request a reset; worker-owned state is changed on
        # the next frame, never concurrently with translation.
        self._reset_pending = True

    def _emit_frame(self, result: FrameResult) -> None:
        self.frame.emit(replace(
            result, request_config=dict(self._request_config),
            target_hwnd=self._request_hwnd,
        ))

    def _stabilize_items(self, items: list[OcrItem]) -> list[OcrItem]:
        """Keep accepted anchors; confirm small textual changes once.

        Similarity only delays a change, never aliases translation cache keys.
        Even a changing candidate is accepted after at most two held frames.
        """
        items = sorted(items, key=lambda item: (item.bounds[1], item.bounds[0]))
        old = self._stable_items
        if old is not None and len(old) == len(items):
            items = [
                previous if _cache_key_text(current.text) == _cache_key_text(previous.text)
                and all(abs(a - b) <= 6 for a, b in zip(current.bounds, previous.bounds))
                else current
                for current, previous in zip(items, old)
            ]
        text = _cache_key_text(" ".join(item.text for item in items))
        old_text = _cache_key_text(" ".join(item.text for item in old or []))
        if old and items and text == old_text:
            def extent(lines):
                return (min(i.bounds[0] for i in lines), min(i.bounds[1] for i in lines),
                        max(i.bounds[2] for i in lines), max(i.bounds[3] for i in lines))
            if all(abs(a - b) <= 6 for a, b in zip(extent(items), extent(old))):
                items = old
        if old and text != old_text and (
            not items or SequenceMatcher(None, old_text, text, autojunk=False).ratio() >= 0.85
        ):
            self._candidate_frames += 1
            if text != self._candidate_text and self._candidate_frames <= 2:
                self._candidate_text = text
                return old
        self._candidate_text = None
        self._candidate_frames = 0
        self._stable_items = items
        return items

    @Slot(object, object)
    def process(self, window: WindowInfo, raw_config: dict) -> None:
        self._request_config = dict(raw_config)
        self._request_hwnd = window.hwnd
        context = (window.hwnd, raw_config)
        if self._reset_pending or context != self._frame_context:
            self._reset_pending = False
            self._cache.clear()
            self._previous_frame = None
            self._previous_result = None
            self._stable_items = None
            self._candidate_text = None
            self._candidate_frames = 0
            self._frame_context = (window.hwnd, dict(raw_config))
        try:
            self._process_frame(window, raw_config)
        except Exception as exc:
            self._logger.exception("帧处理失败")
            self._emit_frame(FrameResult(note=str(exc), error=True))

    def _process_frame(self, window: WindowInfo, raw_config: dict) -> None:
        if QThread.currentThread().isInterruptionRequested():
            self._emit_frame(FrameResult(note="程序正在退出"))
            return
        requested = self._requested_ocr_backend
        if requested != self._ocr_backend:
            previous_backend = self._ocr_backend
            self._ocr_backend = requested
            try:
                self._create_ocr()
            except Exception:
                self._ocr_backend = previous_backend
                raise
            self._previous_frame = None
            self._previous_result = None
            self._stable_items = None
            self._candidate_text = None
            self._candidate_frames = 0
            self._cache.clear()
        if self._ocr is None:
            self._emit_frame(FrameResult(note="OCR 尚未就绪", error=True))
            return

        # Counting frames here rather than after the OCR call means a frame that
        # fails early still counts towards the sampling interval.
        self._check_memory()

        started = time.perf_counter()
        timing = CaptureTiming()

        capture_started = time.perf_counter()
        frame = capture_window(window)
        timing.capture_ms = (time.perf_counter() - capture_started) * 1000.0
        if frame is None:
            # The window is minimised, closing, or refusing to render.  An empty
            # result is emitted rather than nothing at all: the pipeline is
            # strictly one frame in flight, and a silent return here would leave
            # the UI waiting for a reply that never comes.
            self._logger.debug("截图失败：hwnd=%s", window.hwnd)
            self._emit_frame(FrameResult(note=NOTE_CAPTURE_FAILED, timing=timing))
            return
        if is_probably_blank(frame):
            self._logger.debug("截图全黑：hwnd=%s", window.hwnd)
            self._emit_frame(FrameResult(note=NOTE_CAPTURE_BLANK, timing=timing))
            return

        # Full client size: the overlay maps frame pixels onto the window, so
        # this must stay the whole area even when only a slice is recognised.
        height, width = frame.shape[:2]

        region_offset_y = 0
        if raw_config.get("region_bottom_only"):
            fraction = _region_fraction(raw_config)
            if fraction < 1.0:
                region_offset_y = int(height * (1.0 - fraction))
                # Crop before OCR so detection cost drops with the area, which
                # is most of the point of the option.
                frame = frame[region_offset_y:, :]
                if frame.shape[0] < 8:
                    self._emit_frame(
                        FrameResult(note=NOTE_REGION_TOO_SMALL, timing=timing)
                    )
                    return

        if self._previous_result is not None and self._previous_result.frame_size != (width, height):
            self._stable_items = None
            self._candidate_text = None
            self._candidate_frames = 0
        if (self._previous_result is not None and self._previous_frame is not None
                and self._previous_result.frame_size == (width, height)
                and np.array_equal(frame, self._previous_frame)
                and self._candidate_frames == 0):
            self._emit_frame(replace(self._previous_result, timing=timing,
                                     from_cache=len(self._previous_result.items)))
            return

        ocr_started = time.perf_counter()
        try:
            raw_items = self._ocr.recognize(frame)
        except RuntimeError as exc:
            self._emit_frame(FrameResult(note=str(exc), error=True))
            return
        timing.ocr_ms = (time.perf_counter() - ocr_started) * 1000.0
        if QThread.currentThread().isInterruptionRequested():
            self._emit_frame(FrameResult(note="程序正在退出"))
            return

        if region_offset_y:
            # Back into whole-window coordinates, so everything downstream -
            # line merging, the overlay, the diagnostics - works in one space.
            raw_items = [_shift_item(item, region_offset_y) for item in raw_items]

        config = TranslatorConfig.from_dict(raw_config)

        # Stabilize individual OCR lines before merging: a one-pixel change
        # near the wrap threshold must not split the same paragraph next frame.
        items = prepare(self._stabilize_items(raw_items))
        if items and config.skip_chinese and is_chinese_language(config.target_language):
            # With a Chinese target, text that is already Chinese is dropped
            # rather than sent: translating it would either do nothing or
            # quietly reword the user's own text.  With any other target,
            # Chinese source text is exactly what needs translating.
            before = len(items)
            items = [item for item in items if not is_already_chinese(item.text)]
            if len(items) != before:
                self._logger.debug("跳过 %d 条已是中文的文本", before - len(items))
        if not items:
            # Distinguish "nothing to translate here" from "we filtered it all
            # out", so the status line can say which.
            note = NOTE_ALL_SKIPPED if raw_items else NOTE_NO_TEXT
            result = FrameResult([], (width, height), timing, note=note)
            self._previous_frame = frame.copy()
            self._previous_result = result
            self._emit_frame(result)
            return

        try:
            items, cached = self._translate(items, config, timing)
        except TranslationError as exc:
            self._emit_frame(FrameResult(note=str(exc), error=True))
            return

        self._logger.debug(
            "帧完成：%d 项（缓存 %d），截图 %.0f ms OCR %.0f ms 翻译 %.0f ms 合计 %.0f ms",
            len(items),
            cached,
            timing.capture_ms,
            timing.ocr_ms,
            timing.translate_ms,
            (time.perf_counter() - started) * 1000.0,
        )
        result = FrameResult(items, (width, height), timing, cached)
        self._previous_frame = frame.copy()
        self._previous_result = result
        self._emit_frame(result)

    def _translate(
        self,
        items: list[OcrItem],
        config: TranslatorConfig,
        timing: CaptureTiming,
    ) -> tuple[list[OcrItem], int]:
        prefix = (
            config.engine,
            config.source_language,
            config.target_language,
            config.model_id,
        )

        pending: list[str] = []
        seen: set[str] = set()
        hits = 0
        for item in items:
            key = _cache_key_text(item.text)
            if self._cache.get((*prefix, key)) is not None:
                hits += 1
            elif key not in seen:
                # The same string can appear many times in one frame; translate
                # it once and let the cache fan it back out below.
                seen.add(key)
                pending.append(key)

        if pending:
            self._status(f"翻译中（{len(pending)} 条）")
            translate_started = time.perf_counter()
            translator = create_translator(config, llama_manager=self._llama)
            translations = translator.translate_batch(pending)
            timing.translate_ms = (time.perf_counter() - translate_started) * 1000.0
            for source, translated in zip(pending, translations, strict=True):
                self._cache.put((*prefix, source), translated)
        else:
            timing.translate_ms = 0.0

        translated_items = [
            item.with_translation(self._cache.get((*prefix, _cache_key_text(item.text))) or "")
            for item in items
        ]
        return translated_items, hits
