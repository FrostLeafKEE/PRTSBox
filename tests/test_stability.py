"""Multi-frame regressions for stationary dialogue and stale UI results."""
from types import SimpleNamespace

import numpy as np
import pytest

from prtsbox import pipeline as module
from prtsbox.models import LayoutMode, OcrItem, WindowInfo
from prtsbox.overlay import TranslationOverlay
from prtsbox.pipeline import FrameResult, PipelineWorker
from prtsbox.ui.main_window import MainWindow


def item(text="Loading...", y=0, x=0, width=200):
    return OcrItem(((x, y), (x + width, y), (x + width, y + 20), (x, y + 20)), text, .99)


@pytest.fixture
def session(monkeypatch):
    state = SimpleNamespace(image=np.full((120, 400, 3), 150, np.uint8),
                            items=[item()], ocr_calls=0, requests=[], results=[])

    def recognize(frame):
        state.ocr_calls += 1
        return state.items

    def translate(texts):
        state.requests.append(texts)
        return [f"translation:{t}" for t in texts]

    monkeypatch.setattr(module, "capture_window", lambda _: state.image)
    monkeypatch.setattr(module, "create_translator", lambda *a, **kw:
                        SimpleNamespace(translate_batch=translate))
    state.worker = PipelineWorker(None)
    state.worker._ocr = SimpleNamespace(recognize=recognize)
    state.worker.frame.connect(state.results.append)
    state.config = {"skip_chinese": False, "session_id": 1}
    state.target = WindowInfo(1, "test", 0, 0, 400, 120)

    def frame(changed=False):
        if changed:
            state.image = state.image.copy()
            state.image[-1, -1, 0] += 1  # animated background, unchanged text
        state.worker.process(state.target, state.config)
        return state.results[-1]

    state.frame = frame
    return state


def test_stationary_image_skips_ocr_and_translation(session):
    first = session.frame()
    for _ in range(10):
        assert session.frame().items == first.items
    assert session.ocr_calls == 1
    assert len(session.requests) == 1


def test_single_frame_typo_does_not_replace_translation(session):
    first = session.frame()
    session.items = [item("Loading..")]
    assert session.frame(True).items == first.items
    session.items = [item()]
    assert session.frame(True).items == first.items
    assert len(session.requests) == 1


@pytest.mark.parametrize("changed", ["Health: 101", "Health: 100!"])
def test_real_small_change_is_confirmed_even_if_pixels_stop_changing(session, changed):
    session.items = [item("Health: 100")]
    first = session.frame()
    session.items = [item(changed)]
    assert session.frame(True).items == first.items
    assert session.frame().items[0].text == changed
    assert len(session.requests) == 2


def test_continuously_changing_counter_is_not_frozen(session):
    session.items = [item("Health: 100")]
    session.frame()
    for value in [101, 102, 103]:
        session.items = [item(f"Health: {value}")]
        result = session.frame(True)
    assert result.items[0].text == "Health: 103"


def test_obvious_new_page_is_immediate(session):
    session.frame()
    session.items = [item("Tomorrow we leave the city.")]
    assert session.frame(True).items[0].text == "Tomorrow we leave the city."


def test_small_box_jitter_does_not_split_paragraph(session):
    session.items = [item("We need to"), item("leave now.", y=35)]
    first = session.frame()
    session.items = [item("We need to"), item("leave now.", y=36)]
    assert session.frame(True).items == first.items
    assert len(session.requests) == 1


def test_confirmed_empty_frame_clears_result(session):
    first = session.frame()
    session.items = []
    assert session.frame(True).items == first.items
    assert session.frame().items == []


@pytest.mark.parametrize("change", ["session", "target", "language", "invalidate", "resize"])
def test_image_reuse_is_scoped_to_current_context(session, change):
    session.frame()
    if change == "session":
        session.config["session_id"] += 1
    elif change == "target":
        session.target = WindowInfo(2, "other", 0, 0, 400, 120)
    elif change == "language":
        session.config["target_language"] = "en"
    elif change == "resize":
        session.image = np.full((140, 400, 3), 150, np.uint8)
    else:
        session.worker.invalidate_cache()
    session.frame()
    assert session.ocr_calls == 2


def test_unexpected_error_returns_one_tagged_result(session, monkeypatch):
    def broken(_):
        raise ValueError("capture failure")
    monkeypatch.setattr(module, "capture_window", broken)
    result = session.frame()
    assert result.error
    assert result.request_config == session.config
    assert result.target_hwnd == 1
    assert len(session.results) == 1


class OverlayProbe:
    _is_same_content = TranslationOverlay._is_same_content
    update_content = TranslationOverlay.update_content

    def __init__(self):
        self._items = [item().with_translation("translation")]
        self._layout_mode = LayoutMode.BELOW
        self._frame_size = (400, 120)
        self._font_size = 14
        self._show_source = False
        self.updates = 0

    def sync_geometry(self, target):
        pass

    def update(self):
        self.updates += 1


def test_overlay_retains_displayed_anchor_and_ignores_whitespace():
    overlay = OverlayProbe()
    for y in [1, 3, 0, 5]:
        overlay.update_content(None, [item("Loading ...", y).with_translation("translation")],
                               LayoutMode.BELOW, (400, 120))
    assert overlay.updates == 0
    assert overlay._items[0].bounds[1] == 0
    overlay.update_content(None, [item(y=10).with_translation("translation")],
                           LayoutMode.BELOW, (400, 120))
    assert overlay.updates == 1
    assert overlay._items[0].bounds[1] == 10


@pytest.mark.parametrize("reason", ["stopped", "session", "window", "settings"])
def test_ui_rejects_stale_results(reason):
    rendered, cleared = [], []
    ui = SimpleNamespace(_running=True, _shutting_down=False, _worker_busy=True,
                         _selected_window=SimpleNamespace(hwnd=1), _last_result=None,
                         _frame_config=lambda: {"session_id": 2}, _render=rendered.append,
                         _overlay=SimpleNamespace(clear=lambda: cleared.append(1)))
    result = FrameResult(items=[item()], request_config={"session_id": 2}, target_hwnd=1)
    if reason == "stopped":
        ui._running = False
    elif reason == "session":
        result.request_config = {"session_id": 1}
    elif reason == "window":
        result.target_hwnd = 2
    else:
        result.request_config = {"session_id": 2, "target_language": "en"}
    MainWindow._on_frame(ui, result)
    assert not rendered and not cleared
    assert not ui._worker_busy
    assert ui._last_result is None


def test_ui_clears_confirmed_empty_frame():
    cleared = []
    ui = SimpleNamespace(_running=True, _shutting_down=False, _worker_busy=True,
                         _set_status=lambda *a, **kw: None,
                         _overlay=SimpleNamespace(clear=lambda: cleared.append(1)))
    MainWindow._on_frame(ui, FrameResult(note=module.NOTE_NO_TEXT))
    assert cleared == [1]
