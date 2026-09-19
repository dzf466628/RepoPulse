from __future__ import annotations

import uuid
from pathlib import Path
from urllib.parse import urlparse

from PyQt6.QtCore import QSettings, Qt, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QComboBox,
    QCheckBox,
    QAbstractSpinBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from app.core.git_service import GitCommandError, GitService
from app.models import ProjectConfig, RemoteConfig
from app.ui.theme import ACCENT_COLOR, BORDER_COLOR, PANEL_COLOR, PANEL_RAISED, SPACE_1, SPACE_2, SPACE_4


GIT_TYPES = [
    ("Local Repository · 本地 Git 渠道", "local"),
    ("GitHub · GitHub", "github"),
    ("GitLab · GitLab", "gitlab"),
    ("Gitee · Gitee", "gitee"),
    ("Gitea · 自建 Git 服务", "gitea"),
    ("NAS Gitea · NAS 套件", "nas"),
    ("Bitbucket · Bitbucket", "bitbucket"),
    ("Azure DevOps · Azure DevOps", "azure_devops"),
    ("Codeberg · Codeberg", "codeberg"),
    ("Other Git · 其他 Git 仓库", "custom"),
]


def _compact_button_box(box: QDialogButtonBox) -> QDialogButtonBox:
    """Use compact Chinese labels for every dialog action row."""
    labels = {
        QDialogButtonBox.StandardButton.Ok: "确定",
        QDialogButtonBox.StandardButton.Cancel: "取消",
        QDialogButtonBox.StandardButton.Close: "关闭",
    }
    for standard, label in labels.items():
        button = box.button(standard)
        if button:
            button.setText(label)
            button.setStyleSheet(
                "QPushButton { padding: 0 12px; min-height: 0; max-height: 30px; }"
            )
            button.setFixedHeight(30)
            button.setMinimumWidth(64 if standard != QDialogButtonBox.StandardButton.Close else 72)
    box.setContentsMargins(0, 0, 0, 0)
    box.setFixedHeight(34)
    return box


class DoubleClickLabel(QLabel):
    double_clicked = pyqtSignal()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            self.double_clicked.emit()
        super().mouseDoubleClickEvent(event)


class ProjectDetailDialog(QDialog):
    """显示项目本地信息以及所有全局 Git 渠道状态。"""

    sync_requested = pyqtSignal()
    delete_requested = pyqtSignal()

    def __init__(self, project: ProjectConfig, stats: dict, icon: QIcon | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"项目详情 · {project.name}")
        self.setFixedSize(420, 560)

        icon_label = QLabel()
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_label.setStyleSheet(
            f"background: {PANEL_RAISED}; border: 1px solid {ACCENT_COLOR}; border-radius: 6px; "
            f"color: {ACCENT_COLOR}; font-size: 20px; font-weight: 700;"
        )
        if icon and not icon.isNull():
            icon_label.setPixmap(icon.pixmap(32, 32))
        else:
            icon_label.setText((project.name.strip() or "?")[:1].upper())
        icon_label.setFixedSize(36, 36)
        name_label = QLabel(project.name)
        name_label.setStyleSheet("font-size: 21px; font-weight: 700; color: #F4F7FB;")
        name_label.setWordWrap(True)
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(SPACE_2)
        header.addWidget(icon_label)
        header.addWidget(name_label, 1)

        info = QFrame()
        info.setObjectName("projectInfo")
        info.setStyleSheet(
            f"QFrame#projectInfo {{ background: {PANEL_COLOR}; border: 1px solid {BORDER_COLOR}; border-radius: 5px; }}"
        )
        info_layout = QFormLayout(info)
        info_layout.setContentsMargins(SPACE_2, SPACE_2, SPACE_2, SPACE_2)
        info_layout.setSpacing(SPACE_1)
        for label, value in (
            ("项目目录", str(project.workspace_path)),
            ("创建日期", str(stats.get("created_at") or "未知")),
            ("文件数", f"{stats.get('file_count', 0)} 个"),
            ("当前总大小", str(stats.get("total_size") or "0 B")),
        ):
            value_label = QLabel(value)
            value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            value_label.setWordWrap(True)
            info_layout.addRow(label, value_label)

        git_title = QLabel("已连接 Git 与最新提交")
        git_title.setStyleSheet("font-weight: 600; color: #E8ECF2;")
        git_list = QTextBrowser()
        git_list.setOpenExternalLinks(False)
        git_list.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        git_list.setHtml(str(stats.get("git_summary_html") or "<span>暂无 Git 渠道</span>"))
        git_list.setMinimumHeight(180)

        delete_button = QPushButton("删除项目")
        delete_button.setFixedHeight(30)
        delete_button.setStyleSheet(
            "QPushButton { background: #7D3044; border-color: #A9435D; color: #FFE8EE; padding: 0 10px; }"
            "QPushButton:hover { background: #A9435D; border-color: #F27788; }"
            "QPushButton:pressed { background: #5D2434; }"
        )
        delete_button.clicked.connect(self._confirm_delete)
        sync_button = QPushButton("提交并同步")
        sync_button.setFixedHeight(30)
        sync_button.clicked.connect(self.sync_requested.emit)
        close_button = QPushButton("关闭")
        close_button.setFixedHeight(30)
        close_button.setMinimumWidth(72)
        close_button.clicked.connect(self.reject)
        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(SPACE_1)
        footer.addWidget(delete_button)
        footer.addStretch(1)
        footer.addWidget(sync_button)
        footer.addWidget(close_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE_4, SPACE_4, SPACE_4, SPACE_4)
        layout.setSpacing(SPACE_2)
        layout.addLayout(header)
        layout.addWidget(info)
        layout.addWidget(git_title)
        layout.addWidget(git_list, 1)
        layout.addLayout(footer)

    def _confirm_delete(self) -> None:
        confirm = QMessageBox(self)
        confirm.setWindowTitle("确认删除项目")
        confirm.setIcon(QMessageBox.Icon.Warning)
        confirm.setText(
            "确定删除这个项目吗？\n\n"
            "将删除本地开发目录、Local Git 目录，以及 NAS/GitHub 中对应的远程仓库。此操作不可恢复。"
        )
        delete_button = confirm.addButton("确认删除", QMessageBox.ButtonRole.DestructiveRole)
        confirm.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        confirm.setFixedHeight(160)
        confirm.exec()
        if confirm.clickedButton() is delete_button:
            self.delete_requested.emit()


class RepositoryDetailDialog(QDialog):
    """显示仓库状态，以及当前 Git 渠道下已管理的项目。"""

    project_view_requested = pyqtSignal(str, str)
    project_delete_requested = pyqtSignal(str)
    open_repository_requested = pyqtSignal(str)
    rename_requested = pyqtSignal(str)
    remove_repository_requested = pyqtSignal()

    def __init__(
        self,
        title: str,
        status: str,
        status_color: str,
        body: str,
        rich_body: bool = False,
        projects: list[dict] | None = None,
        repository_target: str = "",
        rename_enabled: bool = True,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(f"仓库详情 · {title}")
        self.setFixedSize(400, 600)

        self.title_label = DoubleClickLabel(title)
        self.title_label.setStyleSheet("font-size: 19px; font-weight: 700; color: #F4F7FB;")
        self.title_label.setCursor(Qt.CursorShape.IBeamCursor)
        self.title_label.setToolTip("双击名称修改")
        self.title_label.setEnabled(rename_enabled)
        self.title_label.double_clicked.connect(self._request_rename)
        status_label = QLabel(status)
        status_label.setStyleSheet(
            f"color: {status_color}; font-weight: 700; background: {PANEL_RAISED}; padding: 2px 8px; border-radius: 6px;"
        )
        open_repository = QPushButton("打开仓库")
        open_repository.setEnabled(bool(repository_target))
        open_repository.setToolTip("在文件夹或浏览器中打开当前仓库")
        open_repository.clicked.connect(lambda: self.open_repository_requested.emit(repository_target))
        detail = QTextBrowser()
        detail.setOpenExternalLinks(False)
        detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        detail.setMaximumHeight(132)
        if rich_body:
            detail.setHtml(body)
        else:
            detail.setPlainText(body)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.addWidget(self.title_label)
        header.addStretch(1)
        header.addWidget(status_label)
        header.addWidget(open_repository)

        projects_title = QLabel("项目列表")
        projects_title.setStyleSheet("font-weight: 600; color: #E8ECF2;")
        self.projects_scroll = QScrollArea()
        self.projects_scroll.setWidgetResizable(True)
        self.projects_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.projects_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.projects_scroll.setWidget(self._build_projects_widget(projects or []))
        remove_button = QPushButton("删除本仓库")
        remove_button.setFixedHeight(30)
        remove_button.clicked.connect(self.remove_repository_requested.emit)
        close_button = QPushButton("关闭")
        close_button.setFixedHeight(30)
        close_button.setMinimumWidth(72)
        close_button.clicked.connect(self.reject)
        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(SPACE_2)
        footer.addWidget(remove_button)
        footer.addStretch(1)
        footer.addWidget(close_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE_4, SPACE_4, SPACE_4, SPACE_4)
        layout.setSpacing(SPACE_2)
        layout.addLayout(header)
        layout.addWidget(detail)
        layout.addWidget(projects_title)
        layout.addWidget(self.projects_scroll, 1)
        layout.addLayout(footer)

        if not rename_enabled:
            remove_button.setVisible(False)

    def _request_rename(self) -> None:
        dialog = QInputDialog(self)
        dialog.setWindowTitle("修改仓库名称")
        dialog.setLabelText("仓库名称：")
        dialog.setTextValue(self.title_label.text())
        button_box = dialog.findChild(QDialogButtonBox)
        if button_box:
            _compact_button_box(button_box)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        name = dialog.textValue()
        name = name.strip()
        if not accepted or not name:
            return
        self.title_label.setText(name)
        self.setWindowTitle(f"仓库详情 · {name}")
        self.rename_requested.emit(name)

    def _build_projects_widget(self, projects: list[dict]) -> QWidget:
        self._project_rows: dict[str, QWidget] = {}
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE_1)
        for project in projects:
            self._add_project_row(layout, project)
        if not projects:
            empty = QLabel("当前渠道还没有已管理项目")
            empty.setStyleSheet("color: #8A97AA; padding: 8px 0;")
            layout.addWidget(empty)
            self._empty_projects_label = empty
        layout.addStretch(1)
        return container

    def _add_project_row(self, layout: QVBoxLayout, project: dict) -> None:
        project_id = str(project.get("project_id") or "")
        row = QFrame()
        row.setObjectName("projectRow")
        row.setStyleSheet(
            f"QFrame#projectRow {{ background: {PANEL_COLOR}; border: 1px solid {BORDER_COLOR}; border-radius: 4px; }}"
        )
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(SPACE_2, SPACE_1, SPACE_2, SPACE_1)
        row_layout.setSpacing(SPACE_1)
        name = QLabel(str(project.get("name") or "未命名项目"))
        name.setStyleSheet("color: #E8ECF2; font-weight: 600;")
        name.setToolTip(str(project.get("workspace_path") or ""))
        row_layout.addWidget(name, 1)
        view_button = QPushButton("查看")
        view_button.setFixedWidth(52)
        project_target = str(project.get("project_target") or project.get("url") or "")
        view_button.setEnabled(bool(project_target))
        view_button.clicked.connect(lambda: self.project_view_requested.emit(project_id, project_target))
        delete_button = QPushButton("删除")
        delete_button.setFixedWidth(52)
        delete_button.clicked.connect(lambda: self._confirm_delete(project_id, str(project.get("name") or "未命名项目")))
        row_layout.addWidget(view_button)
        row_layout.addWidget(delete_button)
        layout.addWidget(row)
        self._project_rows[project_id] = row

    def _confirm_delete(self, project_id: str, project_name: str) -> None:
        confirm = QMessageBox(self)
        confirm.setWindowTitle("确认移除项目")
        confirm.setIcon(QMessageBox.Icon.Question)
        confirm.setText(
            f"确定从当前 Git 渠道移除“{project_name}”吗？\n\n"
            "只移除 RepoPulse 中的关联，不会删除本地文件或远程仓库。"
        )
        yes_button = confirm.addButton("确认删除", QMessageBox.ButtonRole.AcceptRole)
        confirm.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        confirm.setFixedHeight(150)
        confirm.exec()
        if confirm.clickedButton() is not yes_button:
            return
        self.project_delete_requested.emit(project_id)
        row = self._project_rows.pop(project_id, None)
        if row:
            row.deleteLater()


class SettingsDialog(QDialog):
    """同步设置弹窗：左侧导航，右侧内容。"""

    add_git_requested = pyqtSignal(object)
    settings_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("同步设置")
        self.setMinimumSize(680, 420)
        self.resize(720, 460)
        self._build_ui()

    def _build_ui(self) -> None:
        self.menu_list = QListWidget()
        self.menu_list.setFixedWidth(150)
        self.menu_list.addItems(["添加 Git", "同步设置", "软件设置", "关于"])

        self.pages = QStackedWidget()
        self.pages.addWidget(self._build_git_page())
        self.pages.addWidget(self._build_appearance_page())
        self.pages.addWidget(self._build_software_page())
        self.pages.addWidget(self._build_about_page())
        self.menu_list.currentRowChanged.connect(self._switch_page)
        self.menu_list.setCurrentRow(0)

        content = QHBoxLayout()
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(SPACE_2)
        content.addWidget(self.menu_list)
        content.addWidget(self.pages, 1)

        self.close_button = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        _compact_button_box(self.close_button)
        self.close_button.rejected.connect(self.reject)
        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(SPACE_2)
        footer.addStretch(1)
        if hasattr(self, "git_action_box"):
            footer.addWidget(self.git_action_box)
        footer.addWidget(self.close_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE_4, SPACE_4, SPACE_4, SPACE_4)
        layout.setSpacing(SPACE_2)
        layout.addLayout(content, 1)
        layout.addLayout(footer)

    def _switch_page(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        if hasattr(self, "git_action_box"):
            self.git_action_box.setVisible(index == 0)

    def _build_git_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(SPACE_2, SPACE_2, SPACE_2, SPACE_2)
        layout.setSpacing(SPACE_2)
        title = QLabel("添加 Git")
        title.setStyleSheet("font-size: 17px; font-weight: 700; color: #F4F7FB;")
        layout.addWidget(title)
        current_project = self.parent()._current_project() if self.parent() and hasattr(self.parent(), "_current_project") else None
        self.git_form = GitDialog(self, project=current_project)
        self.git_form.setWindowFlags(Qt.WindowType.Widget)
        self.git_form.setWindowTitle("")
        self.git_form.setMinimumWidth(0)
        self.git_form.accepted.connect(lambda: self.add_git_requested.emit(self.git_form))
        self.git_form.rejected.connect(self.git_form.show)
        form_layout = self.git_form.layout()
        action_item = form_layout.takeAt(form_layout.count() - 1)
        self.git_action_box = action_item.widget() if action_item else None
        if self.git_action_box:
            self.git_action_box.setParent(self)
        layout.addWidget(self.git_form, 1)
        self.git_form.show()
        return page

    def _build_appearance_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(SPACE_2, SPACE_2, SPACE_2, SPACE_2)
        layout.setSpacing(SPACE_2)
        page.setStyleSheet(
            f"""
            QCheckBox {{ color: #E8ECF2; spacing: 8px; padding: 0; }}
            QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 5px; border: 1px solid #3D6475; background: #0A2231; }}
            QCheckBox::indicator:hover {{ border-color: {ACCENT_COLOR}; background: #123D4D; }}
            QCheckBox::indicator:checked {{ border-color: {ACCENT_COLOR}; background: {ACCENT_COLOR}; }}
            QSpinBox {{ color: #F4F7FB; background: #0A2231; border: 1px solid #315365; border-radius: 5px; padding: 0 4px; min-height: 20px; max-height: 20px; }}
            QSpinBox:hover {{ border-color: {ACCENT_COLOR}; }}
            QSpinBox:focus {{ border-color: {ACCENT_COLOR}; }}
            """
        )
        title = QLabel("同步设置")
        title.setStyleSheet("font-size: 17px; font-weight: 700; color: #F4F7FB;")
        layout.addWidget(title)

        settings = QSettings("RepoPulse", "RepoPulse")
        self.full_sync_check = QCheckBox("本地 Git / NAS Git 全量同步")
        self.full_sync_check.setChecked(settings.value("sync_full_files", False, type=bool))
        self.full_sync_check.setToolTip("同步本地 Git 和 NAS Git 时完整对齐文件")
        self.ignore_github_check = QCheckBox("GitHub 连接失败自动忽略")
        self.ignore_github_check.setChecked(settings.value("ignore_github_failure", False, type=bool))
        self.ignore_github_check.setToolTip("GitHub 不可用时显示“未代理”，不影响其他渠道状态")

        self.scheduled_check = QCheckBox("开启定时同步")
        self.scheduled_check.setChecked(settings.value("scheduled_sync_enabled", False, type=bool))
        self.scheduled_hours = QSpinBox()
        self.scheduled_hours.setRange(0, 23)
        self.scheduled_minutes = QSpinBox()
        self.scheduled_minutes.setRange(0, 59)
        self.scheduled_seconds = QSpinBox()
        self.scheduled_seconds.setRange(1, 59)
        for spin in (self.scheduled_hours, self.scheduled_minutes, self.scheduled_seconds):
            spin.setFixedWidth(64)
            spin.setFixedHeight(22)
            spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.UpDownArrows)
            spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scheduled_hours.setValue(settings.value("scheduled_sync_hours", 0, type=int))
        self.scheduled_minutes.setValue(settings.value("scheduled_sync_minutes", 0, type=int))
        self.scheduled_seconds.setValue(settings.value("scheduled_sync_seconds", 30, type=int))
        interval = QHBoxLayout()
        interval.setContentsMargins(0, 0, 0, 0)
        interval.addWidget(QLabel("每"))
        interval.addWidget(self.scheduled_hours)
        interval.addWidget(QLabel("时"))
        interval.addWidget(self.scheduled_minutes)
        interval.addWidget(QLabel("分"))
        interval.addWidget(self.scheduled_seconds)
        interval.addWidget(QLabel("秒"))
        interval.addStretch(1)

        self.leading_check = QCheckBox("开启修改同步")
        self.leading_check.setChecked(settings.value("leading_sync_enabled", False, type=bool))
        self.leading_threshold = QSpinBox()
        self.leading_threshold.setRange(1, 9999)
        self.leading_threshold.setValue(settings.value("leading_sync_threshold", 1, type=int))
        self.leading_threshold.setFixedWidth(72)
        self.leading_threshold.setFixedHeight(22)
        self.leading_threshold.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.UpDownArrows)
        self.leading_threshold.setAlignment(Qt.AlignmentFlag.AlignCenter)
        leading_row = QHBoxLayout()
        leading_row.setContentsMargins(0, 0, 0, 0)
        leading_row.setSpacing(SPACE_1)
        leading_row.addWidget(QLabel("本地领先"))
        leading_row.addWidget(self.leading_threshold)
        leading_row.addWidget(QLabel("步时自动同步"))
        leading_row.addStretch(1)

        for widget in (
            self.full_sync_check,
            self.ignore_github_check,
            self.scheduled_check,
            self.scheduled_hours,
            self.scheduled_minutes,
            self.scheduled_seconds,
            self.leading_check,
            self.leading_threshold,
        ):
            if isinstance(widget, QCheckBox):
                widget.toggled.connect(self._save_sync_settings)
            else:
                widget.valueChanged.connect(self._save_sync_settings)
        layout.addWidget(self.full_sync_check)
        layout.addWidget(self.ignore_github_check)
        layout.addWidget(self.scheduled_check)
        layout.addLayout(interval)
        layout.addWidget(self.leading_check)
        layout.addLayout(leading_row)
        layout.addStretch(1)
        return page

    def _save_sync_settings(self) -> None:
        try:
            settings = QSettings("RepoPulse", "RepoPulse")
            settings.setValue("sync_full_files", self.full_sync_check.isChecked())
            settings.setValue("ignore_github_failure", self.ignore_github_check.isChecked())
            settings.setValue("scheduled_sync_enabled", self.scheduled_check.isChecked())
            settings.setValue("scheduled_sync_hours", self.scheduled_hours.value())
            settings.setValue("scheduled_sync_minutes", self.scheduled_minutes.value())
            settings.setValue("scheduled_sync_seconds", self.scheduled_seconds.value())
            settings.setValue("leading_sync_enabled", self.leading_check.isChecked())
            settings.setValue("leading_sync_threshold", self.leading_threshold.value())
            settings.sync()
        except Exception:
            pass
        self.settings_changed.emit()

    def _build_software_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(SPACE_2, SPACE_2, SPACE_2, SPACE_2)
        layout.setSpacing(SPACE_2)
        title = QLabel("软件设置")
        title.setStyleSheet("font-size: 17px; font-weight: 700; color: #F4F7FB;")

        settings = QSettings("RepoPulse", "RepoPulse")
        try:
            prompt = settings.value("close_prompt", True, type=bool)
            close_to_tray = settings.value("close_to_tray", True, type=bool)
        except Exception:
            prompt, close_to_tray = True, True

        self.close_prompt_check = QCheckBox("关闭软件时提示选择")
        self.close_prompt_check.setChecked(prompt)
        self.close_prompt_check.setToolTip("关闭窗口时选择最小化到托盘或退出软件")
        self.close_action_combo = QComboBox()
        self.close_action_combo.addItem("最小化到托盘", True)
        self.close_action_combo.addItem("关闭软件", False)
        self.close_action_combo.setCurrentIndex(0 if close_to_tray else 1)
        self.close_action_combo.setToolTip("关闭提示被关闭后使用的默认操作")
        self.close_prompt_check.toggled.connect(self._save_software_settings)
        self.close_action_combo.currentIndexChanged.connect(self._save_software_settings)

        form = QFormLayout()
        form.setSpacing(SPACE_2)
        form.addRow("关闭行为", self.close_prompt_check)
        form.addRow("默认操作", self.close_action_combo)
        layout.addWidget(title)
        layout.addLayout(form)
        layout.addStretch(1)
        return page

    def _save_software_settings(self) -> None:
        try:
            settings = QSettings("RepoPulse", "RepoPulse")
            settings.setValue("close_prompt", self.close_prompt_check.isChecked())
            settings.setValue("close_to_tray", bool(self.close_action_combo.currentData()))
            settings.sync()
        except Exception:
            pass

    def _build_about_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(SPACE_2, SPACE_2, SPACE_2, SPACE_2)
        title = QLabel("关于 RepoPulse")
        title.setStyleSheet("font-size: 17px; font-weight: 700; color: #F4F7FB;")
        version = QLabel("Git 状态台 · v0.2.1")
        layout.addWidget(title)
        layout.addWidget(version)
        layout.addStretch(1)
        return page

class ProjectDialog(QDialog):
    """创建项目脚手架，或选择已有目录接入并准备 Git 渠道。"""

    def __init__(self, parent=None, project: ProjectConfig | None = None):
        super().__init__(parent)
        self.project = project
        self.setWindowTitle("编辑项目" if project else "添加项目")
        self.setMinimumWidth(560)
        self._build_ui()
        if project:
            self.name_edit.setText(project.name)
            self.workspace_edit.setText(project.workspace_path)

    def _build_ui(self) -> None:
        self.existing_check = QCheckBox("现有项目")
        self.existing_check.setToolTip("直接读取已有项目目录，不创建新文件夹")
        self.existing_check.stateChanged.connect(self._sync_existing_mode)
        self.name_edit = QLineEdit()
        self.workspace_edit = QLineEdit()
        browse = QPushButton("选择目录")
        self.browse_button = browse
        browse.clicked.connect(self._browse_workspace)
        workspace_row = QHBoxLayout()
        workspace_row.setContentsMargins(0, 0, 0, 0)
        workspace_row.addWidget(self.workspace_edit)
        workspace_row.addWidget(browse)
        form = QFormLayout()
        form.setSpacing(SPACE_2)
        form.addRow("项目名称", self.name_edit)
        form.addRow("项目根目录", workspace_row)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        _compact_button_box(buttons)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE_4, SPACE_4, SPACE_4, SPACE_4)
        layout.setSpacing(SPACE_2)
        layout.addWidget(self.existing_check)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def _sync_existing_mode(self) -> None:
        existing = self.existing_check.isChecked()
        self.browse_button.setText("选择项目" if existing else "选择目录")

    def _browse_workspace(self) -> None:
        title = "选择已有项目目录" if self.existing_check.isChecked() else "选择项目根目录"
        path = QFileDialog.getExistingDirectory(self, title)
        if path:
            self.workspace_edit.setText(path)
            if self.existing_check.isChecked() and not self.name_edit.text().strip():
                self.name_edit.setText(Path(path).name)

    def _accept(self) -> None:
        path = self.workspace_edit.text().strip()
        if not self.name_edit.text().strip() and self.existing_check.isChecked():
            self.name_edit.setText(Path(path).name)
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, "信息不完整", "请填写项目名称。")
            return
        if not path:
            QMessageBox.warning(self, "信息不完整", "请选择项目根目录。")
            return
        if not Path(path).is_dir():
            QMessageBox.warning(self, "目录不存在", "项目根目录必须是已经存在的文件夹。")
            return
        self.accept()

    def build_project(self) -> ProjectConfig:
        project_id = self.project.project_id if self.project else uuid.uuid4().hex
        return ProjectConfig(
            project_id=project_id,
            name=self.name_edit.text().strip(),
            workspace_path=self.workspace_edit.text().strip(),
            default_branch=self.project.default_branch if self.project else "",
            remotes=dict(self.project.remotes) if self.project else {},
            card_order=list(self.project.card_order) if self.project else [],
        )


class GitDialog(QDialog):
    """给当前选中的项目添加一个 Git 储存渠道。"""

    def __init__(self, parent=None, project: ProjectConfig | None = None, remote: RemoteConfig | None = None):
        super().__init__(parent)
        self.project = project
        self.remote = remote
        self.discovered_repositories: list[dict] = []
        self.setWindowTitle("编辑 Git 渠道" if remote else "添加 Git")
        self.setMinimumWidth(540)
        self._build_ui()
        self._sync_type()
        if remote:
            self._load_remote(remote)

    def _build_ui(self) -> None:
        self.type_combo = QComboBox()
        for label, kind in GIT_TYPES:
            self.type_combo.addItem(label, kind)
        self.type_combo.currentIndexChanged.connect(self._sync_type)

        self.name_edit = QLineEdit()
        self.location_edit = QLineEdit()
        self.browse_button = QPushButton("选择目录")
        self.browse_button.clicked.connect(self._browse_location)
        location_row = QHBoxLayout()
        location_row.setContentsMargins(0, 0, 0, 0)
        location_row.addWidget(self.location_edit)
        location_row.addWidget(self.browse_button)
        self.location_label = QLabel("本地渠道目录")
        self.nas_ip_label = QLabel("NAS 地址")
        self.nas_ip_edit = QLineEdit()
        self.nas_port_label = QLabel("端口")
        self.nas_port_edit = QLineEdit()
        self.nas_port_edit.setMaximumWidth(110)
        nas_row = QHBoxLayout()
        nas_row.setContentsMargins(0, 0, 0, 0)
        nas_row.addWidget(self.nas_ip_edit, 1)
        nas_row.addWidget(self.nas_port_label)
        nas_row.addWidget(self.nas_port_edit)
        self.account_label = QLabel("账号 / 用户名")
        self.account_edit = QLineEdit()
        self.secret_label = QLabel("密码 / Token")
        self.secret_edit = QLineEdit()
        self.secret_edit.setEchoMode(QLineEdit.EchoMode.Password)
        form = QFormLayout()
        form.setSpacing(SPACE_2)
        form.addRow("Git 类型", self.type_combo)
        form.addRow("卡片名称", self.name_edit)
        form.addRow(self.location_label, location_row)
        form.addRow(self.nas_ip_label, nas_row)
        form.addRow(self.account_label, self.account_edit)
        form.addRow(self.secret_label, self.secret_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        _compact_button_box(buttons)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE_4, SPACE_4, SPACE_4, SPACE_4)
        layout.setSpacing(SPACE_2)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def _browse_location(self) -> None:
        if self.type_combo.currentData() == "local":
            path = QFileDialog.getExistingDirectory(self, "选择 Local Git 仓库")
            if path:
                self.location_edit.setText(path)
                if not self.name_edit.text().strip():
                    self.name_edit.setText("Local Git")

    def _sync_type(self) -> None:
        kind = str(self.type_combo.currentData())
        is_local = kind == "local"
        is_github = kind == "github"
        is_nas = kind == "nas"
        self.location_label.setText("本地仓库或集合" if is_local else "服务地址")
        self.browse_button.setVisible(is_local)
        self.location_label.setVisible(not is_github)
        self.location_edit.setVisible(not is_github)
        self.browse_button.setVisible(is_local)
        self.nas_ip_label.setVisible(is_nas)
        self.nas_ip_edit.setVisible(is_nas)
        self.nas_port_label.setVisible(is_nas)
        self.nas_port_edit.setVisible(is_nas)
        if is_nas:
            self.location_label.setVisible(False)
            self.location_edit.setVisible(False)
        self.account_label.setVisible(not is_local)
        self.account_edit.setVisible(not is_local)
        self.secret_label.setVisible(not is_local)
        self.secret_edit.setVisible(not is_local)
        if not self.name_edit.text().strip():
            defaults = {"local": "Local Git", "nas": "NAS Gitea", "github": "GitHub"}
            self.name_edit.setText(defaults.get(kind, self.type_combo.currentText().split("·", 1)[0].strip()))

    def _load_remote(self, remote: RemoteConfig) -> None:
        index = self.type_combo.findData(remote.kind)
        if index < 0:
            index = self.type_combo.findData("custom")
        self.type_combo.setCurrentIndex(index)
        self.name_edit.setText(remote.label)
        if remote.kind == "local":
            self.location_edit.setText(remote.path)
        elif remote.kind == "nas":
            self._load_nas_service(remote.service_url or remote.url)
        elif remote.kind == "github":
            self.location_edit.clear()
        else:
            self.location_edit.setText(remote.service_url or self._service_from_url(remote.url))
        self.account_edit.setText(remote.username)
        self.secret_edit.setText(remote.secret)

    def _load_nas_service(self, value: str) -> None:
        parsed = urlparse(value if "://" in value else f"http://{value}")
        self.nas_ip_edit.setText(parsed.hostname or "")
        self.nas_port_edit.setText(str(parsed.port or 3003))

    def _service_from_url(self, url: str) -> str:
        if not url:
            return ""
        if "://" in url:
            prefix, remainder = url.split("://", 1)
            host = remainder.split("/", 1)[0]
            return f"{prefix}://{host}"
        return url.split("/", 1)[0]

    def _accept(self) -> None:
        kind = str(self.type_combo.currentData())
        location = self.location_edit.text().strip()
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, "信息不完整", "请填写 Git 卡片名称。")
            return
        if kind not in {"local", "github"} and not location:
            if kind != "nas":
                QMessageBox.warning(self, "信息不完整", "请填写 Git 服务地址。")
                return
        if kind == "nas":
            if not self.nas_ip_edit.text().strip():
                QMessageBox.warning(self, "信息不完整", "请填写 NAS 地址。")
                return
            try:
                raw_address = self.nas_ip_edit.text().strip()
                parsed = urlparse(raw_address if "://" in raw_address else f"http://{raw_address}")
                if not parsed.hostname:
                    raise ValueError
                port_text = self.nas_port_edit.text().strip()
                port = int(port_text or parsed.port or 3003)
                if not 1 <= port <= 65535:
                    raise ValueError
            except ValueError:
                QMessageBox.warning(self, "地址或端口不正确", "请输入有效的 NAS 地址和端口。")
                return
        if kind not in {"local"} and not self.account_edit.text().strip():
            QMessageBox.warning(self, "信息不完整", "请填写账号或用户名。")
            return
        if kind not in {"local"} and not self.secret_edit.text():
            QMessageBox.warning(self, "信息不完整", "请填写密码或 Token。")
            return
        if kind == "local":
            if not Path(location).is_dir():
                QMessageBox.warning(self, "目录不存在", "请选择已经存在的 Local Git 项目集合目录。")
                return
            try:
                self.discovered_repositories = GitService().discover_repositories(location)
            except GitCommandError:
                # A new installation may intentionally point at an empty
                # collection directory before the first project is created.
                self.discovered_repositories = []
        self.accept()

    def get_discovered_repositories(self) -> list[dict]:
        return list(self.discovered_repositories)

    def build_remote(self) -> RemoteConfig:
        kind = str(self.type_combo.currentData())
        location = self.location_edit.text().strip()
        is_service = kind in {"github", "nas", "gitea"}
        nas_port_text = self.nas_port_edit.text().strip()
        nas_port = int(nas_port_text) if nas_port_text else None
        return RemoteConfig(
            kind=kind,
            path=location if kind == "local" else "",
            url=location if kind not in {"local", "github", "nas", "gitea"} else "",
            service_url=(
                "https://github.com"
                if kind == "github"
                else self._nas_service_url(nas_port)
                if kind == "nas"
                else location
                if is_service
                else ""
            ),
            auth_method="none" if kind == "local" else "https",
            username=self.account_edit.text().strip(),
            secret=self.secret_edit.text(),
            ssh_key_path="",
            enabled=True,
            label=self.name_edit.text().strip(),
        )

    def _nas_service_url(self, port: int | None = None) -> str:
        """Normalize a NAS host, domain, or URL into the Gitea service root."""
        raw_address = self.nas_ip_edit.text().strip().rstrip("/")
        if not raw_address:
            return ""
        parsed = urlparse(raw_address if "://" in raw_address else f"http://{raw_address}")
        host = parsed.hostname or ""
        if not host:
            return ""
        effective_port = port or parsed.port or 3003
        scheme = parsed.scheme if parsed.scheme in {"http", "https"} else "http"
        display_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
        return f"{scheme}://{display_host}:{effective_port}"
