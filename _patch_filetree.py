"""临时补丁：给 StatusCard 加双击信号 + 接入文件树对话框。"""
import pathlib

mw = pathlib.Path(r"E:\工程源文件\RepoPulse\app\ui\main_window.py")
text = mw.read_text(encoding="utf-8")

# 1. StatusCard 加 double_clicked 信号
old_sig = """class StatusCard(QFrame):
    clicked = pyqtSignal(str)
    context_menu_requested = pyqtSignal(str, QPoint)"""
new_sig = """class StatusCard(QFrame):
    clicked = pyqtSignal(str)
    double_clicked = pyqtSignal(str)
    context_menu_requested = pyqtSignal(str, QPoint)"""
assert old_sig in text, "信号定义未找到"
text = text.replace(old_sig, new_sig, 1)

# 2. 在 contextMenuEvent 前加 mouseDoubleClickEvent
old_ctx = """    def contextMenuEvent(self, event) -> None:  # noqa: N802 - Qt API
        self.context_menu_requested.emit(self.card_key, event.globalPos())"""
new_ctx = """    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            self.double_clicked.emit(self.card_key)
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event) -> None:  # noqa: N802 - Qt API
        self.context_menu_requested.emit(self.card_key, event.globalPos())"""
assert old_ctx in text, "contextMenuEvent 未找到"
text = text.replace(old_ctx, new_ctx, 1)

# 3. _add_card 里连接双击信号
old_connect = """    def _add_card(self, key: str, card: StatusCard, index: int) -> None:
        card.clicked.connect(self._select_card)
        card.context_menu_requested.connect(self._show_card_context_menu)"""
new_connect = """    def _add_card(self, key: str, card: StatusCard, index: int) -> None:
        card.clicked.connect(self._select_card)
        card.double_clicked.connect(self._on_card_double_clicked)
        card.context_menu_requested.connect(self._show_card_context_menu)"""
assert old_connect in text, "_add_card 未找到"
text = text.replace(old_connect, new_connect, 1)

# 4. import FileTreeDialog（插在 dialogs import 前）
old_import = "from app.ui.dialogs import ("
new_import = "from app.ui.file_tree_dialog import FileTreeDialog\nfrom app.ui.dialogs import ("
if "FileTreeDialog" not in text:
    assert old_import in text, "dialogs import 未找到"
    text = text.replace(old_import, new_import, 1)

# 5. 在 _show_card_context_menu 前插入双击处理方法
anchor = "    def _show_card_context_menu(self, key: str, global_pos: QPoint) -> None:"
handler = '''    def _on_card_double_clicked(self, key: str) -> None:
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
assert anchor in text, "_show_card_context_menu 锚点未找到"
text = text.replace(anchor, handler + anchor, 1)

mw.write_text(text, encoding="utf-8")
print("OK: main_window.py patched")
