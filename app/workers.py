from __future__ import annotations

from PyQt6.QtCore import QThread, pyqtSignal

from app.core.git_service import GitService
from app.models import ProjectConfig


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

    def __init__(self, project: ProjectConfig, commit_message: str = "", full_sync: bool = False):
        super().__init__()
        self.project = project
        self.commit_message = commit_message
        self.full_sync = full_sync

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

    def __init__(self, project: ProjectConfig):
        super().__init__()
        self.project = project

    def run(self) -> None:
        try:
            service = GitService(log=self.log_message.emit)
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

    def __init__(self, clone_url: str, target_dir: str):
        super().__init__()
        self.clone_url = clone_url
        self.target_dir = target_dir

    def run(self) -> None:
        try:
            service = GitService(log=self.log_message.emit)
            service._run(
                ["clone", "--progress", str(self.clone_url), str(self.target_dir)],
                timeout=600,
                capture_progress=True,
                progress_task="克隆仓库",
            )
            self.completed.emit(str(self.target_dir))
        except Exception as exc:  # pragma: no cover
            self.failed.emit(str(exc))

