# Copyright (C) 2026 dudu <https://duadu.cc>
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import os
import subprocess
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

from app.core.git_service import GitService
from app.models import ProjectConfig, RemoteConfig


def compute_local_stats(workspace_path: str, git_exe: str = "git") -> dict:
    """统计工作区本地信息：文件数、总大小、目录创建时间、当前分支与短 hash。

    纯文件/git 操作，不含任何 Qt 对象；os.walk 会完整遍历大项目/网络盘，
    必须放在后台线程调用，UI 线程直接调会卡界面。
    """
    path = Path(workspace_path).expanduser()
    file_count = 0
    total_bytes = 0
    if path.is_dir():
        try:
            for root, directories, files in os.walk(path):
                directories[:] = [item for item in directories if item != ".git"]
                for filename in files:
                    try:
                        total_bytes += (Path(root) / filename).stat().st_size
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
    git_version = "未知"
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        _br = subprocess.run(
            [git_exe, "-C", str(path), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=5, creationflags=flags,
        )
        _sh = subprocess.run(
            [git_exe, "-C", str(path), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5, creationflags=flags,
        )
        if _br.returncode == 0 and _sh.returncode == 0:
            git_version = f"{_br.stdout.strip() or '?'} · {_sh.stdout.strip() or '?'}"
    except Exception:
        git_version = "未知"
    return {
        "created_at": created_at,
        "file_count": file_count,
        "total_size": size_text,
        "git_version": git_version,
    }


class ProjectStatsWorker(QThread):
    """后台统计单个项目的本地信息（文件数/总大小/当前分支）。

    为什么要单独一个线程：项目详情和暂存区卡片每次打开都会统计一遍，
    大项目或网络盘上 os.walk + git 调用能把 UI 线程卡住好几秒。
    """

    stats_ready = pyqtSignal(str, dict)  # project_id, stats
    failed = pyqtSignal(str, str)        # project_id, reason

    def __init__(self, project_id: str, workspace_path: str, parent=None):
        super().__init__(parent)
        self.project_id = project_id
        self._workspace_path = workspace_path

    def run(self) -> None:
        try:
            git_exe = "git"
            try:
                git_exe = GitService().git  # 优先用内置/已定位的那份 Git
            except Exception:
                pass
            self.stats_ready.emit(
                self.project_id, compute_local_stats(self._workspace_path, git_exe)
            )
        except Exception as exc:  # pragma: no cover - 最后一道线程保护
            self.failed.emit(self.project_id, f"{type(exc).__name__}: {exc}")


class StatusWorker(QThread):
    project_ready = pyqtSignal(str, object)
    log_message = pyqtSignal(str)
    failed = pyqtSignal(str)
    completed = pyqtSignal()
    progress_changed = pyqtSignal(int, int)

    def __init__(self, projects: list[ProjectConfig], ignore_github_failure: bool = False, skip_github: set[str] | None = None):
        super().__init__()
        self.projects = projects
        self.ignore_github_failure = ignore_github_failure
        self.skip_github = skip_github

    def run(self) -> None:
        try:
            service = GitService(log=self.log_message.emit)
            total = sum(1 + len(project.remotes) for project in self.projects)
            completed = 0
            self.progress_changed.emit(0, total)

            def mark_complete() -> None:
                nonlocal completed
                completed += 1
                self.progress_changed.emit(completed, total)

            for project in self.projects:
                self.log_message.emit(f"开始检查：{project.name}")
                result = service.inspect_project(
                    project,
                    progress=mark_complete,
                    ignore_github_failure=self.ignore_github_failure,
                    skip_github=self.skip_github,
                )
                self.project_ready.emit(project.project_id, result)
                self.log_message.emit(f"完成检查：{project.name}")
        except Exception as exc:  # pragma: no cover - 最后一道线程保护
            self.failed.emit(str(exc))
        finally:
            self.completed.emit()


class SyncWorker(QThread):
    result_ready = pyqtSignal(object)
    log_message = pyqtSignal(str)
    failed = pyqtSignal(str)
    completed = pyqtSignal()
    progress_changed = pyqtSignal(int, int)
    channel_started = pyqtSignal(str)
    channel_finished = pyqtSignal(str, bool)
    detailed_progress = pyqtSignal(str, int, int, str)  # task_name, current, total, detail_text

    def __init__(self, project: ProjectConfig, commit_message: str = "", full_sync: bool = False, automatic: bool = False):
        super().__init__()
        self.project = project
        self.commit_message = commit_message
        self.full_sync = full_sync
        self.automatic = automatic

    def run(self) -> None:
        try:
            service = GitService(log=self.log_message.emit, progress=self.detailed_progress.emit)
            local = service.local_status(self.project)
            # 未创建的新项目：同步时自动 init + 首次提交 + 建远程仓库
            if local.get("uncreated"):
                self.log_message.emit("检测到新项目，自动初始化 Git 仓库并创建远程仓库")
                total = 1 + len(self.project.remotes)
                completed = 0
                self.progress_changed.emit(0, total)

                def mark_complete() -> None:
                    nonlocal completed
                    completed += 1
                    self.progress_changed.emit(completed, total)

                prepared = service.prepare_project(self.project, progress=mark_complete)
                errors = list(prepared.get("errors", []))
                # prepare_project 返回的是 errors 列表，转成标准 results 结构，
                # 让 _on_sync_result 能正确统计失败渠道
                results = [{
                    "ok": not errors,
                    "message": "本地仓库初始化完成" if not errors else "本地仓库已初始化，但有 Git 渠道未完成",
                    "key": "__local__",
                    "label": "本地",
                }]
                for err in errors:
                    results.append({"ok": False, "message": err, "key": "", "label": ""})
                self.result_ready.emit({
                    "project_id": self.project.project_id,
                    "results": results,
                    "created": True,
                    "project": prepared.get("project", self.project),
                })
                return
            needs_commit = bool(self.commit_message) or not local.get("clean", False)
            total = len(self.project.remotes) + (1 if needs_commit else 0)
            completed = 0
            self.progress_changed.emit(0, total)

            def mark_complete() -> None:
                nonlocal completed
                completed += 1
                self.progress_changed.emit(completed, total)

            if needs_commit:
                commit_result = service.commit_project(
                    self.project,
                    self.commit_message or f"更新项目：{self.project.name}",
                    automatic=self.automatic,
                )
                self.log_message.emit(commit_result["message"])
                mark_complete()
            result = service.sync_project(
                self.project,
                progress=mark_complete,
                full_sync=self.full_sync,
                on_channel_start=lambda key: self.channel_started.emit(key),
                on_channel_finish=lambda key, ok: self.channel_finished.emit(key, ok),
            )
            self.result_ready.emit(result)
        except Exception as exc:  # pragma: no cover - 最后一道线程保护
            self.failed.emit(str(exc))
        finally:
            self.completed.emit()


class ProjectCreateWorker(QThread):
    result_ready = pyqtSignal(object)
    log_message = pyqtSignal(str)
    failed = pyqtSignal(str)
    completed = pyqtSignal()
    progress_changed = pyqtSignal(int, int)
    detailed_progress = pyqtSignal(str, int, int, str)  # task_name, current, total, detail_text

    def __init__(self, project: ProjectConfig):
        super().__init__()
        self.project = project

    def run(self) -> None:
        try:
            service = GitService(log=self.log_message.emit, progress=self.detailed_progress.emit)
            total = 1 + len(self.project.remotes)
            completed = 0
            self.progress_changed.emit(0, total)

            def mark_complete() -> None:
                nonlocal completed
                completed += 1
                self.progress_changed.emit(completed, total)

            result = service.prepare_project(self.project, progress=mark_complete)
            self.result_ready.emit(result)
        except Exception as exc:  # pragma: no cover
            self.failed.emit(str(exc))
        finally:
            self.completed.emit()


class PullWorker(QThread):
    result_ready = pyqtSignal(object)
    log_message = pyqtSignal(str)
    failed = pyqtSignal(str)
    completed = pyqtSignal()
    progress_changed = pyqtSignal(int, int)
    detailed_progress = pyqtSignal(str, int, int, str)

    def __init__(self, project: ProjectConfig):
        super().__init__()
        self.project = project

    def run(self) -> None:
        try:
            service = GitService(log=self.log_message.emit, progress=self.detailed_progress.emit)
            total = len([r for r in self.project.remotes.values() if r.kind != "local" and r.enabled])
            self.progress_changed.emit(0, total)

            def mark_complete() -> None:
                nonlocal_done = [0]
                nonlocal_done[0] += 1
                self.progress_changed.emit(nonlocal_done[0], total)

            results = service.pull_to_local(self.project, progress=mark_complete)
            self.result_ready.emit(results)
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            self.completed.emit()


class DivergeResolveWorker(QThread):
    """后台处理已分叉的仓库：按用户选的策略 merge / rebase / force。"""

    result_ready = pyqtSignal(object)
    log_message = pyqtSignal(str)
    failed = pyqtSignal(str)
    completed = pyqtSignal()
    progress_changed = pyqtSignal(int, int)
    detailed_progress = pyqtSignal(str, int, int, str)

    def __init__(self, project: ProjectConfig, remote: RemoteConfig, strategy: str, remote_key: str = ""):
        super().__init__()
        self.project = project
        self.remote = remote
        self.strategy = strategy
        self.remote_key = remote_key

    def run(self) -> None:
        try:
            service = GitService(log=self.log_message.emit, progress=self.detailed_progress.emit)
            self.progress_changed.emit(0, 1)
            result = service.resolve_divergence(
                self.project,
                self.remote,
                self.strategy,
                progress=lambda: self.progress_changed.emit(1, 1),
                remote_key=self.remote_key,
            )
            self.result_ready.emit(result)
        except Exception as exc:  # pragma: no cover - 最后一道线程保护
            self.failed.emit(str(exc))
        finally:
            self.completed.emit()


class SingleChannelWorker(QThread):
    """单渠道推送或拉取（右键菜单"单独推送/单独拉取"用）。"""

    result_ready = pyqtSignal(object)
    log_message = pyqtSignal(str)
    failed = pyqtSignal(str)
    completed = pyqtSignal()
    detailed_progress = pyqtSignal(str, int, int, str)

    def __init__(self, project: ProjectConfig, remote_key: str, action: str = "push"):
        super().__init__()
        self.project = project
        self.remote_key = remote_key
        self.action = action  # "push" | "pull"

    def run(self) -> None:
        try:
            service = GitService(log=self.log_message.emit, progress=self.detailed_progress.emit)
            if self.action == "push":
                result = service.push_channel(self.project, self.remote_key)
            else:
                result = service.pull_channel(self.project, self.remote_key)
            self.result_ready.emit(result)
        except Exception as exc:  # pragma: no cover
            self.failed.emit(str(exc))
        finally:
            self.completed.emit()


class SingleRefreshWorker(QThread):
    """单渠道状态刷新（右键菜单"单独刷新"用）。"""

    result_ready = pyqtSignal(str, dict)  # remote_key, status
    log_message = pyqtSignal(str)
    failed = pyqtSignal(str)
    completed = pyqtSignal()

    def __init__(self, project: ProjectConfig, remote_key: str):
        super().__init__()
        self.project = project
        self.remote_key = remote_key

    def run(self) -> None:
        try:
            service = GitService(log=self.log_message.emit)
            local = service.local_status(self.project)
            remote = self.project.remotes.get(self.remote_key)
            if remote is None:
                self.failed.emit(f"未找到渠道：{self.remote_key}")
                return
            result = service.remote_status(self.project, remote, local, self.remote_key)
            self.result_ready.emit(self.remote_key, result)
        except Exception as exc:  # pragma: no cover
            self.failed.emit(str(exc))
        finally:
            self.completed.emit()


class ProjectDeleteWorker(QThread):
    result_ready = pyqtSignal(object)
    log_message = pyqtSignal(str)
    failed = pyqtSignal(str)
    completed = pyqtSignal()

    def __init__(self, project: ProjectConfig):
        super().__init__()
        self.project = project

    def run(self) -> None:
        try:
            service = GitService(log=self.log_message.emit)
            self.result_ready.emit(service.delete_project(self.project))
        except Exception as exc:  # pragma: no cover
            self.failed.emit(str(exc))
        finally:
            self.completed.emit()


class LocalStatusWorker(QThread):
    """轻量只读本地仓库状态（暂存区），不查远程。

    用于新建项目、选中未开启同步的项目时，快速把"未创建/暂存区"卡片渲染出来，
    不必走完整的 StatusWorker（那只会处理 sync_enabled=True 的项目）。
    """

    local_ready = pyqtSignal(str, dict)
    failed = pyqtSignal(str)
    completed = pyqtSignal()

    def __init__(self, project: ProjectConfig):
        super().__init__()
        self.project = project

    def run(self) -> None:
        try:
            service = GitService()
            local = service.local_status(self.project)
            self.local_ready.emit(self.project.project_id, local)
        except Exception as exc:  # pragma: no cover
            self.failed.emit(str(exc))
        finally:
            self.completed.emit()


class DiscoverReposWorker(QThread):
    """后台读取某 Git 渠道（GitHub/Gitea/NAS）远程账号下的真实仓库列表。"""

    repos_ready = pyqtSignal(object)  # list[dict]
    failed = pyqtSignal(str)
    completed = pyqtSignal()

    def __init__(self, remote):
        super().__init__()
        self.remote = remote

    def run(self) -> None:
        try:
            service = GitService()
            repos = service.discover_host_repositories(self.remote)
            self.repos_ready.emit(repos)
        except Exception as exc:  # pragma: no cover
            self.failed.emit(str(exc))
        finally:
            self.completed.emit()


class CloneRepoWorker(QThread):
    """后台把远程仓库 clone 到本地目录（用于拉取尚未落地的远程仓库）。"""

    log_message = pyqtSignal(str)
    failed = pyqtSignal(str)
    completed = pyqtSignal(str)  # 成功后返回目标目录
    detailed_progress = pyqtSignal(str, int, int, str)

    def __init__(self, clone_url: str, target_dir: str):
        super().__init__()
        self.clone_url = clone_url
        self.target_dir = target_dir

    def run(self) -> None:
        try:
            service = GitService(log=self.log_message.emit, progress=self.detailed_progress.emit)
            service._run(
                ["clone", "--progress", str(self.clone_url), str(self.target_dir)],
                timeout=600,
                capture_progress=True,
                progress_task="克隆仓库",
            )
            self.completed.emit(str(self.target_dir))
        except Exception as exc:  # pragma: no cover
            self.failed.emit(str(exc))


class MigrateWorker(QThread):
    """跨盘迁移项目：逐文件复制 + 容错删源，实时报字节进度。"""

    log_message = pyqtSignal(str)
    progress = pyqtSignal(int, int, str)  # current_bytes, total_bytes, detail
    failed = pyqtSignal(str)
    completed = pyqtSignal(str)  # new_path

    def __init__(self, src: str, dst: str):
        super().__init__()
        self.src = src
        self.dst = dst

    def run(self) -> None:
        import os
        import shutil
        import stat
        from pathlib import Path

        src = Path(self.src)
        dst = Path(self.dst)

        def _remove_readonly(func, path, _exc):
            try:
                os.chmod(path, stat.S_IWRITE)
                func(path)
            except Exception:
                pass

        try:
            total = 0
            for f in src.rglob("*"):
                try:
                    if f.is_file():
                        total += f.stat().st_size
                except OSError:
                    pass

            copied = 0
            for f in src.rglob("*"):
                if not f.is_file():
                    continue
                rel = f.relative_to(src)
                target = dst / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, target)
                size = f.stat().st_size
                copied += size
                self.progress.emit(
                    copied,
                    total,
                    f"{copied / 1048576:.1f} / {total / 1048576:.1f} MB" if total else "复制中...",
                )
            shutil.rmtree(str(src), onerror=_remove_readonly)
            self.completed.emit(str(dst))
        except Exception as exc:
            try:
                if dst.exists():
                    shutil.rmtree(str(dst), onerror=_remove_readonly)
            except Exception:
                pass
            self.failed.emit(str(exc))

class CompleteRepoWorker(QThread):
    """在已有非 git 目录里完善成 git 仓库：init + 关联远程 + fetch + checkout。"""

    log_message = pyqtSignal(str)
    failed = pyqtSignal(str)
    completed = pyqtSignal(str)  # 成功后返回目标目录
    detailed_progress = pyqtSignal(str, int, int, str)

    def __init__(self, clone_url: str, target_dir: str, branch: str):
        super().__init__()
        self.clone_url = clone_url
        self.target_dir = target_dir
        self.branch = branch or "main"

    def run(self) -> None:
        try:
            tgt = str(self.target_dir)
            service = GitService(
                log=self.log_message.emit,
                progress=self.detailed_progress.emit,
            )
            service._run(
                ["init"],
                cwd=tgt,
                timeout=60,
            )
            service._run(
                ["remote", "add", "origin", str(self.clone_url)],
                cwd=tgt,
                timeout=30,
            )
            service._run(
                ["fetch", "--progress", "origin"],
                cwd=tgt,
                timeout=600,
                capture_progress=True,
                progress_task="拉取远程",
            )
            # 尝试 checkout 远程分支；失败就只关联，不动本地文件
            try:
                service._run(
                    ["checkout", "-b", self.branch, f"origin/{self.branch}"],
                    cwd=tgt,
                    timeout=60,
                )
            except Exception:
                self.log_message.emit("本地文件与远程有差异，已关联远程，未自动覆盖。")
            self.completed.emit(tgt)
        except Exception as exc:  # pragma: no cover
            self.failed.emit(str(exc))


class CommitHistoryWorker(QThread):
    """后台加载各渠道的提交历史。

    每个渠道一个信号：本地工作区直接 log HEAD；远端渠道先 fetch（只更新
    元数据、绝不动工作区）再 log FETCH_HEAD；本地 Git 渠道读它的备份仓库。
    某个渠道失败不影响其他渠道，结果逐个发回。
    """

    channel_history = pyqtSignal(str, str, list)  # channel_key, label, commits
    history_failed = pyqtSignal(str, str, str)    # channel_key, label, reason
    # 「加载更多」结果：channel_key, label, commits, has_more(取满一页说明可能还有)
    history_more = pyqtSignal(str, str, list, bool)
    completed = pyqtSignal()

    def __init__(
        self,
        project: ProjectConfig,
        remotes: dict[str, RemoteConfig],
        limit: int = 100,
        more_key: str = "",
        more_skip: int = 0,
        parent=None,
    ):
        super().__init__(parent)
        self.project = project
        self.remotes = remotes
        self.limit = limit
        # more_key 非空时进入「加载更多」模式：只加载这一个渠道、
        # 从 more_skip 条开始的一页，不再全量刷新其他渠道
        self.more_key = more_key
        self.more_skip = more_skip

    def _load_channel(self, service: "GitService", key: str, remote: RemoteConfig | None) -> list[dict]:
        if remote is None:  # 本地工作区
            return service.commit_history(self.project.workspace_path, "HEAD", self.limit, skip=self.more_skip)
        if remote.kind == "local":
            if not remote.path:
                raise RuntimeError("未配置本地 Git 渠道路径")
            channel_path = service.local_channel_path(remote, self.project)
            if not Path(channel_path).is_dir():
                raise RuntimeError("本地 Git 渠道还没有备份仓库")
            return service.commit_history(channel_path, "HEAD", self.limit, skip=self.more_skip)
        return service.remote_commit_history(self.project, remote, self.limit, skip=self.more_skip)

    def run(self) -> None:
        try:
            service = GitService()
            if self.more_key:
                # —— 加载更多模式：单渠道分页 ——
                if self.more_key == "__workspace__":
                    label = "本地工作区"
                    try:
                        commits = self._load_channel(service, self.more_key, None)
                        has_more = len(commits) >= self.limit
                        self.history_more.emit(self.more_key, label, commits, has_more)
                    except Exception as exc:  # noqa: BLE001
                        self.history_failed.emit(self.more_key, label, str(exc))
                else:
                    remote = self.remotes.get(self.more_key)
                    if remote is None:
                        return
                    label = remote.label or {
                        "local": "本地 Git", "nas": "NAS Gitea", "github": "GitHub",
                    }.get(remote.kind, "远程 Git")
                    try:
                        commits = self._load_channel(service, self.more_key, remote)
                        has_more = len(commits) >= self.limit
                        self.history_more.emit(self.more_key, label, commits, has_more)
                    except Exception as exc:  # noqa: BLE001
                        self.history_failed.emit(self.more_key, label, str(exc))
                return
            # —— 首次全量加载模式 ——
            try:
                commits = service.commit_history(self.project.workspace_path, "HEAD", self.limit)
                self.channel_history.emit("__workspace__", "本地工作区", commits)
            except Exception as exc:  # noqa: BLE001 - 单渠道失败不拖垮其他渠道
                self.history_failed.emit("__workspace__", "本地工作区", str(exc))
            for key, remote in self.remotes.items():
                label = remote.label or {
                    "local": "本地 Git", "nas": "NAS Gitea", "github": "GitHub",
                }.get(remote.kind, "远程 Git")
                try:
                    if not remote.enabled:
                        continue
                    commits = self._load_channel(service, key, remote)
                    self.channel_history.emit(key, label, commits)
                except Exception as exc:  # noqa: BLE001
                    self.history_failed.emit(key, label, str(exc))
        finally:
            self.completed.emit()


class RestoreCommitWorker(QThread):
    """把工作区恢复到指定提交：必要时先整目录备份，再 git reset --hard。

    备份和 reset 都在后台线程跑（robocopy 大目录可能要几十秒），UI 只收结果。
    """

    result_ready = pyqtSignal(object)  # dict: {backup_path}
    failed = pyqtSignal(str)
    completed = pyqtSignal()

    def __init__(self, project: ProjectConfig, commit_hash: str, parent=None):
        super().__init__(parent)
        self.project = project
        self.commit_hash = commit_hash

    def run(self) -> None:
        try:
            service = GitService(log=lambda _m: None)
            result = service.restore_workspace_to_commit(self.project, self.commit_hash)
            self.result_ready.emit(result)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
        finally:
            self.completed.emit()
