"""暂存区文件管理对话框：文件树 + iOS 风格胶囊开关。

双击暂存区卡片弹出，显示项目所有文件/文件夹，开关控制是否纳入 git 跟踪：
- 打开（青色）= 纳入 git
- 关闭（深灰）= 写入 .gitignore，已跟踪文件额外 git rm --cached
- 禁用（半透明）= .git 目录、超过 100MB 的文件
- 文件夹支持三态：全开 / 全关 / 部分（滑块居中、琥珀色）
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt6.QtCore import (
    QEasingCurve,
    QPropertyAnimation,
    QSize,
    Qt,
    QThread,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import QColor, QCursor, QFont, QIcon, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.core.file_tree import FileNode, FileTreeService
from app.ui.theme import (
    ACCENT_COLOR,
    ACCENT_HOVER,
    BORDER_COLOR,
    CANVAS_COLOR,
    PANEL_COLOR,
    PANEL_RAISED,
    SPACE_1,
    SPACE_2,
    SPACE_3,
    SPACE_4,
)

# 开关颜色
TRACK_ON = QColor(ACCENT_COLOR)
TRACK_OFF = QColor("#2A3F4D")
TRACK_PARTIAL = QColor("#F5A623")
THUMB_COLOR = QColor("#FFFFFF")
DISABLED_ALPHA = 0.35

# 推荐标签颜色
COLOR_RECOMMEND = "#70D6A5"
COLOR_AVOID = "#F27788"
COLOR_NEUTRAL = "#8A97AA"
COLOR_DISABLED = "#6B7B8D"


class ToggleSwitch(QWidget):
    """iOS 风格胶囊开关，支持开/关/部分三态和禁用。"""

    toggled = pyqtSignal(bool)

    def __init__(self, checked: bool = False, parent=None):
        super().__init__(parent)
        self.setFixedSize(44, 24)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._checked = checked
        self._partial = False
        self._disabled = False
        self._thumb_x = 2.0 if not checked else 22.0
        self._track_color = QColor(TRACK_OFF if not checked else TRACK_ON)
        self._animation = QPropertyAnimation(self, b"thumb_pos", self)
        self._animation.setDuration(160)
        self._animation.setEasingCurve(QEasingCurve.Type.InOutCubic)

    # 自定义属性，动画驱动滑块位置
    def get_thumb_pos(self) -> float:
        return self._thumb_x

    def set_thumb_pos(self, value: float) -> None:
        self._thumb_x = value
        self.update()

    thumb_pos = property(get_thumb_pos, set_thumb_pos)

    def isChecked(self) -> bool:
        return self._checked

    def isPartial(self) -> bool:
        return self._partial

    def setChecked(self, checked: bool) -> None:
        self._checked = checked
        self._partial = False
        self._animate_to(22.0 if checked else 2.0)
        self._track_color = QColor(TRACK_ON if checked else TRACK_OFF)
        self.update()

    def setPartial(self) -> None:
        """文件夹部分子项打开时的半开状态。"""
        self._partial = True
        self._checked = False
        self._animate_to(12.0)
        self._track_color = QColor(TRACK_PARTIAL)
        self.update()

    def setDisabledState(self, disabled: bool) -> None:
        self._disabled = disabled
        self.setEnabled(not disabled)
        self.setCursor(
            QCursor(Qt.CursorShape.ForbiddenCursor if disabled else Qt.CursorShape.PointingHandCursor)
        )
        self.update()

    def isDisabledState(self) -> bool:
        return self._disabled

    def _animate_to(self, target_x: float) -> None:
        self._animation.stop()
        self._animation.setStartValue(self._thumb_x)
        self._animation.setEndValue(target_x)
        self._animation.start()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if self._disabled or event.button() != Qt.MouseButton.LeftButton:
            return
        self.setChecked(not self._checked)
        self.toggled.emit(self._checked)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        alpha = DISABLED_ALPHA if self._disabled else 1.0
        track = QColor(self._track_color)
        track.setAlphaF(track.alphaF() * alpha)

        # 胶囊轨道
        radius = self.height() / 2
        path = QPainterPath()
        path.addRoundedRect(0, 0, self.width(), self.height(), radius, radius)
        painter.fillPath(path, track)

        # 滑块
        thumb_size = self.height() - 4
        thumb = QColor(THUMB_COLOR)
        thumb.setAlphaF(alpha)
        painter.setBrush(thumb)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(int(self._thumb_x), 2, int(thumb_size), int(thumb_size))
        painter.end()


class FileTreeLoadThread(QThread):
    """后台线程构建文件树，避免大项目卡 UI。"""

    loaded = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, service: FileTreeService):
        super().__init__()
        self._service = service

    def run(self) -> None:
        try:
            tree = self._service.build_tree()
            self.loaded.emit(tree)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class FileTreeDialog(QDialog):
    """暂存区文件管理对话框。"""

    COLS_NAME = 0
    COLS_SIZE = 1
    COLS_HINT = 2
    COLS_TOGGLE = 3

    def __init__(self, project_path: str | Path, project_name: str = "", parent=None):
        super().__init__(parent)
        self._service = FileTreeService(project_path)
        self._project_name = project_name
        self._root_node: Optional[FileNode] = None
        self._item_map: dict[QTreeWidgetItem, FileNode] = {}
        self._switch_map: dict[QTreeWidgetItem, ToggleSwitch] = {}
        self._initial_state: dict[str, bool] = {}  # rel_path -> checked
        self._loading = True
        self._build_ui()
        self._start_loading()

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self.setWindowTitle("暂存区文件管理")
        self.setMinimumSize(680, 560)
        self.setStyleSheet(f"background-color: {CANVAS_COLOR}; color: #E8F0F5;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE_4, SPACE_4, SPACE_4, SPACE_4)
        layout.setSpacing(SPACE_2)

        # 顶部：标题 + 搜索 + 推荐按钮
        top = QHBoxLayout()
        top.setSpacing(SPACE_2)
        title = QLabel(f"文件跟踪管理 — {self._project_name or self._service.root.name}")
        title.setStyleSheet("font-size: 14px; font-weight: 600; color: #E8F0F5;")
        top.addWidget(title)
        top.addStretch()

        self._search = QLineEdit()
        self._search.setPlaceholderText("搜索文件名…")
        self._search.setFixedWidth(180)
        self._search.setStyleSheet(self._input_style())
        self._search.textChanged.connect(self._apply_filter)
        top.addWidget(self._search)

        btn_recommend = QPushButton("按推荐设置")
        btn_recommend.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn_recommend.setStyleSheet(self._button_style(ACCENT_COLOR))
        btn_recommend.clicked.connect(self._apply_recommendations)
        top.addWidget(btn_recommend)
        layout.addLayout(top)

        # 说明
        hint = QLabel(
            "开关打开 = 纳入 git 同步；关闭 = 写入 .gitignore（文件保留在磁盘）。"
            "灰色开关不可操作。"
        )
        hint.setStyleSheet("color: #8A97AA; font-size: 11px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # 文件树
        self._tree = QTreeWidget()
        self._tree.setColumnCount(4)
        self._tree.setHeaderLabels(["名称", "大小", "建议", ""])
        self._tree.setRootIsDecorated(True)
        self._tree.setUniformRowHeights(True)
        self._tree.setIconSize(QSize(18, 18))
        self._tree.setStyleSheet(self._tree_style())
        header = self._tree.header()
        header.setSectionResizeMode(self.COLS_NAME, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(self.COLS_SIZE, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(self.COLS_HINT, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(self.COLS_TOGGLE, QHeaderView.ResizeMode.ResizeToContents)
        self._tree.setIndentation(20)
        layout.addWidget(self._tree, 1)

        # 加载提示
        self._loading_label = QLabel("正在扫描项目文件…")
        self._loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._loading_label.setStyleSheet("color: #8A97AA; font-size: 13px; padding: 40px;")
        layout.addWidget(self._loading_label)
        self._tree.hide()

        # 底部统计 + 按钮
        bottom = QHBoxLayout()
        self._stats = QLabel("")
        self._stats.setStyleSheet("color: #8A97AA; font-size: 11px;")
        bottom.addWidget(self._stats)
        bottom.addStretch()

        btn_cancel = QPushButton("取消")
        btn_cancel.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn_cancel.setStyleSheet(self._button_style(BORDER_COLOR))
        btn_cancel.clicked.connect(self.reject)
        bottom.addWidget(btn_cancel)

        self._btn_save = QPushButton("保存并应用")
        self._btn_save.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._btn_save.setStyleSheet(self._button_style(ACCENT_COLOR))
        self._btn_save.clicked.connect(self._on_save)
        self._btn_save.setEnabled(False)
        bottom.addWidget(self._btn_save)
        layout.addLayout(bottom)

    @staticmethod
    def _input_style() -> str:
        return (
            f"QLineEdit {{ background: {PANEL_COLOR}; border: 1px solid {BORDER_COLOR};"
            f" border-radius: 6px; padding: 6px 10px; color: #E8F0F5; font-size: 12px; }}"
            f"QLineEdit:focus {{ border-color: {ACCENT_COLOR}; }}"
        )

    @staticmethod
    def _button_style(color: str) -> str:
        is_accent = color == ACCENT_COLOR
        text_color = "#071D2C" if is_accent else "#E8F0F5"
        return (
            f"QPushButton {{ background: {color}; border: none; border-radius: 6px;"
            f" padding: 7px 18px; color: {text_color}; font-size: 12px; font-weight: 600; }}"
            f"QPushButton:hover {{ background: {ACCENT_HOVER if is_accent else PANEL_RAISED}; }}"
            f"QPushButton:disabled {{ background: #2A3F4D; color: #6B7B8D; }}"
        )

    @staticmethod
    def _tree_style() -> str:
        return (
            f"QTreeWidget {{ background: {PANEL_COLOR}; border: 1px solid {BORDER_COLOR};"
            f" border-radius: 8px; font-size: 12px; }}"
            f"QTreeWidget::item {{ padding: 3px 0; border-bottom: 1px solid rgba(30,75,92,0.3); }}"
            f"QTreeWidget::item:selected {{ background: {PANEL_RAISED}; }}"
            f"QTreeWidget::branch {{ background: {PANEL_COLOR}; }}"
            f"QHeaderView::section {{ background: {PANEL_RAISED}; color: #8A97AA;"
            f" border: none; border-bottom: 1px solid {BORDER_COLOR}; padding: 6px 8px;"
            f" font-size: 11px; font-weight: 600; }}"
        )

    # ------------------------------------------------------------------
    # 后台加载
    # ------------------------------------------------------------------

    def _start_loading(self) -> None:
        self._load_thread = FileTreeLoadThread(self._service)
        self._load_thread.loaded.connect(self._on_tree_loaded)
        self._load_thread.failed.connect(self._on_load_failed)
        self._load_thread.start()

    def _on_tree_loaded(self, root: FileNode) -> None:
        self._root_node = root
        self._loading_label.hide()
        self._tree.show()
        self._populate_tree(root)
        self._loading = False
        self._btn_save.setEnabled(True)
        self._update_stats()
        # 默认展开第一层
        self._tree.expandToDepth(0)

    def _on_load_failed(self, error: str) -> None:
        self._loading_label.setText(f"扫描失败：{error}")
        self._loading_label.setStyleSheet("color: #F27788; font-size: 13px; padding: 40px;")

    # ------------------------------------------------------------------
    # 填充树
    # ------------------------------------------------------------------

    def _populate_tree(self, root: FileNode) -> None:
        self._tree.clear()
        self._item_map.clear()
        self._switch_map.clear()
        self._initial_state.clear()
        for child in root.children:
            self._add_node(child, None)

    def _add_node(self, node: FileNode, parent_item: Optional[QTreeWidgetItem]) -> None:
        item = QTreeWidgetItem(parent_item)
        if parent_item is None:
            self._tree.addTopLevelItem(item)

        # 名称
        icon = self._icon_for(node)
        item.setIcon(self.COLS_NAME, icon)
        item.setText(self.COLS_NAME, "  " + node.name)
        item.setToolTip(self.COLS_NAME, node.rel_path)
        # 大小
        item.setText(self.COLS_SIZE, node.display_size)
        item.setTextAlignment(self.COLS_SIZE, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        # 建议标签
        hint_text, hint_color = self._hint_for(node)
        item.setText(self.COLS_HINT, hint_text)
        item.setForeground(self.COLS_HINT, QColor(hint_color))
        item.setTextAlignment(self.COLS_HINT, Qt.AlignmentFlag.AlignCenter)

        # 开关
        toggle = ToggleSwitch(checked=node.checked)
        if node.disabled:
            toggle.setDisabledState(True)
            item.setForeground(self.COLS_NAME, QColor(COLOR_DISABLED))
            item.setToolTip(self.COLS_NAME, f"{node.rel_path}\n{node.disabled_reason}")
        elif node.collapsed_large:
            item.setToolTip(self.COLS_NAME, f"{node.rel_path}\n大目录，不展开显示子文件")
        toggle.toggled.connect(lambda checked, it=item: self._on_toggle(it, checked))
        self._tree.setItemWidget(item, self.COLS_TOGGLE, toggle)

        self._item_map[item] = node
        self._switch_map[item] = toggle
        self._initial_state[node.rel_path] = node.checked

        # 递归子节点
        for child in node.children:
            self._add_node(child, item)

        # 文件夹初始三态
        if node.is_dir and not node.disabled:
            self._refresh_parent_state(item)

    @staticmethod
    def _icon_for(node: FileNode) -> QIcon:
        # 用简单字符图标，避免依赖外部资源
        return QIcon()

    @staticmethod
    def _hint_for(node: FileNode) -> tuple[str, str]:
        if node.disabled:
            return node.disabled_reason or "不可操作", COLOR_DISABLED
        if node.collapsed_large:
            return "大目录", COLOR_AVOID
        if node.recommendation == "recommend":
            return "推荐", COLOR_RECOMMEND
        if node.recommendation == "avoid":
            return "不推荐", COLOR_AVOID
        return "", COLOR_NEUTRAL

    # ------------------------------------------------------------------
    # 开关联动
    # ------------------------------------------------------------------

    def _on_toggle(self, item: QTreeWidgetItem, checked: bool) -> None:
        node = self._item_map[item]
        if node.disabled:
            return
        # 文件夹：递归设置所有子节点
        if node.is_dir:
            self._set_children(item, checked)
        # 更新所有祖先的三态
        parent = item.parent()
        while parent is not None:
            self._refresh_parent_state(parent)
            parent = parent.parent()
        self._update_stats()

    def _set_children(self, parent_item: QTreeWidgetItem, checked: bool) -> None:
        for i in range(parent_item.childCount()):
            child = parent_item.child(i)
            node = self._item_map[child]
            toggle = self._switch_map[child]
            if node.disabled:
                continue
            if toggle.isPartial():
                toggle.setChecked(checked)
            elif toggle.isChecked() != checked:
                toggle.setChecked(checked)
            if node.is_dir:
                self._set_children(child, checked)

    def _refresh_parent_state(self, item: QTreeWidgetItem) -> None:
        """根据子节点状态刷新文件夹开关（全开/全关/部分）。"""
        node = self._item_map[item]
        if not node.is_dir or node.disabled:
            return
        toggle = self._switch_map[item]
        total = 0
        checked_count = 0
        for i in range(item.childCount()):
            child = item.child(i)
            child_node = self._item_map[child]
            if child_node.disabled:
                continue
            child_toggle = self._switch_map[child]
            total += 1
            if child_toggle.isChecked() or child_toggle.isPartial():
                checked_count += 1
        if total == 0:
            return
        if checked_count == total:
            if not toggle.isChecked() or toggle.isPartial():
                toggle.setChecked(True)
        elif checked_count == 0:
            if toggle.isChecked() or toggle.isPartial():
                toggle.setChecked(False)
        else:
            toggle.setPartial()

    # ------------------------------------------------------------------
    # 搜索过滤
    # ------------------------------------------------------------------

    def _apply_filter(self, text: str) -> None:
        keyword = text.strip().lower()
        if not keyword:
            # 全部显示，恢复展开
            self._filter_walk(None, True)
            self._tree.expandToDepth(0)
            return
        self._filter_walk(None, False, keyword)

    def _filter_walk(self, parent_item: Optional[QTreeWidgetItem], visible: bool, keyword: str = "") -> None:
        count = self._tree.topLevelItemCount() if parent_item is None else parent_item.childCount()
        for i in range(count):
            item = self._tree.topLevelItem(i) if parent_item is None else parent_item.child(i)
            node = self._item_map[item]
            if keyword:
                self_match = keyword in node.name.lower()
                # 先递归子节点，看有没有匹配的
                child_match = self._filter_walk(item, False, keyword)
                show = self_match or child_match
                item.setHidden(not show)
                if show:
                    item.setExpanded(True)
                return self_match or child_match
            else:
                item.setHidden(not visible)
                self._filter_walk(item, visible)
        return False

    # ------------------------------------------------------------------
    # 按推荐设置
    # ------------------------------------------------------------------

    def _apply_recommendations(self) -> None:
        """不推荐的关闭，推荐的打开，中性保持不变。"""
        for i in range(self._tree.topLevelItemCount()):
            self._apply_recommend_recursive(self._tree.topLevelItem(i))
        self._update_stats()

    def _apply_recommend_recursive(self, item: QTreeWidgetItem) -> None:
        node = self._item_map[item]
        toggle = self._switch_map[item]
        if not node.disabled:
            if node.recommendation == "avoid":
                if toggle.isChecked() or toggle.isPartial():
                    toggle.setChecked(False)
            elif node.recommendation == "recommend":
                if not toggle.isChecked():
                    toggle.setChecked(True)
        for i in range(item.childCount()):
            self._apply_recommend_recursive(item.child(i))
        if node.is_dir and not node.disabled:
            self._refresh_parent_state(item)

    # ------------------------------------------------------------------
    # 统计
    # ------------------------------------------------------------------

    def _collect_current_state(self) -> dict[str, bool]:
        """收集当前所有叶子文件的开关状态。"""
        result: dict[str, bool] = {}

        def walk(item: QTreeWidgetItem) -> None:
            node = self._item_map[item]
            toggle = self._switch_map[item]
            if not node.disabled:
                result[node.rel_path] = toggle.isChecked()
            for i in range(item.childCount()):
                walk(item.child(i))

        for i in range(self._tree.topLevelItemCount()):
            walk(self._tree.topLevelItem(i))
        return result

    def _collect_changes(self) -> list[tuple[str, bool]]:
        """对比初始状态，返回变更列表。"""
        current = self._collect_current_state()
        changes: list[tuple[str, bool]] = []
        for rel_path, was_checked in self._initial_state.items():
            now_checked = current.get(rel_path, was_checked)
            if now_checked != was_checked:
                changes.append((rel_path, now_checked))
        return changes

    def _update_stats(self) -> None:
        if self._loading:
            return
        changes = self._collect_changes()
        if changes:
            off_count = sum(1 for _, c in changes if not c)
            on_count = sum(1 for _, c in changes if c)
            parts = [f"待保存变更：{len(changes)} 项"]
            if off_count:
                parts.append(f"关闭 {off_count}")
            if on_count:
                parts.append(f"打开 {on_count}")
            self._stats.setText("（".join(parts) + "）" if len(parts) == 1 else "，".join(parts))
            self._btn_save.setEnabled(True)
        else:
            total = len(self._initial_state)
            self._stats.setText(f"共 {total} 项，无变更")
            self._btn_save.setEnabled(False)

    # ------------------------------------------------------------------
    # 保存
    # ------------------------------------------------------------------

    def _on_save(self) -> None:
        changes = self._collect_changes()
        if not changes:
            self.reject()
            return

        added, removed = self._service.preview_changes(changes)
        untrack_files = [
            rel for rel, track in changes
            if not track and (rel in self._service._tracked_files
                              or self._service._dir_has_tracked(rel))
        ]

        # 构建确认文本
        lines = ["以下变更将写入 .gitignore：", ""]
        if added:
            lines.append(f"将忽略（{len(added)} 项）：")
            for rule in added[:15]:
                lines.append(f"  {rule}")
            if len(added) > 15:
                lines.append(f"  …等 {len(added)} 项")
        if removed:
            lines.append(f"将恢复跟踪（{len(removed)} 项）：")
            for rule in removed[:15]:
                lines.append(f"  {rule}")
            if len(removed) > 15:
                lines.append(f"  …等 {len(removed)} 项")
        if untrack_files:
            lines.append("")
            lines.append(f"注意：{len(untrack_files)} 个已跟踪项将执行 git rm --cached（本地文件保留）。")

        box = QMessageBox(self)
        box.setWindowTitle("确认变更")
        box.setText("\n".join(lines))
        box.setIcon(QMessageBox.Icon.Question)
        btn_ok = box.addButton("保存并应用", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.setStyleSheet(f"QMessageBox {{ background: {PANEL_COLOR}; color: #E8F0F5; }}"
                          f"QPushButton {{ padding: 6px 16px; border-radius: 4px; }}")
        box.exec()
        if box.clickedButton() is not btn_ok:
            return

        result = self._service.apply_changes(changes)
        if result["errors"]:
            QMessageBox.warning(self, "部分操作失败", "\n".join(result["errors"]))
        self.accept()
