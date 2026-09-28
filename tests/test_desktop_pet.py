"""Pet controls share the main session and remain independently hideable."""
from PySide6.QtCore import QPoint, QPointF, Qt, QEvent
from PySide6.QtGui import QContextMenuEvent, QMouseEvent
from PySide6.QtWidgets import QApplication

from test_gui_session import qapp, patched_pipeline, make_window, select_target


def test_pet_toggle_close_and_reopen(qapp, patched_pipeline):
    window = make_window(patched_pipeline)
    try:
        pet = window._pet
        assert not pet.isVisible()
        window._pet_button.click()
        assert pet.isVisible() and pet._timer.isActive()
        assert window._pet_button.text() == "关闭桌宠"
        pet._close_action.trigger()
        assert not pet.isVisible() and not pet._timer.isActive()
        assert window._pet_button.text() == "显示桌宠"
        window._pet_button.click()
        assert window._pet is pet and pet.isVisible()
    finally:
        window.shutdown()
    assert not pet.isVisible() and not pet._timer.isActive()


def test_pet_animation_frames_are_populated(qapp, patched_pipeline):
    window = make_window(patched_pipeline)
    try:
        for frames in window._pet._animations.values():
            for frame in frames:
                assert not frame.isNull()
                assert frame.hasAlphaChannel()
                assert not frame.mask().isNull()
    finally:
        window.shutdown()


def test_pet_menu_uses_main_translation_control(qapp, patched_pipeline):
    window = make_window(patched_pipeline)
    try:
        pet = window._pet
        window._selected_window = None
        window._sync_pet_state()
        assert not pet._translation_action.isEnabled()
        select_target(window)
        window._sync_pet_state()
        clicks = []
        # Inspect routing without starting an actual model-loading thread.
        window._start_button.clicked.disconnect(window.toggle_running)
        window._start_button.clicked.connect(lambda: clicks.append(True))
        pet._translation_action.trigger()
        assert clicks == [True]
        window._running = True
        window._sync_pet_state()
        assert pet._translation_action.text() == "停止翻译"
        assert pet._state == "thinking"
        pet.set_translation_state(False, True, True)
        assert not pet._translation_action.isEnabled()
        assert pet._state == "thinking"
    finally:
        window.shutdown()


def test_right_click_opens_menu_and_refreshes_state(qapp, patched_pipeline):
    window = make_window(patched_pipeline)
    try:
        window._toggle_pet()
        window._running = True
        pet = window._pet
        event = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, QPoint(70, 70),
                                  pet.mapToGlobal(QPoint(70, 70)))
        QApplication.sendEvent(pet, event)
        assert pet._menu.isVisible()
        assert pet._translation_action.text() == "停止翻译"
        assert pet._main_window_action.text() == "打开主窗口"
    finally:
        window.shutdown()


def test_pet_drag_preserves_mouse_offset(qapp, patched_pipeline):
    window = make_window(patched_pipeline)
    try:
        pet = window._pet
        pet.show()
        pet.move(100, 100)
        start = pet.pos()
        local = QPointF(70, 70)
        origin = QPointF(pet.mapToGlobal(QPoint(70, 70)))
        QApplication.sendEvent(pet, QMouseEvent(QEvent.Type.MouseButtonPress, local, origin,
                              Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                              Qt.KeyboardModifier.NoModifier))
        assert pet._state == "idle"
        end = origin + QPointF(45, 30)
        QApplication.sendEvent(pet, QMouseEvent(QEvent.Type.MouseMove, local, end,
                              Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
                              Qt.KeyboardModifier.NoModifier))
        assert pet.pos() == start + QPoint(45, 30)
        assert pet._state == "run_right"
        left = origin + QPointF(10, 30)
        QApplication.sendEvent(pet, QMouseEvent(QEvent.Type.MouseMove, local, left,
                              Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
                              Qt.KeyboardModifier.NoModifier))
        assert pet._state == "run_left"
        QApplication.sendEvent(pet, QMouseEvent(QEvent.Type.MouseButtonRelease, local, end,
                              Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton,
                              Qt.KeyboardModifier.NoModifier))
        assert pet._drag_offset is None and pet._state == "idle"
    finally:
        window.shutdown()


def test_idle_blinks_slowly_without_speaking_frames(qapp, patched_pipeline):
    window = make_window(patched_pipeline)
    try:
        pet = window._pet
        pet.show()
        assert len(pet._animations["idle"]) == 2
        assert pet._frame == 0 and pet._timer.interval() == 6000
        pet._advance()
        assert pet._frame == 1 and pet._timer.interval() == 160
        pet._advance()
        assert pet._frame == 0 and pet._timer.interval() == 6000
        pet.set_translation_state(True, False, True)
        pet._set_animation("run_left")
        pet._resume_rest()
        assert pet._state == "thinking"
        pet.set_translation_state(False, False, True)
        assert pet._state == "idle"
    finally:
        window.shutdown()


def test_pet_restores_minimized_main_window(qapp, patched_pipeline):
    window = make_window(patched_pipeline)
    try:
        window.show()
        window._toggle_pet()
        window.showMinimized()
        qapp.processEvents()
        assert window.isMinimized()
        assert window._pet.isVisible()
        window._pet._main_window_action.trigger()
        qapp.processEvents()
        assert window.isVisible() and not window.isMinimized()
    finally:
        window.shutdown()
        window.hide()
