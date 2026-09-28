"""Verify the layout across window sizes and DPI settings.

Two things are checked at every size:

*Control heights.*  A control shorter than its own size hint is being squeezed,
and a squeezed control is the first sign of a layout that has run out of room.

*Overlap.*  Two controls sharing pixels is the failure a user actually notices,
because the lower one cannot be clicked.  This is what Qt's layouts do when a
window is smaller than the content's minimum and the content has no way to
scroll.

    .venv\\Scripts\\python.exe scripts\\check_layout.py
    $env:QT_SCALE_FACTOR="1.25"; .venv\\Scripts\\python.exe scripts\\check_layout.py
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QPoint, QRect
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QGroupBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
)

from prtsbox.config import ConfigStore
from prtsbox.llama import LlamaManager
from prtsbox.ui.main_window import MainWindow, bootstrap
from prtsbox.ui.settings_dialog import SettingsDialog

_SQUEEZE_TOLERANCE = 4
_INPUT_TYPES = (
    QComboBox,
    QSpinBox,
    QCheckBox,
    QPushButton,
    QLineEdit,
    QLabel,
    QGroupBox,
)

# The sizes the user asked to be verified, plus the default.
WINDOW_SIZES = ((580, 720), (800, 700), (700, 600), (460, 460), (400, 420))


def describe(widget) -> str:
    if isinstance(widget, QComboBox):
        return widget.currentText()[:18]
    if isinstance(widget, QSpinBox):
        return str(widget.value())
    if isinstance(widget, (QCheckBox, QPushButton, QLabel, QGroupBox)):
        text = widget.text() if hasattr(widget, "text") else widget.title()
        return text[:18]
    if isinstance(widget, QLineEdit):
        return widget.placeholderText()[:18] or "line edit"
    return type(widget).__name__


def controls(container) -> list:
    """Visible interactive controls, excluding nested ones.

    A QSpinBox owns a QLineEdit for its text area and a QComboBox owns an
    internal line edit; counting those would compare a widget against its own
    child and always report a total overlap.
    """
    found = []
    for kind in _INPUT_TYPES:
        for widget in container.findChildren(kind):
            if not widget.isVisible() or widget.width() <= 1 or widget.height() <= 1:
                continue
            if any(isinstance(widget.parent(), parent_kind) for parent_kind in _INPUT_TYPES):
                continue
            found.append(widget)
    return found


def visible_rect(widget, container) -> QRect:
    """Widget bounds in ``container`` coordinates, clipped by scroll viewports.

    ``geometry()`` is relative to the direct parent, so controls under different
    parents cannot be compared with it.  Mapping to the container puts them in
    one frame, but a widget inside a QScrollArea keeps a position that can extend
    past the viewport when the content is scrolled - comparing that raw rect
    reports overlaps with whatever sits below the scroll area, even though the
    widget is clipped and cannot be seen or clicked there.
    """
    rect = QRect(widget.mapTo(container, QPoint(0, 0)), widget.size())

    parent = widget.parentWidget()
    while parent is not None and parent is not container:
        if isinstance(parent, QScrollArea):
            viewport = parent.viewport()
            rect = rect.intersected(
                QRect(viewport.mapTo(container, QPoint(0, 0)), viewport.size())
            )
            if rect.isEmpty():
                break
        parent = parent.parentWidget()
    return rect


def is_clipped_away(widget, container) -> bool:
    """Whether a widget is scrolled entirely out of sight."""
    return visible_rect(widget, container).isEmpty()


# Widgets whose height can legitimately differ from their size hint, so the
# naive comparison below would report false problems.
_HEIGHT_HINT_UNRELIABLE = (QLabel, QGroupBox)


def height_problem(widget) -> str | None:
    """Describe a control that is too short for its own content, if any.

    ``sizeHint().height()`` is the wrong measure for two kinds of widget here:
    a word-wrapped QLabel reports the height for a single ideal-width line
    rather than the width it was actually given (``heightForWidth`` is the
    correct question), and a QGroupBox folds its stylesheet margins and title
    band into its hint in a way the layout does not grant back.  Both are
    skipped; the controls that matter for usability are measured directly, and
    genuine clipping still shows up as an overlap between siblings.
    """
    height = widget.height()

    if isinstance(widget, QLabel):
        if not widget.wordWrap():
            return None
        needed = widget.heightForWidth(max(1, widget.width()))
        if needed > height + _SQUEEZE_TOLERANCE:
            return f"换行标签 {describe(widget)!r} 高度 {height} < 需要 {needed}"
        return None

    if isinstance(widget, _HEIGHT_HINT_UNRELIABLE):
        return None

    hint = widget.sizeHint().height()
    if hint > 0 and height < hint - _SQUEEZE_TOLERANCE:
        return f"{type(widget).__name__} {describe(widget)!r} 高度 {height} < 提示 {hint}"
    return None


def inspect(container) -> tuple[int, list[str]]:
    problems: list[str] = []
    widgets = controls(container)

    for widget in widgets:
        problem = height_problem(widget)
        if problem:
            problems.append(problem)

    for index, first in enumerate(widgets):
        for second in widgets[index + 1 :]:
            # A group box spans its children, so sharing pixels with them is
            # expected rather than a defect.
            if isinstance(first, QGroupBox) or isinstance(second, QGroupBox):
                continue
            first_rect = visible_rect(first, container)
            second_rect = visible_rect(second, container)
            # A control scrolled out of its viewport is not reachable, so it
            # cannot be the cause of a visible overlap.
            if first_rect.isEmpty() or second_rect.isEmpty():
                continue
            area = first_rect.intersected(second_rect)
            if area.width() > 2 and area.height() > 2:
                problems.append(
                    f"重叠 {describe(first)!r} × {describe(second)!r} "
                    f"（{area.width()}×{area.height()}）"
                )
    return len(widgets), problems


def settle(widget) -> None:
    layout = widget.layout()
    if layout is not None:
        layout.activate()
    for _ in range(10):
        QApplication.processEvents()
    if layout is not None:
        layout.activate()


def main() -> int:
    logging.basicConfig(level=logging.ERROR)
    bootstrap()
    app = QApplication(sys.argv)

    config = ConfigStore()
    config.load()
    manager = LlamaManager()
    screen_ratio = app.primaryScreen().devicePixelRatio()
    print(f"屏幕缩放 {screen_ratio}")

    failures = 0
    window = MainWindow(config, manager)
    window.load_from_config()
    window.show()

    print("\n=== 主窗口 ===")
    for width, height in WINDOW_SIZES:
        window.resize(width, height)
        window.refresh_windows()
        settle(window)
        central = window.centralWidget()
        needed = central.minimumSizeHint()
        count, problems = inspect(central)
        label = f"{width}×{height}"
        if problems:
            failures += len(problems)
            print(f"  {label}: {len(problems)} 处问题（{count} 个控件）")
            for problem in problems[:6]:
                print(f"      {problem}")
        else:
            print(
                f"  {label}: 正常（{count} 个控件，内容最少需 {needed.height()}px，"
                f"可滚动）"
            )

    print("\n=== 设置对话框 ===")
    dialog = SettingsDialog(config, manager, window)
    dialog.show()
    tabs = dialog.findChild(QTabWidget)
    assert tabs is not None
    for index in range(tabs.count()):
        tabs.setCurrentIndex(index)
        for width, height in ((600, 620), (700, 600), (420, 420)):
            dialog.resize(width, height)
            settle(dialog)
            count, problems = inspect(dialog)
            label = f"{tabs.tabText(index)} {width}×{height}"
            if problems:
                failures += len(problems)
                print(f"  {label}: {len(problems)} 处问题")
                for problem in problems[:6]:
                    print(f"      {problem}")
            else:
                print(f"  {label}: 正常（{count} 个控件）")

    dialog.reject()
    window.close()
    print("\n" + "=" * 46)
    print("布局检查通过" if not failures else f"发现 {failures} 处问题")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
