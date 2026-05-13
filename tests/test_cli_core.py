from __future__ import annotations

import json
import hashlib
import hmac
import importlib
import datetime
import os
import shutil
import sqlite3
import struct
import sys
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wechat_agent_cli.copying import copy_databases
from wechat_agent_cli.dashboard import DashboardStore, decode_message_content, decode_wechat_v4_image
from wechat_agent_cli.decrypt import decrypt_databases
from wechat_agent_cli.key_extract import (
    HMAC_SHA512_SIZE,
    IV_SIZE,
    KEY_STUB_SUFFIX,
    PAGE_SIZE,
    ROUND_COUNT,
    iter_raw_key_hex_strings,
    iter_key_pointers,
    looks_like_key_material,
    verify_wechat_sqlcipher_raw_key,
    verify_wechat_sqlcipher_key,
)
from wechat_agent_cli.keys import fingerprint_key, load_key, normalize_key, save_key
from wechat_agent_cli.scanner import scan_environment
from wechat_agent_cli.verify import verify_databases
from wechat_agent_cli.workspace import ensure_gitignore_entry


TEST_KEY = "a" * 64
try:
    stdlib_zstd = importlib.import_module("compression.zstd")
except ModuleNotFoundError:
    stdlib_zstd = None


@contextmanager
def temp_dir():
    base = Path(os.environ.get("TEST_TMPDIR", str(ROOT / ".test-tmp")))
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"test-{uuid.uuid4().hex}"
    path.mkdir()
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


class KeyTests(unittest.TestCase):
    def test_normalize_key_accepts_sqlcipher_literal(self) -> None:
        self.assertEqual(normalize_key(f"x'{TEST_KEY.upper()}'"), TEST_KEY)

    def test_fingerprint_does_not_expose_full_key(self) -> None:
        fingerprint = fingerprint_key(TEST_KEY)
        self.assertIn("sha256:", fingerprint)
        self.assertNotIn(TEST_KEY, fingerprint)

    def test_save_and_load_key(self) -> None:
        with temp_dir() as workspace:
            save_key(workspace, "default", TEST_KEY, source="manual")

            self.assertEqual(load_key(workspace, "default"), TEST_KEY)
            payload = json.loads((workspace / "secrets.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["profiles"]["default"]["fingerprint"], fingerprint_key(TEST_KEY))

    def test_verify_wechat_sqlcipher_key(self) -> None:
        passphrase = bytes.fromhex(TEST_KEY)
        salt = bytes(range(16))
        page = bytearray(PAGE_SIZE)
        page[:16] = salt
        for index in range(16, PAGE_SIZE):
            page[index] = index % 251

        mac_salt = bytes(value ^ 0x3A for value in salt)
        derived_key = hashlib.pbkdf2_hmac("sha512", passphrase, salt, ROUND_COUNT, 32)
        mac_key = hashlib.pbkdf2_hmac("sha512", derived_key, mac_salt, 2, 32)
        reserve = IV_SIZE + HMAC_SHA512_SIZE
        mac_start = PAGE_SIZE - reserve + IV_SIZE
        digest = hmac.new(mac_key, bytes(page[16:mac_start]), hashlib.sha512)
        digest.update((1).to_bytes(4, "little"))
        page[mac_start:mac_start + HMAC_SHA512_SIZE] = digest.digest()

        self.assertTrue(verify_wechat_sqlcipher_key(passphrase, bytes(page)))
        self.assertFalse(verify_wechat_sqlcipher_key(b"b" * 32, bytes(page)))

    def test_verify_wechat_sqlcipher_raw_key(self) -> None:
        raw_key = bytes(range(1, 33))
        salt = bytes(range(16))
        page = bytearray(PAGE_SIZE)
        page[:16] = salt
        for index in range(16, PAGE_SIZE):
            page[index] = (index * 7) % 251

        mac_salt = bytes(value ^ 0x3A for value in salt)
        mac_key = hashlib.pbkdf2_hmac("sha512", raw_key, mac_salt, 2, 32)
        reserve = IV_SIZE + HMAC_SHA512_SIZE
        mac_start = PAGE_SIZE - reserve + IV_SIZE
        digest = hmac.new(mac_key, bytes(page[16:mac_start]), hashlib.sha512)
        digest.update((1).to_bytes(4, "little"))
        page[mac_start:mac_start + HMAC_SHA512_SIZE] = digest.digest()

        self.assertTrue(verify_wechat_sqlcipher_raw_key(raw_key + salt, bytes(page)))
        self.assertFalse(verify_wechat_sqlcipher_raw_key(bytes(range(2, 34)) + salt, bytes(page)))

    def test_iter_key_pointers_finds_wechat_stub(self) -> None:
        pointer = 0x123456789ABC
        data = b"prefix" + pointer.to_bytes(8, "little") + KEY_STUB_SUFFIX + b"suffix"

        self.assertEqual(list(iter_key_pointers(data)), [pointer])

    def test_iter_raw_key_hex_strings_finds_ascii_and_utf16(self) -> None:
        value = ("ab" * 48).lower()
        ascii_data = b"prefix x'" + value.encode("ascii") + b"' suffix"
        utf16_data = ("x'" + value + "'").encode("utf-16le")

        self.assertEqual(list(iter_raw_key_hex_strings(ascii_data)), [value])
        self.assertEqual(list(iter_raw_key_hex_strings(utf16_data)), [value])

    def test_looks_like_key_material_filters_obvious_non_keys(self) -> None:
        self.assertFalse(looks_like_key_material(b"\x00" * 32))
        self.assertFalse(looks_like_key_material(b"a" * 32))
        self.assertTrue(looks_like_key_material(bytes(range(1, 33))))


class WorkspaceTests(unittest.TestCase):
    def test_gitignore_entry_is_appended_once(self) -> None:
        with temp_dir() as root:
            workspace = root / ".wechat-agent"
            workspace.mkdir()

            ensure_gitignore_entry(root, workspace)
            ensure_gitignore_entry(root, workspace)

            lines = (root / ".gitignore").read_text(encoding="utf-8").splitlines()
            self.assertEqual(lines.count(".wechat-agent/"), 1)


class ScannerTests(unittest.TestCase):
    def test_scan_uses_xwechat_config_roots(self) -> None:
        with temp_dir() as root:
            appdata = root / "AppData" / "Roaming"
            config = appdata / "Tencent" / "xwechat" / "config"
            config.mkdir(parents=True)
            wechat_root = root / "custom-wechat-root"
            wx4 = wechat_root / "xwechat_files" / "wxid_123" / "db_storage" / "message"
            wx4.mkdir(parents=True)
            (wx4 / "message_0.db").write_bytes(b"encrypted")
            (config / "profile.ini").write_text(str(wechat_root), encoding="utf-8")

            with patch.dict(os.environ, {"APPDATA": str(appdata)}):
                result = scan_environment(data_dirs=[], max_depth=6)

            self.assertIn(str(wechat_root.resolve()), result.roots)
            self.assertTrue(any(item.path.endswith("message_0.db") for item in result.databases))

    def test_scan_detects_wechat_3_and_4_database_layouts(self) -> None:
        with temp_dir() as root:
            wx4 = root / "xwechat_files" / "wxid_123" / "db_storage" / "message"
            wx4.mkdir(parents=True)
            (wx4 / "message_0.db").write_bytes(b"encrypted")

            wx3 = root / "wxid_456" / "Msg" / "Multi"
            wx3.mkdir(parents=True)
            (wx3 / "MSG0.db").write_bytes(b"encrypted")

            result = scan_environment(data_dirs=[root], max_depth=6)
            families = {item.family for item in result.databases}

            self.assertIn("wechat_4", families)
            self.assertIn("wechat_3_msg", families)
            message_db = next(item for item in result.databases if item.path.endswith("message_0.db"))
            self.assertEqual(message_db.account, "wxid_123")
            self.assertEqual(message_db.category, "message")


class CopyDecryptVerifyTests(unittest.TestCase):
    def test_copy_database_with_sidecars(self) -> None:
        with temp_dir() as root:
            source = root / "MSG0.db"
            source.write_bytes(b"db")
            Path(str(source) + "-wal").write_bytes(b"wal")
            Path(str(source) + "-shm").write_bytes(b"shm")

            result = copy_databases(
                workspace=root / ".wechat-agent",
                sources=[source],
                data_dirs=[],
                max_depth=1,
                run_id="test-run",
            )

            self.assertTrue(result["ok"])
            copied = Path(result["databases"][0]["dest"])
            self.assertTrue(copied.exists())
            self.assertTrue(Path(str(copied) + "-wal").exists())
            self.assertTrue(Path(str(copied) + "-shm").exists())
            self.assertTrue((root / ".wechat-agent" / "work" / "test-run" / "manifest.json").exists())

    def test_copy_filters_by_account_and_core_categories(self) -> None:
        with temp_dir() as root:
            account_a_message = root / "xwechat_files" / "wxid_a123_3d2d" / "db_storage" / "message"
            account_a_message.mkdir(parents=True)
            (account_a_message / "message_0.db").write_bytes(b"a")
            account_a_sns = root / "xwechat_files" / "wxid_a123_3d2d" / "db_storage" / "sns"
            account_a_sns.mkdir(parents=True)
            (account_a_sns / "sns.db").write_bytes(b"a")
            account_b_message = root / "xwechat_files" / "wxid_b456_4747" / "db_storage" / "message"
            account_b_message.mkdir(parents=True)
            (account_b_message / "message_0.db").write_bytes(b"b")

            result = copy_databases(
                workspace=root / ".wechat-agent",
                sources=[],
                data_dirs=[root],
                max_depth=7,
                run_id="filtered-run",
                accounts=["wxid_a123"],
                categories=[],
                names=[],
                core=True,
            )

            copied_sources = [item["source"] for item in result["databases"]]
            self.assertEqual(len(copied_sources), 1)
            self.assertIn("wxid_a123_3d2d", copied_sources[0])
            self.assertIn("message_0.db", copied_sources[0])

    def test_plain_sqlite_decrypt_and_verify(self) -> None:
        with temp_dir() as root:
            source = root / "message.db"
            con = sqlite3.connect(str(source))
            try:
                con.execute(
                    "CREATE TABLE message (CreateTime INTEGER, Type INTEGER, Content TEXT)"
                )
                con.commit()
            finally:
                con.close()

            decrypt_result = decrypt_databases(
                input_path=source,
                output_dir=root / "decrypted",
                key=None,
                provider_cmd=None,
            )
            self.assertTrue(decrypt_result["ok"])
            self.assertEqual(decrypt_result["databases"][0]["method"], "plain-copy")
            manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(manifest["decrypt"]["ok"])

            verify_result = verify_databases(root / "decrypted")
            self.assertTrue(verify_result["ok"])
            self.assertEqual(verify_result["databases"][0]["schema_family"], "wechat_4_message")


class DashboardStoreTests(unittest.TestCase):
    def test_decode_message_content_decompresses_zstd_blob(self) -> None:
        if stdlib_zstd is None:
            self.skipTest("stdlib compression.zstd is not available")
        expected = "compressed dashboard message"
        blob = stdlib_zstd.compress(expected.encode("utf-8"))

        self.assertEqual(decode_message_content(blob), expected)

    def test_single_chat_sender_uses_name2id_rowid_not_contact_id(self) -> None:
        with temp_dir() as root:
            decrypted = root / "decrypted"
            decrypted.mkdir()
            self_username = "wxid_self"
            peer_username = "wxid_peer"
            table = "Msg_" + hashlib.md5(peer_username.encode("utf-8")).hexdigest()

            message_db = decrypted / "message_0-test.db"
            con = sqlite3.connect(str(message_db))
            try:
                con.execute("CREATE TABLE Name2Id (user_name TEXT, is_session INTEGER)")
                con.execute("INSERT INTO Name2Id (rowid, user_name, is_session) VALUES (2, ?, 0)", (self_username,))
                con.execute("INSERT INTO Name2Id (rowid, user_name, is_session) VALUES (1973, ?, 1)", (peer_username,))
                con.execute(
                    f'CREATE TABLE "{table}" ('
                    "local_id INTEGER, server_id INTEGER, local_type INTEGER, sort_seq INTEGER, "
                    "real_sender_id INTEGER, create_time INTEGER, status INTEGER, upload_status INTEGER, "
                    "download_status INTEGER, server_seq INTEGER, origin_source INTEGER, source BLOB, "
                    "message_content TEXT, compress_content TEXT, packed_info_data BLOB, "
                    "WCDB_CT_message_content BLOB, WCDB_CT_source BLOB)"
                )
                con.execute(
                    f'INSERT INTO "{table}" '
                    "(local_id, server_id, local_type, sort_seq, real_sender_id, create_time, status, "
                    "upload_status, download_status, server_seq, origin_source, source, message_content, "
                    "compress_content, packed_info_data, WCDB_CT_message_content, WCDB_CT_source) "
                    "VALUES (1, 1, 1, 1, 2, 1700000000, 3, 0, 0, 0, 0, x'', 'from self', '', x'', x'', x'')"
                )
                con.execute(
                    f'INSERT INTO "{table}" '
                    "(local_id, server_id, local_type, sort_seq, real_sender_id, create_time, status, "
                    "upload_status, download_status, server_seq, origin_source, source, message_content, "
                    "compress_content, packed_info_data, WCDB_CT_message_content, WCDB_CT_source) "
                    "VALUES (2, 2, 1, 2, 1973, 1700000001, 3, 0, 0, 0, 0, x'', 'from peer', '', x'', x'', x'')"
                )
                con.commit()
            finally:
                con.close()

            contact_db = decrypted / "contact-test.db"
            con = sqlite3.connect(str(contact_db))
            try:
                con.execute(
                    "CREATE TABLE contact (id INTEGER, username TEXT, alias TEXT, remark TEXT, nick_name TEXT, "
                    "local_type INTEGER, verify_flag INTEGER, is_in_chat_room INTEGER, chat_room_type INTEGER, "
                    "delete_flag INTEGER)"
                )
                con.execute("INSERT INTO contact VALUES (2, 'wrong_self', '', 'Wrong Self', '', 0, 0, 0, 0, 0)")
                con.execute("INSERT INTO contact VALUES (1973, 'wrong_peer', '', 'Wrong Peer', '', 0, 0, 0, 0, 0)")
                con.execute(
                    "INSERT INTO contact VALUES (10, ?, '', 'Me Display', '', 0, 0, 0, 0, 0)",
                    (self_username,),
                )
                con.execute(
                    "INSERT INTO contact VALUES (11, ?, '', 'Peer Display', '', 0, 0, 0, 0, 0)",
                    (peer_username,),
                )
                con.commit()
            finally:
                con.close()

            session_db = decrypted / "session-test.db"
            con = sqlite3.connect(str(session_db))
            try:
                con.execute(
                    "CREATE TABLE SessionTable (username TEXT, type INTEGER, unread_count INTEGER, summary TEXT, "
                    "last_timestamp INTEGER, sort_timestamp INTEGER, last_msg_type INTEGER, last_msg_sender TEXT, "
                    "last_sender_display_name TEXT)"
                )
                con.execute(
                    "INSERT INTO SessionTable VALUES (?, 1, 0, 'summary', 1700000001, 1700000001, 1, ?, 'sender')",
                    (peer_username, peer_username),
                )
                con.commit()
            finally:
                con.close()

            store = DashboardStore(decrypted)
            messages = {item["local_id"]: item for item in store.messages(chat=peer_username)["items"]}

            self.assertEqual(messages[1]["sender_username"], self_username)
            self.assertEqual(messages[1]["sender_display_name"], "Me Display")
            self.assertTrue(messages[1]["is_self"])
            self.assertEqual(messages[2]["sender_username"], peer_username)
            self.assertEqual(messages[2]["sender_display_name"], "Peer Display")
            self.assertFalse(messages[2]["is_self"])

    def test_dashboard_store_maps_contacts_sessions_and_messages(self) -> None:
        if stdlib_zstd is None:
            self.skipTest("stdlib compression.zstd is not available")
        with temp_dir() as root:
            decrypted = root / "decrypted"
            decrypted.mkdir()
            username = "group_room@chatroom"
            sender_username = "wxid_sender"
            wrong_sender_username = "wxid_wrong"
            table = "Msg_" + hashlib.md5(username.encode("utf-8")).hexdigest()

            message_db = decrypted / "message_0-test.db"
            con = sqlite3.connect(str(message_db))
            try:
                con.execute("CREATE TABLE Name2Id (user_name TEXT, is_session INTEGER)")
                con.execute("INSERT INTO Name2Id VALUES (?, 1)", (username,))
                con.execute(
                    f'CREATE TABLE "{table}" ('
                    "local_id INTEGER, server_id INTEGER, local_type INTEGER, sort_seq INTEGER, "
                    "real_sender_id INTEGER, create_time INTEGER, status INTEGER, upload_status INTEGER, "
                    "download_status INTEGER, server_seq INTEGER, origin_source INTEGER, source BLOB, "
                    "message_content TEXT, compress_content TEXT, packed_info_data BLOB, "
                    "WCDB_CT_message_content BLOB, WCDB_CT_source BLOB)"
                )
                con.execute(
                    f'INSERT INTO "{table}" '
                    "(local_id, server_id, local_type, sort_seq, real_sender_id, create_time, status, "
                    "upload_status, download_status, server_seq, origin_source, source, message_content, "
                    "compress_content, packed_info_data, WCDB_CT_message_content, WCDB_CT_source) "
                    "VALUES (1, 99, 1, 10, 3, 1700000000, 0, 0, 0, 0, 0, x'', ?, '', x'', x'', x'')",
                    (f"{sender_username}:\nhello dashboard",),
                )
                compressed = stdlib_zstd.compress(
                    f"{sender_username}:\ncompressed dashboard message".encode("utf-8")
                )
                con.execute(
                    f'INSERT INTO "{table}" '
                    "(local_id, server_id, local_type, sort_seq, real_sender_id, create_time, status, "
                    "upload_status, download_status, server_seq, origin_source, source, message_content, "
                    "compress_content, packed_info_data, WCDB_CT_message_content, WCDB_CT_source) "
                    "VALUES (2, 100, 1, 11, 3, 1700000001, 0, 0, 0, 0, 0, x'', ?, '', x'', x'', x'')",
                    (sqlite3.Binary(compressed),),
                )
                xml_content = (
                    f"{wrong_sender_username}:\n"
                    f"<msg><appmsg><title>xml special dashboard</title><type>57</type></appmsg>"
                    f"<fromusername>{sender_username}</fromusername></msg>"
                )
                con.execute(
                    f'INSERT INTO "{table}" '
                    "(local_id, server_id, local_type, sort_seq, real_sender_id, create_time, status, "
                    "upload_status, download_status, server_seq, origin_source, source, message_content, "
                    "compress_content, packed_info_data, WCDB_CT_message_content, WCDB_CT_source) "
                    "VALUES (3, 101, 244813135921, 12, 3, 1700000002, 0, 0, 0, 0, 0, x'', ?, '', x'', x'', x'')",
                    (xml_content,),
                )
                pat_content = (
                    f"<msg><appmsg><title>Pat special dashboard</title><type>62</type>"
                    f"<patinfo><fromusername>{sender_username}</fromusername>"
                    f"<chatusername>{username}</chatusername>"
                    f"<pattedusername>wxid_target</pattedusername></patinfo></appmsg>"
                    f"<fromusername></fromusername></msg>"
                )
                con.execute(
                    f'INSERT INTO "{table}" '
                    "(local_id, server_id, local_type, sort_seq, real_sender_id, create_time, status, "
                    "upload_status, download_status, server_seq, origin_source, source, message_content, "
                    "compress_content, packed_info_data, WCDB_CT_message_content, WCDB_CT_source) "
                    "VALUES (4, 102, 266287972401, 13, 3, 1700000003, 0, 0, 0, 0, 0, x'', ?, '', x'', x'', x'')",
                    (pat_content,),
                )
                con.commit()
            finally:
                con.close()

            contact_db = decrypted / "contact-test.db"
            con = sqlite3.connect(str(contact_db))
            try:
                con.execute(
                    "CREATE TABLE contact (id INTEGER, username TEXT, alias TEXT, remark TEXT, nick_name TEXT, "
                    "local_type INTEGER, verify_flag INTEGER, is_in_chat_room INTEGER, chat_room_type INTEGER, "
                    "delete_flag INTEGER)"
                )
                con.execute(
                    "INSERT INTO contact VALUES (1, ?, '', 'Group Remark', 'Group Nick', 0, 0, 1, 0, 0)",
                    (username,),
                )
                con.execute(
                    "INSERT INTO contact VALUES (2, ?, '', 'Sender Remark', 'Sender Nick', 0, 0, 0, 0, 0)",
                    (sender_username,),
                )
                con.execute(
                    "INSERT INTO contact VALUES (3, ?, '', 'DZ-qingfeng', 'Wrong Nick', 0, 0, 0, 0, 0)",
                    (wrong_sender_username,),
                )
                con.commit()
            finally:
                con.close()

            session_db = decrypted / "session-test.db"
            con = sqlite3.connect(str(session_db))
            try:
                con.execute(
                    "CREATE TABLE SessionTable (username TEXT, type INTEGER, unread_count INTEGER, summary TEXT, "
                    "last_timestamp INTEGER, sort_timestamp INTEGER, last_msg_type INTEGER, last_msg_sender TEXT, "
                    "last_sender_display_name TEXT)"
                )
                con.execute(
                    "INSERT INTO SessionTable VALUES (?, 1, 0, 'summary', 1700000000, 1700000001, 1, ?, 'sender')",
                    (username, username),
                )
                con.commit()
            finally:
                con.close()

            store = DashboardStore(decrypted)

            contacts = store.contacts()
            contact_items = {item["username"]: item for item in contacts["items"]}
            self.assertEqual(contact_items[username]["display_name"], "Group Remark")

            sessions = store.sessions()
            self.assertEqual(sessions["items"][0]["display_name"], "Group Remark")

            chats = store.chats()
            self.assertEqual(chats["items"][0]["table"], table)
            self.assertEqual(chats["items"][0]["message_count"], 4)

            messages = store.messages(chat=username, q="dashboard")
            contents = [item["message_content"] for item in messages["items"]]
            self.assertIn("hello dashboard", contents)
            self.assertIn("compressed dashboard message", contents)
            self.assertTrue(all(not content.startswith(f"{sender_username}:") for content in contents))
            self.assertEqual(messages["items"][0]["chat_display_name"], "Group Remark")
            self.assertTrue(all(item["sender_username"] == sender_username for item in messages["items"]))
            self.assertTrue(all(item["sender_display_name"] == "Sender Remark" for item in messages["items"]))

            compressed_matches = store.messages(chat=username, q="compressed")
            self.assertEqual(compressed_matches["items"][0]["message_content"], "compressed dashboard message")
            self.assertEqual(compressed_matches["items"][0]["sender_display_name"], "Sender Remark")

            xml_matches = store.messages(chat=username, q="xml special")
            self.assertEqual(xml_matches["items"][0]["sender_username"], sender_username)
            self.assertEqual(xml_matches["items"][0]["sender_display_name"], "Sender Remark")
            self.assertEqual(xml_matches["items"][0]["message_content"], "xml special dashboard")

            pat_matches = store.messages(chat=username, q="Pat special")
            self.assertEqual(pat_matches["items"][0]["sender_username"], sender_username)
            self.assertEqual(pat_matches["items"][0]["sender_display_name"], "Sender Remark")
            self.assertEqual(pat_matches["items"][0]["message_content"], "Pat special dashboard")

    def test_dashboard_store_summarizes_text_messages_in_range(self) -> None:
        with temp_dir() as root:
            decrypted = root / "decrypted"
            decrypted.mkdir()
            peer_username = "wxid_peer"
            table = "Msg_" + hashlib.md5(peer_username.encode("utf-8")).hexdigest()

            message_db = decrypted / "message_0-test.db"
            con = sqlite3.connect(str(message_db))
            try:
                con.execute("CREATE TABLE Name2Id (user_name TEXT, is_session INTEGER)")
                con.execute("INSERT INTO Name2Id (rowid, user_name, is_session) VALUES (7, ?, 1)", (peer_username,))
                con.execute(
                    f'CREATE TABLE "{table}" ('
                    "local_id INTEGER, server_id INTEGER, local_type INTEGER, sort_seq INTEGER, "
                    "real_sender_id INTEGER, create_time INTEGER, status INTEGER, message_content TEXT, "
                    "compress_content TEXT, packed_info_data BLOB)"
                )
                rows = [
                    (1, 1, 1, 1, 7, 1700000000, 0, "first text", "", sqlite3.Binary(b"")),
                    (2, 2, 3, 2, 7, 1700000001, 0, "image text should not summarize", "", sqlite3.Binary(b"")),
                    (3, 3, 1, 3, 7, 1700000002, 0, "second text", "", sqlite3.Binary(b"")),
                    (4, 4, 1, 4, 7, 1700000100, 0, "outside range", "", sqlite3.Binary(b"")),
                ]
                con.executemany(
                    f'INSERT INTO "{table}" '
                    "(local_id, server_id, local_type, sort_seq, real_sender_id, create_time, status, "
                    "message_content, compress_content, packed_info_data) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    rows,
                )
                con.commit()
            finally:
                con.close()

            fake_summary = {
                "title": "范围内消息",
                "executive_summary": "讨论了两条文本。",
                "message_count": 0,
                "time_range": "test range",
                "sentiment": "平稳",
                "key_points": [],
                "decisions": [],
                "action_items": [],
                "risks": [],
                "open_questions": [],
                "notable_messages": [],
            }
            store = DashboardStore(decrypted)
            with patch("wechat_agent_cli.dashboard.request_openai_message_summary") as summarize:
                summarize.return_value = {"summary": fake_summary, "response_id": "resp_test", "model": "test-model"}

                result = store.summarize_messages(chat=peer_username, after=1700000000, before=1700000002)

            summarize.assert_called_once()
            summarized_messages = summarize.call_args.kwargs["messages"]
            self.assertEqual([item["message_content"] for item in summarized_messages], ["first text", "second text"])
            self.assertEqual(result["messages"]["included"], 2)
            self.assertEqual(result["summary"]["message_count"], 2)
            self.assertEqual(result["openai"]["response_id"], "resp_test")

    def test_dashboard_store_exposes_image_media_without_xml_body(self) -> None:
        with temp_dir() as root:
            decrypted = root / "decrypted"
            decrypted.mkdir()
            account_root = root / "wechat" / "xwechat_files" / "wxid_self_abcd"
            (account_root / "db_storage" / "message").mkdir(parents=True)
            peer_username = "wxid_peer"
            table = "Msg_" + hashlib.md5(peer_username.encode("utf-8")).hexdigest()
            create_time = 1700000000
            month = datetime.datetime.fromtimestamp(create_time).strftime("%Y-%m")
            thumb_dir = account_root / "cache" / month / "Message" / table[4:] / "Thumb"
            thumb_dir.mkdir(parents=True)
            attach_dir = account_root / "msg" / "attach" / table[4:] / month / "Img"
            attach_dir.mkdir(parents=True)
            storage_dir = account_root / "FileStorage" / "Image" / month
            storage_dir.mkdir(parents=True)
            thumb_bytes = b"\xff\xd8\xff\xe0JFIF\x00\xff\xd9"
            image_md5 = "48ceaa4143631e21cc0eb8760f95bf09"
            xor_key = 0xA7
            (storage_dir / f"{image_md5}.dat").write_bytes(bytes(value ^ xor_key for value in thumb_bytes))
            (attach_dir / f"1_{create_time}_t.dat").write_bytes(bytes(value ^ xor_key for value in thumb_bytes))
            (thumb_dir / f"1_{create_time}_thumb.jpg").write_bytes(thumb_bytes)
            (root / "manifest.json").write_text(
                json.dumps(
                    {
                        "filters": {"accounts": ["wxid_self"]},
                        "databases": [
                            {
                                "source": str(account_root / "db_storage" / "message" / "message_0.db"),
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            message_db = decrypted / "message_0-test.db"
            con = sqlite3.connect(str(message_db))
            try:
                con.execute("CREATE TABLE Name2Id (user_name TEXT, is_session INTEGER)")
                con.execute("INSERT INTO Name2Id (rowid, user_name, is_session) VALUES (7, ?, 1)", (peer_username,))
                con.execute(
                    f'CREATE TABLE "{table}" ('
                    "local_id INTEGER, server_id INTEGER, local_type INTEGER, sort_seq INTEGER, "
                    "real_sender_id INTEGER, create_time INTEGER, status INTEGER, upload_status INTEGER, "
                    "download_status INTEGER, server_seq INTEGER, origin_source INTEGER, source BLOB, "
                    "message_content TEXT, compress_content TEXT, packed_info_data BLOB, "
                    "WCDB_CT_message_content BLOB, WCDB_CT_source BLOB)"
                )
                image_xml = (
                    '<?xml version="1.0"?><msg><img cdnthumbwidth="100" cdnthumbheight="150" '
                    f'cdnthumblength="12" length="42" md5="{image_md5}"></img></msg>'
                )
                con.execute(
                    f'INSERT INTO "{table}" '
                    "(local_id, server_id, local_type, sort_seq, real_sender_id, create_time, status, "
                    "upload_status, download_status, server_seq, origin_source, source, message_content, "
                    "compress_content, packed_info_data, WCDB_CT_message_content, WCDB_CT_source) "
                    "VALUES (1, 123, 3, 1, 7, ?, 3, 0, 0, 0, 0, x'', ?, '', x'', x'', x'')",
                    (create_time, image_xml),
                )
                con.commit()
            finally:
                con.close()

            store = DashboardStore(decrypted)
            item = store.messages(chat=peer_username)["items"][0]
            self.assertEqual(item["message_kind"], "image")
            self.assertEqual(item["message_content"], "[image]")
            self.assertEqual(item["media"]["width"], 100)
            self.assertTrue(item["media"]["available"])
            self.assertEqual(item["media"]["source"], "cache-thumb")
            self.assertIn("/api/wechat/media/image?", item["media"]["url"])

            response = store.image_response(chat=peer_username, local_id=1, server_id=123)
            self.assertEqual(response.content_type, "image/jpeg")
            self.assertEqual(response.body, thumb_bytes)

    def test_dashboard_store_resolves_weixin4_attach_image_by_message_time(self) -> None:
        with temp_dir() as root:
            decrypted = root / "decrypted"
            decrypted.mkdir()
            account_root = root / "wechat" / "xwechat_files" / "wxid_self_abcd"
            (account_root / "db_storage" / "message").mkdir(parents=True)
            peer_username = "wxid_peer"
            table = "Msg_" + hashlib.md5(peer_username.encode("utf-8")).hexdigest()
            create_time = 1700000000
            month = datetime.datetime.fromtimestamp(create_time).strftime("%Y-%m")
            attach_dir = account_root / "msg" / "attach" / table[4:] / month / "Img"
            attach_dir.mkdir(parents=True)
            thumb_bytes = b"\xff\xd8\xff\xe0JFIF\x00\xff\xd9"
            xor_key = 0x41
            (attach_dir / f"1_{create_time}_t.dat").write_bytes(bytes(value ^ xor_key for value in thumb_bytes))
            (root / "manifest.json").write_text(
                json.dumps(
                    {
                        "filters": {"accounts": ["wxid_self"]},
                        "databases": [
                            {
                                "source": str(account_root / "db_storage" / "message" / "message_0.db"),
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            message_db = decrypted / "message_0-test.db"
            con = sqlite3.connect(str(message_db))
            try:
                con.execute("CREATE TABLE Name2Id (user_name TEXT, is_session INTEGER)")
                con.execute("INSERT INTO Name2Id (rowid, user_name, is_session) VALUES (7, ?, 1)", (peer_username,))
                con.execute(
                    f'CREATE TABLE "{table}" ('
                    "local_id INTEGER, server_id INTEGER, local_type INTEGER, sort_seq INTEGER, "
                    "real_sender_id INTEGER, create_time INTEGER, status INTEGER, upload_status INTEGER, "
                    "download_status INTEGER, server_seq INTEGER, origin_source INTEGER, source BLOB, "
                    "message_content TEXT, compress_content TEXT, packed_info_data BLOB)"
                )
                image_xml = (
                    '<?xml version="1.0"?><msg><img cdnthumbwidth="100" cdnthumbheight="150" '
                    'cdnthumblength="12" length="42" md5="48ceaa4143631e21cc0eb8760f95bf09" /></msg>'
                )
                con.execute(
                    f'INSERT INTO "{table}" '
                    "(local_id, server_id, local_type, sort_seq, real_sender_id, create_time, status, "
                    "upload_status, download_status, server_seq, origin_source, source, message_content, "
                    "compress_content, packed_info_data) "
                    "VALUES (1, 123, 3, 1, 7, ?, 3, 0, 0, 0, 0, x'', ?, '', x'')",
                    (create_time, image_xml),
                )
                con.commit()
            finally:
                con.close()

            store = DashboardStore(decrypted)
            item = store.messages(chat=peer_username)["items"][0]
            self.assertTrue(item["media"]["available"])
            self.assertEqual(item["media"]["source"], "attach-thumb")

            response = store.image_response(chat=peer_username, local_id=1, server_id=123)
            self.assertEqual(response.content_type, "image/jpeg")
            self.assertEqual(response.body, thumb_bytes)

    def test_dashboard_store_uses_xwechat_config_for_custom_media_root(self) -> None:
        with temp_dir() as root:
            appdata = root / "AppData" / "Roaming"
            config_dir = appdata / "Tencent" / "xwechat" / "config"
            config_dir.mkdir(parents=True)
            wechat_root = root / "custom-wechat"
            account_root = wechat_root / "xwechat_files" / "wxid_self_abcd"
            (account_root / "db_storage" / "message").mkdir(parents=True)
            config_dir.joinpath("profile.ini").write_text(str(wechat_root), encoding="utf-8")

            decrypted = root / "decrypted"
            decrypted.mkdir()
            peer_username = "wxid_peer"
            table = "Msg_" + hashlib.md5(peer_username.encode("utf-8")).hexdigest()
            create_time = 1700000000
            month = datetime.datetime.fromtimestamp(create_time).strftime("%Y-%m")
            attach_dir = account_root / "msg" / "attach" / table[4:] / month / "Img"
            attach_dir.mkdir(parents=True)
            thumb_bytes = b"\xff\xd8\xff\xe0JFIF\x00\xff\xd9"
            xor_key = 0x52
            (attach_dir / f"1_{create_time}_t.dat").write_bytes(bytes(value ^ xor_key for value in thumb_bytes))

            message_db = decrypted / "message_0-test.db"
            con = sqlite3.connect(str(message_db))
            try:
                con.execute("CREATE TABLE Name2Id (user_name TEXT, is_session INTEGER)")
                con.execute("INSERT INTO Name2Id (rowid, user_name, is_session) VALUES (7, ?, 1)", (peer_username,))
                con.execute(
                    f'CREATE TABLE "{table}" ('
                    "local_id INTEGER, server_id INTEGER, local_type INTEGER, sort_seq INTEGER, "
                    "real_sender_id INTEGER, create_time INTEGER, status INTEGER, message_content TEXT, "
                    "compress_content TEXT, packed_info_data BLOB)"
                )
                image_xml = (
                    '<?xml version="1.0"?><msg><img cdnthumbwidth="100" cdnthumbheight="150" '
                    'cdnthumblength="12" length="42" md5="48ceaa4143631e21cc0eb8760f95bf09" /></msg>'
                )
                con.execute(
                    f'INSERT INTO "{table}" '
                    "(local_id, server_id, local_type, sort_seq, real_sender_id, create_time, status, "
                    "message_content, compress_content, packed_info_data) "
                    "VALUES (1, 123, 3, 1, 7, ?, 3, ?, '', x'')",
                    (create_time, image_xml),
                )
                con.commit()
            finally:
                con.close()

            with patch.dict(os.environ, {"APPDATA": str(appdata)}):
                store = DashboardStore(decrypted)
                item = store.messages(chat=peer_username)["items"][0]
                response = store.image_response(chat=peer_username, local_id=1, server_id=123)

            self.assertTrue(item["media"]["available"])
            self.assertEqual(item["media"]["source"], "attach-thumb")
            self.assertEqual(response.body, thumb_bytes)

    def test_decode_wechat_v4_image_uses_fixed_aes_key_and_tail_xor(self) -> None:
        plain_head = b"\xff\xd8\xff\xe0JFIF\x00head"
        pad = bytes([16]) * 16
        plain_tail = b"tail\xff\xd9"
        xor_key = 0x33
        header = b"\x07\x08V2\x08\x07" + struct.pack("<H", len(plain_head)) + (b"\x00" * 7)
        encrypted_tail = bytes(value ^ xor_key for value in plain_tail)
        payload = header + (b"encrypted-block!!"[:16]) + encrypted_tail

        class FakeAes:
            def __init__(self, key: bytes):
                self.key = key

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb) -> None:
                return None

            def decrypt(self, ciphertext: bytes) -> bytes:
                return plain_head + pad

        with patch("wechat_agent_cli.dashboard.AesEcbDecryptor", FakeAes):
            self.assertEqual(decode_wechat_v4_image(payload), plain_head + plain_tail)


if __name__ == "__main__":
    unittest.main()
