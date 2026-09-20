from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
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


class GitService:
    def __init__(self, log: Callable[[str], None] | None = None):
        self.log = log or (lambda _message: None)
        self.git = shutil.which("git") or "git"

    def _run(
        self,
        args: list[str],
        cwd: str | Path | None = None,
        timeout: int = 20,
        extra_env: dict[str, str] | None = None,
    ) -> str:
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
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
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
                    with urllib.request.urlopen(retry, timeout=20) as response:
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
        has_head = False
        try:
            has_head = bool(self._run(["rev-parse", "--verify", "HEAD"], cwd=source))
        except GitCommandError:
            pass
        if not has_head:
            if not any(item.name != ".git" for item in source.iterdir()):
                (source / "README.md").write_text(f"# {project.name}\n", encoding="utf-8")
            self._run(["add", "-A"], cwd=source)
            try:
                self._run(["config", "user.name"], cwd=source)
            except GitCommandError:
                self._run(["config", "user.name", "RepoPulse"], cwd=source)
            try:
                self._run(["config", "user.email"], cwd=source)
            except GitCommandError:
                self._run(["config", "user.email", "repopulse@local"], cwd=source)
            self._run(["commit", "-m", "chore: initialize project"], cwd=source)
        if progress:
            progress()

        errors: list[str] = []
        for key, remote in project.remotes.items():
            label = remote.label or remote.kind
            remote.branch = remote.branch or branch
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
                        self._run(["clone", "--no-local", str(source), str(target)], timeout=60)
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
            result["error"] = "本地路径不存在"
            return result
        try:
            self._run(["rev-parse", "--show-toplevel"], cwd=path)
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
                    "date": parts[1],
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
                else:
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
                    "date": parts[1],
                    "author": parts[2],
                    "subject": parts[3],
                }
        except GitCommandError as exc:
            result["error"] = str(exc)
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
            try:
                if remote.kind == "local":
                    result = self._sync_local_channel(source, branch, remote, project, label, full_sync=full_sync)
                else:
                    target_url = remote.url or self.repository_url_for_project(remote, project.name)
                    result = self._sync_remote_channel(source, branch, remote, target_url, label, full_sync=full_sync)
            except GitCommandError as exc:
                result = {"ok": False, "message": f"{label}：{exc}"}
            result["key"] = key
            result["label"] = label
            results.append(result)
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
        if tracked_dirty:
            return {"ok": False, "message": f"{label}：目标目录有已跟踪文件改动，已跳过"}
        if target_status:
            self.log(f"{label}：保留目标目录中的未跟踪文件，继续同步")
        self.log(f"同步 {label}：{target}")
        self._run(["fetch", "--quiet", str(source), branch], cwd=target, timeout=45)
        self._run(["checkout", "-B", branch, "FETCH_HEAD"], cwd=target, timeout=45)
        if full_sync:
            self._run(["clean", "-fdx"], cwd=target, timeout=45)
        return {"ok": True, "message": f"{label}：同步完成"}

    def _sync_remote_channel(
        self,
        source: Path,
        branch: str,
        remote: RemoteConfig,
        target_url: str,
        label: str,
        full_sync: bool = False,
    ) -> dict:
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
        return {"ok": True, "message": f"{label}：同步完成"}
