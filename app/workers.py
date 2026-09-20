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

    def __init__(self, project: ProjectConfig, commit_message: str = "", full_sync: bool = False):
        super().__init__()
        self.project = project
        self.commit_message = commit_message
        self.full_sync = full_sync

    def run(self) -> None:
        try:
            service = GitService(log=self.log_message.emit)
            local = service.local_status(self.project)
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

    def __init__(self, project: ProjectConfig):
        super().__init__()
        self.project = project

    def run(self) -> None:
        try:
            service = GitService(log=self.log_message.emit)
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
