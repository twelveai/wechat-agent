from __future__ import annotations

import sqlite3
from pathlib import Path


def verify_databases(input_path: Path) -> dict:
    input_path = input_path.expanduser().resolve()
    if not input_path.exists():
        raise ValueError(f"Input does not exist: {input_path}")
    databases = collect_sqlite_inputs(input_path)
    result = {"ok": True, "databases": []}
    for database in databases:
        item = verify_one_database(database)
        result["databases"].append(item)
        if not item["ok"]:
            result["ok"] = False
    return result


def collect_sqlite_inputs(input_path: Path) -> list[Path]:
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


def verify_one_database(path: Path) -> dict:
    item = {"ok": False, "path": str(path)}
    try:
        con = sqlite3.connect(str(path))
        con.row_factory = sqlite3.Row
        try:
            tables = [
                row["name"]
                for row in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                )
            ]
            columns = {name: table_columns(con, name) for name in tables[:30]}
        finally:
            con.close()
    except sqlite3.DatabaseError as exc:
        item["error"] = str(exc)
        return item

    item.update(
        {
            "ok": True,
            "table_count": len(tables),
            "tables": tables[:100],
            "columns": columns,
            "schema_family": infer_schema_family(tables, columns),
        }
    )
    return item


def table_columns(con: sqlite3.Connection, table: str) -> list[str]:
    escaped = table.replace('"', '""')
    rows = con.execute(f'PRAGMA table_info("{escaped}")').fetchall()
    return [row["name"] for row in rows]


def infer_schema_family(tables: list[str], columns: dict[str, list[str]]) -> str:
    table_set = {table.lower() for table in tables}
    if "message" in table_set:
        message_columns = {column.lower() for column in columns.get("message", [])}
        if {"createtime", "type", "content"} <= message_columns:
            return "wechat_4_message"
        return "wechat_message"
    if "msg" in table_set:
        return "wechat_3_msg"
    if "contact" in table_set:
        return "wechat_contacts"
    if "session" in table_set:
        return "wechat_session"
    return "plain_sqlite"
