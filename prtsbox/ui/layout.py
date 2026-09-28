"""Layout helpers shared by the main window and the settings dialog.

These exist to keep two rules out of the widget code:

*Anything that can be tall should be scrollable rather than squeezed.*  Qt's
layouts compress by overlapping rows once a window is smaller than the content's
minimum, so a panel that cannot fit must be given a scroll area instead of a
smaller minimum size.

*Anything that can be wide should shrink gracefully.*  A combo box defaults to a
size hint based on its widest entry, which stops a window from being narrowed at
all; asking it to size to a short minimum content length fixes that without
pinning an explicit pixel width.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import (
    QApplication,
    QBoxLayout,
    QComboBox,
    QFrame,
    QScrollArea,
    QSizePolicy,
    QWidget,
)

# Below this a horizontal row is stacked vertically instead.  Chosen so the
# longest row (a combo box plus a button) still fits side by side at the
# window's default width, but stacks well before the controls would be squeezed
# to unreadable widths.
STACK_BELOW_WIDTH = 330


class _ScrollPageOnWheel(QObject):
    """Scroll the containing page without changing a form value."""

    def eventFilter(self, watched, event) -> bool:
        if event.type() != QEvent.Type.Wheel:
            return False
        parent = watched.parentWidget()
        while parent is not None and not isinstance(parent, QScrollArea):
            parent = parent.parentWidget()
        if parent is not None:
            viewport = parent.viewport()
            forwarded = QWheelEvent(
                viewport.mapFromGlobal(event.globalPosition().toPoint()).toPointF(),
                event.globalPosition(), event.pixelDelta(), event.angleDelta(),
                event.buttons(), event.modifiers(), event.phase(), event.inverted(),
                event.source(),
            )
            QApplication.sendEvent(viewport, forwarded)
        event.accept()
        return True


def scroll_page_on_wheel(widget: QWidget) -> QWidget:
    widget.installEventFilter(_ScrollPageOnWheel(widget))
    return widget


def scrollable(content: QWidget) -> QScrollArea:
    """Wrap a widget so it scrolls instead of being compressed.

    ``setWidgetResizable`` is what lets the content track the viewport width -
    without it the widget keeps whatever size it was given and the layout stops
    responding to window resizing.

    The horizontal bar is disabled on purpose: the content is built to shrink
    horizontally, and a horizontal scrollbar would be a symptom of a minimum
    width that is too large rather than something to paper over.
    """
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    area.setWidget(content)
    return area


def expanding(widget: QWidget, *, vertical: QSizePolicy.Policy | None = None) -> QWidget:
    """Let a widget take the horizontal space it is offered.

    Vertically it stays ``Fixed`` by default, so a taller window gives the extra
    room to the scroll area rather than stretching input controls into
    oversized boxes.
    """
    widget.setSizePolicy(
        QSizePolicy.Policy.Expanding,
        vertical if vertical is not None else QSizePolicy.Policy.Fixed,
    )
    return widget


def shrinkable_combo(combo: QComboBox, minimum_characters: int = 6) -> QComboBox:
    """Make a combo box able to narrow.

    By default a combo's size hint follows its widest entry, so a long model
    name sets a floor on the window width.  Sizing to a short minimum content
    length keeps the hint small while the control still grows to fill the space
    the layout offers it.
    """
    combo.setSizeAdjustPolicy(
        QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
    )
    combo.setMinimumContentsLength(minimum_characters)
    scroll_page_on_wheel(combo)
    expanding(combo)
    return combo


class ResponsiveRow(QWidget):
    """A row of controls that stacks vertically when it gets too narrow.

    Both QHBoxLayout and QVBoxLayout derive from QBoxLayout, so the same layout
    can simply be given a different direction - no widget rebuilding, no
    duplicated layouts, and the children keep their signal connections and
    state across the switch.
    """

    def __init__(
        self,
        *,
        threshold: int = STACK_BELOW_WIDTH,
        spacing: int = 9,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._threshold = threshold
        self._box = QBoxLayout(QBoxLayout.Direction.LeftToRight, self)
        self._box.setContentsMargins(0, 0, 0, 0)
        self._box.setSpacing(spacing)
        self._stacked = False

    def add(self, widget: QWidget, stretch: int = 0) -> QWidget:
        self._box.addWidget(widget, stretch)
        return widget

    def add_stretch(self, stretch: int = 1) -> None:
        self._box.addStretch(stretch)

    @property
    def is_stacked(self) -> bool:
        return self._stacked

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply(self.width() < self._threshold)

    def _apply(self, stack: bool) -> None:
        if stack == self._stacked:
            return
        self._stacked = stack
        self._box.setDirection(
            QBoxLayout.Direction.TopToBottom if stack else QBoxLayout.Direction.LeftToRight
        )
        # A stacked row is only as wide as its widest child, so the children
        # need to be told to fill the column instead.
        for index in range(self._box.count()):
            item = self._box.itemAt(index)
            widget = item.widget()
            if widget is not None and stack:
                widget.setSizePolicy(
                    QSizePolicy.Policy.Expanding, widget.sizePolicy().verticalPolicy()
                )
