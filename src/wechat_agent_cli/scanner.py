from __future__ import annotations

import csv
import os
import platform
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


WECHAT_PROCESS_NAMES = {"wechat.exe", "weixin.exe", "wechatappex.exe"}
DB_SUFFIXES = {".db"}
SIDE_SUFFIXES = ("-wal", "-shm")
ABSOLUTE_WINDOWS_PATH_RE = re.compile(r"[A-Za-z]:\\[^\r\n<>:\"|?*]+")


@dataclass(frozen=True)
class DatabaseCandidate:
    path: str
    family: str
    size: int
    has_wal: bool
    has_shm: bool
    account: str | None
    category: str | None
    name: str

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "family": self.family,
            "size": self.size,
            "has_wal": self.has_wal,
            "has_shm": self.has_shm,
            "account": self.account,
            "category": self.category,
            "name": self.name,
        }


@dataclass(frozen=True)
class ScanResult:
    platform: str
    processes: list[dict]
    roots: list[str]
    databases: list[DatabaseCandidate]

    def to_dict(self) -> dict:
        return {
            "platform": self.platform,
            "processes": self.processes,
            "roots": self.roots,
            "databases": [database.to_dict() for database in self.databases],
        }


def scan_environment(data_dirs: Iterable[Path] | None = None, max_depth: int = 7) -> ScanResult:
    roots = resolve_candidate_roots(data_dirs or [])
    databases: list[DatabaseCandidate] = []
    seen: set[Path] = set()
    for root in roots:
        for path in iter_database_files(root, max_depth=max_depth):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            databases.append(candidate_from_path(resolved))

    return ScanResult(
        platform=platform.platform(),
        processes=find_wechat_processes(),
        roots=[str(root) for root in roots],
        databases=sorted(databases, key=lambda item: item.path.lower()),
    )


def resolve_candidate_roots(data_dirs: Iterable[Path]) -> list[Path]:
    explicit = [path.expanduser() for path in data_dirs]
    if explicit:
        return [path for path in explicit if path.exists()]

    candidates: list[Path] = []
    env_vars = ["WECHAT_FILES_DIR", "WEIXIN_FILES_DIR"]
    for name in env_vars:
        value = os.environ.get(name)
        if value:
            candidates.append(Path(value).expanduser())

    candidates.extend(read_windows_weixin_config_roots())

    home = Path.home()
    documents = home / "Documents"
    documents = read_windows_documents_dir() or documents
    appdata = os.environ.get("APPDATA")
    local_appdata = os.environ.get("LOCALAPPDATA")

    candidates.extend(
        [
            documents / "WeChat Files",
            documents / "Weixin Files",
            documents / "xwechat_files",
            home / "WeChat Files",
            home / "Weixin Files",
            home / "xwechat_files",
        ]
    )
    if appdata:
        candidates.extend(
            [
                Path(appdata) / "Tencent" / "WeChat",
                Path(appdata) / "Tencent" / "Weixin",
                Path(appdata) / "Tencent" / "xwechat_files",
            ]
        )
    if local_appdata:
        candidates.extend(
            [
                Path(local_appdata) / "Tencent" / "WeChat",
                Path(local_appdata) / "Tencent" / "Weixin",
                Path(local_appdata) / "Tencent" / "xwechat",
            ]
        )

    existing: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        try:
            resolved = candidate.expanduser().resolve()
        except OSError:
            continue
        if resolved.exists() and resolved not in seen:
            existing.append(resolved)
            seen.add(resolved)
    return existing


def read_windows_documents_dir() -> Path | None:
    if os.name != "nt":
        return None
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "Personal")
    except OSError:
        return None
    expanded = os.path.expandvars(str(value))
    return Path(expanded).expanduser()


def read_windows_weixin_config_roots() -> list[Path]:
    if os.name != "nt":
        return []
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return []
    config_dir = Path(appdata) / "Tencent" / "xwechat" / "config"
    if not config_dir.exists():
        return []

    roots: list[Path] = []
    for path in config_dir.glob("*.ini"):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for candidate in config_paths_from_text(text):
            roots.extend(expand_weixin_root_candidate(candidate))
    return roots


def config_paths_from_text(text: str) -> list[Path]:
    paths: list[Path] = []
    for line in text.splitlines():
        stripped = line.strip().strip("\ufeff").strip()
        if stripped and not stripped.startswith(("#", ";", "[")):
            paths.append(Path(os.path.expandvars(stripped)))
    for match in ABSOLUTE_WINDOWS_PATH_RE.findall(text):
        paths.append(Path(os.path.expandvars(match.strip())))
    return paths


def expand_weixin_root_candidate(path: Path) -> list[Path]:
    path = path.expanduser()
    candidates = [path]
    if path.name.lower() != "xwechat_files":
        candidates.append(path / "xwechat_files")
    return candidates


def find_wechat_processes() -> list[dict]:
    if os.name != "nt":
        return []
    try:
        completed = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if completed.returncode != 0:
        return []

    processes: list[dict] = []
    for row in csv.reader(completed.stdout.splitlines()):
        if len(row) < 2:
            continue
        name = row[0]
        if name.lower() in WECHAT_PROCESS_NAMES:
            processes.append({"name": name, "pid": row[1]})
    return processes


def iter_database_files(root: Path, max_depth: int) -> Iterable[Path]:
    if not root.exists():
        return
    root = root.resolve()
    stack: list[tuple[Path, int]] = [(root, 0)]
    while stack:
        current, depth = stack.pop()
        try:
            children = list(current.iterdir())
        except OSError:
            continue
        for child in children:
            name = child.name
            if child.is_dir():
                if depth < max_depth and not should_skip_dir(name):
                    stack.append((child, depth + 1))
                continue
            if is_database_file(child):
                yield child


def should_skip_dir(name: str) -> bool:
    lower = name.lower()
    return lower in {"backup", "backupfiles", "file", "image", "video", "cache", "temp", "tmp"}


def is_database_file(path: Path) -> bool:
    name = path.name.lower()
    if path.suffix.lower() not in DB_SUFFIXES:
        return False
    if name.endswith(SIDE_SUFFIXES):
        return False
    return True


def candidate_from_path(path: Path) -> DatabaseCandidate:
    family = infer_database_family(path)
    return DatabaseCandidate(
        path=str(path),
        family=family,
        size=safe_size(path),
        has_wal=Path(str(path) + "-wal").exists(),
        has_shm=Path(str(path) + "-shm").exists(),
        account=infer_account(path),
        category=infer_category(path),
        name=path.name,
    )


def infer_database_family(path: Path) -> str:
    parts = {part.lower() for part in path.parts}
    name = path.name.lower()
    if "db_storage" in parts or "xwechat_files" in parts:
        return "wechat_4"
    if "msg" in parts or name.startswith("msg"):
        return "wechat_3_msg"
    if name == "micromsg.db":
        return "wechat_contacts"
    if name == "session.db":
        return "wechat_session"
    return "unknown"


def infer_account(path: Path) -> str | None:
    for part in path.parts:
        lower = part.lower()
        if lower.startswith("wxid_"):
            return normalize_account_part(part)
    return None


def normalize_account_part(part: str) -> str:
    match = re.fullmatch(r"(wxid_[A-Za-z0-9]+)(?:_[0-9a-fA-F]{4})?", part)
    if match:
        return match.group(1)
    return part


def infer_category(path: Path) -> str | None:
    parts = list(path.parts)
    lower_parts = [part.lower() for part in parts]
    if "db_storage" in lower_parts:
        index = lower_parts.index("db_storage")
        if index + 1 < len(parts) - 1:
            return parts[index + 1].lower()
    if path.name.lower() == "key_info.db" and "login" in lower_parts:
        return "login"
    if "msg" in lower_parts or "multi" in lower_parts:
        return "message"
    return None


def safe_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0
