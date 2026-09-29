# -*- coding: utf-8 -*-
"""补丁：详情弹窗加"文件管理"入口，移除 main_window 的双击冲突逻辑。"""
from pathlib import Path

# ---------- 1. dialogs.py：加信号 + 按钮 ----------
dlg = Path(r"E:\工程源文件\RepoPulse\app\ui\dialogs.py")
d = dlg.read_text(encoding="utf-8")

# 信号
old_sig = """    # 暂存区（项目本地信息）模式
    view_project_requested = pyqtSignal()
    migrate_project_requested = pyqtSignal()
    rename_project_requested = pyqtSignal()
    delete_project_requested = pyqtSignal()"""
new_sig = """    # 暂存区（项目本地信息）模式
    view_project_requested = pyqtSignal()
    migrate_project_requested = pyqtSignal()
    rename_project_requested = pyqtSignal()
    delete_project_requested = pyqtSignal()
    manage_files_requested = pyqtSignal()"""
assert old_sig in d, "dialog signal anchor"
d = d.replace(old_sig, new_sig, 1)

# 按钮：在 view_button 前插入"文件管理"按钮
old_btn = '''        view_button = QPushButton("查看")
        view_button.setFixedHeight(30)
        view_button.clicked.connect(self.view_project_requested.emit)'''
new_btn = '''        files_button = QPushButton("文件管理")
        files_button.setFixedHeight(30)
        files_button.setStyleSheet(
            f"QPushButton {{ background: {ACCENT_COLOR}; color: #071D2C; font-weight: 700; padding: 0 14px; border: none; border-radius: 5px; }}"
            f"QPushButton:hover {{ background: {ACCENT_HOVER}; }}"
        )
        files_button.setCursor(Qt.CursorShape.PointingHandCursor)
        files_button.clicked.connect(self.manage_files_requested.emit)
        view_button = QPushButton("查看")
        view_button.setFixedHeight(30)
        view_button.clicked.connect(self.view_project_requested.emit)'''
assert old_btn in d, "view_button anchor"
d = d.replace(old_btn, new_btn, 1)

# footer 布局加入 files_button
old_footer = """        footer.addWidget(view_button)
        footer.addWidget(migrate_button)"""
new_footer = """        footer.addWidget(files_button)
        footer.addWidget(view_button)
        footer.addWidget(migrate_button)"""
assert old_footer in d, "footer anchor"
d = d.replace(old_footer, new_footer, 1)

# 确认 ACCENT_HOVER 已导入
if "ACCENT_HOVER" not in d:
    d = d.replace("ACCENT_COLOR,", "ACCENT_COLOR,\n    ACCENT_HOVER,", 1)

dlg.write_text(d, encoding="utf-8")
print("OK: dialogs.py")

# ---------- 2. main_window.py：移除双击，改连详情信号 ----------
mw = Path(r"E:\工程源文件\RepoPulse\app\ui\main_window.py")
m = mw.read_text(encoding="utf-8")

# 移除 double_clicked 信号定义
m = m.replace(
    """    clicked = pyqtSignal(str)
    double_clicked = pyqtSignal(str)
    context_menu_requested = pyqtSignal(str, QPoint)""",
    """    clicked = pyqtSignal(str)
    context_menu_requested = pyqtSignal(str, QPoint)""",
    1,
)

# 移除 mouseDoubleClickEvent 方法
old_dbl = """    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            self.double_clicked.emit(self.card_key)
        super().mouseDoubleClickEvent(event)

"""
assert old_dbl in m, "doubleclick method anchor"
m = m.replace(old_dbl, "", 1)

# 移除 _add_card 里的连接
m = m.replace(
    """        card.clicked.connect(self._select_card)
        card.double_clicked.connect(self._on_card_double_clicked)
        card.context_menu_requested.connect(self._show_card_context_menu)""",
    """        card.clicked.connect(self._select_card)
        card.context_menu_requested.connect(self._show_card_context_menu)""",
    1,
)

# 移除 _on_card_double_clicked 方法
old_handler = '''    def _on_card_double_clicked(self, key: str) -> None:
        """双击暂存区卡片 -> 打开文件跟踪管理对话框。"""
        if key != "__staging__":
            return
        project = self._current_project()
        if not project or not project.workspace_path:
            return
        source = Path(project.workspace_path).expanduser()
        if not source.exists():
            QMessageBox.warning(self, "提示", f"项目目录不存在：\\n{source}")
            return
        dlg = FileTreeDialog(source, project.name, self)
        dlg.exec()
        self.refresh_selected(show_dialog=False)

'''
assert old_handler in m, "double handler anchor"
m = m.replace(old_handler, "", 1)

# 在暂存区详情构建处连接 manage_files_requested
# 锚点：staging 模式下连接 view_project_requested
old_conn = """                detail.view_project_requested.connect(lambda: self._open_project_folder(project))"""
new_conn = """                detail.view_project_requested.connect(lambda: self._open_project_folder(project))
                detail.manage_files_requested.connect(lambda: self._open_file_manager(project, detail))"""
assert old_conn in m, "staging detail connect anchor"
m = m.replace(old_conn, new_conn, 1)

# 新增 _open_file_manager 方法（插在 _show_card_context_menu 前）
anchor = "    def _show_card_context_menu(self, key: str, global_pos: QPoint) -> None:"
method = '''    def _open_file_manager(self, project, detail_dialog=None) -> None:
        """从暂存区详情弹窗打开文件跟踪管理对话框。"""
        if not project or not project.workspace_path:
            return
        source = Path(project.workspace_path).expanduser()
        if not source.exists():
            QMessageBox.warning(self, "提示", f"项目目录不存在：\\n{source}")
            return
        file_dlg = FileTreeDialog(source, project.name, self)
        file_dlg.exec()
        self.refresh_selected(show_dialog=False)
        # 文件管理可能改变跟踪状态，刷新详情里的统计
        if detail_dialog is not None and not self._current_project():
            return

'''
assert anchor in m, "context menu anchor"
m = m.replace(anchor, method + anchor, 1)

mw.write_text(m, encoding="utf-8")
print("OK: main_window.py")
