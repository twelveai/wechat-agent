from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any


def default_workspace() -> Path:
    return Path.cwd() / ".wechat-agent"


def timestamp_run_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def ensure_workspace(workspace: Path, update_gitignore: bool = False) -> Path:
    workspace = workspace.resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "work").mkdir(exist_ok=True)
    if update_gitignore:
        ensure_gitignore_entry(Path.cwd(), workspace)
    return workspace


def ensure_gitignore_entry(project_root: Path, workspace: Path) -> None:
    try:
        relative = workspace.resolve().relative_to(project_root.resolve())
        entry = relative.as_posix().rstrip("/") + "/"
    except ValueError:
        return

    gitignore = project_root / ".gitignore"
    existing = gitignore.read_text(encoding="utf-8").splitlines() if gitignore.exists() else []
    if entry not in existing:
        with gitignore.open("a", encoding="utf-8", newline="\n") as fh:
            if existing and existing[-1] != "":
                fh.write("\n")
            fh.write(entry + "\n")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def restrict_owner_only(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
