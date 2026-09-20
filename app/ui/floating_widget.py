from __future__ import annotations

from PyQt6.QtCore import QPoint, QRect, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QBrush, QLinearGradient, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.ui.theme import ACCENT_COLOR, PANEL_RAISED

# 悬浮窗尺寸
FLOAT_W = 208
FLOAT_H = 52
# 槽位比悬浮窗每边大出的像素
SLOT_PAD = 6
SLOT_W = FLOAT_W + SLOT_PAD * 2
SLOT_H = FLOAT_H + SLOT_PAD * 2
# 无槽位时的距离吸附兜底（像素）
DOCK_SNAP_DISTANCE = 30

_BASE_FLAGS = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
_TOP_FLAGS = _BASE_FLAGS | Qt.WindowType.WindowStaysOnTopHint


class DockSlot(QFrame):
    """主窗口右上角的凹陷槽位：比悬浮窗大一圈，带内阴影，提示停靠位置。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(SLOT_W, SLOT_H)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self._occupied = True

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.hint = QLabel("拖回此处吸附")
        self.hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint.setStyleSheet("color: #5A7686; font-size: 11px; background: transparent;")
        lay.addWidget(self.hint)
        self.hint.setVisible(False)

    def set_occupied(self, occupied: bool) -> None:
        self._occupied = occupied
        self.hint.setVisible(not occupied)
        self.update()

    def paintEvent(self, event):  # noqa: N802 - Qt API
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(2, 2, -2, -2)
        radius = 13.0
        path = QPainterPath()
        path.addRoundedRect(
            float(rect.x()), float(rect.y()),
            float(rect.width()), float(rect.height()),
            radius, radius,
        )
        # 坑底：卡片在槽内时偏深，拖出后提亮以便看清边缘
        p.fillPath(path, QColor(4, 18, 28, 205) if self._occupied else QColor(18, 56, 78, 240))

        # 内阴影：裁进圆角区域，四边各铺一道暗渐变
        p.save()
        p.setClipPath(path)
        shadow = 130 if self._occupied else 165
        depth = 13.0

        g = QLinearGradient(0, rect.top(), 0, rect.top() + depth)
        g.setColorAt(0, QColor(0, 0, 0, shadow))
        g.setColorAt(1, QColor(0, 0, 0, 0))
        p.fillRect(rect, QBrush(g))

        g = QLinearGradient(0, rect.bottom(), 0, rect.bottom() - depth)
        g.setColorAt(0, QColor(0, 0, 0, shadow))
        g.setColorAt(1, QColor(0, 0, 0, 0))
        p.fillRect(rect, QBrush(g))

        g = QLinearGradient(rect.left(), 0, rect.left() + depth, 0)
        g.setColorAt(0, QColor(0, 0, 0, shadow))
        g.setColorAt(1, QColor(0, 0, 0, 0))
        p.fillRect(rect, QBrush(g))

        g = QLinearGradient(rect.right(), 0, rect.right() - depth, 0)
        g.setColorAt(0, QColor(0, 0, 0, shadow))
        g.setColorAt(1, QColor(0, 0, 0, 0))
        p.fillRect(rect, QBrush(g))
        p.restore()


class FloatingStatusWidget(QWidget):
    """可拖拽脱离 / 自动吸附主窗口槽位的圆角状态悬浮窗。

    始终是独立顶层 Tool 窗：
    - 吸附时 owner=主窗口、不置顶 -> 显示在槽位、随主窗口走、不浮到其他应用；
    - 拖出时无 owner、置顶 -> 独立存活，不受主窗口最小化/托盘影响。
    owner / 置顶只在松手判定后切换，拖拽过程中不重建窗口，保证一次拖成。
    """

    sync_clicked = pyqtSignal()
    project_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(None)
        self.main_window = parent
        self.docked = True
        self._drag_offset: QPoint | None = None
        self._dragging = False
        self._press_pos: QPoint | None = None
        self._project_name = ""
        self._task_text = ""

        self.setWindowFlags(_BASE_FLAGS)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(FLOAT_W, FLOAT_H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("点击切换项目；按住拖动可脱离；拖回槽位自动吸附")

        self._build_ui()
        self._dock_to_main()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(10, 6, 8, 6)
        root.setSpacing(6)

        # 左侧：项目总状态图标
        self.state_icon = QLabel()
        self.state_icon.setFixedSize(16, 16)
        self.state_icon.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        # 中间竖排：项目名（运行时追加任务状态）+ 仓库状态图标
        text_box = QVBoxLayout()
        text_box.setContentsMargins(0, 0, 0, 0)
        text_box.setSpacing(2)
        self.project_label = QLabel("未选择项目")
        self.project_label.setStyleSheet("color: #F4F7FB; font-size: 11px; font-weight: 700; background: transparent;")
        self.project_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        repo_row = QHBoxLayout()
        repo_row.setContentsMargins(0, 0, 0, 0)
        repo_row.setSpacing(4)
        self.repo_icons: dict[str, QLabel] = {}
        for key in ("local", "nas", "github"):
            lbl = QLabel()
            lbl.setFixedSize(14, 14)
            lbl.setStyleSheet("background: transparent;")
            lbl.setToolTip(key)
            lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            self.repo_icons[key] = lbl
            repo_row.addWidget(lbl)
        repo_row.addStretch(1)
        text_box.addWidget(self.project_label)
        text_box.addLayout(repo_row)

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
        root.addWidget(self.sync_btn)

    # ------------------------------------------------------------------ 绘制
    def paintEvent(self, event):  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(1, 1, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(rect.x(), rect.y(), rect.width(), rect.height(), 10, 10)
        painter.fillPath(path, QColor(PANEL_RAISED))
        pen = QPen(QColor(ACCENT_COLOR))
        pen.setWidth(2)
        painter.setPen(pen)
        painter.drawPath(path)

    # ------------------------------------------------------------------ 拖拽 / 点击
    def mousePressEvent(self, event):  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            # 拖拽全程不重建窗口，只记录偏移
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._press_pos = event.globalPosition().toPoint()
            self._dragging = False
            event.accept()

    def mouseMoveEvent(self, event):  # noqa: N802 - Qt API
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            if self._press_pos is not None:
                moved = (event.globalPosition().toPoint() - self._press_pos).manhattanLength()
                if moved >= 4:
                    self._dragging = True
            event.accept()

    def mouseReleaseEvent(self, event):  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            was_dragging = self._dragging
            self._drag_offset = None
            self._press_pos = None
            self._dragging = False
            if was_dragging:
                # 松手后才切换归属 / 置顶（此时重建窗口不影响拖拽）
                self._evaluate_dock()
            else:
                self.project_clicked.emit()
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
        dock_action = menu.addAction("吸附回槽位")
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
        """槽位中心（悬浮窗居中放入后）的全局坐标。"""
        mw = self.main_window
        if mw is not None and getattr(mw, "dock_slot", None) is not None:
            return mw.dock_slot.mapToGlobal(QPoint(SLOT_PAD, SLOT_PAD))
        if mw is None:
            return QPoint(0, 0)
        top_right = mw.frameGeometry().topRight()
        return QPoint(top_right.x() - FLOAT_W - 14, top_right.y() + 42)

    def _dock_to_main(self) -> None:
        self.docked = True
        # owner=主窗口：显示在主窗口之上、随其最小化/关闭，但不浮到其他应用
        self.setParent(self.main_window, _BASE_FLAGS)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.move(self._anchor_pos())
        if self.main_window is not None:
            self.main_window._on_floating_dock_changed(True)
        self.show()
        self.update()

    def _detach(self, keep_pos: QPoint) -> None:
        self.docked = False
        # 无 owner + 全局置顶：脱离主窗口独立存活
        self.setParent(None, _TOP_FLAGS)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.move(keep_pos)
        self.show()
        if self.main_window is not None:
            self.main_window._on_floating_dock_changed(False)
        self.update()

    def _evaluate_dock(self) -> None:
        mw = self.main_window
        slot = getattr(mw, "dock_slot", None) if mw is not None else None
        should_dock = False
        if slot is not None:
            sg = slot.mapToGlobal(QPoint(0, 0))
            slot_rect = QRect(sg.x(), sg.y(), slot.width(), slot.height())
            inter = self.frameGeometry().intersected(slot_rect)
            if not inter.isEmpty():
                overlap = inter.width() * inter.height()
                should_dock = overlap >= int(FLOAT_W * FLOAT_H * 0.4)
        else:
            anchor = self._anchor_pos()
            dist = (self.frameGeometry().topLeft() - anchor).manhattanLength()
            should_dock = dist <= DOCK_SNAP_DISTANCE
        if should_dock:
            self._dock_to_main()
        else:
            self._detach(self.frameGeometry().topLeft())

    def follow_main_window(self) -> None:
        if self.docked and self.main_window is not None:
            self.move(self._anchor_pos())

    # ------------------------------------------------------------------ 数据更新
    def set_project_state(self, name: str, state_key: str, color: str, pixmap) -> None:
        self._project_name = name or "未选择项目"
        self._refresh_title()
        if pixmap is not None:
            self.state_icon.setPixmap(pixmap)

    def set_task(self, text: str) -> None:
        self._task_text = text or ""
        self._refresh_title()

    def _refresh_title(self) -> None:
        if self._task_text and self._task_text != "空闲":
            self.project_label.setText(f"{self._project_name} · {self._task_text}")
        else:
            self.project_label.setText(self._project_name)

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
