from __future__ import annotations

import ctypes
import sys

from PyQt6.QtCore import QEvent, QObject
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication, QDialog

# RepoPulse.png uses a deep navy canvas with a bright cyan signal mark.
# Keep the same pairing throughout the application so the window and logo read
# as one product instead of a generic blue Git client.
ACCENT_COLOR = "#16E5EE"
ACCENT_HOVER = "#5AF3F3"
ACCENT_DARK = "#0B6B7A"
CANVAS_COLOR = "#071D2C"
PANEL_COLOR = "#0B293B"
PANEL_RAISED = "#103448"
BORDER_COLOR = "#1E4B5C"

# Shared spacing scale. Keeping UI distances on a 4px rhythm makes panels,
# cards, dialogs, and controls read as one consistent interface.
SPACE_1 = 4
SPACE_2 = 8
SPACE_3 = 12
SPACE_4 = 16


class _DarkTitleBarFilter(QObject):
    """Force the dark title bar on every top-level dialog when it is shown."""

    def eventFilter(self, obj, event):  # noqa: N802 - Qt API
        if event.type() == QEvent.Type.Show and isinstance(obj, QDialog):
            set_dark_title_bar(obj)
        return False


def apply_theme(app: QApplication) -> None:
    app.setFont(QFont("Microsoft YaHei UI", 10))
    app.setStyleSheet(
        """
        QWidget { color: #E8ECF2; background: #071D2C; }
        QMainWindow, QDialog { background: #071D2C; }
        QListWidget, QPushButton, QCheckBox, QComboBox, QLineEdit, QTreeWidget,
        QTableWidget, QTabBar, QToolButton, QMenu { outline: none; }
        QLabel { background: transparent; }
        QMenuBar { background: #071D2C; color: #E8ECF2; padding: 4px 6px; }
        QMenuBar::item { padding: 6px 10px; border-radius: 4px; }
        QMenuBar::item:selected { background: #123D4D; color: white; }
        QMenuBar::item:pressed { background: #0B6B7A; color: white; }
        QMenu { background: #0B293B; border: 1px solid #1E4B5C; padding: 4px; }
        QMenu::item { padding: 7px 28px 7px 10px; border-radius: 4px; }
        QMenu::item:selected { background: #0B6B7A; color: white; }
        QMenu::separator { height: 1px; background: #1E4B5C; margin: 4px 8px; }
        QListWidget, QPlainTextEdit, QTableWidget, QLineEdit, QComboBox {
            background: #0B293B; border: 1px solid #1E4B5C; border-radius: 4px;
            padding: 6px;
        }
        QListWidget::item { padding: 6px 8px; border-radius: 4px; }
        QListWidget::item:hover { background: #123344; }
        QListWidget::item:selected { background: #0B6B7A; color: white; }
        QPushButton {
            background: #103448; border: 1px solid #1E4B5C; border-radius: 4px;
            padding: 6px 12px; min-height: 28px;
        }
        QPushButton:hover { background: #16485B; border-color: #16E5EE; }
        QPushButton:pressed { background: #0B6B7A; }
        QPushButton:disabled { color: #788396; background: #0A202E; }
        QGroupBox { border: 1px solid #1E4B5C; border-radius: 5px; margin-top: 8px; padding: 8px; }
        QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; color: #8CECEF; }
        QHeaderView::section { background: #103448; color: #CDD6E5; padding: 6px; border: 0; }
        QTableWidget { gridline-color: #1E4B5C; }
        QScrollBar:vertical { width: 6px; background: transparent; }
        QScrollBar::handle:vertical { background: #2C7180; border-radius: 3px; min-height: 24px; }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        """
    )
    # Keep a strong reference so the filter is not garbage-collected (pitfall 18).
    app._dark_title_bar_filter = _DarkTitleBarFilter(app)
    app.installEventFilter(app._dark_title_bar_filter)


def set_dark_title_bar(window) -> None:
    if sys.platform != "win32":
        return
    try:
        hwnd = int(window.winId())
        value = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(value), ctypes.sizeof(value))
    except Exception:
        pass
