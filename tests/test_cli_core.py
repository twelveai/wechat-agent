from __future__ import annotations

import json
import hashlib
import hmac
import os
import shutil
import sqlite3
import sys
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wechat_agent_cli.copying import copy_databases
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


if __name__ == "__main__":
    unittest.main()
