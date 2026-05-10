from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


MAX_LIMIT = 500
DEFAULT_LIMIT = 50


@dataclass(frozen=True)
class DatabaseSet:
    root: Path
    message: Path | None
    biz_message: Path | None
    contact: Path | None
    session: Path | None
    message_resource: Path | None

    def to_dict(self) -> dict[str, str | None]:
        return {
            "root": str(self.root),
            "message": str(self.message) if self.message else None,
            "biz_message": str(self.biz_message) if self.biz_message else None,
            "contact": str(self.contact) if self.contact else None,
            "session": str(self.session) if self.session else None,
            "message_resource": str(self.message_resource) if self.message_resource else None,
        }


class DashboardStore:
    def __init__(self, decrypted_dir: Path):
        self.databases = discover_databases(decrypted_dir)
        self._chat_tables: dict[str, str] | None = None

    def health(self) -> dict:
        return {
            "ok": True,
            "databases": self.databases.to_dict(),
            "available": {
                "messages": self.databases.message is not None,
                "contacts": self.databases.contact is not None,
                "sessions": self.databases.session is not None,
            },
        }

    def overview(self) -> dict:
        chats = self.chats(limit=10_000)
        return {
            "ok": True,
            "chat_count": len(chats["items"]),
            "message_count": sum(item["message_count"] for item in chats["items"]),
            "contact_count": self.count_table(self.databases.contact, "contact"),
            "session_count": self.count_table(self.databases.session, "SessionTable"),
            "databases": self.databases.to_dict(),
        }

    def contacts(self, q: str | None = None, limit: int = DEFAULT_LIMIT, offset: int = 0) -> dict:
        if not self.databases.contact:
            return {"ok": True, "items": [], "total": 0}
        limit = clamp_limit(limit)
        conditions = ["delete_flag = 0"]
        params: list[Any] = []
        if q:
            conditions.append("(username LIKE ? OR remark LIKE ? OR nick_name LIKE ? OR alias LIKE ?)")
            needle = f"%{q}%"
            params.extend([needle, needle, needle, needle])
        where_sql = " WHERE " + " AND ".join(conditions)
        sql = (
            "SELECT id, username, alias, remark, nick_name, local_type, verify_flag, "
            "is_in_chat_room, chat_room_type FROM contact"
            f"{where_sql} ORDER BY COALESCE(NULLIF(remark, ''), nick_name, username) LIMIT ? OFFSET ?"
        )
        count_sql = "SELECT COUNT(*) FROM contact" + where_sql
        with connect(self.databases.contact) as con:
            total = con.execute(count_sql, params).fetchone()[0]
            rows = con.execute(sql, [*params, limit, max(0, offset)]).fetchall()
        return {"ok": True, "total": total, "items": [contact_from_row(row) for row in rows]}

    def sessions(self, limit: int = DEFAULT_LIMIT, offset: int = 0) -> dict:
        if not self.databases.session:
            return {"ok": True, "items": [], "total": 0}
        limit = clamp_limit(limit)
        contact_map = self.contact_map()
        with connect(self.databases.session) as con:
            total = con.execute("SELECT COUNT(*) FROM SessionTable").fetchone()[0]
            rows = con.execute(
                "SELECT username, type, unread_count, summary, last_timestamp, sort_timestamp, "
                "last_msg_type, last_msg_sender, last_sender_display_name "
                "FROM SessionTable ORDER BY sort_timestamp DESC LIMIT ? OFFSET ?",
                (limit, max(0, offset)),
            ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["contact"] = contact_map.get(row["username"])
            item["display_name"] = display_name(item["contact"], fallback=row["username"])
            item["last_time_iso"] = iso_from_timestamp(row["last_timestamp"])
            item["sort_time_iso"] = iso_from_timestamp(row["sort_timestamp"])
            items.append(item)
        return {"ok": True, "total": total, "items": items}

    def chats(self, q: str | None = None, limit: int = DEFAULT_LIMIT, offset: int = 0) -> dict:
        if not self.databases.message:
            return {"ok": True, "items": [], "total": 0}
        limit = clamp_limit(limit)
        contact_map = self.contact_map()
        session_map = self.session_map()
        chat_tables = self.chat_tables()
        items = []
        with connect(self.databases.message) as con:
            for username, table in chat_tables.items():
                contact = contact_map.get(username)
                name = display_name(contact, fallback=username)
                if q and q.lower() not in username.lower() and q.lower() not in name.lower():
                    continue
                count = con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                latest = con.execute(
                    f'SELECT create_time FROM "{table}" ORDER BY create_time DESC LIMIT 1'
                ).fetchone()
                session = session_map.get(username)
                items.append(
                    {
                        "username": username,
                        "table": table,
                        "display_name": name,
                        "message_count": count,
                        "latest_create_time": latest["create_time"] if latest else None,
                        "latest_time_iso": iso_from_timestamp(latest["create_time"] if latest else None),
                        "contact": contact,
                        "session": session,
                    }
                )
        items.sort(key=lambda item: item["latest_create_time"] or 0, reverse=True)
        return {"ok": True, "total": len(items), "items": items[max(0, offset): max(0, offset) + limit]}

    def messages(
        self,
        chat: str | None = None,
        q: str | None = None,
        limit: int = DEFAULT_LIMIT,
        before: int | None = None,
        after: int | None = None,
        local_type: int | None = None,
        include_content: bool = True,
    ) -> dict:
        if not self.databases.message:
            return {"ok": True, "items": [], "total_scanned_tables": 0}
        limit = clamp_limit(limit)
        chat_tables = self.chat_tables()
        contact_map = self.contact_map()

        selected: list[tuple[str, str]]
        if chat:
            table = chat_tables.get(chat)
            if not table and chat.startswith("Msg_") and chat in set(chat_tables.values()):
                username = next((name for name, candidate in chat_tables.items() if candidate == chat), chat)
                table = chat
                chat = username
            if not table:
                return {"ok": True, "items": [], "total_scanned_tables": 0, "warning": "chat not found"}
            selected = [(chat, table)]
        else:
            selected = list(chat_tables.items())

        items: list[dict] = []
        per_table_limit = limit if chat else min(limit, 50)
        with connect(self.databases.message) as con:
            for username, table in selected:
                sql, params = build_message_query(table, q, per_table_limit, before, after, local_type)
                for row in con.execute(sql, params).fetchall():
                    contact = contact_map.get(username)
                    items.append(message_from_row(row, username, table, contact, include_content))

        items.sort(key=lambda item: item["create_time"] or 0, reverse=True)
        return {
            "ok": True,
            "total_scanned_tables": len(selected),
            "items": items[:limit],
        }

    def contact_map(self) -> dict[str, dict]:
        if not self.databases.contact:
            return {}
        with connect(self.databases.contact) as con:
            rows = con.execute(
                "SELECT id, username, alias, remark, nick_name, local_type, verify_flag, "
                "is_in_chat_room, chat_room_type FROM contact WHERE delete_flag = 0"
            ).fetchall()
        return {row["username"]: contact_from_row(row) for row in rows}

    def session_map(self) -> dict[str, dict]:
        if not self.databases.session:
            return {}
        with connect(self.databases.session) as con:
            rows = con.execute(
                "SELECT username, type, unread_count, summary, last_timestamp, sort_timestamp, "
                "last_msg_type, last_msg_sender, last_sender_display_name FROM SessionTable"
            ).fetchall()
        result = {}
        for row in rows:
            item = dict(row)
            item["last_time_iso"] = iso_from_timestamp(row["last_timestamp"])
            item["sort_time_iso"] = iso_from_timestamp(row["sort_timestamp"])
            result[row["username"]] = item
        return result

    def chat_tables(self) -> dict[str, str]:
        if self._chat_tables is not None:
            return self._chat_tables
        if not self.databases.message:
            self._chat_tables = {}
            return self._chat_tables
        with connect(self.databases.message) as con:
            table_names = {
                row["name"]
                for row in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'"
                )
            }
            rows = con.execute("SELECT user_name FROM Name2Id WHERE is_session = 1").fetchall()
        mapping = {}
        for row in rows:
            username = row["user_name"]
            table = "Msg_" + hashlib.md5(username.encode("utf-8")).hexdigest()
            if table in table_names:
                mapping[username] = table
        self._chat_tables = mapping
        return mapping

    @staticmethod
    def count_table(path: Path | None, table: str) -> int:
        if not path:
            return 0
        try:
            with connect(path) as con:
                return int(con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
        except sqlite3.DatabaseError:
            return 0


class DashboardHandler(BaseHTTPRequestHandler):
    store: DashboardStore

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        try:
            payload = self.route(parsed.path, query)
            self.write_json(200, payload)
        except ValueError as exc:
            self.write_json(400, {"ok": False, "error": str(exc)})
        except Exception as exc:
            self.write_json(500, {"ok": False, "error": str(exc)})

    def route(self, path: str, query: dict[str, list[str]]) -> dict:
        if path in {"/", "/api"}:
            return api_index()
        if path == "/api/health":
            return self.store.health()
        if path == "/api/databases":
            return {"ok": True, "databases": self.store.databases.to_dict()}
        if path == "/api/overview":
            return self.store.overview()
        if path == "/api/contacts":
            return self.store.contacts(
                q=query_string(query, "q"),
                limit=query_int(query, "limit", DEFAULT_LIMIT),
                offset=query_int(query, "offset", 0),
            )
        if path == "/api/sessions":
            return self.store.sessions(
                limit=query_int(query, "limit", DEFAULT_LIMIT),
                offset=query_int(query, "offset", 0),
            )
        if path == "/api/chats":
            return self.store.chats(
                q=query_string(query, "q"),
                limit=query_int(query, "limit", DEFAULT_LIMIT),
                offset=query_int(query, "offset", 0),
            )
        if path == "/api/messages":
            return self.store.messages(
                chat=query_string(query, "chat"),
                q=query_string(query, "q"),
                limit=query_int(query, "limit", DEFAULT_LIMIT),
                before=query_optional_int(query, "before"),
                after=query_optional_int(query, "after"),
                local_type=query_optional_int(query, "type"),
                include_content=query_bool(query, "include_content", True),
            )
        raise ValueError(f"Unknown API path: {path}")

    def write_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "http://127.0.0.1")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return


def run_dashboard_server(decrypted_dir: Path, host: str, port: int) -> None:
    store = DashboardStore(decrypted_dir)

    class Handler(DashboardHandler):
        pass

    Handler.store = store
    server = ThreadingHTTPServer((host, port), Handler)
    safe_print(f"Dashboard API listening on http://{host}:{port}")
    safe_print(f"Using decrypted databases from {store.databases.root}")
    server.serve_forever()


def discover_databases(decrypted_dir: Path) -> DatabaseSet:
    root = decrypted_dir.expanduser().resolve()
    if not root.exists():
        raise ValueError(f"Decrypted directory does not exist: {root}")
    return DatabaseSet(
        root=root,
        message=find_db(root, ["message_0-*.db", "message_0.db"]),
        biz_message=find_db(root, ["biz_message_0-*.db", "biz_message_0.db"]),
        contact=find_db(root, ["contact-*.db", "contact.db"]),
        session=find_db(root, ["session-*.db", "session.db"]),
        message_resource=find_db(root, ["message_resource-*.db", "message_resource.db"]),
    )


def find_db(root: Path, patterns: list[str]) -> Path | None:
    for pattern in patterns:
        matches = sorted(root.glob(pattern), key=lambda path: path.stat().st_mtime, reverse=True)
        if matches:
            return matches[0]
    return None


def connect(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def build_message_query(
    table: str,
    q: str | None,
    limit: int,
    before: int | None,
    after: int | None,
    local_type: int | None,
) -> tuple[str, list[Any]]:
    conditions: list[str] = []
    params: list[Any] = []
    if q:
        conditions.append("message_content LIKE ?")
        params.append(f"%{q}%")
    if before is not None:
        conditions.append("create_time < ?")
        params.append(before)
    if after is not None:
        conditions.append("create_time > ?")
        params.append(after)
    if local_type is not None:
        conditions.append("local_type = ?")
        params.append(local_type)
    where_sql = " WHERE " + " AND ".join(conditions) if conditions else ""
    sql = (
        f'SELECT local_id, server_id, local_type, sort_seq, real_sender_id, create_time, '
        f'status, message_content FROM "{table}"'
        f"{where_sql} ORDER BY create_time DESC LIMIT ?"
    )
    params.append(limit)
    return sql, params


def contact_from_row(row: sqlite3.Row) -> dict:
    item = dict(row)
    item["display_name"] = display_name(item, fallback=item.get("username"))
    item["is_room"] = bool(item.get("is_in_chat_room")) or str(item.get("username", "")).endswith("@chatroom")
    return item


def message_from_row(
    row: sqlite3.Row,
    username: str,
    table: str,
    contact: dict | None,
    include_content: bool,
) -> dict:
    content = row["message_content"] if include_content else None
    if isinstance(content, bytes):
        content = content.decode("utf-8", errors="replace")
    return {
        "chat": username,
        "chat_table": table,
        "chat_display_name": display_name(contact, fallback=username),
        "local_id": row["local_id"],
        "server_id": row["server_id"],
        "local_type": row["local_type"],
        "sort_seq": row["sort_seq"],
        "real_sender_id": row["real_sender_id"],
        "create_time": row["create_time"],
        "create_time_iso": iso_from_timestamp(row["create_time"]),
        "status": row["status"],
        "message_content": content,
    }


def display_name(contact: dict | None, fallback: str | None) -> str | None:
    if not contact:
        return fallback
    for key in ("remark", "nick_name", "alias", "username"):
        value = contact.get(key)
        if value:
            return value
    return fallback


def iso_from_timestamp(value: Any) -> str | None:
    if value in {None, ""}:
        return None
    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return None
    if timestamp <= 0:
        return None
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def query_string(query: dict[str, list[str]], name: str) -> str | None:
    values = query.get(name)
    if not values:
        return None
    value = values[0].strip()
    return value or None


def query_int(query: dict[str, list[str]], name: str, default: int) -> int:
    value = query_optional_int(query, name)
    return default if value is None else value


def query_optional_int(query: dict[str, list[str]], name: str) -> int | None:
    values = query.get(name)
    if not values or values[0] == "":
        return None
    try:
        return int(values[0])
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def query_bool(query: dict[str, list[str]], name: str, default: bool) -> bool:
    values = query.get(name)
    if not values:
        return default
    return values[0].lower() in {"1", "true", "yes", "on"}


def clamp_limit(value: int) -> int:
    return min(max(1, value), MAX_LIMIT)


def api_index() -> dict:
    return {
        "ok": True,
        "name": "WeChat Agent Dashboard API",
        "endpoints": [
            "/api/health",
            "/api/databases",
            "/api/overview",
            "/api/contacts?q=&limit=&offset=",
            "/api/sessions?limit=&offset=",
            "/api/chats?q=&limit=&offset=",
            "/api/messages?chat=&q=&type=&before=&after=&limit=",
        ],
    }


def safe_print(message: str) -> None:
    try:
        if sys.stdout:
            print(message, flush=True)
    except (AttributeError, OSError, ValueError):
        return
