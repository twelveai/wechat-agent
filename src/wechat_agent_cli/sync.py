from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

from .copying import copy_databases
from .decrypt import decrypt_databases


SyncCallback = Callable[[Path, dict], None]
SyncLogger = Callable[[str], None]
DASHBOARD_DATABASE_PATTERNS = {
    "message": ("message_0-*.db", "message_0.db"),
    "contact": ("contact-*.db", "contact.db"),
    "session": ("session-*.db", "session.db"),
}


@dataclass(frozen=True)
class AutoSyncConfig:
    workspace: Path
    data_dirs: tuple[Path, ...] = ()
    max_depth: int = 7
    accounts: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()
    names: tuple[str, ...] = ()
    core: bool = True
    key: str | None = None
    database_keys: dict[str, str] = field(default_factory=dict)
    provider_cmd: str | None = None
    interval_seconds: int = 60
    sync_on_start: bool = True


class AutoSyncStatus:
    def __init__(self, enabled: bool, interval_seconds: int):
        self._lock = threading.Lock()
        self._payload: dict = {
            "ok": True,
            "enabled": enabled,
            "interval_seconds": interval_seconds,
            "running": False,
            "last_ok": None,
            "last_error": None,
            "last_started_at": None,
            "last_finished_at": None,
            "last_run_id": None,
            "last_raw_dir": None,
            "last_decrypted_dir": None,
            "last_copied": 0,
            "last_decrypted": 0,
            "next_run_at": None,
        }

    def update(self, **values: object) -> None:
        with self._lock:
            self._payload.update(values)

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._payload)


class AutoSyncWorker:
    def __init__(
        self,
        config: AutoSyncConfig,
        on_decrypted: SyncCallback,
        logger: SyncLogger | None = None,
    ):
        self.config = config
        self.on_decrypted = on_decrypted
        self.logger = logger
        self.status = AutoSyncStatus(enabled=True, interval_seconds=config.interval_seconds)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run_loop, name="wechat-agent-auto-sync", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)

    def run_once(self) -> dict:
        return run_auto_sync_cycle(self.config, self.on_decrypted)

    def _run_loop(self) -> None:
        first = True
        while not self._stop.is_set():
            if first and self.config.sync_on_start:
                first = False
            else:
                first = False
                next_run_at = now_iso_after(self.config.interval_seconds)
                self.status.update(next_run_at=next_run_at)
                if self._stop.wait(self.config.interval_seconds):
                    break

            self.status.update(
                running=True,
                last_started_at=now_iso(),
                last_finished_at=None,
                last_error=None,
                next_run_at=None,
            )
            try:
                result = self.run_once()
                self.status.update(
                    running=False,
                    last_ok=True,
                    last_finished_at=now_iso(),
                    last_run_id=result["run_id"],
                    last_raw_dir=result["raw_dir"],
                    last_decrypted_dir=result["decrypted_dir"],
                    last_copied=result["copied"],
                    last_decrypted=result["decrypted"],
                    last_error=result.get("warning"),
                )
                self._log(
                    "Auto-sync completed: "
                    f"run={result['run_id']} copied={result['copied']} decrypted={result['decrypted']}"
                )
            except Exception as exc:
                self.status.update(
                    running=False,
                    last_ok=False,
                    last_finished_at=now_iso(),
                    last_error=str(exc),
                )
                self._log(f"Auto-sync failed: {exc}")

    def _log(self, message: str) -> None:
        if self.logger:
            self.logger(message)


def run_auto_sync_cycle(config: AutoSyncConfig, on_decrypted: SyncCallback | None = None) -> dict:
    run_id = sync_run_id()
    copy_result = copy_databases(
        workspace=config.workspace,
        sources=[],
        data_dirs=config.data_dirs,
        max_depth=config.max_depth,
        run_id=run_id,
        accounts=config.accounts,
        categories=config.categories,
        names=config.names,
        core=config.core,
    )
    copied = [item for item in copy_result["databases"] if item.get("ok")]
    if not copied:
        raise RuntimeError("auto-sync copied no database files")

    raw_dir = Path(copy_result["raw_dir"])
    decrypted_dir = raw_dir.parent / "decrypted"
    decrypt_result = decrypt_databases(
        input_path=raw_dir,
        output_dir=decrypted_dir,
        key=config.key,
        database_keys=config.database_keys,
        provider_cmd=config.provider_cmd,
    )
    decrypted = [item for item in decrypt_result["databases"] if item.get("ok")]
    if not decrypted:
        raise RuntimeError("auto-sync decrypted no database files")
    missing_dashboard = missing_dashboard_databases(decrypted_dir)
    if missing_dashboard:
        raise RuntimeError(
            "auto-sync did not produce dashboard databases "
            f"({', '.join(missing_dashboard)}). "
            f"{decrypt_failure_summary(decrypt_result, missing_dashboard)}"
        )

    result = {
        "ok": True,
        "run_id": run_id,
        "raw_dir": str(raw_dir),
        "decrypted_dir": str(decrypted_dir),
        "copied": len(copied),
        "decrypted": len(decrypted),
        "copy_ok": copy_result["ok"],
        "decrypt_ok": decrypt_result["ok"],
    }
    if not copy_result["ok"] or not decrypt_result["ok"]:
        result["warning"] = "some databases failed to copy or decrypt"

    if on_decrypted:
        on_decrypted(decrypted_dir, result)
    return result


def missing_dashboard_databases(decrypted_dir: Path) -> list[str]:
    missing = []
    for label, patterns in DASHBOARD_DATABASE_PATTERNS.items():
        if not any(any(decrypted_dir.glob(pattern)) for pattern in patterns):
            missing.append(label)
    return missing


def decrypt_failure_summary(decrypt_result: dict, labels: Iterable[str]) -> str:
    failures = []
    for label in labels:
        prefix = "message_0" if label == "message" else label
        for item in decrypt_result.get("databases", []):
            source = Path(str(item.get("source", ""))).name
            if item.get("ok") or not source.startswith(prefix):
                continue
            reason = item.get("native_error") or item.get("error") or "unknown decrypt error"
            failures.append(f"{source}: {reason}")
            break
    if not failures:
        return "Check the auto-sync manifest for decrypt failures."
    return (
        "Likely the saved database key profile is incomplete or belongs to another database salt. "
        "Failures: " + "; ".join(failures)
    )


def make_auto_sync_config(
    *,
    workspace: Path,
    data_dirs: Iterable[Path] = (),
    max_depth: int = 7,
    accounts: Iterable[str] = (),
    categories: Iterable[str] = (),
    names: Iterable[str] = (),
    core: bool = True,
    key: str | None = None,
    database_keys: dict[str, str] | None = None,
    provider_cmd: str | None = None,
    interval_seconds: int = 60,
    sync_on_start: bool = True,
) -> AutoSyncConfig:
    if interval_seconds < 5:
        raise ValueError("sync interval must be at least 5 seconds")
    return AutoSyncConfig(
        workspace=workspace,
        data_dirs=tuple(data_dirs),
        max_depth=max_depth,
        accounts=tuple(accounts),
        categories=tuple(categories),
        names=tuple(names),
        core=core,
        key=key,
        database_keys=dict(database_keys or {}),
        provider_cmd=provider_cmd,
        interval_seconds=interval_seconds,
        sync_on_start=sync_on_start,
    )


def sync_run_id() -> str:
    return "autosync-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def now_iso_after(seconds: int) -> str:
    return datetime.fromtimestamp(time.time() + seconds, timezone.utc).isoformat()
