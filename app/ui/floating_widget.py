from __future__ import annotations

from PyQt6.QtCore import QPoint, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.ui.theme import ACCENT_COLOR, BORDER_COLOR, PANEL_RAISED

# 吸附判定：浮动窗左上角离主窗口锚点小于该距离（像素）就自动吸附
DOCK_SNAP_DISTANCE = 48
FLOAT_W = 208
FLOAT_H = 52


class FloatingStatusWidget(QWidget):
    """可拖拽脱离 / 自动吸附主窗口右上角的圆角状态悬浮窗。"""

    sync_clicked = pyqtSignal()
    closed_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(None)
        self.main_window = parent
        self.docked = True
        self._drag_offset: QPoint | None = None
        self._dragging = False

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(FLOAT_W, FLOAT_H)
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.setToolTip("按住拖动可脱离主窗口；拖回右上角自动吸附")

        self._build_ui()
        self._dock_to_main()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(10, 6, 8, 6)
        root.setSpacing(6)

        # 左侧：项目总状态图标 + 项目名 / 任务状态
        self.state_icon = QLabel()
        self.state_icon.setFixedSize(16, 16)
        text_box = QVBoxLayout()
        text_box.setContentsMargins(0, 0, 0, 0)
        text_box.setSpacing(0)
        self.project_label = QLabel("未选择项目")
        self.project_label.setStyleSheet("color: #F4F7FB; font-size: 11px; font-weight: 700; background: transparent;")
        self.task_label = QLabel("空闲")
        self.task_label.setStyleSheet("color: #8A99AC; font-size: 10px; background: transparent;")
        text_box.addWidget(self.project_label)
        text_box.addWidget(self.task_label)

        # 中间：各仓库状态图标
        self.repo_icons: dict[str, QLabel] = {}
        repo_box = QHBoxLayout()
        repo_box.setContentsMargins(0, 0, 0, 0)
        repo_box.setSpacing(3)
        for key in ("local", "nas", "github"):
            lbl = QLabel()
            lbl.setFixedSize(14, 14)
            lbl.setStyleSheet("background: transparent;")
            lbl.setToolTip(key)
            self.repo_icons[key] = lbl
            repo_box.addWidget(lbl)

        # 右侧：同步按钮
        self.sync_btn = QPushButton("同步")
        self.sync_btn.setFixedSize(40, 26)
        self.sync_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sync_btn.setStyleSheet(
            f"QPushButton {{ background: {ACCENT_COLOR}; color: #04222B; border: 0; border-radius: 4px; "
            "font-size: 11px; font-weight: 700; padding: 0; min-height: 0; }}"
            f"QPushButton:hover {{ background: #5AF3F3; }}"
            f"QPushButton:disabled {{ background: #16323F; color: #5A6678; }}"
        )
        self.sync_btn.clicked.connect(self.sync_clicked.emit)

        root.addWidget(self.state_icon)
        root.addLayout(text_box, 1)
        root.addLayout(repo_box)
        root.addWidget(self.sync_btn)

    # ------------------------------------------------------------------ 绘制
    def paintEvent(self, event):  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(1, 1, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(rect.x(), rect.y(), rect.width(), rect.height(), 10, 10)
        painter.fillPath(path, QColor(PANEL_RAISED))
        pen = QPen(QColor(ACCENT_COLOR if self.docked else BORDER_COLOR))
        pen.setWidth(1)
        painter.setPen(pen)
        painter.drawPath(path)

    # ------------------------------------------------------------------ 拖拽
    def mousePressEvent(self, event):  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._dragging = False
            event.accept()

    def mouseMoveEvent(self, event):  # noqa: N802 - Qt API
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            self._dragging = True
            event.accept()

    def mouseReleaseEvent(self, event):  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = None
            if self._dragging:
                self._dragging = False
                self._evaluate_dock()
            event.accept()

    def mouseDoubleClickEvent(self, event):  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton and self.main_window is not None:
            self.main_window._show_main_from_floating()
            if not self.docked:
                self._dock_to_main()
            event.accept()

    def contextMenuEvent(self, event):  # noqa: N802 - Qt API
        if self.main_window is None:
            return
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { background: #0B293B; border: 1px solid #1E4B5C; padding: 4px; }"
            "QMenu::item { padding: 6px 20px; border-radius: 4px; }"
            "QMenu::item:selected { background: #0B6B7A; color: white; }"
        )
        show_action = menu.addAction("显示主窗口")
        dock_action = menu.addAction("吸附回右上角")
        menu.addSeparator()
        quit_action = menu.addAction("退出软件")
        chosen = menu.exec(event.globalPos())
        if chosen == show_action:
            self.main_window._show_main_from_floating()
        elif chosen == dock_action:
            self._dock_to_main()
        elif chosen == quit_action:
            self.main_window._exit_from_tray()

    # ------------------------------------------------------------------ 吸附
    def _anchor_pos(self) -> QPoint:
        """主窗口右上角锚点的全局坐标。"""
        mw = self.main_window
        if mw is None:
            return QPoint(0, 0)
        top_right = mw.frameGeometry().topRight()
        return QPoint(top_right.x() - FLOAT_W - 14, top_right.y() + 12)

    def _dock_to_main(self) -> None:
        self.docked = True
        self.move(self._anchor_pos())
        self.update()

    def _evaluate_dock(self) -> None:
        anchor = self._anchor_pos()
        dist = (self.frameGeometry().topLeft() - anchor).manhattanLength()
        if dist <= DOCK_SNAP_DISTANCE:
            self._dock_to_main()
        else:
            self.docked = False
            self.update()

    def follow_main_window(self) -> None:
        """主窗口移动 / 缩放时调用：吸附状态下跟随定位。"""
        if self.docked and self.main_window is not None:
            self.move(self._anchor_pos())

    # ------------------------------------------------------------------ 数据更新
    def set_project_state(self, name: str, state_key: str, color: str, pixmap) -> None:
        self.project_label.setText(name or "未选择项目")
        if pixmap is not None:
            self.state_icon.setPixmap(pixmap)

    def set_task(self, text: str) -> None:
        self.task_label.setText(text)

    def set_repo_state(self, key: str, pixmap, tooltip: str) -> None:
        lbl = self.repo_icons.get(key)
        if lbl is None:
            return
        if pixmap is None:
            lbl.clear()
        else:
            lbl.setPixmap(pixmap)
        lbl.setToolTip(tooltip)

    def set_sync_enabled(self, enabled: bool) -> None:
        self.sync_btn.setEnabled(enabled)
