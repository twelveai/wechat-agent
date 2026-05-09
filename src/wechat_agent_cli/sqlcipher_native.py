from __future__ import annotations

import hashlib
import hmac
import shutil
import sqlite3
import struct
from dataclasses import dataclass
from pathlib import Path

from .key_extract import (
    AES_BLOCK_SIZE,
    HMAC_SHA512_SIZE,
    IV_SIZE,
    KEY_SIZE,
    PAGE_SIZE,
    ROUND_COUNT,
    SALT_SIZE,
)
from .native_crypto import AesCbcDecryptor, NativeCryptoError


RESERVE_SIZE = ((IV_SIZE + HMAC_SHA512_SIZE + AES_BLOCK_SIZE - 1) // AES_BLOCK_SIZE) * AES_BLOCK_SIZE
USABLE_SIZE = PAGE_SIZE - RESERVE_SIZE


@dataclass(frozen=True)
class SqlCipherKey:
    aes_key: bytes
    mac_key: bytes
    mode: str


class SqlCipherDecryptError(RuntimeError):
    pass


def decrypt_sqlcipher_database(source: Path, dest: Path, key_hex: str) -> str:
    source = source.expanduser().resolve()
    dest = dest.expanduser().resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)
    key = resolve_sqlcipher_key(source, key_hex)

    if dest.exists():
        dest.unlink()

    with source.open("rb") as input_file, dest.open("wb") as output_file:
        page_no = 1
        with AesCbcDecryptor(key.aes_key) as aes:
            while True:
                page = input_file.read(PAGE_SIZE)
                if not page:
                    break
                if len(page) != PAGE_SIZE:
                    raise SqlCipherDecryptError(
                        f"Unexpected partial page in {source}: page={page_no}, size={len(page)}"
                    )
                plaintext_page = decrypt_page(aes, key.mac_key, page, page_no)
                output_file.write(plaintext_page)
                page_no += 1

    if not sqlite_can_open(dest):
        raise SqlCipherDecryptError("native decrypt completed but output is not readable SQLite")
    return key.mode


def resolve_sqlcipher_key(source: Path, key_hex: str) -> SqlCipherKey:
    key_material = bytes.fromhex(key_hex)
    first_page = read_first_page(source)
    salt = first_page[:SALT_SIZE]
    mac_salt = bytes(value ^ 0x3A for value in salt)

    if len(key_material) == KEY_SIZE + SALT_SIZE:
        aes_key = key_material[:KEY_SIZE]
        embedded_salt = key_material[KEY_SIZE:]
        if embedded_salt != salt:
            raise SqlCipherDecryptError("raw key salt does not match database salt")
        mac_key = hashlib.pbkdf2_hmac("sha512", aes_key, mac_salt, 2, KEY_SIZE)
        if verify_page_hmac(first_page, 1, mac_key, salt_offset=SALT_SIZE):
            return SqlCipherKey(aes_key=aes_key, mac_key=mac_key, mode="native-raw-key")
        raise SqlCipherDecryptError("raw key failed page-1 HMAC verification")

    if len(key_material) != KEY_SIZE:
        raise SqlCipherDecryptError("key must be 64 or 96 hexadecimal characters")

    raw_mac_key = hashlib.pbkdf2_hmac("sha512", key_material, mac_salt, 2, KEY_SIZE)
    if verify_page_hmac(first_page, 1, raw_mac_key, salt_offset=SALT_SIZE):
        return SqlCipherKey(aes_key=key_material, mac_key=raw_mac_key, mode="native-raw-32")

    derived_key = hashlib.pbkdf2_hmac("sha512", key_material, salt, ROUND_COUNT, KEY_SIZE)
    derived_mac_key = hashlib.pbkdf2_hmac("sha512", derived_key, mac_salt, 2, KEY_SIZE)
    if verify_page_hmac(first_page, 1, derived_mac_key, salt_offset=SALT_SIZE):
        return SqlCipherKey(aes_key=derived_key, mac_key=derived_mac_key, mode="native-kdf")

    raise SqlCipherDecryptError("key failed page-1 HMAC verification")


def decrypt_page(aes: AesCbcDecryptor, mac_key: bytes, page: bytes, page_no: int) -> bytes:
    salt_offset = SALT_SIZE if page_no == 1 else 0
    if not verify_page_hmac(page, page_no, mac_key, salt_offset=salt_offset):
        raise SqlCipherDecryptError(f"page {page_no} HMAC verification failed")

    iv = page[USABLE_SIZE:USABLE_SIZE + IV_SIZE]
    ciphertext = page[salt_offset:USABLE_SIZE]
    plaintext = aes.decrypt(ciphertext, iv)
    reserved = b"\x00" * RESERVE_SIZE
    if page_no == 1:
        return b"SQLite format 3\x00" + plaintext + reserved
    return plaintext + reserved


def verify_page_hmac(page: bytes, page_no: int, mac_key: bytes, salt_offset: int) -> bool:
    if len(page) != PAGE_SIZE:
        return False
    mac_start = USABLE_SIZE + IV_SIZE
    mac = hmac.new(mac_key, page[salt_offset:mac_start], hashlib.sha512)
    mac.update(struct.pack("<I", page_no))
    return hmac.compare_digest(mac.digest(), page[mac_start:mac_start + HMAC_SHA512_SIZE])


def read_first_page(source: Path) -> bytes:
    with source.open("rb") as fh:
        page = fh.read(PAGE_SIZE)
    if len(page) != PAGE_SIZE:
        raise SqlCipherDecryptError(f"database is smaller than one page: {source}")
    return page


def sqlite_can_open(path: Path) -> bool:
    try:
        con = sqlite3.connect(str(path))
        try:
            con.execute("PRAGMA schema_version").fetchone()
        finally:
            con.close()
        return True
    except sqlite3.DatabaseError:
        return False


def native_decrypt_available() -> bool:
    try:
        with AesCbcDecryptor(b"\x00" * 32) as aes:
            aes.decrypt(b"\x00" * 16, b"\x00" * 16)
        return True
    except (NativeCryptoError, OSError):
        return False


def copy_plain_sqlite(source: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, dest)
