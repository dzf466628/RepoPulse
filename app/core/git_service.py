from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import threading
import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

from app.models import ProjectConfig, RemoteConfig


class GitCommandError(RuntimeError):
    pass


def _format_git_date(raw: str) -> str:
    """把 git --date=iso-strict 的 '2026-09-20T16:05:09+08:00' 显示成 '2026-09-20 16:05:09'。"""
    text = (raw or "").strip().replace("T", " ")
    m = re.match(r"(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}(?::\d{2})?)", text)
    return m.group(1) if m else (raw or "")


# 全量储存镜像时排除的临时/可再生成目录（任意层级同名目录都会被排除）
_FULL_EXCLUDE_DIRS = [
    ".git", ".venv", "venv", "env", ".tox",
    "node_modules", "__pycache__",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".cache",
    ".idea", ".vscode", ".vs",
    "build", "dist", "out", "target",
]
# 全量储存镜像时排除的临时/可再生成文件
_FULL_EXCLUDE_FILES = [
    "*.pyc", "*.pyo", "*.log", "*.tmp", "*.bak",
    "Thumbs.db", ".DS_Store", "*.swp",
]

# 新项目首次提交时自动生成的 .gitignore（避免把 .venv/node_modules 等临时目录纳入版本库）
_DEFAULT_GITIGNORE = """\
# RepoPulse 自动生成
# Python
__pycache__/
*.py[cod]
*.egg-info/
.venv/
venv/
env/
.pytest_cache/
.mypy_cache/
.ruff_cache/
.tox/

# Node
node_modules/
npm-debug.log*

# 构建产物
build/
dist/
out/
target/
*.spec

# IDE / 编辑器
.idea/
.vscode/
.vs/
*.swp

# 系统
.DS_Store
Thumbs.db

# 日志与临时文件
*.log
*.tmp
*.bak
"""


class GitService:
    def __init__(self, log: Callable[[str], None] | None = None, progress: Callable[[str, int, int, str], None] | None = None):
        self.log = log or (lambda _message: None)
        self.progress = progress or (lambda _task, _current, _total, _detail: None)
        self.git = shutil.which("git") or "git"

    def _run(
        self,
        args: list[str],
        cwd: str | Path | None = None,
        timeout: int = 20,
        extra_env: dict[str, str] | None = None,
        capture_progress: bool = False,
        progress_task: str = "",
    ) -> str:
        """执行 Git 命令，可选捕获进度输出"""
        if capture_progress and progress_task:
            return self._run_with_progress(args, cwd, timeout, extra_env, progress_task)
        
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        if extra_env:
            env.update(extra_env)
        command = [self.git, *args]
        try:
            result = subprocess.run(
                command,
                cwd=str(cwd) if cwd else None,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                env=env,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired as exc:
            raise GitCommandError(f"命令超时（{timeout} 秒）：{' '.join(args)}") from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            # A stale local proxy should not make GitHub appear offline forever.
            # Retry only when Git explicitly reports the loopback proxy refusal;
            # the user's global Git configuration remains untouched.
            if "127.0.0.1:11304" in detail:
                direct_command = [self.git, "-c", "http.https://github.com.proxy=", *args]
                try:
                    direct_result = subprocess.run(
                        direct_command,
                        cwd=str(cwd) if cwd else None,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=timeout,
                        env=env,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                except subprocess.TimeoutExpired:
                    raise GitCommandError(f"直连超时（{timeout} 秒），已跳过：{' '.join(args)}")
                if direct_result.returncode == 0:
                    return direct_result.stdout.strip()
                detail = (direct_result.stderr or direct_result.stdout).strip()
            raise GitCommandError(detail or f"Git 返回错误码 {result.returncode}")
        return result.stdout.strip()

    def _run_with_progress(
        self,
        args: list[str],
        cwd: str | Path | None = None,
        timeout: int = 20,
        extra_env: dict[str, str] | None = None,
        progress_task: str = "",
    ) -> str:
        """执行带进度的 Git 命令（clone/fetch --progress），实时解析 stderr 进度。

        stdout、stderr 各用一个独立线程读取，既避免管道写满阻塞，也不和
        communicate() 抢同一根管道；进度只从 stderr 解析，失败时保留完整 stderr。
        """
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        if extra_env:
            env.update(extra_env)
        command = [self.git, *args]
        process = subprocess.Popen(
            command,
            cwd=str(cwd) if cwd else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        stdout_chunks: list[str] = []
        stderr_chunks: list[str] = []
        progress_re = re.compile(
            r"(Receiving|Compressing|Resolving|Counting|Writing)\s+objects:\s+(\d+)%\s+\((\d+)/(\d+)\)"
        )

        def pump_stdout() -> None:
            for line in process.stdout:
                stdout_chunks.append(line)

        def pump_stderr() -> None:
            for line in process.stderr:
                stderr_chunks.append(line)
                match = progress_re.search(line)
                if match:
                    stage = match.group(1)
                    percentage = int(match.group(2))
                    current = int(match.group(3))
                    total = int(match.group(4))
                    self.progress(
                        progress_task,
                        current,
                        total,
                        f"{stage} objects: {percentage}% ({current}/{total})",
                    )

        out_thread = threading.Thread(target=pump_stdout, daemon=True)
        err_thread = threading.Thread(target=pump_stderr, daemon=True)
        out_thread.start()
        err_thread.start()

        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
            raise GitCommandError(f"命令超时（{timeout} 秒）：{' '.join(args)}")
        out_thread.join(timeout=5)
        err_thread.join(timeout=5)

        if process.returncode != 0:
            detail = "".join(stderr_chunks).strip() or "".join(stdout_chunks).strip()
            raise GitCommandError(detail or f"Git 返回错误码 {process.returncode}")
        return "".join(stdout_chunks).strip()

    def _github_reachable(self) -> bool:
        """快速探测本地 GitHub 代理端口是否开启（2 秒），不通就跳过 GitHub。"""
        import socket
        try:
            with socket.create_connection(("127.0.0.1", 11304), timeout=2):
                return True
        except OSError:
            return False

    def _auth_env(self, remote: RemoteConfig) -> dict[str, str]:
        """为单次 Git 命令准备认证环境，不把密码拼进仓库地址。"""
        method = (remote.auth_method or "auto").lower()
        env: dict[str, str] = {}
        if method == "ssh" and remote.ssh_key_path:
            key_path = Path(remote.ssh_key_path).expanduser()
            if key_path.is_file():
                env["GIT_SSH_COMMAND"] = f'ssh -i "{key_path}" -o IdentitiesOnly=yes'
        if method in {"https", "auto"} and remote.secret:
            username = remote.username or "x-access-token"
            raw = f"{username}:{remote.secret}".encode("utf-8")
            token = base64.b64encode(raw).decode("ascii")
            env["GIT_CONFIG_COUNT"] = "1"
            env["GIT_CONFIG_KEY_0"] = "http.extraHeader"
            env["GIT_CONFIG_VALUE_0"] = f"Authorization: Basic {token}"
        return env

    def _mask_url(self, url: str) -> str:
        return re.sub(r"(https?://)([^/@]+)@", r"\1***@", url)

    def repository_url_for_project(self, remote: RemoteConfig, project_name: str) -> str:
        """按服务账号和项目名生成仓库克隆地址。"""
        if remote.url:
            return remote.url
        repo_name = project_name.strip().removesuffix(".git")
        owner = remote.username.strip()
        if not repo_name or not owner:
            return ""
        if remote.kind == "github":
            return f"https://github.com/{owner}/{urllib.parse.quote(repo_name)}.git"
        base = (remote.service_url or "").rstrip("/")
        if not base:
            return ""
        return f"{base}/{urllib.parse.quote(owner)}/{urllib.parse.quote(repo_name)}.git"

    def local_channel_path(self, remote: RemoteConfig, project: ProjectConfig) -> Path:
        """Resolve a global Local Git collection to this project's directory."""
        base = Path(remote.path).expanduser() if remote.path else Path()
        if not remote.path:
            return base
        # Older RepoPulse versions persisted ``E:/git/<project>`` back into
        # the shared channel config after creating one project. Treat that
        # value as a legacy project path and recover the collection root.
        if base.name.casefold() == project.name.casefold() and (base / ".git").is_dir():
            base = base.parent
        return base / project.name

    def _request_json(
        self,
        url: str,
        remote: RemoteConfig,
        method: str = "GET",
        payload: dict | None = None,
    ) -> object:
        headers = {"Accept": "application/json", "User-Agent": "RepoPulse"}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        if remote.kind == "github":
            headers["Authorization"] = f"Bearer {remote.secret}"
            headers["X-GitHub-Api-Version"] = "2022-11-28"
        else:
            headers["Authorization"] = f"token {remote.secret}"
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(url, headers=headers, method=method, data=body)
        proxy_url = "http://127.0.0.1:11304"
        if remote.kind == "github":
            # GitHub API 必须显式走本地代理，否则 urllib 直连会被墙卡死
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
            )
        else:
            # NAS / 局域网 Gitea 直连，绕过系统代理
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=20) as response:
                raw = response.read()
                return json.loads(raw.decode("utf-8")) if raw else {}
        except urllib.error.HTTPError as exc:
            if remote.kind != "github" and exc.code in {401, 403} and remote.username and remote.secret:
                basic = base64.b64encode(f"{remote.username}:{remote.secret}".encode("utf-8")).decode("ascii")
                headers["Authorization"] = f"Basic {basic}"
                # Preserve the original HTTP method and JSON body when falling
                # back from Gitea token auth to username/password Basic auth.
                retry = urllib.request.Request(url, headers=headers, method=method, data=body)
                try:
                    with opener.open(retry, timeout=20) as response:
                        raw = response.read()
                        return json.loads(raw.decode("utf-8")) if raw else {}
                except urllib.error.HTTPError as retry_exc:
                    detail = retry_exc.read().decode("utf-8", errors="replace")
                    raise GitCommandError(f"服务接口返回 {retry_exc.code}：{detail[:240]}") from retry_exc
            detail = exc.read().decode("utf-8", errors="replace")
            raise GitCommandError(f"服务接口返回 {exc.code}：{detail[:240]}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise GitCommandError(f"无法连接 Git 服务：{exc}") from exc

    def discover_host_repositories(self, remote: RemoteConfig) -> list[dict]:
        """读取 GitHub 或 Gitea 账号下的仓库，供添加渠道时选择。"""
        if remote.kind == "github":
            payload = self._request_json("https://api.github.com/user/repos?per_page=100&sort=updated", remote)
        elif remote.kind in {"nas", "gitea"}:
            base = (remote.service_url or "").rstrip("/")
            if not base:
                raise GitCommandError("未填写 Gitea 服务地址")
            try:
                payload = self._request_json(f"{base}/api/v1/user/repos?limit=50&page=1", remote)
            except GitCommandError:
                encoded_user = urllib.parse.quote(remote.username.strip(), safe="")
                payload = self._request_json(f"{base}/api/v1/users/{encoded_user}/repos?limit=50&page=1", remote)
        else:
            raise GitCommandError("该 Git 类型暂不支持自动读取仓库列表")
        if not isinstance(payload, list):
            raise GitCommandError("Git 服务返回的数据格式不正确")
        repositories: list[dict] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            clone_url = str(item.get("clone_url") or item.get("http_url") or "").strip()
            ssh_url = str(item.get("ssh_url") or "").strip()
            repositories.append({
                "name": name,
                "full_name": str(item.get("full_name") or name),
                "url": clone_url or ssh_url,
                "clone_url": clone_url or ssh_url,
                "ssh_url": ssh_url,
                "default_branch": str(item.get("default_branch") or ""),
                "description": str(item.get("description") or ""),
            })
        repositories.sort(key=lambda item: item["name"].casefold())
        return repositories

    def create_host_repository(self, remote: RemoteConfig, project_name: str) -> str:
        """通过服务 API 创建空远程仓库，并返回可用于 Git 的 HTTPS 地址。"""
        name = project_name.strip().removesuffix(".git")
        if not name:
            raise GitCommandError("项目名称不能为空")
        if remote.kind == "github":
            endpoint = "https://api.github.com/user/repos"
            payload = {"name": name, "private": True, "auto_init": False}
        elif remote.kind in {"nas", "gitea"}:
            base = (remote.service_url or "").rstrip("/")
            if not base:
                raise GitCommandError(f"{remote.label or remote.kind}：未配置服务地址")
            endpoint = f"{base}/api/v1/user/repos"
            payload = {"name": name, "private": True, "auto_init": False}
        else:
            raise GitCommandError(f"{remote.label or remote.kind}：暂不支持通过接口创建远程仓库")
        self.log(f"创建远程仓库：{remote.label or remote.kind} / {name}")
        response = self._request_json(endpoint, remote, method="POST", payload=payload)
        if not isinstance(response, dict):
            raise GitCommandError(f"{remote.label or remote.kind}：服务返回的数据格式不正确")
        clone_url = str(response.get("clone_url") or response.get("http_url") or "").strip()
        if not clone_url:
            clone_url = self.repository_url_for_project(remote, name)
        if not clone_url:
            raise GitCommandError(f"{remote.label or remote.kind}：未返回仓库地址")
        return clone_url

    def delete_host_repository(self, remote: RemoteConfig, project_name: str) -> None:
        """先检查远程仓库列表；不存在时视为已删除。"""
        if not self.host_repository_exists(remote, project_name):
            self.log(f"{remote.label or remote.kind}：未找到项目“{project_name}”，视为已删除")
            return
        name = urllib.parse.quote(project_name.strip().removesuffix(".git"), safe="")
        owner = urllib.parse.quote(remote.username.strip(), safe="")
        if remote.kind == "github":
            endpoint = f"https://api.github.com/repos/{owner}/{name}"
        elif remote.kind in {"nas", "gitea"}:
            base = (remote.service_url or "").rstrip("/")
            if not base:
                raise GitCommandError(f"{remote.label or remote.kind}：未配置服务地址")
            endpoint = f"{base}/api/v1/repos/{owner}/{name}"
        else:
            raise GitCommandError(f"{remote.label or remote.kind}：暂不支持删除远程仓库")
        self.log(f"删除远程仓库：{remote.label or remote.kind} / {project_name}")
        self._request_json(endpoint, remote, method="DELETE")

    def host_repository_exists(self, remote: RemoteConfig, project_name: str) -> bool:
        """先读取服务仓库列表，确认项目真实存在后再允许删除。"""
        wanted = project_name.strip().removesuffix(".git").casefold()
        return any(str(item.get("name") or "").casefold() == wanted for item in self.discover_host_repositories(remote))

    def delete_project(self, project: ProjectConfig) -> dict:
        """删除项目的本地目录、Local Git 目录和远程服务仓库。"""
        errors: list[str] = []
        deleted_paths: set[str] = set()
        protected_root = Path(__file__).resolve().parents[2]
        for remote in project.remotes.values():
            label = remote.label or remote.kind
            try:
                if remote.kind == "local":
                    target = self.local_channel_path(remote, project) if remote.path else None
                    if target and target.exists():
                        if target.resolve() == protected_root.resolve():
                            raise GitCommandError("拒绝删除 RepoPulse 软件目录")
                        path_key = str(target.resolve()).casefold()
                        if path_key not in deleted_paths:
                            self._remove_tree(target)
                            deleted_paths.add(path_key)
                            self.log(f"已删除 {label} 目录：{target}")
                elif remote.kind in {"github", "nas", "gitea"}:
                    self.delete_host_repository(remote, project.name)
                else:
                    raise GitCommandError("暂不支持自动删除此类型远程仓库")
            except (GitCommandError, OSError) as exc:
                errors.append(f"{label}：{exc}")
        workspace = Path(project.workspace_path).expanduser()
        try:
            if workspace.exists():
                if workspace.resolve() == protected_root.resolve():
                    raise GitCommandError("拒绝删除 RepoPulse 软件目录")
                path_key = str(workspace.resolve()).casefold()
                if path_key not in deleted_paths:
                    self._remove_tree(workspace)
                    self.log(f"已删除开发目录：{workspace}")
        except (GitCommandError, OSError) as exc:
            errors.append(f"开发目录：{exc}")
        return {"project_id": project.project_id, "project": project, "errors": errors}

    @staticmethod
    def _remove_tree(path: Path) -> None:
        """Remove a Windows Git tree even when objects are marked read-only."""
        def clear_readonly(func, failed_path, _exc_info):
            os.chmod(failed_path, stat.S_IWRITE)
            func(failed_path)

        shutil.rmtree(path, onerror=clear_readonly)

    def prepare_project(self, project: ProjectConfig, progress: Callable[[], None] | None = None) -> dict:
        """创建或接入本地项目，并准备 Local Git 与远程仓库。"""
        source = Path(project.workspace_path).expanduser()
        for remote in project.remotes.values():
            if remote.kind in {"github", "nas", "gitea"}:
                if not remote.username.strip():
                    raise GitCommandError(f"{remote.label or remote.kind}：未配置账号或用户名")
                if not remote.secret:
                    raise GitCommandError(f"{remote.label or remote.kind}：未配置 Token 或密码")
        source.mkdir(parents=True, exist_ok=True)
        branch = project.default_branch or "main"
        if not (source / ".git").is_dir():
            self.log(f"初始化项目 Git：{source}")
            self._run(["init", "-b", branch], cwd=source)
        # 首次提交前补一份 .gitignore，避免把 .venv/node_modules 等临时目录纳入版本库
        gitignore = source / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text(_DEFAULT_GITIGNORE, encoding="utf-8")
        has_head = False
        try:
            has_head = bool(self._run(["rev-parse", "--verify", "HEAD"], cwd=source))
        except GitCommandError:
            pass
        if not has_head:
            if not any(item.name not in {".git", ".gitignore"} for item in source.iterdir()):
                (source / "README.md").write_text(f"# {project.name}\n", encoding="utf-8")
            
            # 扫描大文件（>100MB），自动排除避免提交到 Git
            LARGE_FILE_THRESHOLD = 100 * 1024 * 1024  # 100 MB
            large_files = []
            self.log("正在扫描项目文件（>100MB 的大文件将自动排除）...")
            self.progress("扫描大文件", 0, 0, "正在扫描项目文件...")
            
            file_count = 0
            try:
                for file_path in source.rglob('*'):
                    if not file_path.is_file():
                        continue
                    # 排除 .git 目录
                    try:
                        file_path.relative_to(source / ".git")
                        continue
                    except ValueError:
                        pass
                    
                    file_count += 1
                    # 每扫描 500 个文件更新一次进度
                    if file_count % 500 == 0:
                        self.log(f"已扫描 {file_count} 个文件...")
                        self.progress("扫描大文件", 0, 0, f"已扫描 {file_count} 个文件...")
                    
                    try:
                        if file_path.stat().st_size > LARGE_FILE_THRESHOLD:
                            large_files.append(file_path.relative_to(source))
                    except OSError:
                        pass
            except Exception:
                pass  # 扫描失败不影响后续流程
            
            self.log(f"扫描完成，共 {file_count} 个文件")
            self.progress("扫描大文件", 1, 1, f"扫描完成，共 {file_count} 个文件")
            
            if large_files:
                # 追加大文件到 .gitignore
                gitignore_content = gitignore.read_text(encoding="utf-8")
                gitignore_content += "\n\n# 大文件自动排除（>100MB）\n"
                for lf in large_files:
                    gitignore_content += f"/{lf.as_posix()}\n"
                gitignore.write_text(gitignore_content, encoding="utf-8")
                
                large_files_names = [lf.name for lf in large_files[:5]]
                if len(large_files) > 5:
                    large_files_names.append(f"... 等 {len(large_files)} 个文件")
                self.log(f"已自动排除 {len(large_files)} 个大文件（>100MB）：{', '.join(large_files_names)}")
            
            # 大目录（含 node_modules 等）add 可能超过默认 20s，给足时间
            self._run(["add", "-A"], cwd=source, timeout=180)
            try:
                self._run(["config", "user.name"], cwd=source)
            except GitCommandError:
                self._run(["config", "user.name", "RepoPulse"], cwd=source)
            try:
                self._run(["config", "user.email"], cwd=source)
            except GitCommandError:
                self._run(["config", "user.email", "repopulse@local"], cwd=source)
            self._run(["commit", "-m", "chore: initialize project"], cwd=source, timeout=180)
        if progress:
            progress()

        errors: list[str] = []
        for key, remote in project.remotes.items():
            label = remote.label or remote.kind
            remote.branch = remote.branch or branch
            if remote.kind == "github" and not self._github_reachable():
                self.log(f"{label}：代理未开启，已跳过（不影响本地/NAS）")
                errors.append(f"{label}：代理未开启，已跳过")
                if progress:
                    progress()
                continue
            try:
                if remote.kind == "local":
                    target = self.local_channel_path(remote, project) if remote.path else None
                    if target is None or not str(target):
                        raise GitCommandError("未配置本地镜像目录")
                    if target.exists() and any(target.iterdir()) and not (target / ".git").is_dir():
                        raise GitCommandError(f"目标目录已存在且不是 RepoPulse 创建的 Git 目录：{target}")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if not (target / ".git").is_dir():
                        self.log(f"创建 {label} 项目目录：{target}")
                        # Source and Local Git may be on different Windows drives;
                        # --local relies on same-filesystem hardlinks and fails there.
                        self._run(
                            ["clone", "--no-local", "--progress", str(source), str(target)],
                            timeout=600,
                            capture_progress=True,
                            progress_task="克隆仓库"
                        )
                    else:
                        self.log(f"{label} 项目目录已存在，继续使用：{target}")
                    # Keep the configured Local Git path as the shared
                    # collection root. The project-specific target is derived
                    # by local_channel_path() whenever it is needed.
                else:
                    wanted = project.name.casefold()
                    existing = None
                    self.log(f"检查已有远程仓库：{label} / {project.name}")
                    try:
                        existing = next(
                            (
                                item
                                for item in self.discover_host_repositories(remote)
                                if str(item.get("name") or "").casefold() == wanted
                            ),
                            None,
                        )
                    except GitCommandError:
                        # If repository discovery is unavailable, retain the
                        # normal create path and surface its actual error.
                        existing = None
                    if existing:
                        target_url = str(
                            existing.get("clone_url")
                            or existing.get("url")
                            or self.repository_url_for_project(remote, project.name)
                        )
                        self.log(f"复用已有仓库：{label} / {project.name}")
                    else:
                        target_url = self.create_host_repository(remote, project.name)
                    remote_name = key.replace("-", "_") or remote.kind
                    try:
                        self._run(["remote", "remove", remote_name], cwd=source)
                    except GitCommandError:
                        pass
                    self._run(["remote", "add", remote_name, target_url], cwd=source)
                    self._run(
                        ["push", remote_name, f"HEAD:refs/heads/{branch}"],
                        cwd=source,
                        timeout=90,
                        extra_env=self._auth_env(remote),
                    )
                    self.log(f"{label} 项目已创建并推送：{self._mask_url(target_url)}")
            except GitCommandError as exc:
                errors.append(f"{label}：{exc}")
            finally:
                if progress:
                    progress()
        return {"project_id": project.project_id, "project": project, "errors": errors}

    def repository_info(self, local_path: str | Path) -> dict:
        """读取本地仓库的基础信息，用于一键添加仓库。"""
        path = Path(local_path).expanduser()
        top_level = self._run(["rev-parse", "--show-toplevel"], cwd=path)
        branch = ""
        try:
            branch = self._run(["symbolic-ref", "--short", "-q", "HEAD"], cwd=path)
        except GitCommandError:
            branch = ""
        remote_output = self._run(["remote", "-v"], cwd=path)
        remotes: list[dict] = []
        seen: set[tuple[str, str]] = set()
        for line in remote_output.splitlines():
            parts = line.split()
            if len(parts) < 3 or parts[2] != "(fetch)":
                continue
            name, url = parts[0], parts[1]
            if (name, url) in seen:
                continue
            seen.add((name, url))
            lowered = f"{name} {url}".lower()
            if "github.com" in lowered:
                kind, label = "github", "GitHub"
            elif "gitlab.com" in lowered:
                kind, label = "gitlab", "GitLab"
            elif "gitee.com" in lowered:
                kind, label = "gitee", "Gitee"
            elif "bitbucket.org" in lowered:
                kind, label = "bitbucket", "Bitbucket"
            elif "dev.azure.com" in lowered or "visualstudio.com" in lowered:
                kind, label = "azure_devops", "Azure DevOps"
            elif "codeberg.org" in lowered:
                kind, label = "codeberg", "Codeberg"
            elif "gitea" in lowered:
                kind, label = "gitea", "Gitea / 自建 Git"
            elif any(token in lowered for token in ("nas", ".local", "192.168.", "10.")):
                kind, label = "nas", "NAS Gitea"
            else:
                kind, label = "custom", name
            remotes.append({"name": name, "url": url, "kind": kind, "label": label, "branch": branch})
        return {
            "name": Path(top_level).name,
            "path": top_level,
            "branch": branch,
            "remotes": remotes,
        }

    def discover_repositories(self, root_path: str | Path) -> list[dict]:
        """支持选择单个仓库，或选择包含多个项目仓库的父目录。"""
        root = Path(root_path).expanduser()
        if not root.is_dir():
            raise GitCommandError("选择的路径不是有效目录")
        try:
            return [self.repository_info(root)]
        except GitCommandError:
            pass

        candidates: list[Path] = []
        for child in sorted(root.iterdir(), key=lambda item: item.name.casefold()):
            if child.is_dir() and (child / ".git").exists():
                candidates.append(child)
        if not candidates:
            raise GitCommandError("目录本身不是 Git 仓库，且里面没有找到包含 .git 的项目目录")

        repositories = []
        for candidate in candidates:
            try:
                repositories.append(self.repository_info(candidate))
            except GitCommandError:
                continue
        if not repositories:
            raise GitCommandError("没有找到可读取的 Git 项目")
        return repositories

    def local_status(self, project: ProjectConfig) -> dict:
        path = Path(project.workspace_path).expanduser()
        result: dict = {
            "kind": "local",
            "label": "本地",
            "online": False,
            "path": str(path),
            "branch": "",
            "head": "",
            "upstream": "",
            "ahead": 0,
            "behind": 0,
            "modified": 0,
            "staged": 0,
            "untracked": 0,
            "conflicts": 0,
            "clean": False,
            "last_commit": {},
            "error": "",
        }
        if not path.exists():
            # 目录尚未创建（新建项目还没落盘）：视为"未创建"，点同步会自动 mkdir + init
            result["uncreated"] = True
            result["hint"] = "目录尚未创建，点击同步将自动创建并初始化 Git 仓库"
            return result
        # 识别"未创建"：不是 Git 仓库，或已 init 但还没有任何提交
        try:
            self._run(["rev-parse", "--show-toplevel"], cwd=path)
        except GitCommandError:
            result["uncreated"] = True
            result["hint"] = "尚未初始化 Git 仓库，点击同步将自动创建"
            return result
        try:
            self._run(["rev-parse", "--verify", "HEAD"], cwd=path)
        except GitCommandError:
            result["uncreated"] = True
            result["hint"] = "仓库尚未有任何提交，点击同步将自动初始化并首次提交"
            return result
        try:
            porcelain = self._run(["status", "--porcelain=v2", "--branch"], cwd=path)
            for line in porcelain.splitlines():
                if line.startswith("# branch.head "):
                    result["branch"] = line.removeprefix("# branch.head ")
                elif line.startswith("# branch.oid "):
                    result["head"] = line.removeprefix("# branch.oid ")
                elif line.startswith("# branch.upstream "):
                    result["upstream"] = line.removeprefix("# branch.upstream ")
                elif line.startswith("# branch.ab "):
                    match = re.search(r"\+(\d+)\s+-(\d+)", line)
                    if match:
                        result["ahead"] = int(match.group(1))
                        result["behind"] = int(match.group(2))
                elif line.startswith("1 ") or line.startswith("2 "):
                    xy = line[2:4]
                    if xy[0] != ".":
                        result["staged"] += 1
                    if xy[1] != ".":
                        result["modified"] += 1
                elif line.startswith("u "):
                    result["conflicts"] += 1
                elif line.startswith("? "):
                    result["untracked"] += 1
            commit = self._run(
                ["log", "-1", "--format=%H%x1f%ad%x1f%an%x1f%s", "--date=iso-strict"],
                cwd=path,
            )
            parts = commit.split("\x1f", 3)
            if len(parts) == 4:
                result["last_commit"] = {
                    "hash": parts[0],
                    "date": _format_git_date(parts[1]),
                    "author": parts[2],
                    "subject": parts[3],
                }
            result["online"] = True
            result["clean"] = not any(
                result[key] for key in ("modified", "staged", "untracked", "conflicts")
            )
        except GitCommandError as exc:
            result["error"] = str(exc)
        return result

    def _remote_head(self, remote: RemoteConfig, branch: str, url: str | None = None) -> tuple[str, str]:
        auth_env = self._auth_env(remote)
        target_url = url or remote.url
        if branch:
            output = self._run(
                ["ls-remote", target_url, f"refs/heads/{branch}"],
                timeout=10,
                extra_env=auth_env,
            )
            return (output.split()[0] if output else ""), branch
        output = self._run(
            ["ls-remote", "--symref", target_url, "HEAD"],
            timeout=20,
            extra_env=auth_env,
        )
        detected_branch = ""
        for line in output.splitlines():
            if line.startswith("ref: refs/heads/") and line.endswith(" HEAD"):
                detected_branch = line.split("refs/heads/", 1)[1].rsplit(" HEAD", 1)[0]
            elif line.endswith("\tHEAD") and not line.startswith("ref:"):
                return line.split()[0], detected_branch
        return "", detected_branch

    def remote_status(self, project: ProjectConfig, remote: RemoteConfig, local: dict, config_key: str = "") -> dict:
        effective_url = remote.url or self.repository_url_for_project(remote, project.name)
        result: dict = {
            "config_key": config_key,
            "kind": remote.kind,
            "label": remote.label or {
                "local": "本地 Git",
                "nas": "NAS Gitea",
                "github": "GitHub",
            }.get(remote.kind, "远程 Git"),
            "online": False,
            "url": effective_url,
            "branch": remote.branch or project.default_branch or local.get("branch", ""),
            "head": "",
            "ahead": None,
            "behind": None,
            "relation": "未检查",
            "last_commit": {},
            "error": "",
        }
        if remote.kind == "local":
            channel_path = self.local_channel_path(remote, project) if remote.path else None
            if channel_path is None or not str(channel_path):
                result["error"] = "未配置本地 Git 渠道路径"
                result["relation"] = "未配置"
                return result
            channel_project = ProjectConfig(
                project_id=project.project_id,
                name=project.name,
                workspace_path=str(channel_path),
                default_branch=remote.branch or project.default_branch,
            )
            channel_local = self.local_status(channel_project)
            result.update(channel_local)
            result["config_key"] = config_key
            result["kind"] = "local"
            result["label"] = remote.label or "本地 Git"
            result["relation"] = "当前 Git 渠道"
            result["path"] = str(channel_path)
            result["url"] = str(channel_path)
            if local.get("online") and result.get("online"):
                if local.get("head") == result.get("head"):
                    result["relation"] = "一致"
                    result["ahead"] = 0
                    result["behind"] = 0
                else:
                    result["ahead"] = 0
                    result["behind"] = 0
                    try:
                        counts = self._run(
                            ["rev-list", "--left-right", "--count", f"{local.get('head')}...{result.get('head')}"],
                            cwd=channel_path,
                        ).split()
                        if len(counts) == 2:
                            result["ahead"] = int(counts[0])
                            result["behind"] = int(counts[1])
                    except GitCommandError:
                        pass
                    result["relation"] = "版本不同"
            return result
        if not remote.enabled or not effective_url:
            result["relation"] = "未配置"
            return result
        try:
            branch = result["branch"]
            if branch in {"(detached)", "HEAD"}:
                branch = ""
            self.log(f"检查 {result['label']}：{self._mask_url(effective_url)}")
            result["head"], detected_branch = self._remote_head(remote, branch, effective_url)
            if detected_branch:
                result["branch"] = detected_branch
            result["online"] = bool(result["head"])
            if not result["head"]:
                # An API-visible repository with no refs is a newly created
                # empty repository, not a connection failure. Also recover
                # when the service reports a different default branch.
                try:
                    repository = next(
                        (
                            item
                            for item in self.discover_host_repositories(remote)
                            if str(item.get("name") or "").casefold() == project.name.casefold()
                        ),
                        None,
                    )
                except GitCommandError:
                    repository = None
                default_branch = str(repository.get("default_branch") or "") if repository else ""
                if default_branch and default_branch != branch:
                    result["branch"] = default_branch
                    result["head"], _ = self._remote_head(remote, default_branch, effective_url)
                if not result["head"] and repository is not None:
                    result["online"] = True
                    result["relation"] = "待首次同步"
                    return result
                if not result["head"]:
                    result["relation"] = "分支不存在"
                    return result
                result["online"] = True
            if local.get("head") and local["head"] == result["head"]:
                result["relation"] = "一致"
                result["last_commit"] = local.get("last_commit", {})
                return result
            # Fetch 到 FETCH_HEAD 只更新 Git 元数据，不改工作区，用于计算精确 ahead/behind。
            if not local.get("online"):
                result["relation"] = "已连接"
                return result
            path = Path(project.workspace_path)
            self._run(
                ["fetch", "--no-tags", "--quiet", effective_url, branch],
                cwd=path,
                timeout=20,
                extra_env=self._auth_env(remote),
            )
            counts = self._run(["rev-list", "--left-right", "--count", "HEAD...FETCH_HEAD"], cwd=path)
            parts = counts.split()
            if len(parts) == 2:
                result["ahead"] = int(parts[0])
                result["behind"] = int(parts[1])
                if result["ahead"] == 0 and result["behind"] == 0:
                    result["relation"] = "一致"
                elif result["ahead"] > 0 and result["behind"] == 0:
                    result["relation"] = "本地领先"
                elif result["ahead"] == 0 and result["behind"] > 0:
                    result["relation"] = "远程领先"
                else:
                    result["relation"] = "已分叉"
            commit = self._run(
                ["show", "-s", "--format=%H%x1f%ad%x1f%an%x1f%s", "--date=iso-strict", "FETCH_HEAD"],
                cwd=path,
            )
            parts = commit.split("\x1f", 3)
            if len(parts) == 4:
                result["last_commit"] = {
                    "hash": parts[0],
                    "date": _format_git_date(parts[1]),
                    "author": parts[2],
                    "subject": parts[3],
                }
        except GitCommandError as exc:
            error_msg = str(exc)
            # 检测仓库不存在的错误（404 Not Found）
            if any(keyword in error_msg.lower() for keyword in ["not found", "404", "does not exist", "repository not found"]):
                result["error"] = ""
                result["relation"] = "待创建"
                result["not_created"] = True  # 标记为未创建状态
            else:
                result["error"] = error_msg
                result["relation"] = "连接失败"
        return result

    def inspect_project(
        self,
        project: ProjectConfig,
        progress: Callable[[], None] | None = None,
        ignore_github_failure: bool = False,
        skip_github: set[str] | None = None,
    ) -> dict:
        local = self.local_status(project)
        if progress:
            progress()
        remotes = {}
        for key, remote in project.remotes.items():
            remote_url = remote.url or self.repository_url_for_project(remote, project.name)
            if (
                skip_github is not None
                and remote.kind == "github"
                and remote_url in skip_github
            ):
                remotes[key] = {
                    "label": remote.label or "Github",
                    "url": remote_url,
                    "relation": "未代理",
                    "online": False,
                    "ignored": True,
                    "error": "本次运行已跳过",
                }
            else:
                remotes[key] = self.remote_status(project, remote, local, key)
                if (
                    remote.kind == "github"
                    and remotes[key].get("error")
                    and skip_github is not None
                ):
                    skip_github.add(remote_url)
            if ignore_github_failure and remote.kind == "github" and remotes[key].get("error"):
                remotes[key]["relation"] = "未代理"
                remotes[key]["online"] = False
                remotes[key]["ignored"] = True
            if progress:
                progress()
        return {"project_id": project.project_id, "local": local, "remotes": remotes}

    def sync_project(
        self,
        project: ProjectConfig,
        progress: Callable[[], None] | None = None,
        full_sync: bool = False,
        on_channel_start: Callable[[str], None] | None = None,
        on_channel_finish: Callable[[str, bool], None] | None = None,
    ) -> dict:
        """将当前项目的已提交内容同步到每个已配置 Git 渠道。

        同步只处理已提交的 HEAD，不会自动提交工作区改动；本地渠道通过
        fetch/reset 更新目标工作副本，远程渠道通过一次性 push 完成同步。
        """
        source = Path(project.workspace_path).expanduser()
        if not source.is_dir():
            raise GitCommandError(f"本地项目目录不存在：{source}")
        self._run(["rev-parse", "--show-toplevel"], cwd=source)
        dirty = self._run(["status", "--porcelain"], cwd=source)
        if dirty:
            raise GitCommandError("工作区有未提交改动，请先提交后再同步")
        branch = project.default_branch or ""
        if not branch:
            try:
                branch = self._run(["symbolic-ref", "--short", "-q", "HEAD"], cwd=source)
            except GitCommandError:
                branch = "main"
        branch = branch or "main"
        results: list[dict] = []
        for key, remote in project.remotes.items():
            label = remote.label or remote.kind
            if remote.kind == "github" and not self._github_reachable():
                result = {"ok": False, "skipped": True,
                          "message": f"{label}：代理未开启，已跳过（不影响本地/NAS）"}
                result["key"] = key
                result["label"] = label
                results.append(result)
                if on_channel_finish:
                    on_channel_finish(key, False)
                if progress:
                    progress()
                continue
            if on_channel_start:
                on_channel_start(key)
            try:
                if remote.kind == "local":
                    result = self._sync_local_channel(source, branch, remote, project, label, full_sync=full_sync)
                else:
                    result = self._sync_remote_channel(source, branch, remote, project.name, label, full_sync=full_sync)
            except GitCommandError as exc:
                result = {"ok": False, "message": f"{label}：{exc}"}
            result["key"] = key
            result["label"] = label
            results.append(result)
            if on_channel_finish:
                on_channel_finish(key, bool(result.get("ok")))
            if progress:
                progress()
        return {"project_id": project.project_id, "results": results}

    def commit_project(self, project: ProjectConfig, message: str) -> dict:
        """Commit all current project changes before a requested sync."""
        source = Path(project.workspace_path).expanduser()
        if not source.is_dir():
            raise GitCommandError(f"本地项目目录不存在：{source}")
        self._run(["rev-parse", "--show-toplevel"], cwd=source)
        status = self._run(["status", "--porcelain"], cwd=source)
        if not status:
            return {"committed": False, "message": "没有待提交修改"}
        commit_message = message.strip() or f"更新项目：{project.name}"
        self._run(["add", "-A"], cwd=source)
        staged = self._run(["diff", "--cached", "--name-only"], cwd=source)
        if not staged:
            return {"committed": False, "message": "没有可提交的文件"}
        try:
            self._run(["config", "user.name"], cwd=source)
        except GitCommandError:
            self._run(["config", "user.name", "RepoPulse"], cwd=source)
        try:
            self._run(["config", "user.email"], cwd=source)
        except GitCommandError:
            self._run(["config", "user.email", "repopulse@local"], cwd=source)
        self._run(["commit", "-m", commit_message], cwd=source, timeout=60)
        head = self._run(["rev-parse", "HEAD"], cwd=source)
        return {
            "committed": True,
            "message": f"已提交 {len(staged.splitlines())} 个文件：{commit_message}",
            "head": head,
        }

    def _estimate_tree_size(self, root: Path) -> int:
        """统计目录下未被全量镜像排除规则忽略的文件总字节数；读取失败返回 0。"""
        total_size = 0
        try:
            for file_path in root.rglob("*"):
                if not file_path.is_file():
                    continue
                excluded = False
                for exclude_dir in _FULL_EXCLUDE_DIRS:
                    try:
                        file_path.relative_to(root / exclude_dir)
                        excluded = True
                        break
                    except ValueError:
                        continue
                if excluded:
                    continue
                try:
                    total_size += file_path.stat().st_size
                except OSError:
                    pass
        except OSError:
            return 0
        return total_size

    def _mirror_workspace_full(self, source: Path, target: Path) -> None:
        """用 robocopy 把整个工作区镜像到全量本地仓库（含素材大文件，排除临时文件）。

        robocopy 退出码 0-7 均为成功（0=无拷贝，1=有拷贝，2/3=额外文件等），
        >=8 才是真正的错误。被 /XD 排除的目录既不复制也不在镜像模式下被删除，
        因此 target 自身的 .git 会被保留。

        进度说明：robocopy 在管道重定向下只逐文件输出、且文本随系统语言本地化，
        无法稳定解析出“整体百分比”，因此这里使用不确定进度（脉冲）+ 体量日志，
        不伪造百分比；任务在后台线程执行，不会卡住界面。
        """
        total_size = self._estimate_tree_size(source)
        total_mb = total_size / (1024 * 1024) if total_size > 0 else 0
        if total_mb > 0:
            self.log(f"全量备份：准备复制约 {total_mb:.1f} MB")
        else:
            self.log("全量备份：开始镜像（大小未知）")
        self.progress("全量备份", 0, 0, "正在镜像文件，请稍候...")

        cmd = [
            "robocopy", str(source), str(target),
            "/MIR", "/NP", "/NFL", "/NDL", "/NJH", "/NJS",
            "/R:3", "/W:5",
            "/XD", *_FULL_EXCLUDE_DIRS,
            "/XF", *_FULL_EXCLUDE_FILES,
        ]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=600,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired as exc:
            raise GitCommandError("全量镜像超时（600 秒）") from exc

        if result.returncode >= 8:
            detail = (result.stderr or result.stdout or "").strip()
            raise GitCommandError(
                f"全量镜像拷贝失败（robocopy 退出码 {result.returncode}）{detail}"
            )
        self.progress("全量备份", 1, 1, "全量备份完成")
        self.log("全量备份：完成")

    def _sync_local_channel(
        self,
        source: Path,
        branch: str,
        remote: RemoteConfig,
        project: ProjectConfig,
        label: str,
        full_sync: bool = False,
    ) -> dict:
        target = self.local_channel_path(remote, project) if remote.path else None
        if target is None or not str(target):
            return {"ok": False, "message": f"{label}：未配置本地路径"}
        if target.resolve() == source.resolve():
            return {"ok": True, "message": f"{label}：与开发目录相同，已跳过"}
        target.mkdir(parents=True, exist_ok=True)
        try:
            self._run(["rev-parse", "--show-toplevel"], cwd=target)
        except GitCommandError:
            self._run(["init", "-q", "-b", branch], cwd=target)
        target_status = self._run(["status", "--porcelain"], cwd=target)
        tracked_dirty = [
            line for line in target_status.splitlines()
            if line and not line.startswith("?? ")
        ]
        # 普通同步：目标仓库有别人/历史留下的已跟踪改动时，保护起来不覆盖。
        # 全量同步：备份本就以 source 为准，下面会强制 reset 对齐代码，不跳过。
        if tracked_dirty and not full_sync:
            return {"ok": False, "message": f"{label}：目标目录有已跟踪文件改动，已跳过"}
        if target_status:
            self.log(f"{label}：保留目标目录中的未跟踪文件，继续同步")
        self.log(f"同步 {label}：{target}")
        self._run(["fetch", "--quiet", str(source), branch], cwd=target, timeout=45)
        if full_sync:
            # 全量储存：强制把已跟踪代码对齐到 source 最新提交（丢弃备份仓库自己的
            # 脏改动，未跟踪的素材文件不受影响），再把整个工作区（代码 + 素材大文件，
            # 排除临时/可再生成文件）镜像过来。target 自身的 .git 不参与镜像，保持不动。
            self._run(["reset", "--hard", "FETCH_HEAD"], cwd=target, timeout=45)
            self._mirror_workspace_full(source, target)
            self.log(f"{label}：已全量镜像工作区（含素材大文件）")
        else:
            self._run(["checkout", "-B", branch, "FETCH_HEAD"], cwd=target, timeout=45)
        return {"ok": True, "message": f"{label}：同步完成"}

    def _ensure_host_repository(
        self,
        remote: RemoteConfig,
        project_name: str,
        label: str,
    ) -> tuple[str, bool]:
        """同步前确保远程仓库存在，返回 (可推送地址, 是否本次新建)。

        - 已显式配置仓库地址（remote.url）时直接使用，不做发现/创建；
        - github / nas / gitea 渠道：先在账号下查找同名仓库，找不到就通过
          服务接口自动创建，保证“待创建”的远程渠道点一次同步即可建好。
        """
        if remote.url:
            return remote.url, False
        if remote.kind not in {"github", "nas", "gitea"}:
            return self.repository_url_for_project(remote, project_name), False

        name = project_name.strip().removesuffix(".git")
        wanted = name.casefold()
        existing = None
        try:
            existing = next(
                (
                    item
                    for item in self.discover_host_repositories(remote)
                    if str(item.get("name") or "").casefold() == wanted
                ),
                None,
            )
        except GitCommandError:
            # 列表接口不可用时保留新建路径，由创建接口抛出真实错误
            existing = None
        if existing:
            url = str(
                existing.get("clone_url")
                or existing.get("url")
                or self.repository_url_for_project(remote, name)
            )
            self.log(f"复用已有仓库：{label} / {name}")
            return url, False

        url = self.create_host_repository(remote, name)
        self.log(f"{label}：远程仓库不存在，已自动创建：{name}")
        return url, True

    def _sync_remote_channel(
        self,
        source: Path,
        branch: str,
        remote: RemoteConfig,
        project_name: str,
        label: str,
        full_sync: bool = False,
    ) -> dict:
        target_url, created = self._ensure_host_repository(remote, project_name, label)
        if not target_url:
            return {"ok": False, "message": f"{label}：未配置仓库地址"}
        self.log(f"同步 {label}：{self._mask_url(target_url)}")
        self._run(
            ["push", target_url, f"HEAD:refs/heads/{branch}"],
            cwd=source,
            timeout=90,
            extra_env=self._auth_env(remote),
        )
        if full_sync:
            self.log(f"{label}：已执行全量同步")
        if created:
            return {"ok": True, "message": f"{label}：已创建仓库并推送完成"}
        return {"ok": True, "message": f"{label}：同步完成"}

    def pull_to_local(self, project: ProjectConfig, progress: Callable[[], None] | None = None) -> list[dict]:
        """从在线远程拉取最新提交到本地开发目录（只允许快进，不覆盖本地提交）。

        拉取优先级：nas 优先，其他远程其次；本地领先时不产生任何改动。
        """
        source = Path(project.workspace_path).expanduser()
        if not source.is_dir():
            raise GitCommandError(f"本地项目目录不存在：{source}")
        self._run(["rev-parse", "--show-toplevel"], cwd=source)
        dirty = self._run(["status", "--porcelain"], cwd=source)
        if dirty:
            raise GitCommandError("工作区有未提交改动，请先提交或暂存后再拉取")
        branch = project.default_branch or ""
        if not branch:
            try:
                branch = self._run(["symbolic-ref", "--short", "-q", "HEAD"], cwd=source)
            except GitCommandError:
                branch = "main"
        branch = branch or "main"
        remotes = list(project.remotes.values())
        # nas 优先，其他远程其次，跳过 local 渠道
        remotes.sort(key=lambda r: 0 if r.kind == "nas" else 1)
        results: list[dict] = []
        for remote in remotes:
            if remote.kind == "local" or not remote.enabled:
                continue
            label = remote.label or remote.kind
            target_url = remote.url or self.repository_url_for_project(remote, project.name)
            try:
                self.log(f"拉取 {label}：{self._mask_url(target_url)}")
                self._run(
                    ["fetch", "--quiet", target_url, branch],
                    cwd=source,
                    timeout=20,
                    extra_env=self._auth_env(remote),
                )
                # 只快进：远程没有新提交时 merge 是 no-op；分叉时报错，不强行 reset
                self._run(
                    ["merge", "--ff-only", "FETCH_HEAD"],
                    cwd=source,
                    timeout=20,
                )
                results.append({"ok": True, "label": label, "message": f"{label}：已快进拉取最新"})
            except GitCommandError as exc:
                results.append({"ok": False, "label": label, "message": f"{label}：{exc}"})
            if progress:
                progress()
        return results
