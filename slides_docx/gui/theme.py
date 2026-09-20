from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication


@dataclass(frozen=True)
class Theme:
    dark: bool
    window: str
    surface: str
    surface_raised: str
    border: str
    text: str
    muted: str
    accent: str
    accent_hover: str
    accent_text: str
    success: str
    disabled: str
    selection: str


LIGHT_THEME = Theme(
    dark=False,
    window="#f4f6f9",
    surface="#ffffff",
    surface_raised="#eef2f7",
    border="#cfd7e2",
    text="#172033",
    muted="#566176",
    accent="#2767b2",
    accent_hover="#1f579b",
    accent_text="#ffffff",
    success="#217346",
    disabled="#8a94a3",
    selection="#b9d9ff",
)

DARK_THEME = Theme(
    dark=True,
    window="#17191d",
    surface="#22262c",
    surface_raised="#2b3038",
    border="#454d59",
    text="#f1f3f5",
    muted="#b7c0cc",
    accent="#72b7ff",
    accent_hover="#96caff",
    accent_text="#101820",
    success="#68d391",
    disabled="#7c8591",
    selection="#315f8d",
)


def system_theme(application: QApplication, color_scheme=None) -> Theme:
    """Return the theme matching Qt's system color scheme, with a palette fallback."""
    scheme = color_scheme
    if scheme is None:
        scheme = application.styleHints().colorScheme()
    if scheme == Qt.ColorScheme.Dark:
        return DARK_THEME
    if scheme == Qt.ColorScheme.Light:
        return LIGHT_THEME
    window = application.palette().color(QPalette.ColorRole.Window)
    return DARK_THEME if window.lightness() < 128 else LIGHT_THEME


def stylesheet(theme: Theme) -> str:
    return f"""
        QMainWindow, QWidget#appRoot {{
            background-color: {theme.window};
            color: {theme.text};
        }}
        QWidget {{
            color: {theme.text};
        }}
        QLabel#title {{
            font-size: 28px;
            font-weight: 700;
            color: {theme.text};
        }}
        QLabel#subtitle {{
            color: {theme.muted};
            margin-bottom: 10px;
        }}
        QLabel#step {{
            font-size: 16px;
            font-weight: 600;
            color: {theme.accent};
        }}
        QLabel#contactPreview {{
            background-color: {theme.surface};
            color: {theme.muted};
            border: 1px solid {theme.border};
        }}
        QLabel#success {{
            color: {theme.success};
            font-size: 18px;
            padding: 30px;
        }}
        QFrame#card {{
            background-color: {theme.surface};
            border: 1px solid {theme.border};
            border-radius: 10px;
        }}
        QLineEdit, QPlainTextEdit, QComboBox, QDateEdit, QDoubleSpinBox {{
            background-color: {theme.surface};
            color: {theme.text};
            border: 1px solid {theme.border};
            border-radius: 5px;
            padding: 6px;
            selection-background-color: {theme.selection};
            selection-color: {theme.text};
        }}
        QComboBox QAbstractItemView {{
            background-color: {theme.surface};
            color: {theme.text};
            border: 1px solid {theme.border};
            selection-background-color: {theme.selection};
            selection-color: {theme.text};
        }}
        QPushButton, QToolButton {{
            background-color: {theme.surface_raised};
            color: {theme.text};
            border: 1px solid {theme.border};
            border-radius: 5px;
            padding: 7px 14px;
        }}
        QPushButton:hover, QToolButton:hover {{
            border-color: {theme.accent};
        }}
        QPushButton:pressed, QToolButton:pressed {{
            background-color: {theme.selection};
        }}
        QPushButton:default {{
            background-color: {theme.accent};
            color: {theme.accent_text};
            border-color: {theme.accent};
        }}
        QPushButton:default:hover {{
            background-color: {theme.accent_hover};
            border-color: {theme.accent_hover};
        }}
        QPushButton:disabled, QToolButton:disabled,
        QLineEdit:disabled, QComboBox:disabled, QDateEdit:disabled,
        QDoubleSpinBox:disabled, QCheckBox:disabled {{
            color: {theme.disabled};
        }}
        QProgressBar {{
            background-color: {theme.surface};
            color: {theme.text};
            border: 1px solid {theme.border};
            border-radius: 5px;
            text-align: center;
        }}
        QProgressBar::chunk {{
            background-color: {theme.accent};
            border-radius: 4px;
        }}
        QScrollArea, QScrollArea > QWidget > QWidget {{
            background-color: {theme.window};
        }}
        QScrollBar:vertical, QScrollBar:horizontal {{
            background-color: {theme.surface};
            border: none;
        }}
        QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
            background-color: {theme.border};
            border-radius: 4px;
            min-height: 24px;
            min-width: 24px;
        }}
        QToolTip {{
            background-color: {theme.surface_raised};
            color: {theme.text};
            border: 1px solid {theme.border};
        }}
    """


def apply_system_theme(application: QApplication, color_scheme=None) -> Theme:
    theme = system_theme(application, color_scheme)
    application.setStyleSheet(stylesheet(theme))
    application.setProperty("darkMode", theme.dark)
    return theme
