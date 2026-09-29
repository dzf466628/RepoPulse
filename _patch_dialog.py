"""临时补丁：修复 QTreeWidgetItem 不可哈希问题，改用 rel_path 做 key。"""
import pathlib

f = pathlib.Path(r"E:\工程源文件\RepoPulse\app\ui\file_tree_dialog.py")
text = f.read_text(encoding="utf-8")

# 1. import 加 QTreeWidgetItemIterator
old_imp = "from PyQt6.QtWidgets import (\n    QDialog,"
new_imp = "from PyQt6.QtWidgets import (\n    QDialog,\n    QTreeWidgetItemIterator,"
assert old_imp in text
text = text.replace(old_imp, new_imp, 1)

# 2. 加 ROLE_PATH 常量
old_role = "# UserRole"
if "ROLE_PATH" not in text:
    text = text.replace(
        "COLOR_DISABLED = \"#6B7B8D\"",
        "COLOR_DISABLED = \"#6B7B8D\"\n\n# UserRole：在 item 上存 rel_path\nROLE_PATH = Qt.ItemDataRole.UserRole",
        1,
    )

# 3. 替换成员变量声明
text = text.replace(
    "self._item_map: dict[QTreeWidgetItem, FileNode] = {}",
    "self._node_map: dict[str, FileNode] = {}",
)
text = text.replace(
    "self._switch_map: dict[QTreeWidgetItem, ToggleSwitch] = {}",
    "self._switch_map: dict[str, ToggleSwitch] = {}",
)

# 4. _populate_tree 里的 clear
text = text.replace(
    "self._item_map.clear()",
    "self._node_map.clear()",
)

# 5. 重写 _add_node 方法（从方法签名到递归结束）
old_add_start = "    def _add_node(self, node: FileNode, parent_item: Optional[QTreeWidgetItem]) -> None:"
old_add_end = "        if node.is_dir and not node.disabled:\n            self._refresh_parent_state(item)"
start_idx = text.index(old_add_start)
end_idx = text.index(old_add_end) + len(old_add_end)

new_add = '''    def _add_node(self, node: FileNode, parent_item: Optional[QTreeWidgetItem]) -> None:
        item = QTreeWidgetItem(parent_item)
        if parent_item is None:
            self._tree.addTopLevelItem(item)
        item.setData(self.COLS_NAME, ROLE_PATH, node.rel_path)

        prefix = "\\U0001F4C1 " if node.is_dir else "\\U0001F4C4 "
        item.setText(self.COLS_NAME, prefix + node.name)
        tip = node.rel_path
        if node.collapsed_large:
            tip += "\\n大目录，不展开显示子文件"
        if node.disabled_reason:
            tip += f"\\n{node.disabled_reason}"
        item.setToolTip(self.COLS_NAME, tip)

        item.setText(self.COLS_SIZE, node.display_size)
        item.setTextAlignment(self.COLS_SIZE, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        hint_text, hint_color = self._hint_for(node)
        item.setText(self.COLS_HINT, hint_text)
        item.setForeground(self.COLS_HINT, QColor(hint_color))
        item.setTextAlignment(self.COLS_HINT, Qt.AlignmentFlag.AlignCenter)

        toggle = ToggleSwitch(checked=node.checked)
        if node.disabled:
            toggle.setDisabledState(True)
            item.setForeground(self.COLS_NAME, QColor(COLOR_DISABLED))
        toggle.toggled.connect(lambda checked, p=node.rel_path: self._on_toggle(p, checked))
        self._tree.setItemWidget(item, self.COLS_TOGGLE, toggle)

        self._node_map[node.rel_path] = node
        self._switch_map[node.rel_path] = toggle
        self._initial_state[node.rel_path] = node.checked

        for child in node.children:
            self._add_node(child, item)

        if node.is_dir and not node.disabled:
            self._refresh_parent_state(item)'''

text = text[:start_idx] + new_add + text[end_idx:]

# 6. 加 _item_path 辅助方法（插在 _hint_for 后面）
hint_anchor = "    # ------------------------------------------------------------------\n    # 开关联动"
helper = """    @staticmethod
    def _item_path(item: QTreeWidgetItem) -> str:
        return item.data(FileTreeDialog.COLS_NAME, ROLE_PATH) or ""

"""
text = text.replace(hint_anchor, helper + hint_anchor, 1)

# 7. 重写 _on_toggle
old_toggle = """    def _on_toggle(self, item: QTreeWidgetItem, checked: bool) -> None:
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
        self._update_stats()"""
new_toggle = """    def _on_toggle(self, rel_path: str, checked: bool) -> None:
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

    def _find_item(self, rel_path: str) -> Optional[QTreeWidgetItem]:
        it = QTreeWidgetItemIterator(self._tree)
        while it.value():
            if self._item_path(it.value()) == rel_path:
                return it.value()
            it += 1
        return None"""
assert old_toggle in text, "_on_toggle not found"
text = text.replace(old_toggle, new_toggle, 1)

# 8. 重写 _set_children
old_set = """    def _set_children(self, parent_item: QTreeWidgetItem, checked: bool) -> None:
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
                self._set_children(child, checked)"""
new_set = """    def _set_children(self, parent_item: QTreeWidgetItem, checked: bool) -> None:
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
                self._set_children(child, checked)"""
assert old_set in text, "_set_children not found"
text = text.replace(old_set, new_set, 1)

# 9. 重写 _refresh_parent_state
old_ref = """    def _refresh_parent_state(self, item: QTreeWidgetItem) -> None:
        \"\"\"根据子节点状态刷新文件夹开关（全开/全关/部分）。\"\"\"
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
            toggle.setPartial()"""
new_ref = """    def _refresh_parent_state(self, item: QTreeWidgetItem) -> None:
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
            toggle.setPartial()"""
assert old_ref in text, "_refresh_parent_state not found"
text = text.replace(old_ref, new_ref, 1)

# 10. 重写搜索过滤方法
old_filter = """    def _apply_filter(self, text: str) -> None:
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
        return False"""
new_filter = """    def _apply_filter(self, text: str) -> None:
        keyword = text.strip().lower()
        if not keyword:
            for i in range(self._tree.topLevelItemCount()):
                top = self._tree.topLevelItem(i)
                top.setHidden(False)
                self._set_subtree_hidden(top, False)
            self._tree.expandToDepth(0)
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

    def _set_subtree_hidden(self, item: QTreeWidgetItem, hidden: bool) -> None:
        item.setHidden(hidden)
        for i in range(item.childCount()):
            self._set_subtree_hidden(item.child(i), hidden)"""
assert old_filter in text, "filter methods not found"
text = text.replace(old_filter, new_filter, 1)

# 11. 重写推荐设置
old_rec = """    def _apply_recommend_recursive(self, item: QTreeWidgetItem) -> None:
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
            self._refresh_parent_state(item)"""
new_rec = """    def _apply_recommend_recursive(self, item: QTreeWidgetItem) -> None:
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
            self._refresh_parent_state(item)"""
assert old_rec in text, "_apply_recommend_recursive not found"
text = text.replace(old_rec, new_rec, 1)

# 12. 修改 _apply_recommendations 调用
old_call = """        for i in range(self._tree.topLevelItemCount()):
            self._apply_recommend_recursive(self._tree.topLevelItem(i))
        self._update_stats()"""
new_call = """        for i in range(self._tree.topLevelItemCount()):
            self._apply_recommend_recursive(self._tree.topLevelItem(i))
        for i in range(self._tree.topLevelItemCount()):
            self._refresh_dirs_bottom_up(self._tree.topLevelItem(i))
        self._update_stats()"""
assert old_call in text
text = text.replace(old_call, new_call, 1)

# 13. 重写 _collect_current_state
old_collect = """    def _collect_current_state(self) -> dict[str, bool]:
        \"\"\"收集当前所有叶子文件的开关状态。\"\"\"
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
        return result"""
new_collect = """    def _collect_current_state(self) -> dict[str, bool]:
        result: dict[str, bool] = {}
        it = QTreeWidgetItemIterator(self._tree)
        while it.value():
            item = it.value()
            path = self._item_path(item)
            node = self._node_map.get(path)
            toggle = self._switch_map.get(path)
            if node and toggle and not node.disabled:
                result[path] = toggle.isChecked()
            it += 1
        return result"""
assert old_collect in text, "_collect_current_state not found"
text = text.replace(old_collect, new_collect, 1)

f.write_text(text, encoding="utf-8")
print("OK: file_tree_dialog.py patched")
