from __future__ import annotations

import hashlib
import importlib
import json
import mimetypes
import os
import re
import sqlite3
import struct
import sys
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

from .native_crypto import AesEcbDecryptor
from .scanner import normalize_account_part, read_windows_weixin_config_roots


MAX_LIMIT = 500
DEFAULT_LIMIT = 50
SUMMARY_MESSAGE_LIMIT = 500
SUMMARY_MAX_MESSAGE_CHARS = 1200
SUMMARY_CONFIG_ENV = "WECHAT_AGENT_OPENAI_CONFIG"
SUMMARY_PROMPT_ENV = "WECHAT_AGENT_SUMMARY_PROMPT"
DEFAULT_OPENAI_RESPONSES_MODEL = "gpt-4o-mini"
ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
UNRELIABLE_REAL_SENDER_TYPES = {244813135921, 266287972401}
IMAGE_PLACEHOLDER_TEXT = "[image]"
WECHAT_V4_IMAGE_AES_KEYS = {
    b"\x07\x08V1\x08\x07": b"cfcd208495d565ef",
    b"\x07\x08V2\x08\x07": b"43e7d25eb1b9bb64",
}
WECHAT_V4_IMAGE_HEADER_SIZE = 0x0F
WECHAT_V4_IMAGE_XOR_TAIL_SIZE = 0x100000
MEDIA_IMAGE_DETAIL_THUMB = "thumb"
MEDIA_IMAGE_DETAIL_FULL = "full"
MESSAGE_BASE_COLUMNS = [
    "local_id",
    "server_id",
    "local_type",
    "sort_seq",
    "real_sender_id",
    "create_time",
    "status",
    "message_content",
    "compress_content",
]
MESSAGE_OPTIONAL_COLUMNS = ["packed_info_data", "source", "WCDB_CT_source"]
SUMMARY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "title": {"type": "string"},
        "executive_summary": {"type": "string"},
        "message_count": {"type": "integer"},
        "time_range": {"type": "string"},
        "sentiment": {"type": "string"},
        "key_points": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "point": {"type": "string"},
                    "importance": {"type": "string"},
                    "evidence": {"type": "string"},
                },
                "required": ["point", "importance", "evidence"],
            },
        },
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "decision": {"type": "string"},
                    "evidence": {"type": "string"},
                },
                "required": ["decision", "evidence"],
            },
        },
        "action_items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "task": {"type": "string"},
                    "owner": {"type": "string"},
                    "due_time": {"type": "string"},
                    "priority": {"type": "string"},
                    "context": {"type": "string"},
                },
                "required": ["task", "owner", "due_time", "priority", "context"],
            },
        },
        "risks": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "risk": {"type": "string"},
                    "severity": {"type": "string"},
                    "evidence": {"type": "string"},
                },
                "required": ["risk", "severity", "evidence"],
            },
        },
        "open_questions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "question": {"type": "string"},
                    "context": {"type": "string"},
                },
                "required": ["question", "context"],
            },
        },
        "notable_messages": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "time": {"type": "string"},
                    "sender": {"type": "string"},
                    "quote": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["time", "sender", "quote", "reason"],
            },
        },
    },
    "required": [
        "title",
        "executive_summary",
        "message_count",
        "time_range",
        "sentiment",
        "key_points",
        "decisions",
        "action_items",
        "risks",
        "open_questions",
        "notable_messages",
    ],
}


@dataclass(frozen=True)
class DatabaseSet:
    root: Path
    message: Path | None
    biz_message: Path | None
    contact: Path | None
    session: Path | None
    message_resource: Path | None
    hardlink: Path | None

    def to_dict(self) -> dict[str, str | None]:
        return {
            "root": str(self.root),
            "message": str(self.message) if self.message else None,
            "biz_message": str(self.biz_message) if self.biz_message else None,
            "contact": str(self.contact) if self.contact else None,
            "session": str(self.session) if self.session else None,
            "message_resource": str(self.message_resource) if self.message_resource else None,
            "hardlink": str(self.hardlink) if self.hardlink else None,
        }


@dataclass(frozen=True)
class BinaryResponse:
    body: bytes
    content_type: str
    cache_control: str = "private, max-age=3600"


@dataclass(frozen=True)
class OpenAIResponsesConfig:
    url: str
    api_key: str
    model: str = DEFAULT_OPENAI_RESPONSES_MODEL
    timeout_seconds: int = 90
    stream: bool = False


@dataclass(frozen=True)
class MediaFileCandidate:
    path: Path
    source: str
    encrypted_format: str | None = None
    requires_image_key: bool = False


class DashboardStore:
    def __init__(self, decrypted_dir: Path, image_key: str | bytes | None = None):
        self.databases = discover_databases(decrypted_dir)
        self.image_key = normalize_image_key(image_key or os.environ.get("WECHAT_AGENT_IMAGE_KEY"))
        self._chat_tables: dict[str, str] | None = None
        self._self_username: str | None = None
        self._account_roots: list[Path] | None = None
        self._resource_chat_ids: dict[str, int] | None = None
        self._media_file_cache: dict[tuple[str, str, str], MediaFileCandidate | None] = {}

    def health(self) -> dict:
        return {
            "ok": True,
            "databases": self.databases.to_dict(),
            "available": {
                "messages": self.databases.message is not None,
                "contacts": self.databases.contact is not None,
                "sessions": self.databases.session is not None,
                "media": bool(self.account_roots()),
                "image_key": self.image_key is not None,
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
        sender_name_map = self.sender_name_map()
        self_username = self.self_username(sender_name_map)

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
        scan_limit = min(MAX_LIMIT, max(limit, limit * 10)) if q else limit
        per_table_limit = scan_limit if chat else min(scan_limit, 50)
        with connect(self.databases.message) as con:
            for username, table in selected:
                columns = message_table_columns(con, table)
                sql, params = build_message_query(table, per_table_limit, before, after, local_type, columns)
                for row in con.execute(sql, params).fetchall():
                    contact = contact_map.get(username)
                    decoded_content = decode_message_content(row["message_content"], row["compress_content"])
                    sender_username, sender_contact, raw_message_content = resolve_message_sender(
                        chat_username=username,
                        content=decoded_content,
                        local_type=row["local_type"],
                        real_sender_id=row["real_sender_id"],
                        contact_map=contact_map,
                        sender_name_map=sender_name_map,
                    )
                    media = self.message_media_for_row(username, table, row, raw_message_content)
                    message_content = summarize_message_content(raw_message_content, media)
                    if q and not message_matches_query(
                        content=message_content,
                        sender_username=sender_username,
                        sender_contact=sender_contact,
                        q=q,
                    ):
                        continue
                    items.append(
                        message_from_row(
                            row=row,
                            username=username,
                            table=table,
                            contact=contact,
                            sender_username=sender_username,
                            sender_contact=sender_contact,
                            is_self=sender_username is not None and sender_username == self_username,
                            message_content=message_content if include_content else None,
                            media=media,
                        )
                    )

        items.sort(key=lambda item: item["create_time"] or 0, reverse=True)
        return {
            "ok": True,
            "total_scanned_tables": len(selected),
            "items": items[:limit],
        }

    def summarize_messages(
        self,
        chat: str | None,
        after: int | None = None,
        before: int | None = None,
    ) -> dict:
        if not chat:
            raise ValueError("chat is required")
        if after is not None and before is not None and after > before:
            raise ValueError("after must be earlier than before")

        message_payload = self.messages(
            chat=chat,
            limit=SUMMARY_MESSAGE_LIMIT,
            after=after - 1 if after is not None else None,
            before=before + 1 if before is not None else None,
            local_type=1,
            include_content=True,
        )
        text_messages = [
            item
            for item in message_payload["items"]
            if item.get("local_type") == 1 and isinstance(item.get("message_content"), str) and item["message_content"].strip()
        ]
        text_messages.sort(key=lambda item: ((item.get("create_time") or 0), item.get("local_id") or 0))

        chat_display_name = text_messages[0]["chat_display_name"] if text_messages else chat
        if not text_messages:
            return {
                "ok": True,
                "chat": chat,
                "chat_display_name": chat_display_name,
                "range": summary_range(after, before),
                "messages": {
                    "included": 0,
                    "limit": SUMMARY_MESSAGE_LIMIT,
                    "oldest_create_time": None,
                    "newest_create_time": None,
                },
                "summary": empty_summary("没有可总结的文本消息", after, before),
                "openai": None,
            }

        openai_result = request_openai_message_summary(
            chat=chat,
            chat_display_name=chat_display_name,
            after=after,
            before=before,
            messages=text_messages,
        )
        summary = openai_result["summary"]
        summary["message_count"] = len(text_messages)
        return {
            "ok": True,
            "chat": chat,
            "chat_display_name": chat_display_name,
            "range": summary_range(after, before),
            "messages": {
                "included": len(text_messages),
                "limit": SUMMARY_MESSAGE_LIMIT,
                "oldest_create_time": text_messages[0].get("create_time"),
                "newest_create_time": text_messages[-1].get("create_time"),
            },
            "summary": summary,
            "openai": {
                "response_id": openai_result.get("response_id"),
                "model": openai_result.get("model"),
            },
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

    def sender_name_map(self) -> dict[int, str]:
        if not self.databases.message:
            return {}
        with connect(self.databases.message) as con:
            rows = con.execute("SELECT rowid, user_name FROM Name2Id").fetchall()
        return {int(row["rowid"]): row["user_name"] for row in rows if row["rowid"] is not None}

    def self_username(self, sender_name_map: dict[int, str] | None = None) -> str | None:
        if self._self_username is not None:
            return self._self_username
        manifest = self.databases.root.parent / "manifest.json"
        if manifest.exists():
            try:
                payload = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                payload = {}
            accounts = payload.get("filters", {}).get("accounts", [])
            if len(accounts) == 1 and isinstance(accounts[0], str):
                self._self_username = accounts[0]
                return self._self_username
        mapping = sender_name_map if sender_name_map is not None else self.sender_name_map()
        self._self_username = mapping.get(2)
        return self._self_username

    def account_root(self) -> Path | None:
        roots = self.account_roots()
        return roots[0] if roots else None

    def account_roots(self) -> list[Path]:
        if self._account_roots is not None:
            return self._account_roots
        manifest = self.databases.root.parent / "manifest.json"
        account_names = self.account_names(manifest)
        roots: list[Path] = []
        roots.extend(account_roots_from_manifest(manifest))
        roots.extend(account_roots_from_windows_config(account_names))
        if not roots:
            roots.extend(account_roots_from_windows_config([]))

        resolved: list[Path] = []
        seen: set[Path] = set()
        for root in roots:
            try:
                candidate = root.expanduser().resolve()
            except OSError:
                continue
            if not candidate.exists() or candidate in seen:
                continue
            resolved.append(candidate)
            seen.add(candidate)
        self._account_roots = resolved
        return resolved

    def account_names(self, manifest: Path) -> list[str]:
        names: list[str] = []
        if manifest.exists():
            try:
                payload = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                payload = {}
            for value in payload.get("filters", {}).get("accounts", []):
                if isinstance(value, str) and value:
                    names.append(value)
            for item in payload.get("databases", []):
                source = item.get("source") if isinstance(item, dict) else None
                account = account_from_source_path(source)
                if account:
                    names.append(account)
        self_name = self.self_username()
        if self_name:
            names.append(self_name)
        result: list[str] = []
        seen: set[str] = set()
        for name in names:
            normalized = normalize_account_part(name).lower()
            if normalized and normalized not in seen:
                result.append(name)
                seen.add(normalized)
        return result

    def resource_chat_ids(self) -> dict[str, int]:
        if self._resource_chat_ids is not None:
            return self._resource_chat_ids
        if not self.databases.message_resource:
            self._resource_chat_ids = {}
            return self._resource_chat_ids
        with connect(self.databases.message_resource) as con:
            rows = con.execute("SELECT rowid, user_name FROM ChatName2Id").fetchall()
        self._resource_chat_ids = {row["user_name"]: int(row["rowid"]) for row in rows}
        return self._resource_chat_ids

    def message_media_for_row(
        self,
        chat_username: str,
        table: str,
        row: sqlite3.Row,
        content: str | None,
    ) -> dict | None:
        image = parse_image_message(content)
        if not image:
            return None
        candidate = self.resolve_image_candidate(
            chat_username=chat_username,
            table=table,
            row=row,
            image=image,
            detail=MEDIA_IMAGE_DETAIL_THUMB,
        )
        media = {
            "kind": "image",
            "detail": MEDIA_IMAGE_DETAIL_THUMB,
            "url": media_image_url(chat_username, row["local_id"], row["server_id"], MEDIA_IMAGE_DETAIL_THUMB),
            "width": image.get("width"),
            "height": image.get("height"),
            "size": image.get("size"),
            "md5": image.get("md5"),
            "cdn_thumb_size": image.get("cdn_thumb_size"),
            "available": bool(candidate and not candidate.requires_image_key),
            "source": candidate.source if candidate else None,
            "requires_image_key": bool(candidate and candidate.requires_image_key),
        }
        if candidate and candidate.requires_image_key:
            media["unavailable_reason"] = "image_key_required"
        elif not candidate:
            media["unavailable_reason"] = "local_file_missing"
        return media

    def resolve_image_candidate(
        self,
        chat_username: str,
        table: str,
        row: sqlite3.Row,
        image: dict,
        detail: str,
    ) -> MediaFileCandidate | None:
        account_roots = self.account_roots()
        if not account_roots:
            return None
        chat_hash = table[4:] if table.startswith("Msg_") else hashlib.md5(chat_username.encode("utf-8")).hexdigest()
        month = month_from_timestamp(row["create_time"])
        local_id = row["local_id"]
        create_time = row["create_time"]
        file_stem = image_file_stem_from_row(row)
        resource_hash = self.message_resource_hash(chat_username, row)
        token_stems = [
            value for value in [
                file_stem,
                f"{local_id}_{create_time}" if local_id not in {None, ""} and create_time not in {None, ""} else None,
                resource_hash,
                *image.get("cdn_file_ids", []),
            ] if value
        ]
        xml_md5 = normalize_md5(image.get("md5"))

        for account_root in account_roots:
            cache_message = account_root / "cache" / month / "Message" / chat_hash
            attach_image = account_root / "msg" / "attach" / chat_hash / month / "Img"
            paths: list[tuple[Path, str]] = []
            for stem in token_stems:
                paths.extend(image_stem_candidates(attach_image, stem, detail, "attach"))
                paths.extend(image_stem_candidates(cache_message / "Bubble", stem, detail, "cache-bubble"))
            if local_id not in {None, ""} and create_time not in {None, ""}:
                paths.insert(
                    0,
                    (
                        cache_message / "Thumb" / f"{local_id}_{create_time}_thumb.jpg",
                        "cache-thumb",
                    ),
                )

            for path, source in dedupe_path_candidates(paths):
                candidate = self.media_candidate_from_path(account_root, path, source)
                if candidate:
                    return candidate

        if xml_md5:
            for account_root in account_roots:
                md5_candidate = self.resolve_md5_image_candidate(
                    account_root=account_root,
                    chat_hash=chat_hash,
                    month=month,
                    md5=xml_md5,
                    detail=detail,
                )
                if md5_candidate:
                    return md5_candidate
        return None

    def resolve_md5_image_candidate(
        self,
        account_root: Path,
        chat_hash: str,
        month: str,
        md5: str,
        detail: str,
    ) -> MediaFileCandidate | None:
        cache_key = (str(account_root), md5, detail)
        if cache_key in self._media_file_cache:
            return self._media_file_cache[cache_key]

        file_storage = account_root / "FileStorage" / "Image" / month
        attach_image = account_root / "msg" / "attach" / chat_hash / month / "Img"
        cache_message = account_root / "cache" / month / "Message" / chat_hash
        candidates: list[tuple[Path, str]] = [
            (file_storage / f"{md5}.dat", "filestorage-md5"),
            (file_storage / f"{md5}.pic", "filestorage-md5"),
            (file_storage / f"{md5}.jpg", "filestorage-md5"),
            (file_storage / f"{md5}.png", "filestorage-md5"),
            (attach_image / f"{md5}.dat", "attach-md5-full"),
            (attach_image / f"{md5}_t.dat", "attach-md5-thumb"),
            (cache_message / "Bubble" / f"{md5}_b.dat", "cache-md5-bubble"),
        ]
        if detail == MEDIA_IMAGE_DETAIL_THUMB:
            candidates[4], candidates[5] = candidates[5], candidates[4]

        for path, source in candidates:
            candidate = self.media_candidate_from_path(account_root, path, source)
            if candidate:
                self._media_file_cache[cache_key] = candidate
                return candidate

        hardlink = self.resolve_hardlink_image_candidate(account_root, md5, detail)
        if hardlink:
            self._media_file_cache[cache_key] = hardlink
            return hardlink

        found = self.find_md5_media_file(account_root, md5)
        self._media_file_cache[cache_key] = found
        return found

    def resolve_hardlink_image_candidate(
        self,
        account_root: Path,
        md5: str,
        detail: str,
    ) -> MediaFileCandidate | None:
        if not self.databases.hardlink:
            return None
        row = None
        try:
            with connect(self.databases.hardlink) as con:
                for table_name in ("image_hardlink_info_v4", "image_hardlink_info_v3"):
                    try:
                        row = con.execute(
                            f'SELECT "{table_name}".file_name, "{table_name}".type, '
                            f'dir1.username AS dir1_name, dir2.username AS dir2_name, '
                            f'"{table_name}".extra_buffer '
                            f'FROM "{table_name}" '
                            f'JOIN dir2id AS dir1 ON dir1.rowid = "{table_name}".dir1 '
                            f'LEFT JOIN dir2id AS dir2 ON dir2.rowid = "{table_name}".dir2 '
                            f'WHERE "{table_name}".md5 = ? LIMIT 1',
                            (md5,),
                        ).fetchone()
                    except sqlite3.DatabaseError:
                        continue
                    if row:
                        break
        except sqlite3.DatabaseError:
            return None
        if not row:
            return None

        file_name = sanitize_media_filename(row["file_name"])
        dir1 = sanitize_path_part(row["dir1_name"])
        dir2 = sanitize_path_part(row["dir2_name"])
        if not file_name or not dir1:
            return None

        paths: list[tuple[Path, str]] = []
        if optional_int(row["type"]) == 4 and dir2:
            dir3 = sanitize_path_part(proto_string_field(row["extra_buffer"], 1))
            if dir3:
                paths.append(
                    (
                        account_root / "msg" / "attach" / dir1 / dir2 / "Rec" / dir3 / "Img" / file_name,
                        "hardlink-md5-rec",
                    )
                )
        if dir2:
            paths.append((account_root / "msg" / "attach" / dir1 / dir2 / "Img" / file_name, "hardlink-md5"))

        if detail == MEDIA_IMAGE_DETAIL_THUMB:
            stem = media_stem_from_filename(file_name)
            if stem:
                if dir2:
                    thumb_dir = account_root / "msg" / "attach" / dir1 / dir2 / "Img"
                    paths.extend(
                        image_stem_candidates(thumb_dir, stem, detail, "hardlink-thumb")
                    )

        for path, source in dedupe_path_candidates(paths):
            candidate = self.media_candidate_from_path(account_root, path, source)
            if candidate:
                return candidate
        return None

    def find_md5_media_file(self, account_root: Path, md5: str) -> MediaFileCandidate | None:
        search_roots = [
            account_root / "FileStorage" / "Image",
            account_root / "msg" / "attach",
            account_root / "cache",
            account_root / "temp",
        ]
        for root in search_roots:
            if not root.is_dir():
                continue
            for suffix in (".dat", "_t.dat", "_b.dat", ".pic", ".jpg", ".png"):
                for path in root.rglob(f"{md5}{suffix}"):
                    candidate = self.media_candidate_from_path(account_root, path, "md5-search")
                    if candidate:
                        return candidate
        return None

    def media_candidate_from_path(self, account_root: Path, path: Path, source: str) -> MediaFileCandidate | None:
        try:
            resolved = path.resolve()
            root = account_root.resolve()
        except OSError:
            return None
        if not is_relative_to(resolved, root) or not resolved.is_file():
            return None
        encrypted_format = detect_wechat_dat_format(resolved)
        requires_image_key = self.media_candidate_requires_image_key(resolved, encrypted_format)
        return MediaFileCandidate(
            path=resolved,
            source=source,
            encrypted_format=encrypted_format,
            requires_image_key=requires_image_key,
        )

    def media_candidate_requires_image_key(self, path: Path, encrypted_format: str | None) -> bool:
        if encrypted_format != "wechat-v4" or self.image_key:
            return False
        try:
            with path.open("rb") as fh:
                head = fh.read(WECHAT_V4_IMAGE_HEADER_SIZE + 16)
        except OSError:
            return False
        return wechat_v4_header_requires_image_key(head)

    def message_resource_hash(self, chat_username: str, row: sqlite3.Row) -> str | None:
        if not self.databases.message_resource:
            return None
        chat_id = self.resource_chat_ids().get(chat_username)
        if chat_id is None:
            return None
        with connect(self.databases.message_resource) as con:
            item = con.execute(
                "SELECT packed_info FROM MessageResourceInfo "
                "WHERE chat_id = ? AND (message_local_id = ? OR message_svr_id = ?) "
                "ORDER BY message_id DESC LIMIT 1",
                (chat_id, row["local_id"], row["server_id"]),
            ).fetchone()
        if not item:
            return None
        return extract_resource_hash(item["packed_info"])

    def image_response(
        self,
        chat: str,
        local_id: int,
        server_id: int | None = None,
        detail: str = MEDIA_IMAGE_DETAIL_THUMB,
    ) -> BinaryResponse:
        if detail not in {MEDIA_IMAGE_DETAIL_THUMB, MEDIA_IMAGE_DETAIL_FULL}:
            raise ValueError("detail must be thumb or full")
        table = self.chat_tables().get(chat)
        if not table:
            raise ValueError("chat not found")
        if not self.databases.message:
            raise ValueError("message database not available")
        with connect(self.databases.message) as con:
            columns = message_table_columns(con, table)
            select_clause = message_select_clause(columns)
            params: list[Any] = [local_id]
            condition = "local_id = ?"
            if server_id is not None:
                condition += " AND server_id = ?"
                params.append(server_id)
            row = con.execute(
                f'SELECT {select_clause} FROM "{table}" WHERE {condition} LIMIT 1',
                params,
            ).fetchone()
        if not row:
            raise ValueError("message not found")
        content = decode_message_content(row["message_content"], row["compress_content"])
        prefix = extract_chatroom_sender_prefix(content) if is_chatroom_username(chat) else None
        image = parse_image_message(prefix[1] if prefix else content)
        if not image:
            raise ValueError("message is not an image")
        candidate = self.resolve_image_candidate(chat, table, row, image, detail)
        if not candidate:
            raise ValueError("local image file not found")
        body, content_type = self.read_media_candidate(candidate)
        return BinaryResponse(body=body, content_type=content_type)

    def read_media_candidate(self, candidate: MediaFileCandidate) -> tuple[bytes, str]:
        data = candidate.path.read_bytes()
        if candidate.encrypted_format == "wechat-v4":
            data = decode_wechat_v4_image(data, image_key=self.image_key)
        elif candidate.encrypted_format == "wechat-xor":
            data = decode_wechat_xor_image(data)
        content_type = image_content_type(data) or mimetypes.guess_type(candidate.path.name)[0]
        if not content_type or content_type == "application/octet-stream":
            raise ValueError("decoded image format is not supported")
        return data, content_type

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
            payload = self.route_get(parsed.path, query)
            if isinstance(payload, BinaryResponse):
                self.write_binary(200, payload)
            else:
                self.write_json(200, payload)
        except ValueError as exc:
            self.write_json(400, {"ok": False, "error": str(exc)})
        except Exception as exc:
            self.write_json(500, {"ok": False, "error": str(exc)})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        try:
            payload = self.route_post(parsed.path, query, self.read_json_body())
            self.write_json(200, payload)
        except ValueError as exc:
            self.write_json(400, {"ok": False, "error": str(exc)})
        except Exception as exc:
            self.write_json(500, {"ok": False, "error": str(exc)})

    def read_json_body(self) -> dict:
        length_header = self.headers.get("Content-Length", "0")
        try:
            length = int(length_header)
        except ValueError as exc:
            raise ValueError("Content-Length must be an integer") from exc
        if length <= 0:
            return {}
        if length > 1_000_000:
            raise ValueError("request body is too large")
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("request body must be valid JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError("request body must be a JSON object")
        return payload

    def route_get(self, path: str, query: dict[str, list[str]]) -> dict | BinaryResponse:
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
        if path == "/api/media/image":
            chat = query_string(query, "chat")
            if not chat:
                raise ValueError("chat is required")
            return self.store.image_response(
                chat=chat,
                local_id=query_int(query, "local_id", 0),
                server_id=query_optional_int(query, "server_id"),
                detail=query_string(query, "detail") or MEDIA_IMAGE_DETAIL_THUMB,
            )
        raise ValueError(f"Unknown API path: {path}")

    def route_post(self, path: str, query: dict[str, list[str]], body: dict) -> dict:
        if path == "/api/summary":
            return self.store.summarize_messages(
                chat=body_string(body, "chat") or query_string(query, "chat"),
                after=body_optional_int(body, "after"),
                before=body_optional_int(body, "before"),
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

    def write_binary(self, status: int, payload: BinaryResponse) -> None:
        self.send_response(status)
        self.send_header("Content-Type", payload.content_type)
        self.send_header("Cache-Control", payload.cache_control)
        self.send_header("Access-Control-Allow-Origin", "http://127.0.0.1")
        self.send_header("Content-Length", str(len(payload.body)))
        self.end_headers()
        self.wfile.write(payload.body)

    def log_message(self, format: str, *args: Any) -> None:
        return


def run_dashboard_server(decrypted_dir: Path, host: str, port: int, image_key: str | None = None) -> None:
    store = DashboardStore(decrypted_dir, image_key=image_key)

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
        hardlink=find_db(root, ["hardlink-*.db", "hardlink.db"]),
    )


def find_db(root: Path, patterns: list[str]) -> Path | None:
    for pattern in patterns:
        matches = sorted(root.glob(pattern), key=lambda path: path.stat().st_mtime, reverse=True)
        if matches:
            return matches[0]
    return None


@contextmanager
def connect(path: Path):
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        yield con
    finally:
        con.close()


def build_message_query(
    table: str,
    limit: int,
    before: int | None,
    after: int | None,
    local_type: int | None,
    columns: set[str] | None = None,
) -> tuple[str, list[Any]]:
    conditions: list[str] = []
    params: list[Any] = []
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
    sql = f'SELECT {message_select_clause(columns)} FROM "{table}"{where_sql} ORDER BY create_time DESC LIMIT ?'
    params.append(limit)
    return sql, params


def message_table_columns(con: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in con.execute(f'PRAGMA table_info("{table}")').fetchall()}


def message_select_clause(columns: set[str] | None = None) -> str:
    selected = list(MESSAGE_BASE_COLUMNS)
    if columns:
        selected.extend(column for column in MESSAGE_OPTIONAL_COLUMNS if column in columns)
    return ", ".join(f'"{column}"' for column in selected)


def account_root_from_manifest(manifest: Path) -> Path | None:
    roots = account_roots_from_manifest(manifest)
    return roots[0] if roots else None


def account_roots_from_manifest(manifest: Path) -> list[Path]:
    if not manifest.exists():
        return []
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    roots: list[Path] = []
    for item in payload.get("databases", []):
        source = item.get("source") if isinstance(item, dict) else None
        if not source:
            continue
        account_root = account_root_from_source_path(source)
        if account_root and account_root.exists():
            roots.append(account_root)
    return dedupe_paths(roots)


def account_root_from_source_path(source: str | None) -> Path | None:
    if not source:
        return None
    path = Path(source)
    parts_lower = [part.lower() for part in path.parts]
    if "db_storage" in parts_lower:
        db_storage_at = parts_lower.index("db_storage")
        if db_storage_at > 0:
            return Path(*path.parts[:db_storage_at])
    if "msg" in parts_lower:
        msg_at = parts_lower.index("msg")
        if msg_at > 0:
            return Path(*path.parts[:msg_at])
    return None


def account_from_source_path(source: str | None) -> str | None:
    account_root = account_root_from_source_path(source)
    if not account_root:
        return None
    return account_root.name


def account_roots_from_windows_config(account_names: list[str]) -> list[Path]:
    config_roots = read_windows_weixin_config_roots()
    roots: list[Path] = []
    for config_root in config_roots:
        roots.extend(account_roots_from_storage_root(config_root, account_names))
    return dedupe_paths(roots)


def account_roots_from_storage_root(storage_root: Path, account_names: list[str]) -> list[Path]:
    roots: list[Path] = []
    account_filters = {normalize_account_part(value).lower() for value in account_names if value}
    candidates = [storage_root]
    if storage_root.name.lower() != "xwechat_files":
        candidates.append(storage_root / "xwechat_files")

    for candidate in candidates:
        if looks_like_wechat_account_root(candidate):
            if account_root_matches(candidate, account_filters):
                roots.append(candidate)
            continue
        if not candidate.is_dir():
            continue
        try:
            children = list(candidate.iterdir())
        except OSError:
            continue
        for child in children:
            if child.is_dir() and looks_like_wechat_account_root(child) and account_root_matches(child, account_filters):
                roots.append(child)
    return roots


def looks_like_wechat_account_root(path: Path) -> bool:
    return (path / "db_storage").exists() or (path / "msg").exists()


def account_root_matches(path: Path, account_filters: set[str]) -> bool:
    if not account_filters:
        return True
    normalized = normalize_account_part(path.name).lower()
    return normalized in account_filters


def dedupe_paths(paths: list[Path]) -> list[Path]:
    result: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        try:
            resolved = path.expanduser().resolve()
        except OSError:
            continue
        if resolved not in seen:
            result.append(resolved)
            seen.add(resolved)
    return result


def parse_image_message(content: str | None) -> dict | None:
    root = parse_xml_root(content)
    if root is None:
        return None
    image = root.find("img")
    if image is None:
        return None
    width = optional_int(image.attrib.get("cdnthumbwidth"))
    height = optional_int(image.attrib.get("cdnthumbheight"))
    md5 = first_present(
        image.attrib.get("md5"),
        image.attrib.get("originsourcemd5"),
        image.attrib.get("rawmd5"),
        image.attrib.get("rawthumbmd5"),
        find_text(root, "./img/md5"),
        find_text(root, "./img/originsourcemd5"),
        find_text(root, "./md5"),
    )
    return {
        "width": width,
        "height": height,
        "size": optional_int(image.attrib.get("length")),
        "cdn_thumb_size": optional_int(image.attrib.get("cdnthumblength")),
        "md5": md5,
        "aeskey": image.attrib.get("aeskey"),
        "cdn_file_ids": [
            value for value in [
                sanitize_media_file_stem(image.attrib.get("cdnthumburl")),
                sanitize_media_file_stem(image.attrib.get("cdnmidimgurl")),
                sanitize_media_file_stem(image.attrib.get("cdnbigimgurl")),
            ] if value
        ],
    }


def parse_xml_root(content: str | None) -> ET.Element | None:
    if not content:
        return None
    body = content.strip()
    if not body.startswith("<"):
        return None
    try:
        return ET.fromstring(body)
    except ET.ParseError:
        return None


def summarize_message_content(content: str | None, media: dict | None = None) -> str | None:
    if media and media.get("kind") == "image":
        return IMAGE_PLACEHOLDER_TEXT
    return summarize_xml_message(content)


def media_image_url(chat: str, local_id: Any, server_id: Any, detail: str) -> str:
    params = {
        "chat": chat,
        "local_id": local_id,
        "detail": detail,
    }
    if server_id not in {None, ""}:
        params["server_id"] = server_id
    return "/api/wechat/media/image?" + urlencode(params)


def image_file_stem_from_row(row: sqlite3.Row) -> str | None:
    info = packed_image_info(row_value(row, "packed_info_data"))
    return sanitize_media_file_stem(info.get("filename"))


def image_stem_candidates(directory: Path, stem: str, detail: str, source_prefix: str) -> list[tuple[Path, str]]:
    filename = sanitize_media_filename(stem)
    stem = sanitize_media_file_stem(stem)
    if not filename or not stem:
        return []
    candidates: list[tuple[Path, str]] = []
    if Path(filename).suffix.lower() in {".dat", ".pic", ".jpg", ".jpeg", ".png", ".gif", ".webp"}:
        candidates.append((directory / filename, source_prefix))

    if detail == MEDIA_IMAGE_DETAIL_THUMB:
        suffixes = [
            ("_t.dat", "thumb"),
            ("_b.dat", "bubble"),
            (".dat", "full"),
            ("_thumb.jpg", "thumb-cache"),
            (".jpg", "jpg"),
            (".png", "png"),
            (".pic", "pic"),
        ]
    else:
        suffixes = [
            ("_W.dat", "wide"),
            ("_h.dat", "hd"),
            (".dat", "full"),
            ("_t.dat", "thumb"),
            (".jpg", "jpg"),
            (".png", "png"),
            (".pic", "pic"),
        ]
    candidates.extend((directory / f"{stem}{suffix}", f"{source_prefix}-{label}") for suffix, label in suffixes)
    return dedupe_path_candidates(candidates)


def dedupe_path_candidates(candidates: list[tuple[Path, str]]) -> list[tuple[Path, str]]:
    result: list[tuple[Path, str]] = []
    seen: set[str] = set()
    for path, source in candidates:
        key = str(path).lower()
        if key in seen:
            continue
        result.append((path, source))
        seen.add(key)
    return result


def row_value(row: sqlite3.Row, key: str) -> Any:
    return row[key] if key in row.keys() else None


def packed_image_info(value: Any) -> dict[str, Any]:
    if isinstance(value, memoryview):
        value = value.tobytes()
    if not isinstance(value, bytes) or not value:
        return {}

    # Weixin 4.0.3+ stores image metadata as PackedInfoDataImg2:
    # top-level field 3 is ImageInfo; ImageInfo field 4 is filename.
    for payload in proto_length_fields(value, 3):
        nested = {
            "height": proto_varint_field(payload, 1),
            "width": proto_varint_field(payload, 2),
            "filename": proto_string_field(payload, 4),
        }
        if nested["filename"] or nested["width"] or nested["height"]:
            return nested

    # Some test builds used PackedInfoDataImg: top-level field 3 is filename.
    filename = proto_string_field(value, 3)
    if filename:
        return {"filename": filename}
    return {}


def proto_length_fields(data: bytes, wanted_field: int) -> list[bytes]:
    values: list[bytes] = []
    for field, wire_type, value in iter_proto_fields(data):
        if field == wanted_field and wire_type == 2 and isinstance(value, bytes):
            values.append(value)
    return values


def proto_string_field(data: Any, wanted_field: int) -> str | None:
    if isinstance(data, memoryview):
        data = data.tobytes()
    if not isinstance(data, bytes) or not data:
        return None
    for field, wire_type, value in iter_proto_fields(data):
        if field != wanted_field or wire_type != 2 or not isinstance(value, bytes):
            continue
        try:
            text = value.decode("utf-8")
        except UnicodeDecodeError:
            continue
        text = text.strip().strip('"').strip()
        if is_plausible_media_filename(text):
            return text
    return None


def proto_varint_field(data: bytes, wanted_field: int) -> int | None:
    for field, wire_type, value in iter_proto_fields(data):
        if field == wanted_field and wire_type == 0 and isinstance(value, int):
            return value
    return None


def iter_proto_fields(data: bytes):
    offset = 0
    length = len(data)
    while offset < length:
        try:
            key, offset = read_proto_varint(data, offset)
        except ValueError:
            return
        if key == 0:
            return
        field = key >> 3
        wire_type = key & 0x07
        try:
            if wire_type == 0:
                value, offset = read_proto_varint(data, offset)
            elif wire_type == 1:
                if offset + 8 > length:
                    return
                value = data[offset:offset + 8]
                offset += 8
            elif wire_type == 2:
                size, offset = read_proto_varint(data, offset)
                if size < 0 or offset + size > length:
                    return
                value = data[offset:offset + size]
                offset += size
            elif wire_type == 5:
                if offset + 4 > length:
                    return
                value = data[offset:offset + 4]
                offset += 4
            else:
                return
        except ValueError:
            return
        yield field, wire_type, value


def read_proto_varint(data: bytes, offset: int) -> tuple[int, int]:
    shift = 0
    value = 0
    while offset < len(data) and shift <= 63:
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, offset
        shift += 7
    raise ValueError("invalid protobuf varint")


def sanitize_media_file_stem(value: Any) -> str | None:
    filename = sanitize_media_filename(value)
    if not filename:
        return None
    suffix = Path(filename).suffix
    return filename[: -len(suffix)] if suffix else filename


def media_stem_from_filename(value: Any) -> str | None:
    return sanitize_media_file_stem(value)


def sanitize_media_filename(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    name = Path(value.strip().strip('"').strip()).name
    if not is_plausible_media_filename(name):
        return None
    return name


def sanitize_path_part(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    part = Path(value.strip().strip('"').strip()).name
    if not part or part in {".", ".."} or any(char in part for char in '\\/:*?"<>|\x00'):
        return None
    return part


def is_plausible_media_filename(value: str) -> bool:
    if not value or len(value) > 260:
        return False
    if value in {".", ".."}:
        return False
    if any(char in value for char in '\\/:*?"<>|\x00'):
        return False
    return all(not unicodedata.category(char).startswith("C") for char in value)


def month_from_timestamp(value: Any) -> str:
    timestamp = optional_int(value) or 0
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m")


def extract_resource_hash(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, memoryview):
        value = value.tobytes()
    if isinstance(value, str):
        value = value.encode("utf-8", errors="ignore")
    if not isinstance(value, bytes):
        return None
    match = re.search(rb"[0-9a-f]{32}", value, re.IGNORECASE)
    return match.group(0).decode("ascii").lower() if match else None


def normalize_md5(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip().lower()
    return value if re.fullmatch(r"[0-9a-f]{32}", value) else None


def first_present(*values: str | None) -> str | None:
    for value in values:
        if value:
            return value
    return None


def detect_wechat_dat_format(path: Path) -> str | None:
    if path.suffix.lower() not in {".dat", ".pic"}:
        return None
    try:
        with path.open("rb") as fh:
            head = fh.read(16)
    except OSError:
        return None
    if is_wechat_v4_image_header(head):
        return "wechat-v4"
    if infer_xor_key(head) is not None:
        return "wechat-xor"
    return None


def is_wechat_v4_image_header(head: bytes) -> bool:
    return head[:6] in WECHAT_V4_IMAGE_AES_KEYS


def is_wechat_v4_v2_image_header(head: bytes) -> bool:
    return head[:6] == b"\x07\x08V2\x08\x07"


def decode_wechat_xor_image(data: bytes) -> bytes:
    key = infer_xor_key(data[:16])
    if key is None:
        raise ValueError("unsupported WeChat image dat format")
    return bytes(value ^ key for value in data)


def infer_xor_key(head: bytes) -> int | None:
    signatures = (b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"GIF87a", b"GIF89a", b"RIFF")
    for signature in signatures:
        if len(head) < len(signature):
            continue
        key = head[0] ^ signature[0]
        if bytes(value ^ key for value in head[: len(signature)]) == signature:
            return key
    return None


def decode_wechat_v4_image(data: bytes, image_key: bytes | None = None) -> bytes:
    if len(data) < WECHAT_V4_IMAGE_HEADER_SIZE or not is_wechat_v4_image_header(data):
        raise ValueError("unsupported WeChat 4 image format")
    header = data[:WECHAT_V4_IMAGE_HEADER_SIZE]
    aes_key = image_key or WECHAT_V4_IMAGE_AES_KEYS[header[:6]]
    encrypt_length = struct.unpack_from("<H", header, 6)[0]
    cipher_length = encrypt_length // 16 * 16 + 16
    payload = data[WECHAT_V4_IMAGE_HEADER_SIZE:]
    if cipher_length <= 0 or not payload:
        raise ValueError("invalid WeChat 4 image payload")
    encrypted_head = payload[:cipher_length]
    rest = payload[cipher_length:]
    if len(encrypted_head) % 16:
        encrypted_head += b"\x00" * (16 - len(encrypted_head) % 16)
    with AesEcbDecryptor(aes_key) as aes:
        decrypted_head = aes.decrypt(encrypted_head)
    decrypted_head = strip_pkcs_padding(decrypted_head)
    if encrypt_length and len(decrypted_head) > encrypt_length:
        decrypted_head = decrypted_head[:encrypt_length]
    if image_key is None and is_wechat_v4_v2_image_header(header) and image_content_type(decrypted_head) is None:
        raise ValueError("WeChat 4 V2 image key is required")

    xor_tail_size = min(len(rest), WECHAT_V4_IMAGE_XOR_TAIL_SIZE)
    middle = rest[:-xor_tail_size] if xor_tail_size else rest
    encrypted_tail = rest[-xor_tail_size:] if xor_tail_size else b""
    if encrypted_tail:
        xor_key = infer_image_tail_xor_key(encrypted_tail)
        if xor_key is None:
            xor_key = infer_wechat_v4_tail_xor_key(decrypted_head, middle, encrypted_tail)
        if xor_key is None:
            raise ValueError("unable to infer WeChat 4 image xor key")
        tail = bytes(value ^ xor_key for value in encrypted_tail)
    else:
        tail = b""
    decoded = decrypted_head + middle + tail
    if image_content_type(decoded) is None:
        raise ValueError("decoded WeChat 4 image is not a supported image")
    return decoded


def wechat_v4_header_requires_image_key(head: bytes) -> bool:
    if len(head) < WECHAT_V4_IMAGE_HEADER_SIZE + 16 or not is_wechat_v4_v2_image_header(head):
        return False
    encrypted_block = head[WECHAT_V4_IMAGE_HEADER_SIZE:WECHAT_V4_IMAGE_HEADER_SIZE + 16]
    try:
        with AesEcbDecryptor(WECHAT_V4_IMAGE_AES_KEYS[b"\x07\x08V2\x08\x07"]) as aes:
            decrypted_block = aes.decrypt(encrypted_block)
    except Exception:
        return True
    return image_content_type(decrypted_block) is None


def strip_pkcs_padding(data: bytes) -> bytes:
    if not data:
        return data
    pad_length = data[-1]
    if 1 <= pad_length <= 16 and data.endswith(bytes([pad_length]) * pad_length):
        return data[:-pad_length]
    return data


def infer_image_tail_xor_key(tail: bytes) -> int | None:
    known_tails = (
        b"\xff\xd9",
        b"IEND\xaeB`\x82",
        b";",
    )
    for signature in known_tails:
        if len(tail) < len(signature):
            continue
        keys = [tail[-len(signature) + index] ^ signature[index] for index in range(len(signature))]
        if len(set(keys)) == 1:
            return keys[0]
    return None


def infer_wechat_v4_tail_xor_key(head: bytes, middle: bytes, tail: bytes) -> int | None:
    if infer_image_tail_xor_key(tail) is not None:
        return infer_image_tail_xor_key(tail)
    if image_content_type(head + middle + tail) is not None:
        return 0
    return None


def image_content_type(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def normalize_image_key(value: str | bytes | None) -> bytes | None:
    if value in {None, ""}:
        return None
    if isinstance(value, bytes):
        if len(value) != 16:
            raise ValueError("Image key bytes must be exactly 16 bytes.")
        return value
    text = value.strip()
    if text.lower().startswith("0x"):
        text = text[2:]
    if re.fullmatch(r"[0-9a-fA-F]{32}", text):
        return bytes.fromhex(text)
    raw = text.encode("utf-8")
    if len(raw) == 16:
        return raw
    raise ValueError("Image key must be 32 hex characters or 16 ASCII characters.")


def is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


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
    sender_username: str | None,
    sender_contact: dict | None,
    is_self: bool,
    message_content: str | None,
    media: dict | None = None,
) -> dict:
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
        "sender_username": sender_username,
        "sender_display_name": display_name(sender_contact, fallback=sender_username or str(row["real_sender_id"])),
        "sender_contact": sender_contact,
        "is_self": is_self,
        "message_kind": media["kind"] if media else "text",
        "media": media,
        "message_content": message_content,
    }


def resolve_message_sender(
    chat_username: str,
    content: str | None,
    local_type: Any,
    real_sender_id: Any,
    contact_map: dict[str, dict],
    sender_name_map: dict[int, str],
) -> tuple[str | None, dict | None, str | None]:
    sender_username: str | None = None
    sender_contact: dict | None = None
    message_content = content

    prefix = extract_chatroom_sender_prefix(content) if is_chatroom_username(chat_username) else None
    xml_sender_username = extract_xml_sender_username(prefix[1] if prefix else content)
    if prefix:
        prefix_username, message_content = prefix
        sender_username = xml_sender_username or prefix_username
        sender_contact = contact_map.get(sender_username)
    elif xml_sender_username and is_chatroom_username(chat_username):
        sender_username = xml_sender_username
        sender_contact = contact_map.get(sender_username)
    else:
        sender_id = optional_int(real_sender_id)
        if sender_id is not None and not is_unreliable_real_sender_type(local_type, chat_username):
            sender_username = sender_name_map.get(sender_id)
            if sender_username:
                sender_contact = contact_map.get(sender_username)

    if sender_username is None and real_sender_id not in {None, ""}:
        sender_username = str(real_sender_id)

    return sender_username, sender_contact, message_content


def is_unreliable_real_sender_type(local_type: Any, chat_username: str) -> bool:
    return is_chatroom_username(chat_username) and optional_int(local_type) in UNRELIABLE_REAL_SENDER_TYPES


def message_matches_query(
    content: str | None,
    sender_username: str | None,
    sender_contact: dict | None,
    q: str,
) -> bool:
    needle = q.casefold()
    if content and needle in content.casefold():
        return True
    if sender_username and needle in sender_username.casefold():
        return True
    if sender_contact:
        for key in ("display_name", "remark", "nick_name", "alias", "username"):
            value = sender_contact.get(key)
            if isinstance(value, str) and needle in value.casefold():
                return True
    return False


def extract_chatroom_sender_prefix(content: str | None) -> tuple[str, str] | None:
    if not content:
        return None
    prefix, separator, remainder = content.partition(":\n")
    if not separator:
        return None
    if not is_plausible_sender_prefix(prefix):
        return None
    return prefix, remainder


def extract_xml_sender_username(content: str | None) -> str | None:
    if not content:
        return None
    body = content.strip()
    if not body.startswith("<"):
        return None
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return None

    candidates: list[str | None] = []
    if root.tag == "sysmsg":
        for pat in root.iter("pat"):
            candidates.extend(
                [
                    pat.attrib.get("fromusername"),
                    pat.attrib.get("fromusr"),
                    pat.attrib.get("username"),
                ]
            )
    for path in (
        "./appmsg/patinfo/fromusername",
        "./appmsg/patinfo/fromusr",
        "./appmsg/patinfo/username",
        "./appmsg/fromusername",
        "./fromusername",
    ):
        node = root.find(path)
        if node is not None:
            candidates.append(node.text)

    for candidate in candidates:
        if candidate and is_plausible_sender_prefix(candidate):
            return candidate
    return None


def summarize_xml_message(content: str | None) -> str | None:
    if not content:
        return content
    body = content.strip()
    if not body.startswith("<"):
        return content
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return content
    for path in ("./appmsg/title", "./sysmsgtemplate/content_template/template"):
        value = find_text(root, path)
        if value:
            return value
    return content


def find_text(root: ET.Element, path: str) -> str | None:
    node = root.find(path)
    if node is None or node.text is None:
        return None
    value = node.text.strip()
    return value or None


def is_chatroom_username(username: str) -> bool:
    return username.endswith("@chatroom")


def is_plausible_sender_prefix(value: str) -> bool:
    if not value or len(value) > 128:
        return False
    return re.fullmatch(r"[A-Za-z0-9_@.-]{3,128}", value) is not None


def optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def decode_message_content(message_content: Any, compress_content: Any = None) -> str | None:
    decoded = decode_message_content_value(message_content)
    if decoded not in {None, ""}:
        return decoded
    return decode_message_content_value(compress_content)


def decode_message_content_value(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, memoryview):
        value = value.tobytes()
    if not isinstance(value, bytes):
        return str(value)
    if not value:
        return None

    payload = value
    if value.startswith(ZSTD_MAGIC):
        payload = decompress_zstd(value)
        if payload is None:
            return f"[zstd-compressed message: {len(value)} bytes]"

    text = decode_text_bytes(payload)
    if text is not None:
        return text
    return f"[binary message content: {len(value)} bytes]"


def decompress_zstd(value: bytes) -> bytes | None:
    try:
        zstd = importlib.import_module("compression.zstd")
    except ModuleNotFoundError:
        return None
    try:
        return zstd.decompress(value)
    except Exception:
        return None


def decode_text_bytes(value: bytes) -> str | None:
    if not value:
        return None
    encodings = ["utf-8-sig"]
    if value.startswith((b"\xff\xfe", b"\xfe\xff")) or has_utf16_nul_pattern(value):
        encodings.insert(0, "utf-16")
    for encoding in encodings:
        try:
            text = value.decode(encoding)
        except UnicodeDecodeError:
            continue
        if is_probably_text(text):
            return text
    text = value.decode("utf-8", errors="replace")
    if text.count("\ufffd") <= max(1, len(text) // 100) and is_probably_text(text):
        return text
    return None


def has_utf16_nul_pattern(value: bytes) -> bool:
    sample = value[:200]
    if len(sample) < 8:
        return False
    odd_nuls = sum(1 for index in range(1, len(sample), 2) if sample[index] == 0)
    even_nuls = sum(1 for index in range(0, len(sample), 2) if sample[index] == 0)
    pairs = max(1, len(sample) // 2)
    return odd_nuls / pairs > 0.3 or even_nuls / pairs > 0.3


def is_probably_text(value: str) -> bool:
    if not value:
        return False
    sample = value[:2000]
    controls = 0
    for char in sample:
        if char in "\r\n\t":
            continue
        if unicodedata.category(char).startswith("C"):
            controls += 1
    return controls / max(1, len(sample)) < 0.05


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


def summary_range(after: int | None, before: int | None) -> dict[str, int | str | None]:
    return {
        "after": after,
        "before": before,
        "after_iso": iso_from_timestamp(after),
        "before_iso": iso_from_timestamp(before),
    }


def empty_summary(reason: str, after: int | None, before: int | None) -> dict:
    range_label = " - ".join(value for value in [iso_from_timestamp(after), iso_from_timestamp(before)] if value)
    return {
        "title": "微信消息总结",
        "executive_summary": reason,
        "message_count": 0,
        "time_range": range_label or "未指定时间范围",
        "sentiment": "无可判断",
        "key_points": [],
        "decisions": [],
        "action_items": [],
        "risks": [],
        "open_questions": [],
        "notable_messages": [],
    }


def request_openai_message_summary(
    chat: str,
    chat_display_name: str,
    after: int | None,
    before: int | None,
    messages: list[dict],
) -> dict:
    config = load_openai_responses_config()
    prompt = load_summary_prompt()
    user_input = build_summary_user_input(
        chat=chat,
        chat_display_name=chat_display_name,
        after=after,
        before=before,
        messages=messages,
    )
    response_payload = call_openai_responses(config=config, instructions=prompt, input_text=user_input)
    output_text = extract_openai_output_text(response_payload)
    return {
        "summary": parse_summary_output(output_text, len(messages), after, before),
        "response_id": response_payload.get("id"),
        "model": response_payload.get("model", config.model),
    }


def load_openai_responses_config() -> OpenAIResponsesConfig:
    for path in openai_config_paths():
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"OpenAI config is not readable: {path}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"OpenAI config must be a JSON object: {path}")
        url = str(payload.get("url") or "").strip()
        api_key = str(payload.get("api_key") or payload.get("apikey") or "").strip()
        model = str(payload.get("model") or DEFAULT_OPENAI_RESPONSES_MODEL).strip()
        timeout = payload.get("timeout_seconds", 90)
        stream = bool(payload.get("stream", False))
        if not url:
            raise ValueError(f"OpenAI config missing url: {path}")
        if not api_key or api_key.lower().startswith(("replace", "sk-xxxx", "your_")):
            raise ValueError(f"OpenAI config missing api_key: {path}")
        try:
            timeout_seconds = int(timeout)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"OpenAI config timeout_seconds must be an integer: {path}") from exc
        return OpenAIResponsesConfig(
            url=url,
            api_key=api_key,
            model=model or DEFAULT_OPENAI_RESPONSES_MODEL,
            timeout_seconds=max(10, timeout_seconds),
            stream=stream,
        )
    searched = ", ".join(str(path) for path in openai_config_paths())
    raise ValueError(f"OpenAI config not found. Create .wechat-agent/openai-responses.json. Searched: {searched}")


def openai_config_paths() -> list[Path]:
    paths: list[Path] = []
    if os.environ.get(SUMMARY_CONFIG_ENV):
        paths.append(Path(os.environ[SUMMARY_CONFIG_ENV]))
    paths.extend(
        [
            Path.cwd() / ".wechat-agent" / "openai-responses.json",
            Path.cwd() / "config" / "openai-responses.json",
            Path(__file__).resolve().parents[2] / "config" / "openai-responses.json",
        ]
    )
    return unique_paths(paths)


def load_summary_prompt() -> str:
    paths: list[Path] = []
    if os.environ.get(SUMMARY_PROMPT_ENV):
        paths.append(Path(os.environ[SUMMARY_PROMPT_ENV]))
    paths.extend(
        [
            Path(__file__).resolve().parent / "prompts" / "wechat_message_summary.md",
            Path.cwd() / "prompts" / "wechat-message-summary.md",
        ]
    )
    for path in unique_paths(paths):
        if path.exists():
            try:
                prompt = path.read_text(encoding="utf-8").strip()
            except OSError as exc:
                raise ValueError(f"Summary prompt is not readable: {path}") from exc
            if prompt:
                return prompt
    searched = ", ".join(str(path) for path in unique_paths(paths))
    raise ValueError(f"Summary prompt not found. Searched: {searched}")


def unique_paths(paths: list[Path]) -> list[Path]:
    result: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path)
        if key in seen:
            continue
        result.append(path)
        seen.add(key)
    return result


def build_summary_user_input(
    chat: str,
    chat_display_name: str,
    after: int | None,
    before: int | None,
    messages: list[dict],
) -> str:
    payload = {
        "chat": chat,
        "chat_display_name": chat_display_name,
        "time_range": summary_range(after, before),
        "message_count": len(messages),
        "messages": [message_summary_item(index, message) for index, message in enumerate(messages, start=1)],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def message_summary_item(index: int, message: dict) -> dict:
    content = str(message.get("message_content") or "").strip()
    return {
        "index": index,
        "time": iso_from_timestamp(message.get("create_time")),
        "sender": message.get("sender_display_name") or message.get("sender_username") or str(message.get("real_sender_id")),
        "is_self": bool(message.get("is_self")),
        "content": truncate_text(content, SUMMARY_MAX_MESSAGE_CHARS),
    }


def truncate_text(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def call_openai_responses(config: OpenAIResponsesConfig, instructions: str, input_text: str) -> dict:
    prompt_text = (
        f"{instructions}\n\n"
        "请严格输出一个 JSON 对象，字段必须符合以下 JSON Schema。不要输出 Markdown 代码块，不要输出额外解释。\n"
        f"{json.dumps(SUMMARY_SCHEMA, ensure_ascii=False)}\n\n"
        "待总结的微信消息 JSON：\n"
        f"{input_text}"
    )
    request_payload = {
        "model": config.model,
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": prompt_text,
                    }
                ],
            }
        ],
    }
    if config.stream:
        request_payload["stream"] = True
    request = Request(
        config.url,
        data=json.dumps(request_payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=config.timeout_seconds) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ValueError(f"OpenAI Responses API error {exc.code}: {truncate_text(detail, 800)}") from exc
    except URLError as exc:
        raise ValueError(f"OpenAI Responses API is not reachable: {exc.reason}") from exc
    if config.stream:
        return parse_openai_sse_response(raw, config.model)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("OpenAI Responses API returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("OpenAI Responses API returned an unexpected payload")
    if isinstance(payload.get("error"), dict):
        message = payload["error"].get("message") or payload["error"]
        raise ValueError(f"OpenAI Responses API error: {message}")
    return payload


def extract_openai_output_text(payload: dict) -> str:
    output_text = payload.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text

    texts: list[str] = []
    for output_item in payload.get("output", []):
        if not isinstance(output_item, dict):
            continue
        for content_item in output_item.get("content", []):
            if not isinstance(content_item, dict):
                continue
            if isinstance(content_item.get("refusal"), str) and content_item["refusal"].strip():
                raise ValueError(f"OpenAI refused the summary request: {content_item['refusal']}")
            text = content_item.get("text")
            if isinstance(text, str) and text.strip():
                texts.append(text)
    if texts:
        return "\n".join(texts)
    raise ValueError("OpenAI Responses API returned no output text")


def parse_openai_sse_response(raw: str, model: str) -> dict:
    texts: list[str] = []
    response_id: str | None = None
    response_model: str | None = None
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        try:
            event = json.loads(data)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        if isinstance(event.get("response"), dict):
            response = event["response"]
            response_id = response_id or response.get("id")
            response_model = response_model or response.get("model")
        if isinstance(event.get("delta"), str):
            texts.append(event["delta"])
        if isinstance(event.get("text"), str):
            texts.append(event["text"])
        output_text = event.get("output_text")
        if isinstance(output_text, str):
            texts.append(output_text)
    if not texts:
        raise ValueError("OpenAI Responses API stream returned no output text")
    return {
        "id": response_id,
        "model": response_model or model,
        "output_text": "".join(texts),
    }


def parse_summary_output(output_text: str, message_count: int, after: int | None, before: int | None) -> dict:
    output_text = extract_json_text(output_text)
    try:
        payload = json.loads(output_text)
    except json.JSONDecodeError:
        summary = empty_summary("模型返回了非 JSON 文本，已作为摘要正文保留。", after, before)
        summary["executive_summary"] = output_text.strip()
        summary["message_count"] = message_count
        return summary
    if not isinstance(payload, dict):
        summary = empty_summary("模型返回格式不是 JSON 对象。", after, before)
        summary["message_count"] = message_count
        return summary
    summary = empty_summary("", after, before)
    summary.update(
        {
            "title": string_or_default(payload.get("title"), "微信消息总结"),
            "executive_summary": string_or_default(payload.get("executive_summary"), "本次范围内没有形成明确摘要。"),
            "message_count": message_count,
            "time_range": string_or_default(payload.get("time_range"), summary["time_range"]),
            "sentiment": string_or_default(payload.get("sentiment"), "未判断"),
            "key_points": normalize_object_list(payload.get("key_points"), ["point", "importance", "evidence"]),
            "decisions": normalize_object_list(payload.get("decisions"), ["decision", "evidence"]),
            "action_items": normalize_object_list(
                payload.get("action_items"),
                ["task", "owner", "due_time", "priority", "context"],
            ),
            "risks": normalize_object_list(payload.get("risks"), ["risk", "severity", "evidence"]),
            "open_questions": normalize_object_list(payload.get("open_questions"), ["question", "context"]),
            "notable_messages": normalize_object_list(
                payload.get("notable_messages"),
                ["time", "sender", "quote", "reason"],
            ),
        }
    )
    return summary


def extract_json_text(output_text: str) -> str:
    text = output_text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]
    return text


def normalize_object_list(value: Any, keys: list[str]) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        normalized = {key: string_or_default(item.get(key), "") for key in keys}
        if any(normalized.values()):
            result.append(normalized)
    return result


def string_or_default(value: Any, default: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return default


def body_string(body: dict, name: str) -> str | None:
    value = body.get(name)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def body_optional_int(body: dict, name: str) -> int | None:
    value = body.get(name)
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc


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
            "POST /api/summary {chat, after, before}",
            "/api/media/image?chat=&local_id=&server_id=&detail=thumb",
        ],
    }


def safe_print(message: str) -> None:
    try:
        if sys.stdout:
            print(message, flush=True)
    except (AttributeError, OSError, ValueError):
        return
