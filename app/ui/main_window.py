from __future__ import annotations

import os
import html
import uuid
import webbrowser
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

from PyQt6.QtCore import QPoint, QSettings, QTimer, QSize, Qt, QVariantAnimation, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QApplication,
    QCheckBox,
    QDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMainWindow,
    QInputDialog,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
    QToolButton,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
)

from app.core.git_service import GitService
from app.models import ProjectConfig, RemoteConfig
from app.storage.project_store import ProjectStore
from app.ui.dialogs import GitDialog, ProjectDetailDialog, RepositoryDetailDialog, SettingsDialog
from app.ui.theme import (
    ACCENT_COLOR,
    ACCENT_DARK,
    ACCENT_HOVER,
    BORDER_COLOR,
    CANVAS_COLOR,
    PANEL_COLOR,
    PANEL_RAISED,
    SPACE_1,
    SPACE_2,
    SPACE_3,
    set_dark_title_bar,
)
from app.workers import ProjectCreateWorker, ProjectDeleteWorker, StatusWorker, SyncWorker


def _settings_icon() -> QIcon:
    pixmap = QPixmap(20, 20)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor("#A9B5C8"), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    for y, knob_x in ((5, 7), (10, 13), (15, 9)):
        painter.drawLine(3, y, 17, y)
        painter.setBrush(QColor("#A9B5C8"))
        painter.drawEllipse(knob_x - 2, y - 2, 4, 4)
    painter.end()
    return QIcon(pixmap)


class OutlinedLabel(QLabel):
    """绘制渠道卡片标题文字，不增加额外容器边框。"""

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        font = self.font()
        font.setBold(True)
        metrics = self.fontMetrics()
        text = metrics.elidedText(self.text(), Qt.TextElideMode.ElideRight, max(0, self.width() - 4))
        painter.setFont(font)
        text_rect = self.rect().adjusted(2, 0, -2, 0)
        flags = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        painter.setPen(QPen(QColor("#F4F7FB"), 1.0))
        painter.drawText(text_rect, flags, text)
        painter.end()


class BusyDialog(QDialog):
    """Application-modal progress hint for operations running in workers."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("请等待")
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setModal(True)
        self.setFixedSize(270, 124)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._message = "正在处理"
        self._dot_count = 0
        self._timer = QTimer(self)
        self._timer.setInterval(320)
        self._timer.timeout.connect(self._animate)

        title = QLabel("请等待")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(
            f"color: {ACCENT_COLOR}; font-size: 18px; font-weight: 700;"
        )
        self.message_label = QLabel()
        self.message_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.message_label.setWordWrap(True)
        self.message_label.setMaximumHeight(34)
        self.message_label.setStyleSheet("color: #D7DFEB; font-size: 11px;")
        progress = QProgressBar()
        progress.setRange(0, 0)
        progress.setTextVisible(False)
        progress.setFixedHeight(6)
        progress.setStyleSheet(
            f"QProgressBar {{ background: {PANEL_RAISED}; border: 1px solid {BORDER_COLOR}; border-radius: 3px; }}"
            f"QProgressBar::chunk {{ background: {ACCENT_COLOR}; border-radius: 3px; }}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE_3, SPACE_2, SPACE_3, SPACE_2)
        layout.setSpacing(SPACE_1)
        layout.addWidget(title)
        layout.addWidget(self.message_label)
        layout.addWidget(progress)
        self.setStyleSheet(
            f"QDialog {{ background: {PANEL_COLOR}; border: 1px solid {ACCENT_DARK}; border-radius: 12px; }}"
        )

    def set_message(self, message: str) -> None:
        self._message = (message.strip() or "正在处理")[:48]
        self._dot_count = 0
        self._animate()

    def _animate(self) -> None:
        self.message_label.setText(f"{self._message}{'.' * self._dot_count}")
        self._dot_count = (self._dot_count + 1) % 4

    def showEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self.parentWidget():
            center = self.parentWidget().frameGeometry().center()
            self.move(center - self.rect().center())
        self._timer.start()
        super().showEvent(event)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        self._timer.stop()
        super().closeEvent(event)


class StatusCard(QFrame):
    clicked = pyqtSignal(str)
    drag_started = pyqtSignal(str)
    drag_finished = pyqtSignal(str, QPoint)

    def __init__(
        self,
        key: str,
        title: str,
        subtitle: str,
        status: str,
        status_color: str,
        body: str,
        rich_body: bool = False,
        detail_body: str | None = None,
        recent_commit: str = "",
    ):
        super().__init__()
        self.card_key = key
        self.card_title = title
        self.card_status = status
        self.card_status_color = status_color
        self.card_body = body
        self.card_rich_body = rich_body
        self._selected = False
        self._hovered = False
        self._hover_animation = QVariantAnimation(self)
        self._hover_animation.setDuration(140)
        self._hover_animation.valueChanged.connect(self._on_hover_color)
        self._press_pos: QPoint | None = None
        self._dragging = False
        self._click_timer = QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.setInterval(240)
        self._click_timer.timeout.connect(lambda: self.clicked.emit(self.card_key))
        self.setObjectName("statusCard")
        self.setFixedHeight(185)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE_3, SPACE_2, SPACE_3, SPACE_2)
        layout.setSpacing(SPACE_1)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        title_label = OutlinedLabel(title)
        title_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        title_label.setStyleSheet(
            "font-size: 20px; font-weight: 700; color: #F4F7FB;"
        )
        status_label = QLabel(status)
        status_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        status_label.setStyleSheet(
            f"color: {status_color}; font-size: 11px; font-weight: 700; background: {PANEL_RAISED}; padding: 2px 8px; border-radius: 6px;"
        )
        status_label.setContentsMargins(0, 0, 0, 0)
        status_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        title_label.setMinimumWidth(60)
        title_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        header.addWidget(title_label, 1)
        header.addWidget(status_label)
        layout.addLayout(header)

        title_label.setWordWrap(True)
        title_label.setMinimumWidth(0)
        title_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        display_body = body
        if recent_commit:
            display_body = "<br>".join(
                line for line in body.split("<br>")
                if "最近提交：" not in line
            )
        body_label = QLabel(display_body)
        body_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        if rich_body:
            body_label.setTextFormat(Qt.TextFormat.RichText)
        body_label.setWordWrap(True)
        body_label.setMinimumWidth(0)
        body_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        body_label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        body_label.setContentsMargins(0, 0, 0, 0)
        body_label.setStyleSheet("color: #D7DFEB; padding: 0; margin: 0;")
        layout.addWidget(body_label)

        if recent_commit:
            recent_label = QLabel(f"最近提交：{recent_commit}")
            recent_label.setTextFormat(Qt.TextFormat.PlainText)
            recent_label.setWordWrap(True)
            # Reserve exactly two lines. Plain text avoids rich-text line boxes
            # adding a third visible line when a subject wraps.
            two_line_height = recent_label.fontMetrics().lineSpacing() * 2
            recent_label.setFixedHeight(two_line_height)
            recent_label.setMaximumHeight(two_line_height)
            recent_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
            recent_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            recent_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            recent_label.setContentsMargins(0, 0, 0, 0)
            recent_label.setStyleSheet("color: #D7DFEB; padding: 0; margin: 0;")
            layout.addWidget(recent_label)

        # Keep spare card height below the content, never between the body and recent commit.
        layout.addStretch(1)

        if detail_body is not None:
            self.card_body = detail_body

        self.set_selected(False)

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        border = ACCENT_COLOR if selected else BORDER_COLOR
        background = "#103646" if selected else PANEL_COLOR
        self._apply_style(border, background)

    def _apply_style(self, border: str, background: str) -> None:
        self.setStyleSheet(
            f"QFrame#statusCard {{ background: {background}; border: 1px solid {border}; border-radius: 6px; }}"
        )

    def _on_hover_color(self, color: QColor) -> None:
        if not self._selected:
            self._apply_style(color.name(), "#103646" if self._hovered else PANEL_COLOR)

    def enterEvent(self, event) -> None:  # noqa: N802 - Qt API
        self._hovered = True
        self._hover_animation.stop()
        self._hover_animation.setStartValue(QColor(BORDER_COLOR))
        self._hover_animation.setEndValue(QColor(ACCENT_COLOR))
        self._hover_animation.start()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt API
        self._hovered = False
        self._hover_animation.stop()
        self._hover_animation.setStartValue(QColor(ACCENT_COLOR))
        self._hover_animation.setEndValue(QColor(BORDER_COLOR))
        self._hover_animation.start()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.position().toPoint()
            self._dragging = False
            self._click_timer.stop()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt API
        if (
            self._press_pos is not None
            and not self._dragging
            and event.buttons() & Qt.MouseButton.LeftButton
            and (event.position().toPoint() - self._press_pos).manhattanLength() >= QApplication.startDragDistance()
        ):
            self._dragging = True
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.drag_started.emit(self.card_key)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            if self._dragging:
                self.setCursor(Qt.CursorShape.PointingHandCursor)
                self.drag_finished.emit(self.card_key, self.mapToGlobal(event.position().toPoint()))
            else:
                self._click_timer.start()
            self._press_pos = None
            self._dragging = False
        super().mouseReleaseEvent(event)


class ProjectListWidget(QListWidget):
    hover_row_changed = pyqtSignal(int)
    switch_clicked = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self._hovered_switch: CapsuleSwitch | None = None

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt API
        item = self.itemAt(event.position().toPoint())
        self.hover_row_changed.emit(self.row(item) if item else -1)
        current_switch = None
        if item is not None and event.position().x() >= self.visualItemRect(item).right() - 56:
            row_widget = self.itemWidget(item)
            if row_widget is not None:
                current_switch = row_widget.findChild(CapsuleSwitch)
        if self._hovered_switch is not current_switch:
            if self._hovered_switch is not None:
                self._hovered_switch.set_hovered(False)
            self._hovered_switch = current_switch
        if current_switch is not None:
            current_switch.set_hovered(True)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt API
        self.hover_row_changed.emit(-1)
        if self._hovered_switch is not None:
            self._hovered_switch.set_hovered(False)
            self._hovered_switch = None
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            item = self.itemAt(event.position().toPoint())
            if item is not None and event.position().x() >= self.visualItemRect(item).right() - 56:
                self.switch_clicked.emit(self.row(item))
                return
        super().mousePressEvent(event)


class CapsuleSwitch(QWidget):
    """项目行右侧的项目连接开关。"""

    toggled = pyqtSignal(bool)

    def __init__(self, checked: bool = False, parent=None):
        super().__init__(parent)
        self._checked = checked
        self._hover_amount = 0.0
        self._hover_animation = QVariantAnimation(self)
        self._hover_animation.setDuration(140)
        self._hover_animation.valueChanged.connect(self._on_hover_value)
        self.setFixedSize(42, 24)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def setChecked(self, checked: bool) -> None:
        self._checked = bool(checked)
        self.update()

    def set_hovered(self, hovered: bool) -> None:
        self._hover_animation.stop()
        self._hover_animation.setStartValue(self._hover_amount)
        self._hover_animation.setEndValue(1.0 if hovered else 0.0)
        self._hover_animation.start()

    def _on_hover_value(self, value) -> None:
        self._hover_amount = float(value)
        self.update()

    @staticmethod
    def _blend(first: QColor, second: QColor, amount: float) -> QColor:
        amount = max(0.0, min(1.0, amount))
        return QColor(
            round(first.red() + (second.red() - first.red()) * amount),
            round(first.green() + (second.green() - first.green()) * amount),
            round(first.blue() + (second.blue() - first.blue()) * amount),
        )

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = self._blend(
            QColor(ACCENT_COLOR if self._checked else "#163546"),
            QColor(ACCENT_HOVER if self._checked else "#245267"),
            self._hover_amount,
        )
        knob = self._blend(
            QColor("#071D2C" if self._checked else "#A9B8C4"),
            QColor("#FFFFFF" if self._checked else "#DCE8EF"),
            self._hover_amount,
        )
        painter.setPen(QPen(track if self._checked else QColor("#315365"), 1))
        painter.setBrush(track)
        painter.drawRoundedRect(1, 3, 40, 18, 9, 9)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(knob)
        painter.drawEllipse(25 if self._checked else 4, 6, 12, 12)
        painter.end()


class ProjectListDelegate(QStyledItemDelegate):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._hover_row = -1
        self._hover_amount = 0.0
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(150)
        self._animation.valueChanged.connect(self._on_animation_value)

    def set_hovered_row(self, row: int) -> None:
        if row == self._hover_row:
            return
        self._hover_row = row
        self._animation.stop()
        self._animation.setStartValue(self._hover_amount)
        self._animation.setEndValue(1.0 if row >= 0 else 0.0)
        self._animation.start()

    def _on_animation_value(self, value) -> None:
        self._hover_amount = float(value)
        if self.parent():
            self.parent().viewport().update()

    def paint(self, painter, option, index) -> None:  # noqa: N802 - Qt API
        adjusted = QStyleOptionViewItem(option)
        adjusted.rect = option.rect
        adjusted.state &= ~(QStyle.StateFlag.State_MouseOver | QStyle.StateFlag.State_Selected)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = index.row() == self._hover_row
        if selected or hovered:
            painter.save()
            painter.setPen(Qt.PenStyle.NoPen)
            # Keep the row widget's geometry untouched. The selected state is
            # only a narrow accent rail plus a quiet fill behind the content.
            if selected:
                painter.setBrush(QColor("#103646"))
                painter.drawRect(adjusted.rect)
                painter.setBrush(QColor(ACCENT_COLOR))
                painter.drawRect(adjusted.rect.left(), adjusted.rect.top() + 1, 3, max(1, adjusted.rect.height() - 2))
            else:
                amount = self._hover_amount
                color = QColor(18, 58, 72, round(80 * amount))
                painter.setBrush(color)
                painter.drawRect(adjusted.rect)
                painter.setBrush(QColor("#2C7180"))
                painter.drawRect(adjusted.rect.left(), adjusted.rect.bottom() - 1, adjusted.rect.width(), 1)
            painter.restore()
        super().paint(painter, adjusted, index)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("RepoPulse · Git 状态台")
        self.setFixedSize(900, 620)
        self.app_icon = QIcon(str(Path(__file__).resolve().parents[2] / "RepoPulse.png"))
        if not self.app_icon.isNull():
            self.setWindowIcon(self.app_icon)
        self.tray_icon: QSystemTrayIcon | None = None
        self.tray_sync_action: QAction | None = None
        self._tray_sync_requested = False
        self._auto_sync_had_error = False
        self._allow_close = False
        self._setup_tray()
        set_dark_title_bar(self)
        self.store = ProjectStore()
        self.projects = self.store.load()
        self.remotes = self.store.global_remotes
        self.card_order = self.store.global_card_order
        for project in self.projects:
            project.remotes = self.remotes
        self.results: dict[str, dict] = {}
        self.worker: StatusWorker | None = None
        self.sync_worker: SyncWorker | None = None
        self.create_worker: ProjectCreateWorker | None = None
        self.quick_buttons: list[QPushButton] = []
        self.card_widgets: dict[str, StatusCard] = {}
        self.selected_git_key: str | None = None
        self.log_history: list[str] = []
        self.busy_dialog: BusyDialog | None = None
        self.auto_sync_queue: list[ProjectConfig] = []
        self.auto_sync_reason = ""
        self.auto_sync_timer = QTimer(self)
        self.auto_sync_timer.timeout.connect(self._run_scheduled_sync)
        self._build_ui()
        self._reload_project_list()
        self._apply_sync_settings()
        QTimer.singleShot(250, self.refresh_all)

    def _setup_tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable() or self.app_icon.isNull():
            return
        self.tray_icon = QSystemTrayIcon(self.app_icon, self)
        self.tray_icon.setToolTip("RepoPulse · Git 状态台")
        menu = QMenu(self)
        show_action = QAction("显示 RepoPulse", self)
        show_action.triggered.connect(self._show_from_tray)
        self.tray_sync_action = QAction("提交并同步", self)
        self.tray_sync_action.triggered.connect(self._tray_sync_all)
        exit_action = QAction("退出软件", self)
        exit_action.triggered.connect(self._exit_from_tray)
        menu.addAction(show_action)
        menu.addAction(self.tray_sync_action)
        menu.addSeparator()
        menu.addAction(exit_action)
        self.tray_icon.setContextMenu(menu)
        self.tray_icon.activated.connect(self._tray_activated)
        self.tray_icon.show()

    def _tray_sync_all(self) -> None:
        enabled_projects = [project for project in self.projects if project.sync_enabled]
        if not enabled_projects:
            self._show_tray_message("没有开启的项目", "请先打开项目列表右侧的同步开关。", QSystemTrayIcon.MessageIcon.Warning)
            return
        if self.auto_sync_queue or (self.sync_worker and self.sync_worker.isRunning()):
            self._show_tray_message("提交并同步进行中", "当前已有同步任务正在处理。", QSystemTrayIcon.MessageIcon.Information)
            return
        self._tray_sync_requested = True
        self._queue_automatic_sync(enabled_projects, "托盘同步")

    def _show_tray_message(
        self,
        title: str,
        message: str,
        icon: QSystemTrayIcon.MessageIcon = QSystemTrayIcon.MessageIcon.Information,
    ) -> None:
        if self.tray_icon is not None:
            self.tray_icon.showMessage(title, message, icon, 3500)

    def _show_from_tray(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()

    def _exit_from_tray(self) -> None:
        self._allow_close = True
        self.close()

    def _tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in {
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        }:
            self._show_from_tray()

    @staticmethod
    def _close_settings() -> tuple[bool, bool]:
        try:
            settings = QSettings("RepoPulse", "RepoPulse")
            prompt = settings.value("close_prompt", True, type=bool)
            close_to_tray = settings.value("close_to_tray", True, type=bool)
            return prompt, close_to_tray
        except Exception:
            return True, True

    @staticmethod
    def _save_close_settings(prompt: bool, close_to_tray: bool) -> None:
        try:
            settings = QSettings("RepoPulse", "RepoPulse")
            settings.setValue("close_prompt", prompt)
            settings.setValue("close_to_tray", close_to_tray)
            settings.sync()
        except Exception:
            pass

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._allow_close:
            if self.tray_icon:
                self.tray_icon.hide()
            event.accept()
            return
        prompt, close_to_tray = self._close_settings()
        can_tray = self.tray_icon is not None
        if prompt:
            confirm = QMessageBox(self)
            confirm.setWindowTitle("关闭 RepoPulse")
            confirm.setIcon(QMessageBox.Icon.Question)
            confirm.setText("要如何处理 RepoPulse？")
            tray_button = confirm.addButton("最小化到托盘", QMessageBox.ButtonRole.AcceptRole)
            close_button = confirm.addButton("关闭软件", QMessageBox.ButtonRole.DestructiveRole)
            confirm.addButton("取消", QMessageBox.ButtonRole.RejectRole)
            tray_button.setEnabled(can_tray)
            remember = QCheckBox("下次不再提醒")
            confirm.setCheckBox(remember)
            confirm.exec()
            clicked = confirm.clickedButton()
            if clicked not in {tray_button, close_button}:
                event.ignore()
                return
            close_to_tray = clicked is tray_button
            if remember.isChecked():
                self._save_close_settings(False, close_to_tray)
        if close_to_tray and can_tray:
            self.hide()
            event.ignore()
            return
        self._allow_close = True
        event.accept()

    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(SPACE_3, SPACE_3, SPACE_3, 0)
        root.setSpacing(SPACE_2)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(SPACE_1)
        title = QLabel(
            "<span style='font-size:39px; font-weight:700;'>R</span>"
            "<span style='font-size:30px; font-weight:700;'>epo</span>"
            "<span style='font-size:39px; font-weight:700;'>P</span>"
            "<span style='font-size:30px; font-weight:700;'>ulse</span>"
        )
        title.setTextFormat(Qt.TextFormat.RichText)
        title.setStyleSheet(f"color: {ACCENT_COLOR};")
        title.setAlignment(Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft)
        title_icon = QLabel()
        title_icon.setFixedSize(46, 46)
        title_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_icon.setStyleSheet(
            f"background: {PANEL_RAISED}; border: 2px solid {ACCENT_COLOR}; border-radius: 9px;"
        )
        if not self.app_icon.isNull():
            title_icon.setPixmap(self.app_icon.pixmap(40, 40))
        self.title_version = QLabel("v0.1.0")
        self.title_version.setStyleSheet("color: #718096; font-size: 10px; padding-bottom: 4px;")
        self.title_version.setAlignment(Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft)
        toolbar.addWidget(title_icon)
        toolbar.addWidget(title)
        toolbar.addWidget(self.title_version)
        toolbar.addStretch(1)
        root.addLayout(toolbar)

        title_divider = QFrame()
        title_divider.setFrameShape(QFrame.Shape.HLine)
        title_divider.setFrameShadow(QFrame.Shadow.Plain)
        title_divider.setFixedHeight(1)
        title_divider.setStyleSheet(f"color: {BORDER_COLOR}; background: {BORDER_COLOR}; border: 0;")
        root.addWidget(title_divider)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, SPACE_1, 0)
        project_header = QHBoxLayout()
        project_header.setContentsMargins(0, 0, 0, 0)
        project_title = QLabel("项目列表")
        project_title.setStyleSheet("font-weight: 600; color: #E8ECF2;")
        project_header.addWidget(project_title)
        project_header.addStretch(1)
        left_layout.addLayout(project_header)
        self.project_list = ProjectListWidget()
        self.project_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.project_list.switch_clicked.connect(self._toggle_project_sync)
        self.project_list.setMinimumHeight(500)
        self.project_list.setIconSize(QSize(40, 40))
        self.project_list.setStyleSheet(
            "QListWidget::item { padding: 0; margin: 0; border: 0; }"
            "QListWidget::item:selected { background: transparent; color: white; padding: 0; margin: 0; border: 0; }"
            "QListWidget::item:hover { background: transparent; padding: 0; margin: 0; border: 0; }"
        )
        project_delegate = ProjectListDelegate(self.project_list)
        self.project_list.setItemDelegate(project_delegate)
        self.project_list.hover_row_changed.connect(project_delegate.set_hovered_row)
        self.project_list.itemDoubleClicked.connect(self._open_project_detail)
        left_layout.addWidget(self.project_list)
        splitter.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(SPACE_1, 0, 0, 0)
        right_layout.setSpacing(SPACE_2)
        repo_header = QHBoxLayout()
        repo_header.setContentsMargins(0, 0, 0, 0)
        repo_title = QLabel("仓库列表")
        repo_title.setStyleSheet("font-weight: 600; color: #E8ECF2;")
        repo_header.addWidget(repo_title)
        repo_header.addStretch(1)
        right_layout.addLayout(repo_header)

        repo_frame = QFrame()
        repo_frame.setObjectName("repoArea")
        repo_frame.setStyleSheet(
            f"QFrame#repoArea {{ background: {CANVAS_COLOR}; border: 1px solid {BORDER_COLOR}; border-radius: 6px; }}"
        )
        repo_frame_layout = QVBoxLayout(repo_frame)
        # Keep the card grid close to the repository frame, matching the project list.
        repo_frame_layout.setContentsMargins(SPACE_1, SPACE_1, SPACE_1, SPACE_1)
        repo_frame_layout.setSpacing(0)
        self.cards_area = QScrollArea()
        self.cards_area.setWidgetResizable(True)
        self.cards_area.setFrameShape(QFrame.Shape.NoFrame)
        self.cards_area.setViewportMargins(0, 0, 0, 0)
        self.cards_widget = QWidget()
        self.cards_grid = QGridLayout(self.cards_widget)
        self.cards_grid.setContentsMargins(SPACE_1, SPACE_1, SPACE_1, SPACE_1)
        self.cards_grid.setHorizontalSpacing(SPACE_2)
        self.cards_grid.setVerticalSpacing(SPACE_2)
        self.cards_grid.setColumnStretch(0, 1)
        self.cards_grid.setColumnStretch(1, 1)
        self.cards_grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.cards_area.setWidget(self.cards_widget)
        repo_frame_layout.addWidget(self.cards_area)
        # 185px cards x 2 rows + 8px gap + 4px grid/frame margins: four cards
        # fit exactly, leaving the same breathing room above and below.
        repo_frame.setFixedHeight(396)
        right_layout.addWidget(repo_frame)

        action_header = QHBoxLayout()
        action_header.setContentsMargins(0, 0, 0, 0)
        action_title = QLabel("快捷控制")
        action_title.setStyleSheet("font-weight: 600; color: #E8ECF2;")
        action_header.addWidget(action_title)
        action_header.addStretch(1)
        right_layout.addLayout(action_header)

        action_frame = QFrame()
        action_frame.setObjectName("actionArea")
        action_frame.setStyleSheet(
            f"QFrame#actionArea {{ background: {CANVAS_COLOR}; border: 1px solid {BORDER_COLOR}; border-radius: 6px; }}"
            f"QPushButton#quickAction {{ background: {PANEL_RAISED}; border: 1px solid {BORDER_COLOR}; border-radius: 4px; "
            "color: #CDD6E5; padding: 6px 8px; min-height: 16px; }"
            f"QPushButton#quickAction:hover {{ background: #16485B; border-color: {ACCENT_COLOR}; color: #FFFFFF; }}"
            f"QPushButton#quickAction:pressed {{ background: {ACCENT_DARK}; border-color: {ACCENT_HOVER}; }}"
        )
        action_frame.setFixedHeight(54)
        action_layout = QHBoxLayout(action_frame)
        action_layout.setContentsMargins(SPACE_2, SPACE_2, SPACE_2, SPACE_2)
        action_layout.setSpacing(SPACE_1)
        sync_button = QPushButton("提交并同步")
        sync_button.setObjectName("quickAction")
        sync_button.setFixedHeight(30)
        sync_button.setCursor(Qt.CursorShape.PointingHandCursor)
        sync_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        sync_button.setToolTip("提交当前修改，并同步到全部 Git 渠道")
        sync_button.clicked.connect(self._sync_current_project)
        new_project_button = QPushButton("新建项目")
        new_project_button.setObjectName("quickAction")
        new_project_button.setFixedHeight(30)
        new_project_button.setCursor(Qt.CursorShape.PointingHandCursor)
        new_project_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        new_project_button.clicked.connect(self._new_project)
        refresh_button = QPushButton("刷新")
        refresh_button.setObjectName("quickAction")
        refresh_button.setFixedHeight(30)
        refresh_button.setCursor(Qt.CursorShape.PointingHandCursor)
        refresh_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        refresh_button.setToolTip("刷新全部项目和 Git 渠道状态")
        refresh_button.clicked.connect(self.refresh_all)
        self.quick_buttons = [sync_button, new_project_button, refresh_button]
        for button in self.quick_buttons:
            action_layout.addWidget(button, 1)
        right_layout.addWidget(action_frame)

        splitter.addWidget(right)
        # Keep the project pane 70px wider than the original proportion while
        # preserving the fixed 900px window and squeezing the right panes.
        splitter.setSizes([350, 650])
        root.addWidget(splitter, 1)

        info_bar = QFrame()
        info_bar.setObjectName("infoBar")
        info_bar.setFixedHeight(34)
        info_bar.setStyleSheet(
            f"QFrame#infoBar {{ background: {CANVAS_COLOR}; border-top: 1px solid {BORDER_COLOR}; }}"
            "QToolButton#settingsButton { border: 0; background: transparent; border-radius: 5px; }"
            f"QToolButton#settingsButton:hover {{ background: #123D4D; }}"
            f"QToolButton#settingsButton:pressed {{ background: {ACCENT_DARK}; }}"
        )
        info_layout = QHBoxLayout(info_bar)
        info_layout.setContentsMargins(0, SPACE_1, 0, SPACE_1)
        info_layout.setSpacing(SPACE_1)
        self.settings_button = QToolButton()
        self.settings_button.setObjectName("settingsButton")
        self.settings_button.setToolTip("同步设置")
        self.settings_button.setAccessibleName("同步设置")
        self.settings_button.setIcon(_settings_icon())
        self.settings_button.setIconSize(QSize(17, 17))
        self.settings_button.setFixedSize(28, 26)
        self.settings_button.clicked.connect(self.open_settings)
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.VLine)
        divider.setFrameShadow(QFrame.Shadow.Plain)
        divider.setFixedWidth(1)
        divider.setStyleSheet(f"color: {BORDER_COLOR}; background: {BORDER_COLOR}; border: 0;")
        self.log_label = QLabel("就绪")
        self.log_label.setMinimumWidth(0)
        self.log_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.log_label.setStyleSheet("color: #A9B5C8; font-size: 10px; padding: 0 4px;")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("%p%")
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFixedWidth(200)
        self.progress_bar.setFixedHeight(18)
        self._set_progress_style("idle")
        info_layout.addWidget(self.settings_button)
        info_layout.addWidget(divider)
        info_layout.addWidget(self.log_label, 1)
        info_layout.addWidget(self.progress_bar)
        root.addWidget(info_bar)
        self.setCentralWidget(central)

        self.project_list.currentRowChanged.connect(self._show_current_project)

    def _reload_project_list(self, select_id: str | None = None) -> None:
        self.project_list.blockSignals(True)
        self.project_list.clear()
        target_row = 0
        for index, project in enumerate(self.projects):
            result = self.results.get(project.project_id)
            # The visible label is rendered by the custom row widget below.
            # Keep the QListWidget item text empty so Qt does not paint a
            # second project name underneath it.
            item = QListWidgetItem("")
            item.setData(Qt.ItemDataRole.UserRole, project.project_id)
            item.setData(Qt.ItemDataRole.UserRole + 1, self._overall_icon(result) if result else "waiting")
            item.setToolTip(project.name)
            item.setSizeHint(QSize(0, 48))
            if result:
                item.setForeground(QColor(self._overall_color(result)))
            self.project_list.addItem(item)
            self.project_list.setItemWidget(item, self._project_row_widget(project, result))
            if select_id and project.project_id == select_id:
                target_row = index
        self.project_list.blockSignals(False)
        if self.projects:
            self.project_list.setCurrentRow(target_row)
        else:
            self._clear_view()

    def _project_icon(self, project: ProjectConfig | None = None) -> QIcon:
        source_icon = QIcon()
        if project:
            root = Path(project.workspace_path).expanduser()
            parent = root.parent
            candidates = [
                root / f"{project.name}.ico",
                root / f"{project.name}.png",
                root / f"{project.name}.jpg",
                root / f"{project.name}.jpeg",
                root / f"{project.name}.svg",
                root / f"{project.name}.webp",
                root / "icon.ico",
                root / "icon.png",
                root / "icon.svg",
                root / "project.ico",
                root / "project.png",
                root / "logo.ico",
                root / "logo.png",
                root / "logo.svg",
                root / "favicon.ico",
                # A project created under a selected parent directory may
                # keep its icon beside the project folder. This also covers
                # the existing RepoPulse project layout.
                parent / f"{project.name}.ico",
                parent / f"{project.name}.png",
                parent / f"{project.name}.jpg",
                parent / f"{project.name}.jpeg",
                parent / f"{project.name}.svg",
            ]
            for candidate in candidates:
                if candidate.is_file():
                    icon = QIcon(str(candidate))
                    if not icon.isNull():
                        source_icon = icon
                        break
        return source_icon

    @staticmethod
    def _project_initial(name: str) -> str:
        value = name.strip()
        return value[:1].upper() if value else "?"

    def _project_row_widget(self, project: ProjectConfig, result: dict | None) -> QWidget:
        row = QWidget()
        row.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        row.setStyleSheet("background: transparent; border: 0;")
        outer = QVBoxLayout(row)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        content = QWidget()
        content.setFixedHeight(47)
        content.setStyleSheet("background: transparent; border: 0;")
        layout = QHBoxLayout(content)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(SPACE_2)
        icon_box = QLabel()
        icon_box.setFixedSize(40, 40)
        icon_box.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_box.setStyleSheet(
            f"background: {PANEL_RAISED}; border: 1px solid {BORDER_COLOR}; border-radius: 6px;"
        )
        icon_slot = QWidget()
        icon_slot.setFixedSize(43, 47)
        icon_slot.setStyleSheet("background: transparent; border: 0;")
        icon_slot_layout = QVBoxLayout(icon_slot)
        # The icon sits three pixels lower than the text baseline by design.
        icon_slot_layout.setContentsMargins(3, 5, 0, 2)
        icon_slot_layout.setSpacing(0)
        icon_slot_layout.addWidget(icon_box)
        project_icon = self._project_icon(project)
        if not project_icon.isNull():
            icon_box.setPixmap(project_icon.pixmap(34, 34))
        else:
            icon_box.setText(self._project_initial(project.name))
            icon_box.setStyleSheet(
                f"background: {PANEL_RAISED}; border: 1px solid {ACCENT_COLOR}; border-radius: 6px; "
                f"color: {ACCENT_COLOR}; font-size: 22px; font-weight: 700;"
            )
        name = QLabel(project.name)
        name.setStyleSheet("color: #E8ECF2; background: transparent;")
        name.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        state = self._overall_icon(result) if result else "waiting"
        colors = {
            "clean": "#70D6A5",
            "warning": "#F5C26B",
            "error": "#F27788",
            "different": "#9CC6FF",
            "waiting": "#8A97AA",
        }
        status = QLabel()
        status.setFixedSize(12, 12)
        status.setToolTip({
            "clean": "状态正常",
            "warning": "有待处理内容",
            "error": "存在连接或读取错误",
            "different": "渠道版本不一致",
            "waiting": "等待检查",
        }.get(state, "等待检查"))
        status.setStyleSheet(
            f"background: {colors.get(state, colors['waiting'])}; border-radius: 6px;"
        )
        layout.addWidget(icon_slot)
        layout.addWidget(status)
        layout.addWidget(name, 1)
        layout.addWidget(CapsuleSwitch(project.sync_enabled, content))
        separator = QFrame()
        separator.setFixedHeight(1)
        separator.setStyleSheet(f"background: {BORDER_COLOR}; border: 0; margin: 0;")
        outer.addWidget(content)
        outer.addWidget(separator)
        return row

    def _project_stats(self, project: ProjectConfig) -> dict:
        path = Path(project.workspace_path).expanduser()
        file_count = 0
        total_bytes = 0
        if path.is_dir():
            try:
                for root, directories, files in os.walk(path):
                    directories[:] = [item for item in directories if item != ".git"]
                    for filename in files:
                        file_path = Path(root) / filename
                        try:
                            total_bytes += file_path.stat().st_size
                            file_count += 1
                        except OSError:
                            continue
            except OSError:
                pass
        if total_bytes < 1024:
            size_text = f"{total_bytes} B"
        elif total_bytes < 1024 * 1024:
            size_text = f"{total_bytes / 1024:.1f} KB"
        elif total_bytes < 1024 * 1024 * 1024:
            size_text = f"{total_bytes / (1024 * 1024):.1f} MB"
        else:
            size_text = f"{total_bytes / (1024 * 1024 * 1024):.1f} GB"
        try:
            created_at = datetime.fromtimestamp(path.stat().st_ctime).strftime("%Y-%m-%d %H:%M")
        except OSError:
            created_at = "未知"

        result = self.results.get(project.project_id) or {}
        remote_results = result.get("remotes", {})
        summary_lines = []
        for key, remote in self.remotes.items():
            data = remote_results.get(key) or {}
            label = remote.label or remote.kind
            if not data:
                summary_lines.append(f"<span style='color:#A9B5C8'>{html.escape(label)}：待刷新</span>")
                continue
            if data.get("error"):
                summary_lines.append(f"<span style='color:#F27788'>{html.escape(label)}：连接失败</span>")
                continue
            relation = str(data.get("relation") or "未连接")
            ahead = data.get("ahead")
            behind = data.get("behind")
            if relation == "一致":
                state = "一致"
                color = "#70D6A5"
            elif data.get("online"):
                state = relation
                if ahead is not None and behind is not None:
                    if ahead > 0 and behind == 0:
                        latest_note = "最新是本地开发目录"
                    elif behind > 0 and ahead == 0:
                        latest_note = f"最新是 {label}"
                    else:
                        latest_note = "两边已分叉"
                    state = f"不一致 · 本地领先 {ahead} 步 / 远程领先 {behind} 步 · {latest_note}"
                color = "#F5C26B"
            else:
                state = relation or "未连接"
                color = "#F27788"
            head = str(data.get("head") or "")[:12]
            latest = head or "暂无提交"
            summary_lines.append(
                f"<span style='color:{color}'>{html.escape(label)}：{html.escape(state)}</span>"
                f"<br><span style='color:#A9B5C8'>commit：{html.escape(latest)}</span>"
            )
        return {
            "created_at": created_at,
            "file_count": file_count,
            "total_size": size_text,
            "git_summary_html": "<br>".join(summary_lines),
        }

    def _open_project_detail(self, item: QListWidgetItem, _column: int = 0) -> None:
        project_id = item.data(Qt.ItemDataRole.UserRole)
        project = next((value for value in self.projects if value.project_id == project_id), None)
        if not project:
            return
        detail = ProjectDetailDialog(project, self._project_stats(project), self._project_icon(project), self)
        detail.sync_requested.connect(lambda: self._sync_from_project_detail(detail))
        detail.delete_requested.connect(lambda: self._delete_from_project_detail(detail, project))
        detail.exec()

    def _sync_from_project_detail(self, detail: ProjectDetailDialog) -> None:
        detail.accept()
        self._sync_current_project()

    def _delete_from_project_detail(self, detail: ProjectDetailDialog, project: ProjectConfig) -> None:
        if self.create_worker and self.create_worker.isRunning():
            self._append_log("项目创建进行中，暂时不能删除项目。")
            return
        if self.worker and self.worker.isRunning():
            self._append_log("状态刷新进行中，暂时不能删除项目。")
            return
        detail.accept()
        self._delete_had_error = False
        self._set_delete_busy(True)
        self._show_busy_dialog("正在删除项目")
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self._set_progress_style("running")
        self._append_log(f"开始删除项目：{project.name}")
        self.delete_worker = ProjectDeleteWorker(project)
        self.delete_worker.log_message.connect(self._append_log)
        self.delete_worker.result_ready.connect(self._on_project_deleted)
        self.delete_worker.failed.connect(self._on_project_delete_failed)
        self.delete_worker.completed.connect(self._on_project_delete_completed)
        self.delete_worker.start()

    def _set_delete_busy(self, busy: bool) -> None:
        for button in self.quick_buttons:
            button.setEnabled(not busy)
        self.settings_button.setEnabled(not busy)

    def _on_project_deleted(self, result: dict) -> None:
        project = result.get("project")
        errors = [str(item) for item in result.get("errors", []) if str(item)]
        self._delete_had_error = bool(errors)
        if errors:
            for error in errors:
                self._append_log(f"项目删除失败：{error}")
            self._hide_busy_dialog()
            QMessageBox.warning(self, "项目删除未完成", "\n".join(errors))
            return
        if isinstance(project, ProjectConfig):
            self.projects = [item for item in self.projects if item.project_id != project.project_id]
            self.results.pop(project.project_id, None)
            self.store.save(self.projects)
            self._reload_project_list()
            self._append_log(f"项目已删除：{project.name}")

    def _on_project_delete_failed(self, message: str) -> None:
        self._delete_had_error = True
        self._hide_busy_dialog()
        self._append_log(f"项目删除失败：{message}")
        QMessageBox.warning(self, "项目删除未完成", message)

    def _on_project_delete_completed(self) -> None:
        self._set_delete_busy(False)
        self._hide_busy_dialog()
        had_error = getattr(self, "_delete_had_error", False)
        self._set_progress_style("error" if had_error else "success")
        if not had_error:
            QMessageBox.information(self, "项目删除完成", "项目目录和已配置 Git 渠道中的对应项目都已删除。")

    def _project_label(self, project: ProjectConfig, result: dict | None) -> str:
        return project.name

    def _overall_icon(self, result: dict) -> str:
        local = result.get("local", {})
        remotes = result.get("remotes", {})
        if local.get("error"):
            return "error"
        if any(remote.get("error") and not remote.get("ignored") for remote in remotes.values()):
            return "error"
        relations = [str(remote.get("relation") or "") for remote in remotes.values()]
        if any(
            any(marker in relation for marker in ("不一致", "版本不同", "分叉", "领先", "待首次同步"))
            for relation in relations
        ):
            return "different"
        if not local.get("clean"):
            return "warning"
        return "clean"

    def _overall_color(self, result: dict) -> str:
        return {"clean": "#70D6A5", "warning": "#F5C26B", "error": "#F27788", "different": "#9CC6FF"}.get(self._overall_icon(result), "#E8ECF2")

    def _current_project(self) -> ProjectConfig | None:
        row = self.project_list.currentRow()
        return self.projects[row] if 0 <= row < len(self.projects) else None

    def _toggle_project_sync(self, row: int) -> None:
        if not (0 <= row < len(self.projects)):
            return
        if any(worker and worker.isRunning() for worker in (self.sync_worker, self.create_worker)):
            self._append_log("当前任务进行中，暂时不能切换项目开关。")
            return
        project = self.projects[row]
        project.sync_enabled = not project.sync_enabled
        self.store.save(self.projects)
        if not project.sync_enabled:
            self.results.pop(project.project_id, None)
        self._reload_project_list(select_id=project.project_id)
        if self._current_project() and self._current_project().project_id == project.project_id:
            self._render_cards(self.results.get(project.project_id))
        self._append_log(
            f"已开启项目连接：{project.name}"
            if project.sync_enabled
            else f"已关闭项目连接：{project.name}"
        )
        if project.sync_enabled:
            self.refresh_selected()

    def _show_current_project(self, _row: int) -> None:
        project = self._current_project()
        if not project:
            self._clear_view()
            return
        self.selected_git_key = None
        self._render_cards(self.results.get(project.project_id))

    def _clear_view(self) -> None:
        self._render_cards(None)

    def _clear_cards(self) -> None:
        self.card_widgets.clear()
        while self.cards_grid.count():
            item = self.cards_grid.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

    def _render_cards(self, result: dict | None) -> None:
        self._clear_cards()
        for row in range(8):
            self.cards_grid.setRowStretch(row, 0)
        project = self._current_project()
        local = result.get("local", {}) if result else {}
        if not project:
            self._add_card("__staging__", self._build_staging_card(local), 0)
            return

        result_remotes = result.get("remotes", {}) if result else {}
        configured = {}
        for key, remote in self.remotes.items():
            if remote.enabled:
                configured[key] = (key, remote, result_remotes.get(key))
        available_keys = ["__staging__", *configured.keys()]
        visible_keys = [key for key in self.card_order if key in available_keys]
        visible_keys.extend(key for key in available_keys if key not in visible_keys)
        visible = []
        for key in visible_keys:
            visible.append((key, None, local) if key == "__staging__" else configured[key])
        for index, (key, remote, remote_result) in enumerate(visible):
            if key == "__staging__":
                card = self._build_staging_card(local)
            else:
                card = self._build_remote_card(key, remote, remote_result)
            self._add_card(key, card, index)

    def _add_card(self, key: str, card: StatusCard, index: int) -> None:
        card.clicked.connect(self._select_card)
        card.drag_started.connect(self._start_card_drag)
        card.drag_finished.connect(self._finish_card_drag)
        self.card_widgets[key] = card
        card.set_selected(key == self.selected_git_key)
        row, column = divmod(index, 2)
        self.cards_grid.addWidget(card, row, column)
        card.installEventFilter(self)
        card.raise_()

    def _rename_card(self, key: str, name: str) -> None:
        if key == "__staging__":
            return
        project = self._current_project()
        remote = project.remotes.get(key) if project else None
        card = self.card_widgets.get(key)
        if not project or not remote or not card:
            return
        remote.label = name
        self.store.save(self.projects)
        self.results.pop(project.project_id, None)
        self._reload_project_list(select_id=project.project_id)
        self._render_cards(None)
        self._append_log(f"已修改仓库名称：{name}")
        self.refresh_selected()

    def _start_card_drag(self, key: str) -> None:
        card = self.card_widgets.get(key)
        if card:
            card.set_selected(True)
            self._dragging_card_key = key

    def _finish_card_drag(self, key: str, global_pos: QPoint) -> None:
        if getattr(self, "_dragging_card_key", None) != key:
            return
        self._dragging_card_key = None
        project = self._current_project()
        if not project:
            return
        ordered = [card_key for card_key, card in self.card_widgets.items() if card.isVisible()]
        if key not in ordered:
            return
        point = self.cards_widget.mapFromGlobal(global_pos)
        target_index = self._drop_index(ordered, key, point)
        old_index = ordered.index(key)
        ordered.pop(old_index)
        if target_index > old_index:
            target_index -= 1
        ordered.insert(max(0, min(target_index, len(ordered))), key)
        if ordered == [card_key for card_key in self._visible_card_order(project)]:
            return
        moved_title = self.card_widgets[key].card_title
        self.card_order = ordered
        self.store.global_card_order = list(ordered)
        self.store.save(self.projects)
        self._render_cards(self.results.get(project.project_id))
        self._append_log(f"已调整卡片位置：{moved_title}")

    def _drop_index(self, ordered: list[str], moving_key: str, point: QPoint) -> int:
        candidates = []
        for index, card_key in enumerate(ordered):
            if card_key == moving_key:
                continue
            card = self.card_widgets.get(card_key)
            if not card:
                continue
            center = card.geometry().center()
            distance = (center - point).manhattanLength()
            before = point.y() < center.y() or (abs(point.y() - center.y()) < card.height() // 2 and point.x() < center.x())
            candidates.append((distance, index if before else index + 1))
        return min(candidates, key=lambda item: item[0])[1] if candidates else len(ordered)

    def _visible_card_order(self, project: ProjectConfig) -> list[str]:
        keys = ["__staging__"]
        keys.extend(key for key, remote in self.remotes.items() if remote.enabled)
        if self.card_order:
            ordered = [key for key in self.card_order if key in keys]
            ordered.extend(key for key in keys if key not in ordered)
            return ordered
        return keys

    def eventFilter(self, watched, event):  # noqa: N802 - Qt API
        if isinstance(watched, StatusCard) and event.type() == event.Type.Enter:
            watched.raise_()
        return super().eventFilter(watched, event)

    def _build_staging_card(self, local: dict) -> StatusCard:
        if not local:
            status, color = "等待检查", "#A9B5C8"
            body = "未检查"
        elif local.get("error"):
            status, color = "读取失败", "#F27788"
            body = local["error"]
        elif local.get("conflicts", 0):
            status, color = "有冲突", "#F27788"
            body = self._staging_body(local)
        elif any(local.get(key, 0) for key in ("staged", "modified", "untracked")):
            status, color = "有待提交内容", "#F5C26B"
            body = self._staging_body(local)
        else:
            status, color = "干净", "#70D6A5"
            body = self._staging_body(local)
        return StatusCard("__staging__", "暂存区", "", status, color, body)

    def _staging_body(self, local: dict) -> str:
        return (
            f"已暂存：{local.get('staged', 0)} 个文件\n"
            f"未暂存修改：{local.get('modified', 0)} 个文件\n"
            f"未跟踪：{local.get('untracked', 0)} 个文件\n"
            f"冲突：{local.get('conflicts', 0)} 个文件"
        )

    def _build_remote_card(self, key: str, remote: RemoteConfig, data: dict | None) -> StatusCard:
        title = remote.label or {
            "local": "Local Repository · 本地 Git",
            "nas": "NAS Gitea · NAS 套件",
            "github": "GitHub Remote · GitHub",
        }.get(remote.kind, "Remote Git · 远程 Git")
        subtitle = {
            "local": "当前电脑上的本地仓库和提交记录",
            "nas": "NAS 上 Gitea 仓库的连接和同步状态",
            "github": "GitHub 上的远程仓库和同步状态",
        }.get(remote.kind, "其他远程 Git 仓库和同步状态")
        if not data:
            return StatusCard(key, title, subtitle, "等待检查", "#A9B5C8", "未检查")
        if data.get("ignored") and remote.kind == "github":
            return StatusCard(key, title, subtitle, "未代理", "#A9B5C8", "GitHub 连接失败，已按设置忽略")
        if data.get("error"):
            return StatusCard(key, title, subtitle, "连接失败", "#F27788", data["error"])
        if remote.kind == "local":
            if not data.get("online"):
                status, color = "读取失败", "#F27788"
            elif data.get("relation") == "一致":
                status, color = "一致", "#70D6A5"
            else:
                status, color = data.get("relation", "版本不同"), "#F5C26B"
        else:
            status = data.get("relation", "已连接") if data.get("online") else "未连接"
            color = "#70D6A5" if data.get("online") and status == "一致" else "#9CC6FF" if data.get("online") else "#F27788"
        last = data.get("last_commit", {})
        counts = ""
        if data.get("ahead") is not None:
            counts = f"本地领先：{data['ahead']}  |  远程领先：{data['behind']}\n"
        address = self._channel_address(remote, self._current_project())
        body_lines = [
            html.escape(f"地址：{address}"),
            html.escape(f"最新 commit：{(data.get('head') or '')[:12] or '未知'}"),
        ]
        if counts:
            body_lines.append(html.escape(counts.rstrip("\n")))
        body_lines.extend([
            html.escape(f"最近提交：{last.get('subject', '未知')}"),
            html.escape(f"提交作者：{last.get('author', '未知')}"),
            html.escape(f"提交时间：{last.get('date', '未知')}"),
        ])
        body = "<br>".join(body_lines)
        return StatusCard(
            key,
            title,
            subtitle,
            status,
            color,
            body,
            rich_body=True,
            recent_commit=str(last.get("subject", "未知")),
        )

    def _channel_address(self, remote: RemoteConfig, project: ProjectConfig | None) -> str:
        """Return the channel entry point, without appending the current project."""
        if remote.kind == "local":
            return remote.path or "本地 Git"
        if remote.kind in {"github", "nas", "gitea"} and remote.service_url and remote.username:
            return f"{remote.service_url.rstrip('/')}/{quote(remote.username.strip(), safe='')}"
        if remote.service_url:
            return remote.service_url.rstrip("/")
        if not remote.url:
            return "当前渠道地址未知"
        # For custom clone URLs, remove the final repository segment as well.
        parsed = urlsplit(remote.url)
        segments = [segment for segment in parsed.path.split("/") if segment]
        if segments:
            segments.pop()
        base_path = "/" + "/".join(segments) if segments else ""
        return urlunsplit((parsed.scheme, parsed.netloc, base_path, "", "")) or remote.url

    def _select_card(self, key: str) -> None:
        self.selected_git_key = key
        for card_key, card in self.card_widgets.items():
            card.set_selected(card_key == key)
        card = self.card_widgets.get(key)
        project = self._current_project()
        if card and project:
            remote = project.remotes.get(key)
            projects = self._detail_projects(project, key, remote)
            repository_target = self._repository_target(project, remote)
            detail = RepositoryDetailDialog(
                title=card.card_title,
                status=card.card_status,
                status_color=card.card_status_color,
                body=card.card_body,
                rich_body=card.card_rich_body,
                projects=projects,
                repository_target=repository_target,
                rename_enabled=key != "__staging__",
                parent=self,
            )
            detail.project_view_requested.connect(self._view_detail_project)
            detail.project_delete_requested.connect(lambda project_id: self._delete_detail_project(project_id, key))
            detail.open_repository_requested.connect(self._open_repository_target)
            detail.rename_requested.connect(lambda name: self._rename_card(key, name))
            detail.remove_repository_requested.connect(lambda: self._remove_repository_card(key, detail))
            detail.exec()

    def _detail_projects(
        self,
        current: ProjectConfig,
        card_key: str,
        remote: RemoteConfig | None,
    ) -> list[dict]:
        """Build the project list represented by one Git channel card."""
        if remote is None:
            return [self._project_entry(current, None)]
        matched: list[dict] = []
        for project in self.projects:
            project_remote = project.remotes.get(card_key)
            if project_remote is None:
                project_remote = next(
                    (candidate for candidate in project.remotes.values() if self._same_channel(candidate, remote)),
                    None,
                )
            if project_remote is not None and self._same_channel(project_remote, remote):
                matched.append(self._project_entry(project, project_remote))
        if not any(item["project_id"] == current.project_id for item in matched):
            matched.insert(0, self._project_entry(current, remote))
        return matched

    def _project_entry(self, project: ProjectConfig, remote: RemoteConfig | None) -> dict:
        project_target = self._project_repository_target(project, remote)
        return {
            "project_id": project.project_id,
            "name": project.name,
            "workspace_path": project.workspace_path,
            "url": project_target,
            "project_target": project_target,
        }

    def _project_repository_target(self, project: ProjectConfig, remote: RemoteConfig | None) -> str:
        """Return the selected project's location inside the current Git channel."""
        if remote is None:
            return project.workspace_path
        if remote.kind == "local":
            return str(GitService().local_channel_path(remote, project)) if remote.path else project.workspace_path
        if remote.url:
            return remote.url
        generated = GitService().repository_url_for_project(remote, project.name)
        return generated or remote.service_url or ""

    def _same_channel(self, first: RemoteConfig, second: RemoteConfig) -> bool:
        if first.kind != second.kind:
            return False
        if first.kind in {"github", "nas", "gitea", "gitlab", "gitee", "bitbucket", "azure_devops", "codeberg"}:
            first_service = (first.service_url or first.url).rstrip("/").casefold()
            second_service = (second.service_url or second.url).rstrip("/").casefold()
            return first_service == second_service and first.username.casefold() == second.username.casefold()
        if first.kind == "local":
            return Path(first.path).expanduser().resolve() == Path(second.path).expanduser().resolve()
        return (first.url or first.path).rstrip("/").casefold() == (second.url or second.path).rstrip("/").casefold()

    def _repository_target(self, project: ProjectConfig, remote: RemoteConfig | None) -> str:
        if remote is None:
            return project.workspace_path
        if remote.kind == "local":
            # Open the global Local Git collection, not the selected project.
            return remote.path or project.workspace_path
        if remote.kind in {"nas", "gitea"} and remote.service_url and remote.username:
            # The service account page is the repository entry point for NAS/Gitea.
            return f"{remote.service_url.rstrip('/')}/{quote(remote.username.strip(), safe='')}"
        return remote.url or remote.service_url or ""

    def _view_detail_project(self, project_id: str, target: str) -> None:
        project = next((item for item in self.projects if item.project_id == project_id), None)
        if not project:
            return
        row = self.projects.index(project)
        self.project_list.setCurrentRow(row)
        self._open_repository_target(target or project.workspace_path)

    def _delete_detail_project(self, project_id: str, card_key: str) -> None:
        project = next((item for item in self.projects if item.project_id == project_id), None)
        if not project:
            return
        if card_key == "__staging__":
            self.projects = [item for item in self.projects if item.project_id != project_id]
            self.results.pop(project_id, None)
            self.store.save(self.projects)
            self._reload_project_list()
            self._append_log(f"已从 RepoPulse 移除项目：{project.name}")
            return
        if project_id not in {item.project_id for item in self.projects}:
            return
        self.projects = [item for item in self.projects if item.project_id != project_id]
        self.store.save(self.projects)
        self.results.pop(project_id, None)
        self._reload_project_list()
        self._append_log(f"已从 RepoPulse 移除项目：{project.name}")

    def _remove_repository_card(self, key: str, dialog: RepositoryDetailDialog) -> None:
        project = self._current_project()
        if not project or key == "__staging__" or key not in self.remotes:
            return
        remote = self.remotes[key]
        confirm = QMessageBox(self)
        confirm.setWindowTitle("确认删除仓库卡片")
        confirm.setIcon(QMessageBox.Icon.Question)
        confirm.setText(
            f"确定删除“{remote.label or remote.kind}”这个仓库卡片吗？\n\n"
            "只删除 RepoPulse 中的配置，不会删除本地文件或远程仓库。"
        )
        yes_button = confirm.addButton("确认删除", QMessageBox.ButtonRole.AcceptRole)
        confirm.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        confirm.exec()
        if confirm.clickedButton() is not yes_button:
            return
        self.remotes.pop(key, None)
        self.card_order = [item for item in self.card_order if item != key]
        self.store.global_remotes = self.remotes
        self.store.global_card_order = list(self.card_order)
        self.store.save(self.projects)
        self.results.pop(project.project_id, None)
        dialog.accept()
        self._reload_project_list(select_id=project.project_id)
        self._render_cards(None)
        self._append_log(f"已删除仓库卡片：{remote.label or remote.kind}")

    def _open_repository_target(self, target: str) -> None:
        if not target:
            return
        path = Path(target).expanduser()
        if path.is_dir():
            os.startfile(str(path))
            return
        browser_url = target
        if browser_url.endswith(".git"):
            browser_url = browser_url[:-4]
        webbrowser.open(browser_url)

    def _append_log(self, message: str) -> None:
        self.log_history.append(message)
        self.log_history = self.log_history[-100:]
        self.log_label.setText(message)
        self.log_label.setToolTip("\n".join(self.log_history))
        if self.busy_dialog is not None and message:
            self.busy_dialog.set_message(message)

    def _sync_current_project(self) -> None:
        project = self._current_project()
        if not project:
            QMessageBox.information(self, "没有选中项目", "请先在左侧选择一个项目。")
            return
        if not project.remotes:
            QMessageBox.information(self, "没有 Git 渠道", "当前项目还没有可同步的 Git 渠道。")
            return
        local = GitService().local_status(project)
        if local.get("error"):
            QMessageBox.warning(self, "无法提交", str(local.get("error")))
            return
        has_changes = any(local.get(key, 0) for key in ("staged", "modified", "untracked", "conflicts"))
        commit_message = ""
        if has_changes:
            default_message = f"更新项目：{project.name}"
            commit_message, ok = QInputDialog.getText(
                self,
                "提交并同步",
                "提交说明：",
                text=default_message,
            )
            if not ok:
                return
            commit_message = commit_message.strip() or default_message
        confirm = QMessageBox(self)
        confirm.setWindowTitle("确认提交并同步")
        confirm.setIcon(QMessageBox.Icon.Question)
        action_text = (
            f"将先提交当前修改（{commit_message}），再同步到全部 Git 渠道。"
            if commit_message
            else "当前没有待提交修改，将直接同步已提交内容。"
        )
        confirm.setText(f"确定处理“{project.name}”吗？\n\n{action_text}")
        yes_button = confirm.addButton("提交并同步", QMessageBox.ButtonRole.AcceptRole)
        confirm.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        confirm.exec()
        if confirm.clickedButton() is not yes_button:
            return
        self._start_sync(project, commit_message)

    def _set_sync_busy(self, busy: bool) -> None:
        for button in self.quick_buttons:
            button.setEnabled(not busy)
        self.settings_button.setEnabled(not busy)
        if self.tray_sync_action is not None:
            self.tray_sync_action.setEnabled(not busy)

    def _set_create_busy(self, busy: bool) -> None:
        for button in self.quick_buttons:
            button.setEnabled(not busy)
        self.settings_button.setEnabled(not busy)
        if self.tray_sync_action is not None:
            self.tray_sync_action.setEnabled(not busy)

    def _start_sync(self, project: ProjectConfig, commit_message: str = "", automatic: bool = False) -> None:
        if self.sync_worker and self.sync_worker.isRunning():
            return
        if self.worker and self.worker.isRunning():
            self._append_log("状态检查进行中，请稍后再同步。")
            return
        self._set_sync_busy(True)
        self._show_busy_dialog("正在提交并同步全部 Git 渠道" if commit_message else "正在同步全部 Git 渠道")
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self._set_progress_style("running")
        self._append_log(f"开始提交并同步：{project.name}" if commit_message else f"开始同步：{project.name}")
        self._sync_had_error = False
        values = self._sync_settings()
        self.sync_worker = SyncWorker(project, commit_message=commit_message, full_sync=bool(values["full_sync"]))
        self.sync_worker.log_message.connect(self._append_log)
        self.sync_worker.progress_changed.connect(self._on_progress_changed)
        self.sync_worker.result_ready.connect(self._on_sync_result)
        self.sync_worker.failed.connect(self._on_sync_failed)
        self.sync_worker.completed.connect(self._on_sync_completed)
        self.sync_worker.start()

    def _on_sync_result(self, result: dict) -> None:
        failures = [item for item in result.get("results", []) if not item.get("ok")]
        for item in result.get("results", []):
            self._append_log(item.get("message", ""))
        self._sync_had_error = bool(failures)

    def _on_sync_failed(self, message: str) -> None:
        self._sync_had_error = True
        self._hide_busy_dialog()
        self._append_log(f"同步失败：{message}")

    def _on_sync_completed(self) -> None:
        self._set_sync_busy(False)
        self._hide_busy_dialog()
        self._set_progress_style("error" if getattr(self, "_sync_had_error", False) else "success")
        self._append_log("同步完成。" if not getattr(self, "_sync_had_error", False) else "同步结束，部分渠道失败。")
        self.refresh_selected()
        if self.auto_sync_queue:
            QTimer.singleShot(0, self._start_next_automatic_sync)

    def _new_project(self) -> None:
        if not self.remotes:
            QMessageBox.information(self, "没有 Git 渠道", "请先在设置中添加全局 Git 渠道。")
            return
        from app.ui.dialogs import ProjectDialog
        dialog = ProjectDialog(self)
        dialog.setWindowTitle("新建项目")
        if not dialog.exec():
            return
        root_path = Path(dialog.workspace_edit.text().strip()).expanduser()
        project_name = dialog.name_edit.text().strip()
        existing_project = dialog.existing_check.isChecked()
        source = root_path if existing_project else root_path / project_name
        if not source.is_dir() and existing_project:
            QMessageBox.warning(self, "项目目录不存在", f"无法接入不存在的项目目录：\n{source}")
            return
        if source.exists() and not existing_project:
            QMessageBox.warning(self, "项目目录已存在", f"无法覆盖已有目录：\n{source}")
            return
        project = ProjectConfig(
            project_id=uuid.uuid4().hex,
            name=project_name,
            workspace_path=str(source),
            default_branch="main",
            remotes=self.remotes,
        )
        if any(item.name.casefold() == project.name.casefold() for item in self.projects):
            QMessageBox.warning(self, "项目名称重复", "已经存在同名项目，请换一个名称。")
            return
        if any(
            Path(item.workspace_path).expanduser().resolve() == source.resolve()
            for item in self.projects
        ):
            QMessageBox.warning(self, "项目已接入", "这个项目目录已经在 RepoPulse 项目列表中。")
            return
        self.projects.append(project)
        self.store.save(self.projects)
        self._reload_project_list(select_id=project.project_id)
        self._render_cards(None)
        self._start_project_creation(project)

    def _start_project_creation(self, project: ProjectConfig) -> None:
        if self.create_worker and self.create_worker.isRunning():
            return
        if self.worker and self.worker.isRunning():
            self._append_log("状态检查进行中，请稍后再创建项目。")
            return
        self._set_create_busy(True)
        self._show_busy_dialog("正在准备项目与 Git 仓库")
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self._set_progress_style("running")
        self._append_log(f"开始创建项目：{project.name}")
        self._create_had_error = False
        self.create_worker = ProjectCreateWorker(project)
        self.create_worker.log_message.connect(self._append_log)
        self.create_worker.progress_changed.connect(self._on_progress_changed)
        self.create_worker.result_ready.connect(self._on_project_created)
        self.create_worker.failed.connect(self._on_project_create_failed)
        self.create_worker.completed.connect(self._on_project_create_completed)
        self.create_worker.start()

    def _on_project_created(self, result: dict) -> None:
        project = result.get("project")
        if isinstance(project, ProjectConfig):
            self.store.save(self.projects)
            errors = [str(item) for item in result.get("errors", []) if str(item)]
            self._create_had_error = bool(errors)
            for error in errors:
                self._append_log(f"项目渠道创建失败：{error}")
            self._append_log(
                f"项目创建完成：{project.name}"
                if not errors
                else f"项目已创建，但有 {len(errors)} 个 Git 渠道未完成。"
            )
            if errors:
                self._hide_busy_dialog()
                QMessageBox.warning(self, "项目创建未完成", "\n".join(errors))

    def _on_project_create_failed(self, message: str) -> None:
        self.store.save(self.projects)
        self._create_had_error = True
        self._hide_busy_dialog()
        self._append_log(f"项目创建失败：{message}")
        QMessageBox.warning(self, "项目创建未完成", message)

    def _on_project_create_completed(self) -> None:
        self._set_create_busy(False)
        self._hide_busy_dialog()
        had_error = getattr(self, "_create_had_error", False)
        self._set_progress_style("error" if had_error else "success")
        if not had_error:
            self._append_log("项目脚手架创建完成。")
            QMessageBox.information(
                self,
                "项目创建完成",
                "项目目录、Local Git 以及已配置的远程仓库都已准备完成。",
            )
            self.refresh_selected()

    def _set_progress_style(self, state: str) -> None:
        colors = {
            "idle": ("#2C7180", PANEL_RAISED),
            "running": (ACCENT_COLOR, PANEL_RAISED),
            "success": ("#70D6A5", PANEL_RAISED),
            "error": ("#F27788", PANEL_RAISED),
        }
        chunk, base = colors.get(state, colors["idle"])
        self.progress_bar.setStyleSheet(
            "QProgressBar { "
            f"background: {base}; border: 1px solid {BORDER_COLOR}; border-radius: 4px; "
            "color: #E8ECF2; text-align: center; padding: 0; }"
            f"QProgressBar::chunk {{ background: {chunk}; border-radius: 3px; }}"
        )

    def _show_busy_dialog(self, message: str) -> None:
        if self.busy_dialog is None:
            self.busy_dialog = BusyDialog(self)
        self.busy_dialog.set_message(message)
        self.busy_dialog.show()
        self.busy_dialog.raise_()
        self.busy_dialog.activateWindow()

    def _hide_busy_dialog(self) -> None:
        if self.busy_dialog is None:
            return
        dialog = self.busy_dialog
        self.busy_dialog = None
        dialog.close()
        dialog.deleteLater()

    def _on_progress_changed(self, completed: int, total: int) -> None:
        percentage = round(completed * 100 / total) if total else 0
        self.progress_bar.setValue(percentage)
        self._set_progress_style("running" if completed < total else "success")

    def open_settings(self) -> None:
        dialog = SettingsDialog(self)
        dialog.add_git_requested.connect(lambda git_dialog: self._submit_settings_git(dialog, git_dialog))
        dialog.settings_changed.connect(self._apply_sync_settings)
        dialog.exec()

    @staticmethod
    def _sync_settings() -> dict[str, object]:
        try:
            settings = QSettings("RepoPulse", "RepoPulse")
            return {
                "full_sync": settings.value("sync_full_files", False, type=bool),
                "ignore_github": settings.value("ignore_github_failure", False, type=bool),
                "scheduled": settings.value("scheduled_sync_enabled", False, type=bool),
                "hours": settings.value("scheduled_sync_hours", 0, type=int),
                "minutes": settings.value("scheduled_sync_minutes", 0, type=int),
                "seconds": settings.value("scheduled_sync_seconds", 30, type=int),
                "leading": settings.value("leading_sync_enabled", False, type=bool),
                "threshold": settings.value("leading_sync_threshold", 1, type=int),
            }
        except Exception:
            return {"full_sync": False, "ignore_github": False, "scheduled": False, "hours": 0, "minutes": 0, "seconds": 30, "leading": False, "threshold": 1}

    def _apply_sync_settings(self) -> None:
        values = self._sync_settings()
        if not values["scheduled"]:
            self.auto_sync_timer.stop()
            return
        interval_ms = (
            int(values["hours"]) * 3600
            + int(values["minutes"]) * 60
            + int(values["seconds"])
        ) * 1000
        self.auto_sync_timer.start(max(1000, interval_ms))

    def _run_scheduled_sync(self) -> None:
        self._queue_automatic_sync(self.projects, "定时同步")

    def _queue_automatic_sync(self, projects: list[ProjectConfig], reason: str) -> None:
        if self.auto_sync_queue or (self.sync_worker and self.sync_worker.isRunning()):
            return
        self.auto_sync_queue = [project for project in projects if project.sync_enabled]
        if not self.auto_sync_queue:
            return
        self.auto_sync_reason = reason
        self._auto_sync_had_error = False
        self._start_next_automatic_sync()

    def _start_next_automatic_sync(self) -> None:
        if not self.auto_sync_queue:
            self.auto_sync_reason = ""
            return
        if self.worker and self.worker.isRunning():
            return
        if self.sync_worker and self.sync_worker.isRunning():
            return
        project = self.auto_sync_queue.pop(0)
        if not project.sync_enabled or not project.workspace_path or not project.remotes:
            self._start_next_automatic_sync()
            return
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        message = f"{self.auto_sync_reason} {timestamp}"
        self._append_log(f"开始{self.auto_sync_reason}：{project.name}")
        self._start_sync(project, message, automatic=True)

    def _submit_settings_git(self, settings_dialog: SettingsDialog, git_dialog: GitDialog) -> None:
        settings_dialog.close()
        self.add_git(git_dialog)

    def _set_busy(self, busy: bool) -> None:
        self.settings_button.setEnabled(not busy)
        for button in self.quick_buttons:
            button.setEnabled(not busy)

    def refresh_all(self) -> None:
        self._start_refresh([project for project in self.projects if project.sync_enabled])

    def refresh_selected(self) -> None:
        project = self._current_project()
        if project and project.sync_enabled:
            self._start_refresh([project])

    def _start_refresh(self, projects: list[ProjectConfig]) -> None:
        projects = [project for project in projects if project.sync_enabled]
        if not projects or (self.worker and self.worker.isRunning()):
            return
        self._set_busy(True)
        self._show_busy_dialog("正在读取全部 Git 状态")
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("%p%")
        self._set_progress_style("running")
        self._append_log("开始刷新状态……")
        values = self._sync_settings()
        self.worker = StatusWorker(projects, ignore_github_failure=bool(values["ignore_github"]))
        self.worker.project_ready.connect(self._on_project_ready)
        self.worker.log_message.connect(self._append_log)
        self.worker.progress_changed.connect(self._on_progress_changed)
        self.worker.failed.connect(lambda message: self._append_log(f"错误：{message}"))
        self.worker.completed.connect(self._on_refresh_completed)
        self.worker.start()

    def _on_project_ready(self, project_id: str, result: dict) -> None:
        self.results[project_id] = result
        selected = self._current_project()
        self._reload_project_list(select_id=selected.project_id if selected else project_id)
        if selected and selected.project_id == project_id:
            self._render_cards(result)
        self._check_leading_sync(result)

    def _check_leading_sync(self, result: dict) -> None:
        values = self._sync_settings()
        if not values["leading"]:
            return
        threshold = max(1, int(values["threshold"]))
        project = next((item for item in self.projects if item.project_id == result.get("project_id")), None)
        if project and not project.sync_enabled:
            self.results.pop(project.project_id, None)
            return
        if not project or not project.sync_enabled:
            return
        if any(
            remote.get("kind") == "local" and int(remote.get("ahead") or 0) >= threshold
            for remote in result.get("remotes", {}).values()
        ):
            self._queue_automatic_sync([project], "检测同步")

    def _on_refresh_completed(self) -> None:
        self._set_busy(False)
        self._hide_busy_dialog()
        has_error = any(
            result.get("local", {}).get("error")
            or any(remote.get("error") and not remote.get("ignored") for remote in result.get("remotes", {}).values())
            for result in self.results.values()
        )
        self._set_progress_style("error" if has_error else "success")
        self._append_log("刷新完成。")
        if self.auto_sync_queue:
            QTimer.singleShot(0, self._start_next_automatic_sync)

    def add_git(self, dialog: GitDialog | None = None) -> None:
        if dialog is None:
            dialog = GitDialog(self, project=None)
            if not dialog.exec():
                return
        remote = dialog.build_remote()
        discovered = dialog.get_discovered_repositories()
        if remote.kind == "local" and not remote.path:
            QMessageBox.warning(self, "信息不完整", "请选择 Local Git 的项目集合目录。")
            return
        if remote.kind == "local":
            selected_path = Path(remote.path).expanduser()
            if (selected_path / ".git").is_dir():
                remote.path = str(selected_path.parent)
        if remote.kind in {"github", "nas", "gitea"}:
            remote.url = ""
        key = remote.kind
        if key in self.remotes:
            QMessageBox.information(self, "Git 渠道已存在", "同类型 Git 渠道已经配置。")
            return
        self.remotes[key] = remote
        self.store.global_remotes = self.remotes
        for project in self.projects:
            project.remotes = self.remotes

        # Local Git can seed the project list when the application is empty.
        first_project_id = ""
        if remote.kind == "local" and not self.projects:
            for info in discovered:
                project = ProjectConfig(
                    project_id=uuid.uuid4().hex,
                    name=info["name"],
                    workspace_path=str(Path.home() / "Desktop" / info["name"]),
                    default_branch=info.get("branch", ""),
                    remotes=self.remotes,
                )
                self.projects.append(project)
                first_project_id = first_project_id or project.project_id
        self.store.save(self.projects)
        self.selected_git_key = key
        self._reload_project_list(select_id=first_project_id or None)
        self._render_cards(None)
        self._append_log(f"已添加全局 Git 渠道：{remote.label}")
        self.refresh_all()

    def _choose_host_repository(self, repositories: list[dict]) -> dict | None:
        if len(repositories) == 1:
            return repositories[0]
        labels = [item.get("full_name") or item.get("name") or "未命名仓库" for item in repositories]
        choice, ok = QInputDialog.getItem(self, "选择仓库", "请选择要添加的仓库：", labels, 0, False)
        if not ok:
            return None
        index = labels.index(choice)
        return repositories[index]

    def _match_local_repository(self, project: ProjectConfig, repositories: list[dict]) -> dict | None:
        if len(repositories) == 1:
            return repositories[0]
        match = next((item for item in repositories if item["name"].casefold() == project.name.casefold()), None)
        if match:
            return match
        names = "、".join(item["name"] for item in repositories)
        QMessageBox.warning(self, "无法匹配项目", f"目录中找到多个 Git 项目，但没有和“{project.name}”同名的项目：\n{names}")
        return None

    def _infer_project_identity(self, remote) -> tuple[str, str]:
        source = remote.path if remote.kind == "local" else remote.url
        source = (source or "未命名项目").rstrip("/\\")
        name = source.replace("\\", "/").rsplit("/", 1)[-1] or "未命名项目"
        if name.lower().endswith(".git"):
            name = name[:-4]
        desktop_path = Path.home() / "Desktop" / name
        return name, str(desktop_path)

    def open_local(self) -> None:
        project = self._current_project()
        if project and Path(project.workspace_path).exists():
            os.startfile(project.workspace_path)

    def open_github(self) -> None:
        project = self._current_project()
        if project:
            url = next((remote.url for remote in project.remotes.values() if remote.kind == "github" and remote.url), "")
            if url:
                webbrowser.open(url)

    def open_terminal(self) -> None:
        project = self._current_project()
        if project and Path(project.workspace_path).exists():
            os.startfile("cmd.exe", "open", f'/K cd /d "{project.workspace_path}"')
