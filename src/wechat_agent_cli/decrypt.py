from __future__ import annotations

import os
import json
import shutil
import sqlite3
import subprocess
from pathlib import Path

from .keys import normalize_key
from .sqlcipher_native import SqlCipherDecryptError, decrypt_sqlcipher_database
from .workspace import write_json


SQLITE_HEADER = b"SQLite format 3\x00"


def decrypt_databases(
    input_path: Path,
    output_dir: Path | None,
    key: str | None,
    database_keys: dict[str, str] | None = None,
    provider_cmd: str | None = None,
) -> dict:
    input_path = input_path.expanduser().resolve()
    if not input_path.exists():
        raise ValueError(f"Input does not exist: {input_path}")

    if output_dir is None:
        output_dir = default_decrypted_dir(input_path)
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    databases = collect_database_inputs(input_path)
    result = {"ok": True, "output_dir": str(output_dir), "databases": []}
    database_keys = database_keys or {}
    for database in databases:
        database_key = database_keys.get(database.name)
        if database_key is None and not database_keys:
            database_key = key
        item = decrypt_one_database(database, output_dir, key=database_key, provider_cmd=provider_cmd)
        result["databases"].append(item)
        if not item["ok"]:
            result["ok"] = False
    persist_decrypt_manifest(input_path, output_dir, result)
    return result


def default_decrypted_dir(input_path: Path) -> Path:
    if input_path.is_dir():
        if input_path.name.lower() == "raw":
            return input_path.parent / "decrypted"
        return input_path / "decrypted"
    return input_path.parent / "decrypted"


def collect_database_inputs(input_path: Path) -> list[Path]:
    if input_path.is_file():
        return [input_path]
    return sorted(
        [
            path
            for path in input_path.rglob("*.db")
            if not path.name.lower().endswith(("-wal", "-shm"))
        ],
        key=lambda item: str(item).lower(),
    )


def decrypt_one_database(
    source: Path,
    output_dir: Path,
    key: str | None,
    provider_cmd: str | None,
) -> dict:
    dest = output_dir / source.name
    item = {"ok": False, "source": str(source), "dest": str(dest)}

    if is_plain_sqlite(source):
        shutil.copy2(source, dest)
        item.update({"ok": True, "method": "plain-copy"})
        return item

    if not key:
        item["error"] = "database appears encrypted and no key was provided or saved"
        return item

    key = normalize_key(key)
    try:
        method = decrypt_sqlcipher_database(source, dest, key)
        item.update({"ok": True, "method": method})
        return item
    except SqlCipherDecryptError as exc:
        item["native_error"] = str(exc)
    except OSError as exc:
        item["native_error"] = str(exc)

    if provider_cmd:
        return decrypt_with_external_command(source, dest, key, provider_cmd, item)

    sqlcipher = shutil.which("sqlcipher")
    if not sqlcipher:
        item["error"] = "database appears encrypted and sqlcipher executable was not found on PATH"
        return item

    return decrypt_with_sqlcipher(sqlcipher, source, dest, key, item)


def is_plain_sqlite(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            return fh.read(len(SQLITE_HEADER)) == SQLITE_HEADER
    except OSError:
        return False


def decrypt_with_external_command(
    source: Path,
    dest: Path,
    key: str,
    command: str,
    item: dict,
) -> dict:
    env = os.environ.copy()
    env.update(
        {
            "WECHAT_AGENT_INPUT": str(source),
            "WECHAT_AGENT_OUTPUT": str(dest),
            "WECHAT_AGENT_DB_KEY": key,
        }
    )
    completed = subprocess.run(
        command,
        shell=True,
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=300,
    )
    if completed.returncode != 0:
        item["error"] = "external decrypt command failed"
        item["stderr"] = redact_key(completed.stderr, key)[-2000:]
        return item
    if not dest.exists():
        item["error"] = "external decrypt command completed but did not create output database"
        return item
    item.update({"ok": True, "method": "external"})
    return item


def decrypt_with_sqlcipher(sqlcipher: str, source: Path, dest: Path, key: str, item: dict) -> dict:
    if dest.exists():
        dest.unlink()
    sql = "\n".join(
        [
            f"PRAGMA key = \"x'{key}'\";",
            "PRAGMA cipher_page_size = 4096;",
            "PRAGMA kdf_iter = 256000;",
            "PRAGMA cipher_hmac_algorithm = HMAC_SHA512;",
            "PRAGMA cipher_kdf_algorithm = PBKDF2_HMAC_SHA512;",
            f"ATTACH DATABASE '{escape_sql_path(dest)}' AS plaintext KEY '';",
            "SELECT sqlcipher_export('plaintext');",
            "DETACH DATABASE plaintext;",
            ".quit",
            "",
        ]
    )
    completed = subprocess.run(
        [sqlcipher, str(source)],
        input=sql,
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    if completed.returncode != 0:
        item["error"] = "sqlcipher decrypt failed"
        item["stderr"] = redact_key(completed.stderr, key)[-2000:]
        return item
    if not dest.exists() or not sqlite_can_open(dest):
        item["error"] = "sqlcipher completed but output is not readable SQLite"
        return item
    item.update({"ok": True, "method": "sqlcipher"})
    return item


def escape_sql_path(path: Path) -> str:
    return str(path).replace("'", "''")


def sqlite_can_open(path: Path) -> bool:
    try:
        con = sqlite3.connect(str(path))
        try:
            con.execute("SELECT name FROM sqlite_master LIMIT 1").fetchall()
        finally:
            con.close()
        return True
    except sqlite3.DatabaseError:
        return False


def redact_key(value: str, key: str) -> str:
    return value.replace(key, "[REDACTED_KEY]")


def persist_decrypt_manifest(input_path: Path, output_dir: Path, result: dict) -> None:
    run_dir: Path | None = None
    if input_path.is_dir() and input_path.name.lower() == "raw":
        run_dir = input_path.parent
    elif output_dir.name.lower() == "decrypted":
        run_dir = output_dir.parent

    if not run_dir:
        return

    manifest_path = run_dir / "manifest.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            manifest = {}
    else:
        manifest = {}
    manifest["decrypt"] = result
    write_json(manifest_path, manifest)
