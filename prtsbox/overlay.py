"""Click-through translation overlay.

The overlay is a borderless, input-transparent window pinned to the target's
client area.  Three window flags combine to make it behave like paint on the
glass rather than a window:

* ``WindowTransparentForInput`` - clicks and scrolls reach the window beneath.
* ``WindowDoesNotAcceptFocus`` - never steals focus, so a game keeps running.
* ``Tool`` - keeps it out of the taskbar and the alt-tab list.

``WindowStaysOnTopHint`` is deliberately *not* used.  It sets ``WS_EX_TOPMOST``,
and the overlay's Z-order is managed explicitly instead (see
:func:`prtsbox.win32.place_overlay_above`): the goal is to sit directly above the
target window, not above every window on the desktop.  Letting Qt force topmost
would both fight the explicit placement and leave translations floating over
unrelated applications.

Coordinates arrive in captured-frame pixels, which are not widget pixels: the
frame is in physical pixels while Qt lays out in logical ones, so the two
differ by the display scale factor.  Rather than reason about DPI directly,
everything is scaled by ``widget_width / frame_width``, which absorbs the DPI
factor and any client-size mismatch in one step.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QPoint, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QGuiApplication,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import QWidget

from .models import LayoutMode, OcrItem, WindowInfo
from .pipeline import _cache_key_text
from .win32 import exclude_from_capture, include_in_capture, place_overlay_above

# Families that exist on a stock Windows install and cover Latin plus CJK.
_FONT_FAMILIES = ["Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", "Arial"]

_PADDING_X = 6.0
_PADDING_Y = 3.0
_GAP = 2.0
_MIN_WIDTH = 40.0
_RADIUS = 5.0

# How far a recognised box may drift between frames before the overlay treats it
# as a real change and repaints. OCR can shift several pixels on a static
# screen; redrawing for that is what makes the chips shimmer.
_POSITION_TOLERANCE = 6.0


def _screen_ratio(target: WindowInfo) -> float:
    """Device pixel ratio of the monitor the target sits on.

    Asking the widget itself is unreliable: ``devicePixelRatioF()`` returns 1.0
    until the window has been shown on a screen, so on a 125% display the
    overlay would be laid out 25% too large and every translation would drift
    further from its source line the lower it appeared.  The target's own
    coordinates are known before showing, so the monitor is looked up directly.
    """
    centre = QPoint(
        int(target.left + target.width / 2), int(target.top + target.height / 2)
    )
    screen = QGuiApplication.screenAt(centre) or QGuiApplication.primaryScreen()
    if screen is None:
        return 1.0
    ratio = float(screen.devicePixelRatio())
    return ratio if ratio > 0 else 1.0


class TranslationOverlay(QWidget):
    def __init__(self) -> None:
        super().__init__(None)
        self._logger = logging.getLogger("prtsbox.overlay")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self._items: list[OcrItem] = []
        self._frame_size: tuple[int, int] = (0, 0)
        self._layout_mode = LayoutMode.BELOW
        self._font_size = 14
        self._show_source = False
        self._target_hwnd = 0
        self._colors = _dark_colors()

    # -- content ---------------------------------------------------------

    def update_content(
        self,
        target: WindowInfo,
        items: list[OcrItem],
        layout_mode: LayoutMode,
        frame_size: tuple[int, int],
        *,
        font_size: int = 14,
        show_source: bool = False,
    ) -> None:
        unchanged = self._is_same_content(
            items, layout_mode, frame_size, font_size, show_source
        )
        # Keep the actual displayed anchors when ignoring measurement noise.
        # Otherwise an unrelated paint event would expose accumulated drift.
        if not unchanged:
            self._items = items
        self._layout_mode = layout_mode
        self._frame_size = frame_size
        self._font_size = font_size
        self._show_source = show_source
        # The window still has to be followed - it may have moved or resized
        # even though the text did not change.
        self.sync_geometry(target)
        if unchanged:
            # Repainting identical content every frame is what makes the
            # overlay look like it is vibrating: the chips are re-laid out from
            # freshly measured text metrics, and sub-pixel differences between
            # passes show up as a shimmer.  Redrawing only on real change also
            # removes the cost of a full repaint per frame.
            return
        self.update()

    def _is_same_content(
        self,
        items: list[OcrItem],
        layout_mode: LayoutMode,
        frame_size: tuple[int, int],
        font_size: int,
        show_source: bool,
    ) -> bool:
        """Whether redrawing would produce the same picture.

        Box positions are compared with a tolerance: OCR jitters by several
        pixels between frames even on a static screen, and treating that as a
        change is precisely the flicker this guards against.  Text and
        translation must match exactly, since those are what the user reads.
        """
        if (
            frame_size != self._frame_size
            or layout_mode != self._layout_mode
            or font_size != self._font_size
            or show_source != self._show_source
            or len(items) != len(self._items)
        ):
            return False

        for new, old in zip(items, self._items, strict=True):
            if (_cache_key_text(new.text) != _cache_key_text(old.text)
                    or new.translation != old.translation):
                return False
            if any(
                abs(a - b) > _POSITION_TOLERANCE
                for a, b in zip(new.bounds, old.bounds, strict=True)
            ):
                return False
        return True

    def set_colors(self, colors: dict[str, QColor]) -> None:
        self._colors = colors
        self.update()

    def clear(self) -> None:
        if self._items:
            self._items = []
            self.update()

    @property
    def item_count(self) -> int:
        return len(self._items)

    # -- geometry --------------------------------------------------------

    def sync_geometry(self, target: WindowInfo) -> None:
        """Match the target's position and keep the overlay just above it."""
        ratio = _screen_ratio(target)
        self.setGeometry(
            int(target.left / ratio),
            int(target.top / ratio),
            max(1, int(target.width / ratio)),
            max(1, int(target.height / ratio)),
        )
        if self._target_hwnd != target.hwnd:
            self._target_hwnd = target.hwnd
            self._logger.info("译文层目标窗口：hwnd=%s", target.hwnd)
        place_overlay_above(int(self.winId()), target)

    def attach(self, target: WindowInfo, *, hide_from_capture: bool = True) -> None:
        """Show the overlay pinned to ``target``.

        ``hide_from_capture`` excludes the overlay from screen capture.  Default
        on, for two reasons: it keeps a future screen-region capture fallback
        from photographing our own translations and feeding them back through
        OCR forever, and it keeps translations out of screen shares and
        recordings.  A user who wants the translations *in* a screenshot can
        turn it off; the trade-off is invisible either way for the normal
        window-capture path, which never sees the overlay regardless.
        """
        if hide_from_capture:
            exclude_from_capture(int(self.winId()))
        else:
            include_in_capture(int(self.winId()))
        self.sync_geometry(target)
        self.show()

    def hide_overlay(self) -> None:
        self.hide()
        self._items = []

    # -- painting --------------------------------------------------------

    def paintEvent(self, _event) -> None:
        if not self._items:
            return
        frame_width, frame_height = self._frame_size
        if frame_width <= 0 or frame_height <= 0:
            return
        scale_x = self.width() / frame_width
        scale_y = self.height() / frame_height

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        # Chips are laid out in reading order and each one remembers where it
        # landed, so a translation that wraps to several lines pushes the next
        # chip down instead of covering it.
        placed: list[QRectF] = []
        try:
            for item in self._items:
                if not item.translation.strip():
                    continue
                box = self._scaled_box(item, scale_x, scale_y)
                if self._show_source and item.text.strip():
                    self._draw_chip(painter, box, box.width(), item.text, self._colors["source"], placed)
                anchor = self._anchor(box)
                self._draw_chip(
                    painter, anchor, box.width(), item.translation, self._colors["translation"], placed
                )
        finally:
            painter.end()

    def _scaled_box(self, item: OcrItem, scale_x: float, scale_y: float) -> QRectF:
        left, top, right, bottom = item.bounds
        return QRectF(
            left * scale_x,
            top * scale_y,
            max(1.0, (right - left) * scale_x),
            max(1.0, (bottom - top) * scale_y),
        )

    def _anchor(self, box: QRectF) -> QRectF:
        """Where the translation chip starts, before it is sized to its text."""
        if self._layout_mode is LayoutMode.RIGHT:
            return QRectF(box.right() + _GAP, box.top(), 0.0, box.height())
        return QRectF(box.left(), box.bottom() + _GAP, 0.0, box.height())

    def _font(self, size_delta: float = 0.0) -> QFont:
        font = QFont()
        font.setFamilies(_FONT_FAMILIES)
        font.setPointSizeF(max(6.0, self._font_size + size_delta))
        return font

    @staticmethod
    def _avoid_overlap(chip: QRectF, placed: list[QRectF]) -> QRectF:
        """Push a chip down until it clears the ones already drawn.

        Translations are rarely the same length as their source, so a chip can
        grow tall enough to cover the chip for the next line.  Nudging it down
        keeps every translation readable; the loop is bounded because the list
        is finite and each step strictly increases the offset.
        """
        candidate = QRectF(chip)
        for _ in range(len(placed) + 1):
            collisions = [rect for rect in placed if rect.intersects(candidate)]
            if not collisions:
                return candidate
            candidate.moveTop(max(rect.bottom() for rect in collisions) + _GAP)
        return candidate

    def _draw_chip(
        self,
        painter: QPainter,
        anchor: QRectF,
        source_width: float,
        text: str,
        color: QColor,
        placed: list[QRectF],
    ) -> None:
        font = self._font()
        metrics = QFontMetricsF(font)

        # Never let a chip run past the right edge; wrap instead of clipping.
        available = max(_MIN_WIDTH, self.width() - anchor.left() - _PADDING_X * 2)
        if self._layout_mode is LayoutMode.RIGHT:
            # Side-by-side layout only has the space to the right of the source.
            available = max(_MIN_WIDTH, min(available, self.width() * 0.5 - anchor.left()))
        # A short source line should not force a narrow column, but a long
        # translation should not become a full-width banner either.
        wrap_width = max(_MIN_WIDTH, min(available, max(source_width * 1.6, 240.0)))

        text_rect = metrics.boundingRect(
            QRectF(0, 0, wrap_width, 1e6).toRect(),
            int(Qt.TextFlag.TextWordWrap),
            text,
        )
        width = text_rect.width() + _PADDING_X * 2
        height = text_rect.height() + _PADDING_Y * 2

        left = max(0.0, min(anchor.left(), self.width() - width))
        top = anchor.top()
        # Keep the chip inside the overlay; a translation for text at the very
        # bottom would otherwise be drawn off-screen.
        if top + height > self.height():
            top = max(0.0, anchor.top() - height - _GAP)
        chip = self._avoid_overlap(QRectF(left, top, width, height), placed)
        # Clamp once more: the nudge above may have pushed it past the bottom.
        if chip.bottom() > self.height():
            chip.moveTop(max(0.0, self.height() - chip.height()))
        placed.append(chip)

        path = QPainterPath()
        path.addRoundedRect(chip, _RADIUS, _RADIUS)

        painter.setPen(QPen(color.lighter(160), 1.0))
        painter.fillPath(path, self._colors["background"])
        painter.drawPath(path)

        painter.setFont(font)
        painter.setPen(color)
        painter.drawText(
            chip.adjusted(_PADDING_X, _PADDING_Y, -_PADDING_X, -_PADDING_Y),
            int(Qt.TextFlag.TextWordWrap | Qt.AlignmentFlag.AlignLeft),
            text,
        )


def _dark_colors() -> dict[str, QColor]:
    return {
        "background": QColor(12, 16, 24, 205),
        "translation": QColor(232, 240, 255),
        "source": QColor(150, 165, 190),
    }


def light_colors() -> dict[str, QColor]:
    return {
        "background": QColor(255, 255, 255, 215),
        "translation": QColor(20, 24, 34),
        "source": QColor(110, 118, 135),
    }
