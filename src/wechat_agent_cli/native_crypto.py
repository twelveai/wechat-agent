from __future__ import annotations

import ctypes
import os
from ctypes import wintypes


BCRYPT_AES_ALGORITHM = "AES"
BCRYPT_CHAIN_MODE_CBC = "ChainingModeCBC"
BCRYPT_CHAINING_MODE = "ChainingMode"
BCRYPT_OBJECT_LENGTH = "ObjectLength"
BCRYPT_BLOCK_LENGTH = "BlockLength"


class NativeCryptoError(RuntimeError):
    pass


class AesCbcDecryptor:
    def __init__(self, key: bytes):
        if len(key) not in {16, 24, 32}:
            raise ValueError("AES key must be 16, 24, or 32 bytes.")
        if os.name != "nt":
            raise NativeCryptoError("Native AES backend currently requires Windows.")

        self._bcrypt = ctypes.WinDLL("bcrypt", use_last_error=True)
        self._alg = wintypes.HANDLE()
        self._key = wintypes.HANDLE()
        self._key_object = None

        self._check(
            self._bcrypt.BCryptOpenAlgorithmProvider(
                ctypes.byref(self._alg),
                BCRYPT_AES_ALGORITHM,
                None,
                0,
            ),
            "BCryptOpenAlgorithmProvider",
        )
        self._set_property_string(self._alg, BCRYPT_CHAINING_MODE, BCRYPT_CHAIN_MODE_CBC)
        object_length = self._get_property_u32(self._alg, BCRYPT_OBJECT_LENGTH)
        block_length = self._get_property_u32(self._alg, BCRYPT_BLOCK_LENGTH)
        if block_length != 16:
            raise NativeCryptoError(f"Unexpected AES block length: {block_length}")

        self._key_object = ctypes.create_string_buffer(object_length)
        key_buffer = ctypes.create_string_buffer(key)
        self._check(
            self._bcrypt.BCryptGenerateSymmetricKey(
                self._alg,
                ctypes.byref(self._key),
                self._key_object,
                object_length,
                key_buffer,
                len(key),
                0,
            ),
            "BCryptGenerateSymmetricKey",
        )

    def decrypt(self, ciphertext: bytes, iv: bytes) -> bytes:
        if len(iv) != 16:
            raise ValueError("AES-CBC IV must be 16 bytes.")
        if len(ciphertext) % 16 != 0:
            raise ValueError("AES-CBC ciphertext length must be a multiple of 16.")

        input_buffer = ctypes.create_string_buffer(ciphertext)
        iv_buffer = ctypes.create_string_buffer(iv)
        output_buffer = ctypes.create_string_buffer(len(ciphertext))
        written = wintypes.ULONG(0)
        self._check(
            self._bcrypt.BCryptDecrypt(
                self._key,
                input_buffer,
                len(ciphertext),
                None,
                iv_buffer,
                len(iv),
                output_buffer,
                len(ciphertext),
                ctypes.byref(written),
                0,
            ),
            "BCryptDecrypt",
        )
        return output_buffer.raw[: written.value]

    def close(self) -> None:
        if self._key:
            self._bcrypt.BCryptDestroyKey(self._key)
            self._key = wintypes.HANDLE()
        if self._alg:
            self._bcrypt.BCryptCloseAlgorithmProvider(self._alg, 0)
            self._alg = wintypes.HANDLE()

    def __enter__(self) -> "AesCbcDecryptor":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _get_property_u32(self, handle: wintypes.HANDLE, name: str) -> int:
        output = wintypes.ULONG(0)
        written = wintypes.ULONG(0)
        self._check(
            self._bcrypt.BCryptGetProperty(
                handle,
                name,
                ctypes.byref(output),
                ctypes.sizeof(output),
                ctypes.byref(written),
                0,
            ),
            f"BCryptGetProperty({name})",
        )
        return int(output.value)

    def _set_property_string(self, handle: wintypes.HANDLE, name: str, value: str) -> None:
        raw = ctypes.create_unicode_buffer(value)
        self._check(
            self._bcrypt.BCryptSetProperty(
                handle,
                name,
                raw,
                ctypes.sizeof(raw),
                0,
            ),
            f"BCryptSetProperty({name})",
        )

    @staticmethod
    def _check(status: int, api: str) -> None:
        if status < 0:
            raise NativeCryptoError(f"{api} failed with NTSTATUS 0x{status & 0xFFFFFFFF:08x}")
