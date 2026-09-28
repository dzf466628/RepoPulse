# Copyright (C) 2026 dudu <https://duadu.cc>
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import json
import os
from pathlib import Path

from app.models import ProjectConfig, RemoteConfig


def default_store_path() -> Path:
    app_data = os.environ.get("APPDATA")
    base = Path(app_data) if app_data else Path.home() / ".config"
    return base / "GitStatusDesk" / "projects.json"


class ProjectStore:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else default_store_path()
        self.global_remotes: dict[str, RemoteConfig] = {}
        self.global_card_order: list[str] = []

    def load(self) -> list[ProjectConfig]:
        if not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        projects = [ProjectConfig.from_dict(item) for item in raw.get("projects", [])]
        raw_global = raw.get("git_channels") or raw.get("global_remotes") or {}
        if raw_global:
            self.global_remotes = {
                str(key): RemoteConfig.from_dict(value or {}, str(key))
                for key, value in raw_global.items()
            }
        else:
            # Migrate the old per-project channel configuration to one shared set.
            for project in projects:
                for key, remote in project.remotes.items():
                    if key not in self.global_remotes:
                        self.global_remotes[key] = remote
            if projects:
                first = projects[0]
                for key, remote in list(self.global_remotes.items()):
                    if remote.kind == "local" and remote.path:
                        path = Path(remote.path)
                        if path.name.casefold() == first.name.casefold():
                            remote.path = str(path.parent)
                    elif remote.kind in {"github", "nas", "gitea"}:
                        remote.url = ""
        self.global_card_order = [str(item) for item in (raw.get("card_order") or []) if str(item)]
        if not self.global_card_order:
            self.global_card_order = [str(item) for item in (projects[0].card_order if projects else []) if str(item)]
        local_remote = self.global_remotes.get("local")
        if local_remote and local_remote.path:
            local_path = Path(local_remote.path).expanduser()
            # Global Local Git stores the collection root, never one project.
            if (local_path / ".git").is_dir():
                local_remote.path = str(local_path.parent)
        for project in projects:
            project.remotes = self.global_remotes
        return projects

    def save(self, projects: list[ProjectConfig]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.global_remotes:
            for project in projects:
                for key, remote in project.remotes.items():
                    self.global_remotes.setdefault(key, remote)
        for project in projects:
            project.remotes = self.global_remotes
        payload = {
            "version": 2,
            "git_channels": {key: value.to_dict() for key, value in self.global_remotes.items()},
            "card_order": list(self.global_card_order),
            "projects": [project.to_dict() for project in projects],
        }
        temp_path = self.path.with_suffix(".tmp")
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_path.replace(self.path)
