from __future__ import annotations

import hashlib
import fnmatch
import shutil
from pathlib import Path
from typing import Iterable

from .scanner import DatabaseCandidate, candidate_from_path, normalize_account_part, scan_environment
from .workspace import timestamp_run_id, write_json

CORE_CATEGORIES = {"login", "message", "contact", "session", "message_resource", "hardlink"}


def copy_databases(
    workspace: Path,
    sources: Iterable[Path],
    data_dirs: Iterable[Path],
    max_depth: int,
    run_id: str | None = None,
    accounts: Iterable[str] = (),
    categories: Iterable[str] = (),
    names: Iterable[str] = (),
    core: bool = False,
) -> dict:
    run_id = run_id or timestamp_run_id()
    run_dir = workspace / "work" / run_id
    raw_dir = run_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    account_filters = list(accounts)
    category_filters = list(categories)
    name_filters = list(names)

    source_paths = [path.expanduser().resolve() for path in sources]
    if not source_paths:
        scan = scan_environment(data_dirs=data_dirs, max_depth=max_depth)
        candidates = filter_candidates(
            scan.databases,
            accounts=account_filters,
            categories=category_filters,
            names=name_filters,
            core=core,
        )
        source_paths = [Path(item.path) for item in candidates]

    result = {
        "ok": True,
        "run_id": run_id,
        "raw_dir": str(raw_dir),
        "filters": {
            "accounts": account_filters,
            "categories": category_filters,
            "names": name_filters,
            "core": core,
        },
        "databases": [],
    }

    for source in source_paths:
        item = copy_one_database(source, raw_dir)
        result["databases"].append(item)
        if not item["ok"]:
            result["ok"] = False

    write_json(run_dir / "manifest.json", result)
    return result


def filter_candidates(
    candidates: Iterable[DatabaseCandidate],
    accounts: Iterable[str],
    categories: Iterable[str],
    names: Iterable[str],
    core: bool,
) -> list[DatabaseCandidate]:
    account_set = {normalize_account_part(value).lower() for value in accounts if value}
    category_set = {value.lower() for value in categories if value}
    if core:
        category_set |= CORE_CATEGORIES
    name_patterns = [value.lower() for value in names if value]

    filtered: list[DatabaseCandidate] = []
    for candidate in candidates:
        if account_set and (candidate.account or "").lower() not in account_set:
            continue
        if category_set and (candidate.category or "").lower() not in category_set:
            continue
        if name_patterns and not any(
            fnmatch.fnmatch(candidate.name.lower(), pattern) for pattern in name_patterns
        ):
            continue
        filtered.append(candidate)
    return filtered


def copy_one_database(source: Path, raw_dir: Path) -> dict:
    source = source.expanduser().resolve()
    if not source.exists():
        return {"ok": False, "source": str(source), "error": "source does not exist"}

    dest_name = destination_name(source)
    dest = raw_dir / dest_name
    item = {
        "ok": True,
        "source": str(source),
        "dest": str(dest),
        "family": candidate_from_path(source).family,
        "sidecars": [],
    }

    try:
        shutil.copy2(source, dest)
        for suffix in ("-wal", "-shm"):
            sidecar = Path(str(source) + suffix)
            if sidecar.exists():
                side_dest = Path(str(dest) + suffix)
                shutil.copy2(sidecar, side_dest)
                item["sidecars"].append(str(side_dest))
    except OSError as exc:
        item["ok"] = False
        item["error"] = str(exc)
    return item


def destination_name(source: Path) -> str:
    digest = hashlib.sha1(str(source).encode("utf-8", errors="ignore")).hexdigest()[:10]
    return f"{source.stem}-{digest}{source.suffix}"
