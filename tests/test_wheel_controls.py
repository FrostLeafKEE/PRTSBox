import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QSpinBox, QVBoxLayout, QWidget

from prtsbox.ui.layout import scrollable, shrinkable_combo, scroll_page_on_wheel


@pytest.mark.parametrize('kind', ['combo', 'spin'])
@pytest.mark.parametrize('focused', [False, True])
def test_wheel_scrolls_page_without_changing_value(kind, focused):
    app = QApplication.instance() or QApplication([])
    content = QWidget()
    layout = QVBoxLayout(content)
    if kind == 'combo':
        control = shrinkable_combo(QComboBox())
        control.addItems(['one', 'two', 'three'])
        control.setCurrentIndex(1)
        value = control.currentIndex
    else:
        control = QSpinBox()
        scroll_page_on_wheel(control)
        control.setValue(30)
        value = control.value
    layout.addWidget(control)
    filler = QWidget()
    filler.setMinimumHeight(1200)
    layout.addWidget(filler)
    area = scrollable(content)
    area.resize(400, 300)
    area.show()
    app.processEvents()
    try:
        if focused:
            control.setFocus()
        else:
            control.clearFocus()
        before = value()
        for delta in [-120, 120]:
            bar = area.verticalScrollBar()
            bar.setValue(200)
            event = QWheelEvent(QPointF(10, 10), QPointF(control.mapToGlobal(QPoint(10, 10))),
                                QPoint(), QPoint(0, delta), Qt.MouseButton.NoButton,
                                Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
            QApplication.sendEvent(control, event)
            assert value() == before
            assert bar.value() > 200 if delta < 0 else bar.value() < 200
        QTest.keyClick(control, Qt.Key.Key_Down)
        assert value() != before
    finally:
        area.close()
