# Copyright (C) 2026 dudu <https://duadu.cc>
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RemoteConfig:
    kind: str
    url: str = ""
    service_url: str = ""
    path: str = ""
    branch: str = ""
    auth_method: str = "auto"
    username: str = ""
    secret: str = ""
    ssh_key_path: str = ""
    enabled: bool = True
    label: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "url": self.url,
            "service_url": self.service_url,
            "path": self.path,
            "branch": self.branch,
            "auth_method": self.auth_method,
            "username": self.username,
            "secret": self.secret,
            "ssh_key_path": self.ssh_key_path,
            "enabled": self.enabled,
            "label": self.label,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], fallback_kind: str) -> "RemoteConfig":
        return cls(
            kind=str(data.get("kind") or fallback_kind),
            url=str(data.get("url") or ""),
            service_url=str(data.get("service_url") or ""),
            path=str(data.get("path") or ""),
            branch=str(data.get("branch") or ""),
            auth_method=str(data.get("auth_method") or "auto"),
            username=str(data.get("username") or ""),
            secret=str(data.get("secret") or ""),
            ssh_key_path=str(data.get("ssh_key_path") or ""),
            enabled=bool(data.get("enabled", True)),
            label=str(data.get("label") or ""),
        )


@dataclass
class ProjectConfig:
    project_id: str
    name: str
    workspace_path: str
    default_branch: str = ""
    remotes: dict[str, RemoteConfig] = field(default_factory=dict)
    card_order: list[str] = field(default_factory=list)
    sync_enabled: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "name": self.name,
            "workspace_path": self.workspace_path,
            "default_branch": self.default_branch,
            "remotes": {key: value.to_dict() for key, value in self.remotes.items()},
            "card_order": list(self.card_order),
            "sync_enabled": self.sync_enabled,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProjectConfig":
        raw_remotes = data.get("remotes") or {}
        remotes = {
            str(key): RemoteConfig.from_dict(value or {}, str(key))
            for key, value in raw_remotes.items()
        }
        return cls(
            project_id=str(data.get("project_id") or ""),
            name=str(data.get("name") or "未命名项目"),
            workspace_path=str(data.get("workspace_path") or data.get("local_path") or ""),
            default_branch=str(data.get("default_branch") or ""),
            remotes=remotes,
            card_order=[str(item) for item in (data.get("card_order") or []) if str(item)],
            sync_enabled=bool(data.get("sync_enabled", False)),
        )
