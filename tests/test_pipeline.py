"""The pipeline contract: every frame must produce exactly one result.

The UI keeps a single frame in flight and only dispatches the next one when the
previous reports back.  An early return that emits nothing therefore stops
translation permanently, which is exactly what a failed window capture used to
do - the status line stayed on "loading model" forever while the pipeline was
already dead.  These tests pin that contract down.
"""

from __future__ import annotations

import numpy as np
import pytest

from prtsbox import pipeline as pipeline_module
from prtsbox.models import OcrItem, WindowInfo
from prtsbox.pipeline import (
    NOTE_CAPTURE_BLANK,
    NOTE_CAPTURE_FAILED,
    NOTE_NO_TEXT,
    FrameResult,
    PipelineWorker,
    TranslationCache,
)


class FakeOcr:
    """Stands in for the real OCR service."""

    using_openvino = True

    def __init__(self, items: list[OcrItem] | None = None) -> None:
        self.items = items or []

    def recognize(self, _frame):
        return list(self.items)

    def recognize_timed(self, frame):
        return list(self.items), {"ocr_ms": 1.0}


def make_item(text: str, top: float = 0.0) -> OcrItem:
    box = ((0.0, top), (100.0, top), (100.0, top + 12.0), (0.0, top + 12.0))
    return OcrItem(box=box, text=text, confidence=0.95)


class FakeTranslator:
    """Returns a fixed translation for every input, ignoring the backend."""

    def translate_batch(self, texts: list[str]) -> list[str]:
        return [f"[{text}]" for text in texts]

    def preflight(self) -> None:
        return None


@pytest.fixture()
def worker(monkeypatch: pytest.MonkeyPatch):
    """A worker with capture, OCR and the translator replaced by fakes."""
    monkeypatch.setattr(pipeline_module, "capture_window", lambda _w: np.zeros((40, 60, 3), np.uint8))
    monkeypatch.setattr(pipeline_module, "is_probably_blank", lambda _f: False)
    monkeypatch.setattr(
        pipeline_module, "create_translator", lambda *_a, **_k: FakeTranslator()
    )
    instance = PipelineWorker(llama_manager=None)  # type: ignore[arg-type]
    instance._ocr = FakeOcr()
    return instance


WINDOW = WindowInfo(hwnd=1, title="t", left=0, top=0, width=60, height=40)
CONFIG = {"engine": "local", "target_language": "zh-CN", "skip_chinese": True, "credentials": {}}


def collect(worker: PipelineWorker) -> dict[str, list]:
    """Record every signal the worker emits."""
    seen: dict[str, list] = {"frame": [], "failed": [], "status": []}
    worker.frame.connect(seen["frame"].append)
    worker.failed.connect(seen["failed"].append)
    worker.status.connect(seen["status"].append)
    return seen


class TestEveryCallReportsBack:
    def test_failed_capture_still_emits(self, worker, monkeypatch) -> None:
        # The window is gone or refuses to render.
        monkeypatch.setattr(pipeline_module, "capture_window", lambda _w: None)
        seen = collect(worker)
        worker.process(WINDOW, CONFIG)
        assert len(seen["frame"]) == 1, "捕获失败时必须回报，否则流水线会永久阻塞"
        assert seen["frame"][0].note == NOTE_CAPTURE_FAILED
        assert seen["frame"][0].items == []

    def test_blank_capture_still_emits(self, worker, monkeypatch) -> None:
        monkeypatch.setattr(pipeline_module, "is_probably_blank", lambda _f: True)
        seen = collect(worker)
        worker.process(WINDOW, CONFIG)
        assert len(seen["frame"]) == 1
        assert seen["frame"][0].note == NOTE_CAPTURE_BLANK

    def test_no_text_still_emits(self, worker) -> None:
        seen = collect(worker)
        worker.process(WINDOW, CONFIG)
        assert len(seen["frame"]) == 1
        assert seen["frame"][0].note == NOTE_NO_TEXT

    def test_every_early_exit_emits_a_result(self, worker, monkeypatch) -> None:
        """Sweep the failure modes; each must report exactly once."""
        cases = {
            "capture failed": lambda: monkeypatch.setattr(
                pipeline_module, "capture_window", lambda _w: None
            ),
            "blank frame": lambda: monkeypatch.setattr(
                pipeline_module, "is_probably_blank", lambda _f: True
            ),
            "ocr unavailable": lambda: setattr(worker, "_ocr", None),
        }
        for label, setup in cases.items():
            monkeypatch.undo()
            monkeypatch.setattr(
                pipeline_module, "capture_window", lambda _w: np.zeros((40, 60, 3), np.uint8)
            )
            monkeypatch.setattr(pipeline_module, "is_probably_blank", lambda _f: False)
            worker._ocr = FakeOcr()
            setup()

            seen = collect(worker)
            worker.process(WINDOW, CONFIG)
            reported = len(seen["frame"]) + len(seen["failed"])
            assert reported == 1, f"{label}: 回报 {reported} 次，应为 1 次"


class TestOcrFailure:
    def test_ocr_exception_is_reported(self, worker) -> None:
        class Exploding(FakeOcr):
            def recognize(self, _frame):
                raise RuntimeError("推理崩溃")

        worker._ocr = Exploding()
        seen = collect(worker)
        worker.process(WINDOW, CONFIG)
        assert len(seen["frame"]) == 1
        assert seen["frame"][0].error
        assert "推理崩溃" in seen["frame"][0].note
        assert seen["frame"][0].request_config == CONFIG


class TestNotes:
    def test_all_chinese_reports_skipped_not_missing(self, worker) -> None:
        worker._ocr = FakeOcr([make_item("目标窗口不在前台")])
        seen = collect(worker)
        worker.process(WINDOW, CONFIG)
        assert len(seen["frame"]) == 1
        assert seen["frame"][0].note == pipeline_module.NOTE_ALL_SKIPPED

    def test_translated_frame_has_no_note(self, worker) -> None:
        worker._ocr = FakeOcr([make_item("Loading...")])
        seen = collect(worker)
        worker.process(WINDOW, CONFIG)
        assert len(seen["frame"]) == 1
        result = seen["frame"][0]
        # Translation itself is not exercised here; what matters is that a frame
        # carrying items is not marked as empty.
        assert result.note == ""
        assert len(result.items) == 1


class TestFrameResultDefaults:
    def test_defaults_are_empty(self) -> None:
        result = FrameResult()
        assert result.items == []
        assert result.note == ""
        assert result.from_cache == 0

    def test_cache_is_bounded(self) -> None:
        cache = TranslationCache(capacity=3)
        for index in range(10):
            cache.put((str(index),), str(index))
        assert len(cache) == 3


class RecordingOcr:
    """Captures the frame it was handed, so the crop can be verified."""

    using_openvino = True

    def __init__(self, items: list[OcrItem] | None = None) -> None:
        self.items = items or []
        self.frames: list[np.ndarray] = []

    def recognize(self, frame):
        self.frames.append(frame)
        return list(self.items)


REGION_CONFIG = {**CONFIG, "region_bottom_only": True, "region_bottom_percent": 30}


class TestBottomRegion:
    @pytest.fixture()
    def tall_worker(self, monkeypatch: pytest.MonkeyPatch):
        """A worker whose captures are 1000px tall, so a 30% crop is obvious."""
        monkeypatch.setattr(
            pipeline_module,
            "capture_window",
            lambda _w: np.full((1000, 400, 3), 200, np.uint8),
        )
        monkeypatch.setattr(pipeline_module, "is_probably_blank", lambda _f: False)
        monkeypatch.setattr(
            pipeline_module, "create_translator", lambda *_a, **_k: FakeTranslator()
        )
        instance = PipelineWorker(llama_manager=None)  # type: ignore[arg-type]
        instance._ocr = RecordingOcr()
        return instance

    def test_disabled_reads_the_whole_frame(self, tall_worker) -> None:
        tall_worker.process(WINDOW, CONFIG)
        assert len(tall_worker._ocr.frames) == 1
        assert tall_worker._ocr.frames[0].shape[0] == 1000

    def test_enabled_crops_to_the_bottom_slice(self, tall_worker) -> None:
        tall_worker.process(WINDOW, REGION_CONFIG)
        assert len(tall_worker._ocr.frames) == 1
        # 30% of 1000px.
        assert tall_worker._ocr.frames[0].shape[0] == 300

    def test_boxes_are_shifted_back_into_window_coordinates(self, tall_worker) -> None:
        # Text at the very top of the crop is 700px down in the real window.
        tall_worker._ocr.items = [make_item("Loading...", top=0.0)]
        seen = collect(tall_worker)
        tall_worker.process(WINDOW, REGION_CONFIG)
        assert len(seen["frame"]) == 1
        item = seen["frame"][0].items[0]
        assert item.bounds[1] == pytest.approx(700.0)

    def test_frame_size_stays_the_whole_window(self, tall_worker) -> None:
        """The overlay maps frame pixels to window pixels, so a crop must not
        shrink the reported size or every chip would land in the wrong place."""
        seen = collect(tall_worker)
        tall_worker._ocr.items = [make_item("Loading...", top=0.0)]
        tall_worker.process(WINDOW, REGION_CONFIG)
        assert seen["frame"][0].frame_size == (400, 1000)

    def test_full_percent_is_the_same_as_no_crop(self, tall_worker) -> None:
        tall_worker.process(WINDOW, {**CONFIG, "region_bottom_only": True, "region_bottom_percent": 100})
        assert tall_worker._ocr.frames[0].shape[0] == 1000

    @pytest.mark.parametrize("percent", [0, -5, "nonsense", None])
    def test_invalid_percent_falls_back(self, tall_worker, percent) -> None:
        tall_worker.process(
            WINDOW, {**CONFIG, "region_bottom_only": True, "region_bottom_percent": percent}
        )
        # A bad value must not crop to nothing or crash.
        assert 0 < tall_worker._ocr.frames[0].shape[0] <= 1000

    def test_crop_to_nothing_is_reported_not_crashed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            pipeline_module, "capture_window", lambda _w: np.full((10, 40, 3), 200, np.uint8)
        )
        monkeypatch.setattr(pipeline_module, "is_probably_blank", lambda _f: False)
        instance = PipelineWorker(llama_manager=None)  # type: ignore[arg-type]
        instance._ocr = RecordingOcr()
        seen = collect(instance)
        instance.process(
            WINDOW, {**CONFIG, "region_bottom_only": True, "region_bottom_percent": 10}
        )
        assert len(seen["frame"]) == 1
        assert seen["frame"][0].note == pipeline_module.NOTE_REGION_TOO_SMALL


class TestCacheKeyNormalisation:
    """OCR wobbles by a space between frames; that must not cause a re-translate."""

    def test_whitespace_runs_collapse(self) -> None:
        # Both forms converge on the same canonical key, which is the point.
        assert pipeline_module._cache_key_text("Loading  ...") == "Loading..."
        assert pipeline_module._cache_key_text("  a\t b \n") == "a b"

    def test_space_before_punctuation_is_dropped(self) -> None:
        # OCR frequently emits "Loading ..." for "Loading...".
        assert pipeline_module._cache_key_text("Loading ...") == "Loading..."
        assert pipeline_module._cache_key_text("确定 ？") == "确定？"

    def test_word_boundaries_are_preserved(self) -> None:
        # Only punctuation-adjacent spaces go; ordinary spacing must survive or
        # unrelated lines would collapse onto each other.
        assert pipeline_module._cache_key_text("Save the file") == "Save the file"

    def test_different_text_is_not_merged(self) -> None:
        assert pipeline_module._cache_key_text("Save") != pipeline_module._cache_key_text("Safe")
        assert pipeline_module._cache_key_text("1 2") != pipeline_module._cache_key_text("12")

    def test_repeated_frame_translates_once(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[list[str]] = []

        class CountingTranslator:
            def translate_batch(self, texts):
                calls.append(list(texts))
                return [f"译:{t}" for t in texts]

            def preflight(self) -> None:
                return None

        monkeypatch.setattr(
            pipeline_module, "capture_window", lambda _w: np.zeros((40, 60, 3), np.uint8)
        )
        monkeypatch.setattr(pipeline_module, "is_probably_blank", lambda _f: False)
        monkeypatch.setattr(
            pipeline_module, "create_translator", lambda *_a, **_k: CountingTranslator()
        )
        worker = PipelineWorker(llama_manager=None)  # type: ignore[arg-type]
        worker._ocr = FakeOcr([make_item("Loading...", top=0.0)])

        worker.process(WINDOW, CONFIG)
        worker.process(WINDOW, CONFIG)
        worker.process(WINDOW, CONFIG)

        assert len(calls) == 1, f"同一句话被翻译了 {len(calls)} 次，应只翻译一次"

    def test_whitespace_wobble_does_not_retranslate(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[list[str]] = []

        class CountingTranslator:
            def translate_batch(self, texts):
                calls.append(list(texts))
                return [f"译:{t}" for t in texts]

            def preflight(self) -> None:
                return None

        monkeypatch.setattr(
            pipeline_module, "capture_window", lambda _w: np.zeros((40, 60, 3), np.uint8)
        )
        monkeypatch.setattr(pipeline_module, "is_probably_blank", lambda _f: False)
        monkeypatch.setattr(
            pipeline_module, "create_translator", lambda *_a, **_k: CountingTranslator()
        )
        worker = PipelineWorker(llama_manager=None)  # type: ignore[arg-type]

        worker._ocr = FakeOcr([make_item("Loading...", top=0.0)])
        worker.process(WINDOW, CONFIG)
        # Same words, an extra space - what OCR actually produces between frames.
        worker._ocr = FakeOcr([make_item("Loading ...", top=1.0)])
        worker.process(WINDOW, CONFIG)

        assert len(calls) == 1, "仅空格差异不应触发重新翻译"
