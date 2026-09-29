"""Design tokens and stylesheets for the dark and light themes.

Both themes are generated from the same token names, so adding a widget means
adding one rule rather than two.  Body text colours clear WCAG AA against the
surface they sit on.

The stylesheet is a :class:`string.Template` rather than an f-string: CSS is
full of braces, and doubling every one of them to satisfy an f-string makes the
sheet unreadable and easy to break.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from string import Template

from PySide6.QtGui import QColor

_ASSETS = Path(__file__).resolve().parent / "assets"


def _asset(name: str) -> str:
    """Absolute posix path for a stylesheet ``url()``.

    Must be absolute: the application changes its working directory to the data
    folder at start-up so the OCR cache lands there, which would break any
    relative path.
    """
    return (_ASSETS / name).as_posix()


@dataclass(frozen=True)
class Tokens:
    name: str
    window: str
    surface: str
    surface_alt: str
    border: str
    border_strong: str
    text: str
    text_muted: str
    accent: str
    accent_hover: str
    accent_pressed: str
    accent_text: str
    danger: str
    input_bg: str
    hover: str

    def overlay_colors(self) -> dict[str, QColor]:
        if self.name == "light":
            return {
                "background": QColor(255, 255, 255, 224),
                "translation": QColor(18, 22, 32),
                "source": QColor(104, 112, 130),
            }
        return {
            "background": QColor(10, 14, 22, 210),
            "translation": QColor(234, 241, 255),
            "source": QColor(146, 160, 186),
        }


DARK = Tokens(
    name="dark",
    window="#0d1117",
    surface="#161b22",
    surface_alt="#21262d",
    border="#30363d",
    border_strong="#3d444d",
    text="#e6edf3",
    text_muted="#8b949e",
    accent="#2f81f7",
    accent_hover="#4d94ff",
    accent_pressed="#1f6feb",
    accent_text="#ffffff",
    danger="#f85149",
    input_bg="#0d1117",
    hover="#21262d",
)

LIGHT = Tokens(
    name="light",
    window="#f6f8fa",
    surface="#ffffff",
    surface_alt="#eef1f5",
    border="#d0d7de",
    border_strong="#afb8c1",
    text="#1f2328",
    text_muted="#59636e",
    accent="#0969da",
    accent_hover="#218bff",
    accent_pressed="#0550ae",
    accent_text="#ffffff",
    danger="#cf222e",
    input_bg="#ffffff",
    hover="#eaeef2",
)

PRIESTESS = Tokens(
    name="priestess",
    window="#111018",
    surface="#1c1927",
    surface_alt="#292438",
    border="#423a55",
    border_strong="#817093",
    text="#f0edf5",
    text_muted="#b6acc8",
    accent="#c5b0e9",
    accent_hover="#dac9f5",
    accent_pressed="#ab91d4",
    accent_text="#21182f",
    danger="#ff9eab",
    input_bg="#14121d",
    hover="#393047",
)

_SHEET = Template(
    """
QWidget {
    color: $text;
    font-family: "Microsoft YaHei UI", "Segoe UI", "Microsoft YaHei", sans-serif;
    font-size: 13px;
    background: transparent;
}
QMainWindow, QDialog { background: $window; }

/* ---- headers ---- */
QLabel[role="title"] { font-size: 19px; font-weight: 600; }
QLabel[role="subtitle"] { color: $text_muted; font-size: 12px; }
QLabel[role="hint"] { color: $text_muted; font-size: 12px; }
QLabel[role="status"] { color: $text_muted; }
QLabel[role="error"] { color: $danger; }
QLabel[role="section"] { color: $text_muted; font-weight: 600; font-size: 12px; }

/* ---- group boxes ---- */
QGroupBox {
    background: $surface;
    border: 1px solid $border;
    border-radius: 10px;
    margin-top: 20px;
    padding: 18px 16px 16px 16px;
    font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 0 6px;
    color: $text_muted;
    background: transparent;
}

/* ---- buttons ---- */
QPushButton {
    background: $surface_alt;
    border: 1px solid $border;
    border-radius: 7px;
    padding: 8px 16px;
    min-height: 18px;
}
QPushButton:hover { background: $hover; border-color: $border_strong; }
QPushButton:pressed { background: $border; }
QPushButton:disabled { color: $text_muted; background: $surface; border-color: $border; }
QPushButton#languageButton:checked { color: $accent; border-color: $accent; }

QPushButton[role="primary"] {
    background: $accent;
    border: 1px solid $accent;
    color: $accent_text;
    font-size: 14px;
    font-weight: 600;
    padding: 13px 20px;
    border-radius: 9px;
}
QPushButton[role="primary"]:hover { background: $accent_hover; border-color: $accent_hover; }
QPushButton[role="primary"]:pressed { background: $accent_pressed; border-color: $accent_pressed; }
QPushButton[role="primary"]:disabled {
    background: $surface_alt; border-color: $border; color: $text_muted;
}

QPushButton[role="danger"] { color: $danger; }
QPushButton[role="danger"]:hover { border-color: $danger; }

/* ---- inputs ----
   min-height is 26px, not 18px.  QSS min-height sets the widget's minimum size
   and *overrides* the style's own minimumSizeHint, so a value that is too small
   silently clips the widget instead of being enlarged to fit.  A spin box carries
   roughly 4px more fixed overhead than a combo box for its two arrow buttons, so
   18px capped the combo at 36px and the spin box at 36px against a 40px need -
   which is why its value box overlapped the checkbox beneath it under 125%
   display scaling.  26px gives both the same comfortable 44px. */
QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox {
    background: $input_bg;
    border: 1px solid $border;
    border-radius: 7px;
    padding: 8px 10px;
    min-height: 26px;
    selection-background-color: $accent;
    selection-color: $accent_text;
}
QComboBox:hover, QLineEdit:hover, QSpinBox:hover { border-color: $border_strong; }
QComboBox:focus, QLineEdit:focus, QSpinBox:focus { border-color: $accent; }
QComboBox:disabled, QLineEdit:disabled, QSpinBox:disabled {
    color: $text_muted; background: $surface_alt;
}
QComboBox::drop-down { border: none; width: 26px; }
QComboBox::down-arrow { image: url("$chevron_down"); width: 14px; height: 14px; }
QComboBox QAbstractItemView {
    background: $surface;
    border: 1px solid $border;
    border-radius: 7px;
    padding: 4px;
    selection-background-color: $accent;
    selection-color: $accent_text;
    outline: none;
}

/* The spin buttons need an explicit height, not just a width.  Styling them
   without one makes Qt fall back to a collapsed size hint, and the whole
   QSpinBox is then laid out shorter than it needs.  The 13px arrows add up to
   exactly the 26px content box, so the spin box needs no more fixed overhead
   than a combo box and the two line up at the same height. */
QSpinBox::up-button {
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 24px;
    height: 13px;
    background: transparent;
    border: none;
    margin-right: 1px;
}
QSpinBox::down-button {
    subcontrol-origin: border;
    subcontrol-position: bottom right;
    width: 24px;
    height: 13px;
    background: transparent;
    border: none;
    margin-right: 1px;
}
QSpinBox::up-button:hover, QSpinBox::down-button:hover { background: $hover; }
QSpinBox::up-arrow { image: url("$chevron_up"); width: 11px; height: 11px; }
QSpinBox::down-arrow { image: url("$chevron_down"); width: 11px; height: 11px; }

/* ---- checkbox ----
   min-height keeps the 18px indicator from being squeezed against the row
   below; without it the box ended up 19px tall against a 25px minimum hint. */
QCheckBox {
    background: transparent;
    spacing: 9px;
    padding: 4px 0;
    min-height: 20px;
}
QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border: 1px solid $border_strong;
    border-radius: 5px;
    background: $input_bg;
}
QCheckBox::indicator:hover { border-color: $accent; }
QCheckBox::indicator:checked {
    background: $accent;
    border-color: $accent;
    image: url("$check");
}
QCheckBox::indicator:disabled { border-color: $border; background: $surface_alt; }
QCheckBox:disabled { color: $text_muted; }

/* ---- progress ---- */
QProgressBar {
    background: $input_bg;
    border: 1px solid $border;
    border-radius: 6px;
    height: 10px;
}
QProgressBar::chunk { background: $accent; border-radius: 5px; }

/* ---- tabs ---- */
QTabWidget::pane {
    background: $surface;
    border: 1px solid $border;
    border-radius: 10px;
    top: -1px;
}
QTabBar::tab {
    background: transparent;
    color: $text_muted;
    padding: 9px 18px;
    margin-right: 2px;
    border: 1px solid transparent;
    border-bottom: none;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
}
QTabBar::tab:hover { color: $text; }
QTabBar::tab:selected {
    color: $text;
    background: $surface;
    border-color: $border;
}
QTabBar::tab:selected:!first { border-left-color: $border; }
/* The pane already draws this edge; a second one from the selected tab would
   show as a dark seam across the top of the page. */
QTabBar::tab:selected { border-bottom: 1px solid $surface; }

/* ---- scroll areas ----
   Panels scroll instead of being compressed on a short window.  The area and
   its viewport are transparent and frameless so the widget looks exactly as it
   did before the scroll area was introduced - no extra border, no tinted
   background band behind the group boxes. */
QScrollArea { border: none; background: transparent; }
QScrollArea > QWidget > QWidget { background: transparent; }
QScrollArea > QWidget > QScrollBar { background: transparent; }
QScrollBar:vertical { background: transparent; width: 11px; margin: 2px; }
QScrollBar::handle:vertical {
    background: $border_strong; border-radius: 5px; min-height: 30px;
}
QScrollBar::handle:vertical:hover { background: $text_muted; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QScrollBar:horizontal { background: transparent; height: 11px; margin: 2px; }
QScrollBar::handle:horizontal {
    background: $border_strong; border-radius: 5px; min-width: 30px;
}

QToolTip {
    background: $surface_alt;
    color: $text;
    border: 1px solid $border_strong;
    border-radius: 6px;
    padding: 6px 9px;
}
"""
)


def tokens_for(name: str) -> Tokens:
    return {"light": LIGHT, "priestess": PRIESTESS}.get(str(name).lower(), DARK)


def stylesheet(tokens: Tokens) -> str:
    sheet = _SHEET.substitute(
        **asdict(tokens),
        chevron_down=_asset(
            "chevron-down-light.svg" if tokens.name == "light" else "chevron-down.svg"
        ),
        chevron_up=_asset(
            "chevron-up-light.svg" if tokens.name == "light" else "chevron-up.svg"
        ),
        check=_asset("check.svg"),
    )
    if tokens.name == "priestess":
        sheet += '''
QMainWindow, QDialog#settingsDialog {
    border-image: url("%s") 0 0 0 0 stretch stretch;
}
QGroupBox, QTabWidget::pane {
    background: rgba(24, 21, 34, 225);
    border-color: #51445f;
}
QLabel[role="title"] { color: #e3d6fa; }
QGroupBox::title { color: #cbb9df; }
QPushButton[role="primary"] { border-color: #e3d6fa; }
''' % _asset("priestess-background.png")
    return sheet
