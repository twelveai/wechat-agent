from __future__ import annotations

import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .workspace import restrict_owner_only

HEX_KEY_RE = re.compile(r"\b[a-fA-F0-9]{64}\b")


def normalize_key(value: str) -> str:
    stripped = value.strip().strip('"')
    if stripped.lower().startswith("x'") and stripped.endswith("'"):
        stripped = stripped[2:-1]
    else:
        stripped = stripped.strip("'")
    if stripped.lower().startswith("0x"):
        stripped = stripped[2:]
    stripped = re.sub(r"[\s:-]", "", stripped)
    if not re.fullmatch(r"[a-fA-F0-9]+", stripped) or len(stripped) not in {64, 96}:
        raise ValueError("Database key must be exactly 64 or 96 hexadecimal characters.")
    return stripped.lower()


def fingerprint_key(value: str) -> str:
    key = normalize_key(value)
    digest = hashlib.sha256(bytes.fromhex(key)).hexdigest()
    return f"sha256:{digest[:12]}:{key[:4]}...{key[-4:]}"


def secrets_path(workspace: Path) -> Path:
    return workspace / "secrets.json"


def load_secrets(workspace: Path) -> dict:
    path = secrets_path(workspace)
    if not path.exists():
        return {"profiles": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def save_key(workspace: Path, profile: str, value: str, source: str) -> None:
    key = normalize_key(value)
    payload = load_secrets(workspace)
    payload.setdefault("profiles", {})[profile] = {
        "key": key,
        "fingerprint": fingerprint_key(key),
        "source": source,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    path = secrets_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    restrict_owner_only(path)


def save_database_keys(workspace: Path, profile: str, values: dict[str, str], source: str) -> None:
    normalized = {name: normalize_key(value) for name, value in values.items()}
    payload = load_secrets(workspace)
    profile_payload = payload.setdefault("profiles", {}).setdefault(profile, {})
    profile_payload.update(
        {
            "source": source,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "database_keys": {
                name: {
                    "key": key,
                    "fingerprint": fingerprint_key(key),
                }
                for name, key in sorted(normalized.items())
            },
        }
    )
    if normalized:
        first_key = next(iter(normalized.values()))
        profile_payload["key"] = first_key
        profile_payload["fingerprint"] = fingerprint_key(first_key)
    path = secrets_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    restrict_owner_only(path)


def load_key(workspace: Path, profile: str) -> str | None:
    payload = load_secrets(workspace)
    item = payload.get("profiles", {}).get(profile)
    if not item:
        return None
    return normalize_key(item["key"])


def load_database_keys(workspace: Path, profile: str) -> dict[str, str]:
    payload = load_secrets(workspace)
    item = payload.get("profiles", {}).get(profile)
    if not item:
        return {}
    database_keys = item.get("database_keys") or {}
    result: dict[str, str] = {}
    for name, value in database_keys.items():
        if isinstance(value, dict):
            value = value.get("key")
        if value:
            result[name] = normalize_key(str(value))
    return result


def extract_key_with_command(command: str, timeout: int = 60) -> str:
    completed = subprocess.run(
        command,
        shell=True,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    output = "\n".join(part for part in [completed.stdout, completed.stderr] if part)
    match = HEX_KEY_RE.search(output)
    if not match:
        raise ValueError("External command did not print a 64-character hexadecimal key.")
    return normalize_key(match.group(0))
