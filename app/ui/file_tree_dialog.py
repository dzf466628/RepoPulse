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
    pyqtProperty,
    pyqtSignal,
)
from PyQt6.QtGui import QColor, QCursor, QPainter, QPainterPath, QPixmap, QBrush, QPen
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QTreeWidgetItemIterator,
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
    SPACE_2,
    SPACE_4,
)

TRACK_ON = QColor(ACCENT_COLOR)
TRACK_OFF = QColor("#2A3F4D")
TRACK_PARTIAL = QColor("#F5A623")
THUMB_COLOR = QColor("#FFFFFF")
DISABLED_ALPHA = 0.35

COLOR_RECOMMEND = "#70D6A5"
COLOR_AVOID = "#F27788"
COLOR_NEUTRAL = "#8A97AA"
COLOR_DISABLED = "#6B7B8D"

ROLE_PATH = Qt.ItemDataRole.UserRole


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
        self._thumb_x = 22.0 if checked else 2.0
        self._track_color = QColor(TRACK_ON if checked else TRACK_OFF)
        self._animation = QPropertyAnimation(self, b"thumb_pos", self)
        self._animation.setDuration(160)
        self._animation.setEasingCurve(QEasingCurve.Type.InOutCubic)

    def get_thumb_pos(self) -> float:
        return self._thumb_x

    def set_thumb_pos(self, value: float) -> None:
        self._thumb_x = value
        self.update()

    thumb_pos = pyqtProperty(float, get_thumb_pos, set_thumb_pos)

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
        w, h = self.width(), self.height()
        radius = h / 2
        # 轨道背景
        track = QColor(self._track_color)
        track.setAlphaF(track.alphaF() * alpha)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track)
        painter.drawRoundedRect(0, 0, w, h, int(radius), int(radius))
        # 关闭状态画边框，让轨道左右边界清晰可见
        if not self._checked:
            border = QColor("#4A6578")
            border.setAlphaF(alpha)
            pen = QPen(border, 1)
            painter.setPen(pen)
            painter.setBrush(Qt.GlobalColor.transparent)
            painter.drawRoundedRect(0, 0, w - 1, h - 1, int(radius), int(radius))
        # 滑块
        thumb_size = h - 4
        thumb = QColor(THUMB_COLOR)
        thumb.setAlphaF(alpha)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(thumb)
        painter.drawEllipse(int(self._thumb_x), 2, int(thumb_size), int(thumb_size))
        painter.end()


class FileTreeLoadThread(QThread):
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


def _make_file_icon(name: str, is_dir: bool) -> QIcon:
    """按文件类型绘制 22x22 图标：文件夹青色，文件按扩展名配色。"""
    from PyQt6.QtGui import QIcon
    pm = QPixmap(22, 22)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    if is_dir:
        # 文件夹：青色
        p.setBrush(QBrush(QColor(ACCENT_COLOR)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(2, 6, 18, 13, 2, 2)
        p.drawRoundedRect(2, 3, 9, 5, 1, 1)
        p.setBrush(QBrush(QColor(0, 0, 0, 40)))
        p.drawRoundedRect(2, 16, 18, 3, 1, 1)
    else:
        # 文件：白色文档 + 底部颜色条
        ext = Path(name.lower()).suffix
        color_map = {
            ".py": "#5B9BD5", ".js": "#F7DF1E", ".ts": "#3178C6",
            ".html": "#E44D26", ".css": "#264DE4", ".scss": "#CD6799",
            ".json": "#70D6A5", ".yaml": "#70D6A5", ".yml": "#70D6A5",
            ".toml": "#70D6A5", ".ini": "#70D6A5", ".cfg": "#70D6A5",
            ".md": "#F5C26B", ".txt": "#F5C26B", ".rst": "#F5C26B",
            ".png": "#B48EAD", ".jpg": "#B48EAD", ".jpeg": "#B48EAD",
            ".gif": "#B48EAD", ".svg": "#B48EAD", ".ico": "#B48EAD",
            ".exe": "#F27788", ".dll": "#F27788", ".pyd": "#F27788",
            ".zip": "#F5A623", ".tar": "#F5A623", ".gz": "#F5A623",
            ".log": "#8A97AA", ".tmp": "#8A97AA", ".bak": "#8A97AA",
            ".bat": "#4EC9B0", ".ps1": "#4EC9B0", ".sh": "#4EC9B0",
            ".spec": "#CE9178", ".iss": "#CE9178",
        }
        bar_color = QColor(color_map.get(ext, "#8A97AA"))
        # 文档主体
        p.setBrush(QBrush(QColor("#E8F0F5")))
        p.setPen(QPen(QColor("#5A7A8A"), 1))
        p.drawRoundedRect(4, 1, 14, 19, 2, 2)
        # 底部颜色条
        p.setBrush(QBrush(bar_color))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(4, 16, 14, 4, 1, 1)
    p.end()
    return QIcon(pm)


def _make_star_icon() -> QIcon:
    """画一个青色星星/魔法棒符号，用于'按推荐设置'按钮。"""
    from PyQt6.QtGui import QIcon, QPolygonF
    from PyQt6.QtCore import QPointF
    import math
    pm = QPixmap(20, 20)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QBrush(QColor("#071D2C")))
    p.setPen(Qt.PenStyle.NoPen)
    # 五角星
    cx, cy, r_outer, r_inner = 10, 10.5, 8, 3.5
    poly = QPolygonF()
    for i in range(10):
        angle = math.pi / 2 + i * math.pi / 5
        r = r_outer if i % 2 == 0 else r_inner
        poly.append(QPointF(cx + r * math.cos(angle), cy - r * math.sin(angle)))
    p.drawPolygon(poly)
    p.end()
    return QIcon(pm)


def _make_search_icon() -> QIcon:
    """放大镜符号，用于搜索框。"""
    from PyQt6.QtGui import QIcon
    pm = QPixmap(20, 20)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor("#C8D8E8"), 2)
    p.setPen(pen)
    p.setBrush(Qt.GlobalColor.transparent)
    p.drawEllipse(3, 3, 10, 10)
    p.drawLine(12, 12, 17, 17)
    p.end()
    return QIcon(pm)


class FileTreeDialog(QDialog):
    COLS_NAME = 0
    COLS_SIZE = 1
    COLS_HINT = 2
    COLS_TOGGLE = 3

    def __init__(self, project_path, project_name: str = "", parent=None):
        super().__init__(parent)
        self._service = FileTreeService(project_path)
        self._project_name = project_name
        self._root_node: Optional[FileNode] = None
        self._node_map: dict[str, FileNode] = {}
        self._switch_map: dict[str, ToggleSwitch] = {}
        self._initial_state: dict[str, bool] = {}
        self._loading = True
        self._load_thread = None
        self._build_ui()
        self._start_loading()

    def _build_ui(self) -> None:
        self.setWindowTitle("暂存区文件管理")
        self.setMinimumSize(500, 480)
        self.resize(500, 600)
        self.setStyleSheet(f"background-color: {CANVAS_COLOR}; color: #E8F0F5;")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE_4, SPACE_4, SPACE_4, SPACE_4)
        layout.setSpacing(SPACE_2)

        top = QHBoxLayout()
        top.setSpacing(SPACE_2)
        title = QLabel(f"文件跟踪管理 — {self._project_name or self._service.root.name}")
        title.setStyleSheet("font-size: 15px; font-weight: 700; color: #F4F7FB;")
        title.setMinimumWidth(0)
        title.setMaximumWidth(240)
        top.addWidget(title)
        top.addStretch()

        self._search = QLineEdit()
        self._search.setPlaceholderText("搜索文件名…")
        self._search.setMinimumWidth(140)
        self._search.setMaximumWidth(220)
        self._search.setStyleSheet(self._input_style())
        self._search.setClearButtonEnabled(True)
        search_action = self._search.addAction(_make_search_icon(), QLineEdit.ActionPosition.LeadingPosition)
        self._search.textChanged.connect(self._apply_filter)
        top.addWidget(self._search)
        layout.addLayout(top)

        hint = QLabel("开关打开 = 纳入 git 同步；关闭 = 写入 .gitignore（文件保留在磁盘）。灰色开关不可操作。")
        hint.setStyleSheet("color: #8A97AA; font-size: 11px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._tree = QTreeWidget()
        self._tree.setColumnCount(4)
        self._tree.setHeaderLabels(["名称", "大小", "建议", ""])
        self._tree.setRootIsDecorated(True)
        self._tree.setUniformRowHeights(True)
        self._tree.setIconSize(QSize(22, 22))
        self._tree.setStyleSheet(self._tree_style())
        header = self._tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(self.COLS_NAME, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(self.COLS_SIZE, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(self.COLS_HINT, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(self.COLS_TOGGLE, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(self.COLS_HINT, 92)
        header.resizeSection(self.COLS_TOGGLE, 56)
        self._tree.setIndentation(18)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._show_tree_menu)
        layout.addWidget(self._tree, 1)

        self._loading_label = QLabel("正在扫描项目文件…")
        self._loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._loading_label.setStyleSheet("color: #8A97AA; font-size: 13px; padding: 40px;")
        layout.addWidget(self._loading_label)
        self._tree.hide()

        # 图标颜色图例
        legend = QLabel(
            '<span style="color:#8A97AA;font-size:10px;">图标颜色：'
            '<span style="color:#5B9BD5;">■</span>代码 '
            '<span style="color:#70D6A5;">■</span>配置 '
            '<span style="color:#F5C26B;">■</span>文档 '
            '<span style="color:#B48EAD;">■</span>图片 '
            '<span style="color:#F27788;">■</span>可执行 '
            '<span style="color:#F5A623;">■</span>压缩 '
            '<span style="color:#8A97AA;">■</span>其他 '
            '<span style="color:#16E5EE;">■</span>文件夹</span>'
        )
        legend.setStyleSheet("color: #8A97AA; font-size: 10px;")
        layout.addWidget(legend)

        bottom = QHBoxLayout()
        self._stats = QLabel("")
        self._stats.setStyleSheet("color: #8A97AA; font-size: 11px;")
        bottom.addWidget(self._stats)
        bottom.addStretch()
        btn_rec = QPushButton("按推荐设置")
        btn_rec.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn_rec.setStyleSheet(self._button_style(PANEL_RAISED))
        btn_rec.setToolTip("推荐的打开，不推荐的关闭")
        btn_rec.clicked.connect(self._apply_recommendations)
        bottom.addWidget(btn_rec)
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
            f" padding: 6px 14px; color: {text_color}; font-size: 12px; font-weight: 600; }}"
            f"QPushButton:hover {{ background: {ACCENT_HOVER if is_accent else PANEL_RAISED}; }}"
            f"QPushButton:disabled {{ background: #2A3F4D; color: #6B7B8D; }}"
        )

    @staticmethod
    def _tree_style() -> str:
        return (
            f"QTreeWidget {{ background: {PANEL_COLOR}; border: 1px solid {BORDER_COLOR};"
            f" border-radius: 8px; font-size: 13px; }}"
            f"QTreeWidget::item {{ padding: 2px 0; min-height: 28px; }}"
            f"QTreeWidget::item:selected {{ background: {PANEL_RAISED}; }}"
            f"QTreeWidget::branch {{ background: {PANEL_COLOR}; }}"
            f"QHeaderView::section {{ background: {PANEL_RAISED}; color: #8A97AA;"
            f" border: none; border-bottom: 1px solid {BORDER_COLOR}; padding: 6px 8px;"
            f" font-size: 11px; font-weight: 600; }}"
        )

    def _start_loading(self) -> None:
        self._load_thread = FileTreeLoadThread(self._service)
        self._load_thread.loaded.connect(self._on_tree_loaded)
        self._load_thread.failed.connect(self._on_load_failed)
        self._load_thread.start()

    def _on_tree_loaded(self, root: FileNode) -> None:
        if not self.isVisible():
            return  # 用户已关闭对话框，忽略加载结果
        self._root_node = root
        self._loading_label.hide()
        self._tree.show()
        self._populate_tree(root)
        # 数据填充后强制重设固定列宽（填充过程可能重置列宽）
        self._tree.header().resizeSection(self.COLS_HINT, 92)
        self._tree.header().resizeSection(self.COLS_TOGGLE, 56)
        self._loading = False
        self._btn_save.setEnabled(True)
        self._update_stats()

    def _on_load_failed(self, error: str) -> None:
        self._loading_label.setText(f"扫描失败：{error}")
        self._loading_label.setStyleSheet("color: #F27788; font-size: 13px; padding: 40px;")

    def _populate_tree(self, root: FileNode) -> None:
        self._tree.clear()
        self._node_map.clear()
        self._switch_map.clear()
        self._initial_state.clear()
        for child in root.children:
            self._add_node(child, None)

    def _add_node(self, node: FileNode, parent_item) -> None:
        item = QTreeWidgetItem(parent_item)
        if parent_item is None:
            self._tree.addTopLevelItem(item)
        item.setData(self.COLS_NAME, ROLE_PATH, node.rel_path)

        item.setIcon(self.COLS_NAME, _make_file_icon(node.name, node.is_dir))
        item.setText(self.COLS_NAME, node.name)
        tip = node.rel_path
        if node.collapsed_large:
            tip += "\n大目录，不展开显示子文件"
        if node.disabled_reason:
            full_reason = node.disabled_reason
            if node.rule_ignored:
                full_reason = "由 .gitignore 通配符规则（如 *.log）忽略，开关无法单独控制"
            if node.disabled_reason == "空目录":
                full_reason = "空目录不被 git 跟踪，可放入 .gitkeep 文件使其被跟踪"
            tip += f"\n{full_reason}"
        item.setToolTip(self.COLS_NAME, tip)
        item.setText(self.COLS_SIZE, node.display_size)
        item.setTextAlignment(self.COLS_SIZE, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        hint_text, hint_color = self._hint_for(node)
        item.setText(self.COLS_HINT, hint_text)
        item.setForeground(self.COLS_HINT, QColor(hint_color))
        item.setTextAlignment(self.COLS_HINT, Qt.AlignmentFlag.AlignCenter)

        toggle = ToggleSwitch(checked=node.checked)
        if node.disabled:
            toggle.setChecked(False)
            toggle.setDisabledState(True)
            item.setForeground(self.COLS_NAME, QColor(COLOR_DISABLED))
        toggle.toggled.connect(lambda checked, p=node.rel_path: self._on_toggle(p, checked))
        # 右对齐容器
        toggle_container = QWidget()
        toggle_container.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        toggle_container.setAutoFillBackground(False)
        cl = QHBoxLayout(toggle_container)
        cl.setContentsMargins(0, 0, 6, 0)
        cl.addStretch()
        cl.addWidget(toggle)
        self._tree.setItemWidget(item, self.COLS_TOGGLE, toggle_container)

        self._node_map[node.rel_path] = node
        self._switch_map[node.rel_path] = toggle
        self._initial_state[node.rel_path] = node.checked

        for child in node.children:
            self._add_node(child, item)
        if node.is_dir and not node.disabled:
            self._refresh_parent_state(item)

    @staticmethod
    def _hint_for(node: FileNode):
        if node.disabled:
            return node.disabled_reason or "不可操作", COLOR_DISABLED
        if node.collapsed_large:
            return "大目录", COLOR_AVOID
        if node.recommendation == "recommend":
            return "推荐", COLOR_RECOMMEND
        if node.recommendation == "avoid":
            return "不推荐", COLOR_AVOID
        return "", COLOR_NEUTRAL

    @staticmethod
    def _item_path(item: QTreeWidgetItem) -> str:
        return item.data(FileTreeDialog.COLS_NAME, ROLE_PATH) or ""

    def _find_item(self, rel_path: str):
        it = QTreeWidgetItemIterator(self._tree)
        while it.value():
            if self._item_path(it.value()) == rel_path:
                return it.value()
            it += 1
        return None

    def _on_toggle(self, rel_path: str, checked: bool) -> None:
        node = self._node_map.get(rel_path)
        if not node or node.disabled:
            return
        item = self._find_item(rel_path)
        if item is None:
            return
        if node.is_dir:
            self._set_children(item, checked)
        parent = item.parent()
        while parent is not None:
            self._refresh_parent_state(parent)
            parent = parent.parent()
        self._update_stats()

    def _set_children(self, parent_item: QTreeWidgetItem, checked: bool) -> None:
        for i in range(parent_item.childCount()):
            child = parent_item.child(i)
            path = self._item_path(child)
            node = self._node_map.get(path)
            toggle = self._switch_map.get(path)
            if not node or not toggle or node.disabled:
                continue
            if toggle.isPartial() or toggle.isChecked() != checked:
                toggle.setChecked(checked)
            if node.is_dir:
                self._set_children(child, checked)

    def _refresh_parent_state(self, item: QTreeWidgetItem) -> None:
        path = self._item_path(item)
        node = self._node_map.get(path)
        toggle = self._switch_map.get(path)
        if not node or not toggle or not node.is_dir or node.disabled:
            return
        total = 0
        checked_count = 0
        for i in range(item.childCount()):
            child = item.child(i)
            cp = self._item_path(child)
            cn = self._node_map.get(cp)
            ct = self._switch_map.get(cp)
            if not cn or cn.disabled or not ct:
                continue
            total += 1
            if ct.isChecked() or ct.isPartial():
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

    def _apply_filter(self, text: str) -> None:
        keyword = text.strip().lower()
        if not keyword:
            for i in range(self._tree.topLevelItemCount()):
                top = self._tree.topLevelItem(i)
                top.setHidden(False)
                self._set_subtree_hidden(top, False)
            return
        for i in range(self._tree.topLevelItemCount()):
            self._filter_item(self._tree.topLevelItem(i), keyword)

    def _filter_item(self, item: QTreeWidgetItem, keyword: str) -> bool:
        path = self._item_path(item)
        node = self._node_map.get(path)
        self_match = node is not None and keyword in node.name.lower()
        child_match = False
        for i in range(item.childCount()):
            if self._filter_item(item.child(i), keyword):
                child_match = True
        show = self_match or child_match
        item.setHidden(not show)
        if show:
            item.setExpanded(True)
        return show

    def _show_tree_menu(self, pos) -> None:
        """右键文件夹：全部打开 / 全部关闭。"""
        item = self._tree.itemAt(pos)
        if item is None:
            return
        path = self._item_path(item)
        node = self._node_map.get(path)
        if not node or not node.is_dir or node.disabled:
            return
        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu {{ background: {PANEL_RAISED}; color: #E8F0F5; border: 1px solid {BORDER_COLOR}; }}"
            f"QMenu::item {{ padding: 6px 24px; }}"
            f"QMenu::item:selected {{ background: {ACCENT_COLOR}; color: #071D2C; }}"
        )
        act_on = menu.addAction("全部打开")
        act_off = menu.addAction("全部关闭")
        chosen = menu.exec(self._tree.viewport().mapToGlobal(pos))
        if chosen is act_on:
            self._set_children(item, True)
            t = self._switch_map.get(path)
            if t: t.setChecked(True)
            self._refresh_ancestors(item)
            self._update_stats()
        elif chosen is act_off:
            self._set_children(item, False)
            t = self._switch_map.get(path)
            if t: t.setChecked(False)
            self._refresh_ancestors(item)
            self._update_stats()

    def _refresh_ancestors(self, item: QTreeWidgetItem) -> None:
        parent = item.parent()
        while parent is not None:
            self._refresh_parent_state(parent)
            parent = parent.parent()

    def _set_subtree_hidden(self, item: QTreeWidgetItem, hidden: bool) -> None:
        item.setHidden(hidden)
        for i in range(item.childCount()):
            self._set_subtree_hidden(item.child(i), hidden)

    def _apply_recommendations(self) -> None:
        for i in range(self._tree.topLevelItemCount()):
            self._apply_recommend_recursive(self._tree.topLevelItem(i))
        for i in range(self._tree.topLevelItemCount()):
            self._refresh_dirs_bottom_up(self._tree.topLevelItem(i))
        self._update_stats()

    def _apply_recommend_recursive(self, item: QTreeWidgetItem) -> None:
        path = self._item_path(item)
        node = self._node_map.get(path)
        toggle = self._switch_map.get(path)
        if node and toggle and not node.disabled:
            if node.recommendation == "avoid":
                if toggle.isChecked() or toggle.isPartial():
                    toggle.setChecked(False)
            elif node.recommendation == "recommend":
                if not toggle.isChecked():
                    toggle.setChecked(True)
        for i in range(item.childCount()):
            self._apply_recommend_recursive(item.child(i))

    def _refresh_dirs_bottom_up(self, item: QTreeWidgetItem) -> None:
        for i in range(item.childCount()):
            self._refresh_dirs_bottom_up(item.child(i))
        path = self._item_path(item)
        node = self._node_map.get(path)
        if node and node.is_dir and not node.disabled:
            self._refresh_parent_state(item)

    def _collect_current_state(self) -> dict:
        result = {}
        it = QTreeWidgetItemIterator(self._tree)
        while it.value():
            item = it.value()
            path = self._item_path(item)
            node = self._node_map.get(path)
            toggle = self._switch_map.get(path)
            if node and toggle and not node.disabled:
                result[path] = toggle.isChecked()
            it += 1
        return result

    def _collect_changes(self):
        current = self._collect_current_state()
        raw = []
        for rel_path, was_checked in self._initial_state.items():
            now_checked = current.get(rel_path, was_checked)
            if now_checked != was_checked:
                raw.append((rel_path, now_checked))
        # 去冗余：若某文件夹同向变更，其下所有子项的同向变更被覆盖
        dir_changes = {p for p, on in raw if self._node_map.get(p) and self._node_map[p].is_dir}
        changes = []
        for rel_path, now_checked in raw:
            redundant = False
            for dir_path in dir_changes:
                if rel_path != dir_path and rel_path.startswith(dir_path + "/"):
                    # 祖先文件夹也在变更，且方向一致 -> 冗余
                    ancestor_on = next(on for p, on in raw if p == dir_path)
                    if ancestor_on == now_checked:
                        redundant = True
                        break
            if not redundant:
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
            self._stats.setText("，".join(parts))
            self._btn_save.setEnabled(True)
        else:
            self._stats.setText(f"共 {len(self._initial_state)} 项，无变更")
            self._btn_save.setEnabled(False)

    def _on_save(self) -> None:
        changes = self._collect_changes()
        if not changes:
            self.reject()
            return
        added, removed = self._service.preview_changes(changes)
        untrack_items = [
            rel for rel, track in changes
            if not track and (rel in self._service._tracked_files
                              or self._service._dir_has_tracked(rel))
        ]
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
        if untrack_items:
            lines.append("")
            lines.append(f"注意：{len(untrack_items)} 个已跟踪项将执行 git rm --cached（本地文件保留）。")

        box = QMessageBox(self)
        box.setWindowTitle("确认变更")
        box.setText("\n".join(lines))
        box.setIcon(QMessageBox.Icon.Question)
        btn_ok = box.addButton("保存并应用", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.setStyleSheet(
            f"QMessageBox {{ background: {PANEL_COLOR}; color: #E8F0F5; }}"
            f"QPushButton {{ padding: 6px 16px; border-radius: 4px; }}"
        )
        box.exec()
        if box.clickedButton() is not btn_ok:
            return
        result = self._service.apply_changes(changes)
        if result["errors"]:
            QMessageBox.warning(self, "部分操作失败", "\n".join(result["errors"]))
        self.accept()
