"""文件树管理：遍历项目文件、判断 git 跟踪状态、读写 .gitignore。

双击暂存区卡片后弹出的文件管理对话框使用本模块：
- 递归构建文件树（大目录只显示本身不展开，避免几万个文件卡死）
- 用 git ls-files 批量获取跟踪/忽略状态，不逐文件调 git
- 按扩展名和目录名给出"推荐 git / 不推荐 git"建议
- 开关切换时同步 .gitignore，已跟踪文件额外执行 git rm --cached
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from app.core.git_locator import resolve_git_path

# GitHub 单文件硬限制
LARGE_FILE_LIMIT = 100 * 1024 * 1024

# 遍历时完全跳过、不显示在树里的目录
SKIP_DIRS = {".git"}

# 大目录：只显示目录本身（开关可操作），不递归子项
COLLAPSE_DIRS = {
    "node_modules", ".venv", "venv", "env", "__pycache__",
    "dist", "build", "out", "target", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", ".tox",
    ".idea", ".vscode", ".vs", "htmlcov", "coverage",
}

# 不推荐纳入 git 的扩展名
AVOID_EXTENSIONS = {
    ".pyc", ".pyo", ".pyd", ".log", ".tmp", ".bak", ".swp",
    ".exe", ".dll", ".so", ".dylib", ".class",
    ".obj", ".o", ".a", ".lib", ".pdb", ".ilk",
    ".db", ".sqlite", ".sqlite3",
}

# 推荐纳入 git 的扩展名
RECOMMEND_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".html", ".htm", ".css", ".scss", ".less",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
    ".md", ".txt", ".rst", ".csv",
    ".sh", ".bat", ".ps1", ".cmd",
    ".svg", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp",
    ".ttf", ".otf", ".woff", ".woff2",
    ".spec", ".iss", ".isl",
}

# 不推荐纳入 git 的目录名
AVOID_DIR_NAMES = COLLAPSE_DIRS | {".egg-info", "node_modules"}

# 推荐纳入 git 的目录名
RECOMMEND_DIR_NAMES = {
    "src", "app", "lib", "docs", "tests", "test", "scripts",
    "assets", "resources", "images", "img", "icons", "ui",
    "config", "configs", "conf", "public", "static", "templates",
}

# RepoPulse 管理的 .gitignore 区块标记
MANAGED_BEGIN = "# >>> RepoPulse 文件管理（可视化开关自动维护） >>>"
MANAGED_END = "# <<< RepoPulse 文件管理 <<<"


@dataclass
class FileNode:
    """文件树中的一个节点（文件或文件夹）。"""

    name: str
    rel_path: str  # 相对于项目根目录，用 / 分隔
    is_dir: bool
    size: int = 0
    tracked: bool = False
    ignored: bool = False
    disabled: bool = False
    disabled_reason: str = ""
    recommendation: str = "neutral"  # recommend / avoid / neutral
    children: list["FileNode"] = field(default_factory=list)
    parent: Optional["FileNode"] = None
    collapsed_large: bool = False  # 大目录只显示本身不展开
    rule_ignored: bool = False  # 被用户手写通配符规则忽略（开关禁用）

    @property
    def display_size(self) -> str:
        if self.is_dir:
            return ""
        if self.size >= 1024 * 1024:
            return f"{self.size / 1048576:.1f} MB"
        if self.size >= 1024:
            return f"{self.size / 1024:.1f} KB"
        return f"{self.size} B"

    @property
    def checked(self) -> bool:
        """开关是否打开：已跟踪或未被忽略。"""
        if self.disabled:
            return False
        if self.tracked:
            return True
        return not self.ignored

    def all_descendants_checked(self) -> bool:
        if not self.is_dir or not self.children:
            return self.checked
        return all(c.all_descendants_checked() for c in self.children)

    def no_descendants_checked(self) -> bool:
        if not self.is_dir or not self.children:
            return not self.checked
        return all(c.no_descendants_checked() for c in self.children)


class FileTreeService:
    """构建文件树、查询 git 状态、管理 .gitignore。"""

    def __init__(self, project_path: str | Path, log: Callable[[str], None] | None = None):
        self.root = Path(project_path).expanduser()
        self.log = log or (lambda _m: None)
        self.git = resolve_git_path() or "git"
        self._tracked_files: set[str] = set()
        self._untracked_visible: set[str] = set()  # 未跟踪但未被忽略（会被 git add 纳入）
        self._wildcard_ignored: set[str] = set()  # 被通配符规则忽略的文件

    def _git(self, args: list[str], timeout: int = 30) -> str:
        """执行 git 命令，返回 stdout；失败返回空字符串。"""
        try:
            result = subprocess.run(
                [self.git, *args],
                cwd=str(self.root),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return result.stdout.strip() if result.returncode == 0 else ""
        except (subprocess.TimeoutExpired, OSError):
            return ""

    # ------------------------------------------------------------------
    # 文件树构建
    # ------------------------------------------------------------------

    def build_tree(self) -> FileNode:
        """构建完整文件树，填充 git 跟踪状态和推荐信息。"""
        self._load_git_file_sets()
        root_node = FileNode(name=self.root.name, rel_path="", is_dir=True)
        self._scan_directory(self.root, root_node)
        self._mark_wildcard_ignored(root_node)
        return root_node

    def _load_git_file_sets(self) -> None:
        """批量获取已跟踪文件集合和未跟踪可见文件集合（各一次 git 调用）。"""
        # 已跟踪
        out = self._git(["ls-files", "-z"], timeout=30)
        self._tracked_files = {p.replace("\\", "/") for p in out.split("\0") if p} if out else set()
        # 未跟踪但未被 .gitignore 忽略（git add -A 会纳入的新文件）
        out2 = self._git(["ls-files", "--others", "--exclude-standard", "-z"], timeout=30)
        self._untracked_visible = {p.replace("\\", "/") for p in out2.split("\0") if p} if out2 else set()

    def _mark_wildcard_ignored(self, root: FileNode) -> None:
        """遍历树，对被忽略的文件批量 check-ignore -v，判断是否为通配符规则。

        被通配符规则（*.log 等）忽略的文件，可视化开关无法精确控制，标记禁用。
        被精确路径规则（/a.py）忽略的文件，开关仍可操作。
        """
        ignored_paths: list[str] = []

        def collect(node: FileNode) -> None:
            if node.ignored and not node.tracked and not node.disabled:
                ignored_paths.append(node.rel_path + ("/" if node.is_dir else ""))
            for child in node.children:
                collect(child)

        collect(root)
        if not ignored_paths:
            return

        # 批量调用 git check-ignore -v --stdin（-z 用 NUL 分隔）
        import subprocess as _sp
        try:
            proc = _sp.run(
                [self.git, "check-ignore", "-v", "-z", "--stdin"],
                cwd=str(self.root),
                input="\0".join(ignored_paths) + "\0",
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                creationflags=getattr(_sp, "CREATE_NO_WINDOW", 0),
            )
        except (_sp.TimeoutExpired, OSError):
            return

        # -z 输出：source\0linenum\0pattern\0pathname\0 每组4个字段
        parts = proc.stdout.split("\0") if proc.stdout else []
        wildcard_paths: set[str] = set()
        # 每组 4 个字段
        i = 0
        while i + 3 < len(parts):
            source = parts[i]
            pattern = parts[i + 2]
            pathname = parts[i + 3]
            # 通配符规则：pattern 含 * ? [ ；精确路径规则（/foo、/bar/）可操作
            if any(ch in pattern for ch in "*?["):
                clean_path = pathname.rstrip("/")
                wildcard_paths.add(clean_path)
            i += 4
        self._wildcard_ignored = wildcard_paths

        # 标记节点
        def mark(node: FileNode) -> None:
            if node.rel_path in wildcard_paths and not node.disabled:
                node.rule_ignored = True
                node.disabled = True
                node.disabled_reason = "规则忽略"
            for child in node.children:
                mark(child)

        mark(root)

    def _scan_directory(self, dir_path: Path, parent_node: FileNode) -> None:
        """递归扫描目录。"""
        try:
            entries = sorted(
                dir_path.iterdir(),
                key=lambda p: (not p.is_dir(), p.name.lower()),
            )
        except (PermissionError, OSError):
            return

        for entry in entries:
            name = entry.name
            rel = entry.relative_to(self.root).as_posix()

            if entry.is_dir():
                if name in SKIP_DIRS:
                    continue
                node = FileNode(name=name, rel_path=rel, is_dir=True)
                node.parent = parent_node
                if name in COLLAPSE_DIRS:
                    # 大目录只显示本身
                    node.collapsed_large = True
                    node.recommendation = "avoid"
                    node.tracked = self._dir_has_tracked(rel)
                    node.ignored = not node.tracked and not self._dir_has_visible(rel)
                    parent_node.children.append(node)
                    continue
                self._scan_directory(entry, node)
                parent_node.children.append(node)
                self._apply_dir_state(node)
            else:
                try:
                    size = entry.stat().st_size
                except OSError:
                    size = 0
                node = FileNode(name=name, rel_path=rel, is_dir=False, size=size)
                node.parent = parent_node
                node.tracked = rel in self._tracked_files
                # 未跟踪且不在"可见未跟踪"集合里 → 被 .gitignore 忽略
                node.ignored = (
                    not node.tracked and rel not in self._untracked_visible
                )
                node.recommendation = self._recommend_file(name, size)
                if size >= LARGE_FILE_LIMIT:
                    node.disabled = True
                    node.disabled_reason = "大文件"
                    node.recommendation = "avoid"
                parent_node.children.append(node)

    def _dir_has_tracked(self, dir_rel: str) -> bool:
        prefix = dir_rel + "/"
        return any(f.startswith(prefix) for f in self._tracked_files)

    def _dir_has_visible(self, dir_rel: str) -> bool:
        prefix = dir_rel + "/"
        return any(f.startswith(prefix) for f in self._untracked_visible)

    def _apply_dir_state(self, node: FileNode) -> None:
        """根据子节点推断文件夹状态。"""
        if not node.children:
            node.disabled = True
            node.disabled_reason = "空目录"
            node.recommendation = "neutral"
            return
        node.tracked = any(c.tracked for c in node.children)
        # 文件夹被忽略 = 没有任何已跟踪或可见未跟踪的子文件
        has_visible = any(
            (c.tracked or not c.ignored) and not c.disabled
            for c in node.children
        )
        node.ignored = not has_visible
        rec_count = sum(1 for c in node.children if c.recommendation == "recommend")
        avoid_count = sum(1 for c in node.children if c.recommendation == "avoid")
        if rec_count > avoid_count:
            node.recommendation = "recommend"
        elif avoid_count > rec_count:
            node.recommendation = "avoid"
        else:
            node.recommendation = "neutral"

    @staticmethod
    def _recommend_file(name: str, size: int) -> str:
        lower = name.lower()
        ext = Path(lower).suffix
        if size >= LARGE_FILE_LIMIT:
            return "avoid"
        if lower in {".gitignore", ".gitattributes", ".gitkeep", "license", "readme.md"}:
            return "recommend"
        if lower in {"thumbs.db", ".ds_store"}:
            return "avoid"
        if ext in AVOID_EXTENSIONS:
            return "avoid"
        if ext in RECOMMEND_EXTENSIONS:
            return "recommend"
        return "neutral"

    # ------------------------------------------------------------------
    # .gitignore 读写
    # ------------------------------------------------------------------

    def read_gitignore(self) -> str:
        gitignore = self.root / ".gitignore"
        if gitignore.exists():
            try:
                return gitignore.read_text(encoding="utf-8")
            except OSError:
                return ""
        return ""

    def _parse_gitignore(self) -> tuple[list[str], list[str]]:
        """读取 .gitignore，拆成 (用户行, RepoPulse 管理行)。"""
        content = self.read_gitignore()
        user_lines: list[str] = []
        managed_lines: list[str] = []
        in_managed = False
        for line in content.splitlines():
            stripped = line.strip()
            if stripped == MANAGED_BEGIN:
                in_managed = True
                continue
            if stripped == MANAGED_END:
                in_managed = False
                continue
            if in_managed:
                if stripped and not stripped.startswith("#"):
                    managed_lines.append(stripped)
            else:
                user_lines.append(line)
        return user_lines, managed_lines

    @staticmethod
    def _dedup_changes(changes: list[tuple[str, bool]]) -> list[tuple[str, bool]]:
        """过滤被祖先文件夹同向变更覆盖的冗余子项。"""
        norm = [(p.replace("\\", "/").strip("/"), v) for p, v in changes if p.replace("\\", "/").strip("/")]
        result = []
        for p, v in norm:
            if not v:
                # 若某祖先文件夹也在本次变更中被关闭，则本子项被覆盖，跳过
                parts = p.split("/")
                redundant = False
                for i in range(1, len(parts)):
                    if ("/".join(parts[:i]), False) in norm:
                        redundant = True
                        break
                if redundant:
                    continue
            result.append((p, v))
        return result

    def apply_changes(self, changes: list[tuple[str, bool]]) -> dict:
        """批量应用开关变更。

        changes: [(rel_path, should_track), ...]
          should_track=True  → 从管理区块移除忽略规则
          should_track=False → 写入忽略规则，已跟踪文件执行 git rm --cached
        """
        changes = self._dedup_changes(changes)
        untracked: list[str] = []
        tracked: list[str] = []
        errors: list[str] = []

        user_lines, managed = self._parse_gitignore()
        managed_set = set(managed)

        for rel_path, should_track in changes:
            rel_path = rel_path.replace("\\", "/").strip("/")
            if not rel_path:
                continue
            full = self.root / rel_path
            is_dir = full.is_dir()
            rule = f"/{rel_path}/" if is_dir else f"/{rel_path}"

            if should_track:
                managed_set.discard(rule)
                managed_set.discard(f"/{rel_path}")
                managed_set.discard(rel_path)
                tracked.append(rel_path)
            else:
                managed_set.add(rule)
                # 已跟踪文件需要 git rm --cached
                if rel_path in self._tracked_files or self._dir_has_tracked(rel_path):
                    args = ["rm", "-r", "--cached", "--quiet", "--ignore-unmatch", "--", rel_path] if is_dir \
                        else ["rm", "--cached", "--quiet", "--ignore-unmatch", "--", rel_path]
                    self._git(args, timeout=60)
                    untracked.append(rel_path)
                else:
                    untracked.append(rel_path)

        # 写回 .gitignore
        self._write_gitignore(user_lines, sorted(managed_set))
        return {"untracked": untracked, "tracked": tracked, "errors": errors}

    def _write_gitignore(self, user_lines: list[str], managed_rules: list[str]) -> None:
        """写回 .gitignore：用户规则原样保留，RepoPulse 规则放管理区块。"""
        parts: list[str] = []
        user_text = "\n".join(user_lines).rstrip()
        if user_text:
            parts.append(user_text)
        if managed_rules:
            block = MANAGED_BEGIN + "\n" + "\n".join(managed_rules) + "\n" + MANAGED_END
            parts.append(block)
        content = "\n\n".join(parts) + "\n"
        gitignore = self.root / ".gitignore"
        gitignore.write_text(content, encoding="utf-8")

    def preview_changes(self, changes: list[tuple[str, bool]]) -> tuple[list[str], list[str]]:
        """预览 .gitignore 变更，返回 (新增规则, 移除规则)。"""
        changes = self._dedup_changes(changes)
        _, managed = self._parse_gitignore()
        managed_set = set(managed)
        added: list[str] = []
        removed: list[str] = []
        for rel_path, should_track in changes:
            rel_path = rel_path.replace("\\", "/").strip("/")
            is_dir = (self.root / rel_path).is_dir()
            rule = f"/{rel_path}/" if is_dir else f"/{rel_path}"
            if should_track:
                if rule in managed_set:
                    removed.append(rule)
            else:
                if rule not in managed_set:
                    added.append(rule)
        return added, removed
