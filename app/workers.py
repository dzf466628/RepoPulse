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

    def __init__(self, projects: list[ProjectConfig], ignore_github_failure: bool = False):
        super().__init__()
        self.projects = projects
        self.ignore_github_failure = ignore_github_failure

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
