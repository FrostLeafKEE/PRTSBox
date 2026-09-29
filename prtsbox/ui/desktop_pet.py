"""Frostbyte sprite window with dragging and translation controls."""
from pathlib import Path

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QWidget

from .i18n import localize


class DesktopPet(QWidget):
    translation_requested = Signal()
    main_window_requested = Signal()
    visibility_changed = Signal(bool)
    menu_requested = Signal()

    def __init__(self) -> None:
        super().__init__(None)
        self.setWindowTitle("PRTSBox · Frostbyte")
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        self.setFixedSize(144, 156)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        sheet = QPixmap(str(Path(__file__).parent / "assets" / "frostbyte" / "spritesheet.webp"))
        if sheet.isNull():
            raise RuntimeError("无法加载 Frostbyte 桌宠素材")
        width, height = sheet.width() // 8, sheet.height() // 11
        # Idle deliberately omits the speaking frames in the supplied row.
        self._animations = {
            state: [sheet.copy(column * width, row * height, width, height).scaled(
                self.size(), Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation) for column in columns]
            for state, row, columns in [("idle", 0, (0, 2)),
                                        ("run_right", 1, range(8)),
                                        ("run_left", 2, range(8)),
                                        ("thinking", 7, range(6))]
        }
        self._state = "idle"
        self._frame = 0
        self._running = False
        self._preparing = False
        self._language = "zh"
        self._drag_offset: QPoint | None = None
        self._placed = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._advance)
        self._drag_idle_timer = QTimer(self)
        self._drag_idle_timer.setSingleShot(True)
        self._drag_idle_timer.setInterval(180)
        self._drag_idle_timer.timeout.connect(self._resume_rest)
        self._menu = QMenu(self)
        self._main_window_action = self._menu.addAction("打开主窗口")
        self._main_window_action.triggered.connect(self.main_window_requested.emit)
        self._translation_action = self._menu.addAction("开启翻译")
        self._translation_action.triggered.connect(self.translation_requested.emit)
        self._menu.addSeparator()
        self._close_action = self._menu.addAction("关闭桌宠")
        self._close_action.triggered.connect(self.close)
        self._refresh_frame()

    def set_language(self, language: str) -> None:
        self._language = language
        self._main_window_action.setText(localize("打开主窗口", language))
        self._close_action.setText(localize("关闭桌宠", language))
        self._translation_action.setText(localize(
            "正在准备翻译…" if self._preparing else
            "停止翻译" if self._running else "开启翻译", language
        ))

    def set_translation_state(self, running: bool, preparing: bool, can_start: bool) -> None:
        self._running = running
        self._preparing = preparing
        self._translation_action.setText(localize(
            "正在准备翻译…" if preparing else
            "停止翻译" if running else "开启翻译", self._language
        ))
        self._translation_action.setEnabled(not preparing and (running or can_start))
        if self._drag_offset is None or not self._drag_idle_timer.isActive():
            self._resume_rest()

    def _resume_rest(self) -> None:
        self._set_animation("thinking" if self._running or self._preparing else "idle")

    def _set_animation(self, state: str) -> None:
        if state != self._state:
            self._state, self._frame = state, 0
            self._refresh_frame()
            self._schedule_frame()

    def _schedule_frame(self) -> None:
        if self.isVisible():
            if self._state == "idle":
                delay = 6000 if self._frame == 0 else 160
            else:
                delay = 90 if self._state.startswith("run_") else 220
            self._timer.start(delay)

    def _refresh_frame(self) -> None:
        self._pixmap = self._animations[self._state][self._frame]
        self.setMask(self._pixmap.mask())
        self.update()

    def _advance(self) -> None:
        self._frame = (self._frame + 1) % len(self._animations[self._state])
        self._refresh_frame()
        self._schedule_frame()

    def showEvent(self, event) -> None:
        if not self._placed:
            screen = QGuiApplication.primaryScreen()
            if screen is not None:
                area = screen.availableGeometry()
                self.move(area.right() - self.width() - 24, area.bottom() - self.height() - 24)
            self._placed = True
        self._keep_on_screen()
        self._schedule_frame()
        self.visibility_changed.emit(True)
        super().showEvent(event)

    def hideEvent(self, event) -> None:
        self._timer.stop()
        self._drag_idle_timer.stop()
        self._drag_offset = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self._resume_rest()
        self._menu.hide()
        self.visibility_changed.emit(False)
        super().hideEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self._pixmap)
        painter.end()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.pos()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self._resume_rest()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_offset is not None:
            position = event.globalPosition().toPoint() - self._drag_offset
            delta_x = position.x() - self.x()
            if delta_x:
                self._set_animation("run_right" if delta_x > 0 else "run_left")
                self._drag_idle_timer.start()
            self.move(position)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._drag_offset is not None:
            self._drag_offset = None
            self._drag_idle_timer.stop()
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            self._keep_on_screen()
            self._resume_rest()
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def _keep_on_screen(self) -> None:
        screen = QGuiApplication.screenAt(self.geometry().center()) or self.screen()
        if screen is not None:
            area = screen.availableGeometry()
            self.move(max(area.left(), min(self.x(), area.right() - self.width() + 1)),
                      max(area.top(), min(self.y(), area.bottom() - self.height() + 1)))

    def contextMenuEvent(self, event) -> None:
        self.menu_requested.emit()
        self._menu.popup(event.globalPos())
        event.accept()
