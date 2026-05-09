from __future__ import annotations

import ctypes
import hashlib
import hmac
import os
import re
import struct
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from ctypes import wintypes

from .keys import fingerprint_key

SQLITE_HEADER = b"SQLite format 3\x00"
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
PROCESS_ALL_ACCESS = 0x1F0FFF

MEM_COMMIT = 0x1000
MEM_PRIVATE = 0x20000
PAGE_NOACCESS = 0x01
PAGE_GUARD = 0x100

TH32CS_SNAPPROCESS = 0x00000002
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
MAX_PATH = 260

PAGE_SIZE = 4096
SALT_SIZE = 16
KEY_SIZE = 32
IV_SIZE = 16
HMAC_SHA512_SIZE = 64
AES_BLOCK_SIZE = 16
ROUND_COUNT = 256000

KEY_STUB_SUFFIX = (
    b"\x00" * 8
    + struct.pack("<Q", KEY_SIZE)
    + struct.pack("<Q", 0x2F)
)
WECHAT_PROCESS_NAMES = {"weixin.exe", "wechat.exe"}
RAW_KEY_RE = re.compile(rb"[xX]'([0-9a-fA-F]{96})'?", re.ASCII)


class MemoryAccessError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    name: str


@dataclass(frozen=True)
class MemoryRegion:
    base: int
    size: int
    protect: int
    region_type: int


@dataclass(frozen=True)
class ProbeDatabase:
    path: Path
    page: bytes
    salt: bytes


@dataclass(frozen=True)
class KeyExtractionResult:
    key_hex: str | None
    database_keys: dict[str, str]
    pid: int
    process_name: str
    probe_database: str | None
    scanned_regions: int
    candidate_count: int
    elapsed_seconds: float

    def to_public_dict(self) -> dict:
        fingerprint = fingerprint_key(self.key_hex) if self.key_hex else None
        return {
            "ok": True,
            "pid": self.pid,
            "process_name": self.process_name,
            "probe_database": self.probe_database,
            "scanned_regions": self.scanned_regions,
            "candidate_count": self.candidate_count,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "fingerprint": fingerprint,
            "database_key_count": len(self.database_keys),
            "database_key_fingerprints": {
                name: fingerprint_key(value) for name, value in sorted(self.database_keys.items())
            },
        }


class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wintypes.DWORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wintypes.DWORD),
        ("Protect", wintypes.DWORD),
        ("Type", wintypes.DWORD),
    ]


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * MAX_PATH),
    ]


def extract_wechat_key(
    workspace: Path,
    raw_dir: Path | None = None,
    pids: Iterable[int] = (),
    max_region_size: int = 64 * 1024 * 1024,
    max_candidates: int = 2000,
) -> KeyExtractionResult:
    if os.name != "nt":
        raise RuntimeError("Automatic key extraction is only supported on Windows.")

    started = time.monotonic()
    probe_databases = collect_probe_databases(raw_dir or latest_raw_dir(workspace))
    legacy_probe_database = select_probe_database_from_probes(probe_databases)

    enable_debug_privilege()
    pid_list = [int(pid) for pid in pids]
    processes = processes_from_pids(pid_list) if pid_list else find_wechat_processes_native()
    if not processes:
        raise RuntimeError("No running Weixin.exe or WeChat.exe process was found.")

    errors: list[str] = []
    for process in processes:
        try:
            result = scan_process_for_key(
                process=process,
                probe_databases=probe_databases,
                legacy_probe_database=legacy_probe_database,
                max_region_size=max_region_size,
                max_candidates=max_candidates,
                started=started,
            )
            if result:
                return result
        except MemoryAccessError as exc:
            errors.append(f"{process.name} pid={process.pid}: {exc}")

    detail = "; ".join(errors[:5])
    if detail:
        raise RuntimeError(
            "Key not found. Run the terminal as Administrator, keep Weixin logged in, "
            f"and retry. Access details: {detail}"
        )
    raise RuntimeError("Key not found. Restart Weixin and retry while it is logged in.")


def processes_from_pids(pids: Iterable[int]) -> list[ProcessInfo]:
    return [ProcessInfo(pid=int(pid), name=f"pid-{int(pid)}") for pid in pids]


def latest_raw_dir(workspace: Path) -> Path:
    work_dir = workspace / "work"
    candidates = [path / "raw" for path in work_dir.iterdir() if (path / "raw").is_dir()] if work_dir.exists() else []
    if not candidates:
        raise RuntimeError("No raw copy directory found. Run `wechat-agent copy --account <wxid> --core` first.")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def collect_probe_databases(raw_dir: Path) -> list[ProbeDatabase]:
    raw_dir = raw_dir.expanduser().resolve()
    if raw_dir.is_file():
        page = read_probe_page(raw_dir)
        if is_plain_sqlite(raw_dir):
            raise RuntimeError(f"Probe database is plain SQLite, not encrypted: {raw_dir}")
        return [ProbeDatabase(path=raw_dir, page=page, salt=page[:SALT_SIZE])]
    if not raw_dir.is_dir():
        raise RuntimeError(f"Raw directory does not exist: {raw_dir}")

    databases = [
        path
        for path in raw_dir.rglob("*.db")
        if not path.name.lower().endswith(("-wal", "-shm"))
    ]
    probes: list[ProbeDatabase] = []
    for database in databases:
        if database.stat().st_size >= PAGE_SIZE and not is_plain_sqlite(database):
            page = read_probe_page(database)
            probes.append(ProbeDatabase(path=database, page=page, salt=page[:SALT_SIZE]))
    if not probes:
        raise RuntimeError(f"No database files found in raw directory: {raw_dir}")
    return probes


def select_probe_database(raw_dir: Path) -> Path:
    return select_probe_database_from_probes(collect_probe_databases(raw_dir)).path


def select_probe_database_from_probes(probes: list[ProbeDatabase]) -> ProbeDatabase:
    if not probes:
        raise RuntimeError("No encrypted SQLCipher probe database found in raw directory.")

    def score(path: Path) -> tuple[int, str]:
        name = path.name.lower()
        preferred = [
            "favorite_fts",
            "head_image",
            "message_0",
            "contact",
            "session",
            "key_info",
        ]
        for index, prefix in enumerate(preferred):
            if name.startswith(prefix):
                return (index, name)
        return (len(preferred), name)

    return sorted(probes, key=lambda probe: score(probe.path))[0]


def read_probe_page(path: Path) -> bytes:
    with path.open("rb") as fh:
        page = fh.read(PAGE_SIZE)
    if len(page) < PAGE_SIZE:
        raise RuntimeError(f"Probe database is smaller than one page: {path}")
    return page


def is_plain_sqlite(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            return fh.read(len(SQLITE_HEADER)) == SQLITE_HEADER
    except OSError:
        return False


def verify_wechat_sqlcipher_key(passphrase: bytes, page: bytes) -> bool:
    if len(passphrase) != KEY_SIZE or len(page) < PAGE_SIZE:
        return False
    salt = page[:SALT_SIZE]
    mac_salt = bytes(value ^ 0x3A for value in salt)
    derived_key = hashlib.pbkdf2_hmac("sha512", passphrase, salt, ROUND_COUNT, KEY_SIZE)
    mac_key = hashlib.pbkdf2_hmac("sha512", derived_key, mac_salt, 2, KEY_SIZE)
    reserve = IV_SIZE + HMAC_SHA512_SIZE
    reserve = ((reserve + AES_BLOCK_SIZE - 1) // AES_BLOCK_SIZE) * AES_BLOCK_SIZE
    mac_start = PAGE_SIZE - reserve + IV_SIZE
    mac = hmac.new(mac_key, page[SALT_SIZE:mac_start], hashlib.sha512)
    mac.update(struct.pack("<I", 1))
    return hmac.compare_digest(mac.digest(), page[mac_start:mac_start + HMAC_SHA512_SIZE])


def verify_wechat_sqlcipher_raw_key(raw_key_with_salt: bytes, page: bytes) -> bool:
    if len(raw_key_with_salt) != KEY_SIZE + SALT_SIZE or len(page) < PAGE_SIZE:
        return False
    raw_key = raw_key_with_salt[:KEY_SIZE]
    salt = raw_key_with_salt[KEY_SIZE:]
    if salt != page[:SALT_SIZE]:
        return False
    mac_salt = bytes(value ^ 0x3A for value in salt)
    mac_key = hashlib.pbkdf2_hmac("sha512", raw_key, mac_salt, 2, KEY_SIZE)
    reserve = IV_SIZE + HMAC_SHA512_SIZE
    reserve = ((reserve + AES_BLOCK_SIZE - 1) // AES_BLOCK_SIZE) * AES_BLOCK_SIZE
    mac_start = PAGE_SIZE - reserve + IV_SIZE
    mac = hmac.new(mac_key, page[SALT_SIZE:mac_start], hashlib.sha512)
    mac.update(struct.pack("<I", 1))
    return hmac.compare_digest(mac.digest(), page[mac_start:mac_start + HMAC_SHA512_SIZE])


def scan_process_for_key(
    process: ProcessInfo,
    probe_databases: list[ProbeDatabase],
    legacy_probe_database: ProbeDatabase,
    max_region_size: int,
    max_candidates: int,
    started: float,
) -> KeyExtractionResult | None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = open_process(kernel32, process.pid)
    if not handle:
        raise MemoryAccessError(last_error_message("OpenProcess"))
    try:
        regions = list_memory_regions(kernel32, handle, max_region_size=max_region_size)
        candidate_count = 0
        seen_pointers: set[int] = set()
        database_keys: dict[str, str] = {}
        salt_to_probes: dict[bytes, list[ProbeDatabase]] = {}
        for probe in probe_databases:
            salt_to_probes.setdefault(probe.salt, []).append(probe)
        for region in regions:
            data = read_process_memory(kernel32, handle, region.base, region.size)
            if not data:
                continue
            for raw_key_hex in iter_raw_key_hex_strings(data):
                candidate_count += 1
                if candidate_count > max_candidates:
                    break
                raw_key = bytes.fromhex(raw_key_hex)
                salt = raw_key[KEY_SIZE:]
                for probe in salt_to_probes.get(salt, []):
                    if probe.path.name in database_keys:
                        continue
                    if verify_wechat_sqlcipher_raw_key(raw_key, probe.page):
                        database_keys[probe.path.name] = raw_key_hex.lower()
                if len(database_keys) >= len(probe_databases):
                    break
            if len(database_keys) >= len(probe_databases):
                return KeyExtractionResult(
                    key_hex=next(iter(database_keys.values())),
                    database_keys=database_keys,
                    pid=process.pid,
                    process_name=process.name,
                    probe_database=None,
                    scanned_regions=len(regions),
                    candidate_count=candidate_count,
                    elapsed_seconds=time.monotonic() - started,
                )
            if candidate_count > max_candidates:
                break
            if database_keys:
                continue
            for pointer in iter_key_pointers(data):
                if pointer in seen_pointers:
                    continue
                seen_pointers.add(pointer)
                candidate_count += 1
                if candidate_count > max_candidates:
                    break
                candidate = read_process_memory(kernel32, handle, pointer, KEY_SIZE)
                if not looks_like_key_material(candidate):
                    continue
                if verify_wechat_sqlcipher_key(candidate, legacy_probe_database.page):
                    return KeyExtractionResult(
                        key_hex=candidate.hex(),
                        database_keys={},
                        pid=process.pid,
                        process_name=process.name,
                        probe_database=str(legacy_probe_database.path),
                        scanned_regions=len(regions),
                        candidate_count=candidate_count,
                        elapsed_seconds=time.monotonic() - started,
                    )
            if candidate_count > max_candidates:
                break
        if database_keys:
            return KeyExtractionResult(
                key_hex=next(iter(database_keys.values())),
                database_keys=database_keys,
                pid=process.pid,
                process_name=process.name,
                probe_database=None,
                scanned_regions=len(regions),
                candidate_count=candidate_count,
                elapsed_seconds=time.monotonic() - started,
            )
    finally:
        kernel32.CloseHandle(handle)
    return None


def iter_raw_key_hex_strings(data: bytes) -> Iterable[str]:
    yielded: set[str] = set()
    for match in RAW_KEY_RE.finditer(data):
        value = match.group(1).decode("ascii").lower()
        if value not in yielded:
            yielded.add(value)
            yield value

    prefix_variants = [b"x\x00'\x00", b"X\x00'\x00"]
    for prefix in prefix_variants:
        start = 0
        while True:
            index = data.find(prefix, start)
            if index < 0:
                break
            hex_start = index + len(prefix)
            hex_end = hex_start + 96 * 2
            chunk = data[hex_start:hex_end]
            if len(chunk) == 96 * 2 and all(chunk[offset + 1] == 0 for offset in range(0, len(chunk), 2)):
                value_bytes = bytes(chunk[offset] for offset in range(0, len(chunk), 2))
                if all(chr(value) in "0123456789abcdefABCDEF" for value in value_bytes):
                    value = value_bytes.decode("ascii").lower()
                    if value not in yielded:
                        yielded.add(value)
                        yield value
            start = index + 1


def iter_key_pointers(data: bytes) -> Iterable[int]:
    start = 0
    while True:
        suffix_at = data.find(KEY_STUB_SUFFIX, start)
        if suffix_at < 8:
            break
        pointer_at = suffix_at - 8
        pointer = struct.unpack_from("<Q", data, pointer_at)[0]
        if 0x10000 <= pointer <= 0x00007FFFFFFFFFFF:
            yield pointer
        start = suffix_at + 1


def looks_like_key_material(candidate: bytes) -> bool:
    if len(candidate) != KEY_SIZE:
        return False
    if candidate == b"\x00" * KEY_SIZE:
        return False
    if candidate.count(0) > 4:
        return False
    if len(set(candidate)) < 16:
        return False
    printable = sum(1 for value in candidate if 32 <= value <= 126)
    if printable > 26:
        return False
    return True


def open_process(kernel32: ctypes.WinDLL, pid: int) -> int:
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    handle = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not handle:
        handle = kernel32.OpenProcess(PROCESS_ALL_ACCESS, False, pid)
    return int(handle or 0)


def list_memory_regions(kernel32: ctypes.WinDLL, handle: int, max_region_size: int) -> list[MemoryRegion]:
    kernel32.VirtualQueryEx.argtypes = [
        wintypes.HANDLE,
        wintypes.LPCVOID,
        ctypes.POINTER(MEMORY_BASIC_INFORMATION),
        ctypes.c_size_t,
    ]
    kernel32.VirtualQueryEx.restype = ctypes.c_size_t

    regions: list[MemoryRegion] = []
    address = 0
    mbi = MEMORY_BASIC_INFORMATION()
    mbi_size = ctypes.sizeof(mbi)
    while kernel32.VirtualQueryEx(handle, ctypes.c_void_p(address), ctypes.byref(mbi), mbi_size):
        base = int(mbi.BaseAddress or 0)
        size = int(mbi.RegionSize or 0)
        if (
            mbi.State == MEM_COMMIT
            and mbi.Type == MEM_PRIVATE
            and size > 0
            and size <= max_region_size
            and is_readable_protect(mbi.Protect)
        ):
            regions.append(MemoryRegion(base=base, size=size, protect=int(mbi.Protect), region_type=int(mbi.Type)))
        next_address = base + size
        if next_address <= address:
            break
        address = next_address
    return regions


def is_readable_protect(protect: int) -> bool:
    return not (protect & PAGE_NOACCESS) and not (protect & PAGE_GUARD)


def read_process_memory(kernel32: ctypes.WinDLL, handle: int, address: int, size: int) -> bytes:
    kernel32.ReadProcessMemory.argtypes = [
        wintypes.HANDLE,
        wintypes.LPCVOID,
        wintypes.LPVOID,
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    kernel32.ReadProcessMemory.restype = wintypes.BOOL
    buffer = ctypes.create_string_buffer(size)
    bytes_read = ctypes.c_size_t(0)
    ok = kernel32.ReadProcessMemory(
        handle,
        ctypes.c_void_p(address),
        buffer,
        size,
        ctypes.byref(bytes_read),
    )
    if not ok or bytes_read.value <= 0:
        return b""
    return buffer.raw[: bytes_read.value]


def find_wechat_processes_native() -> list[ProcessInfo]:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == INVALID_HANDLE_VALUE:
        raise MemoryAccessError(last_error_message("CreateToolhelp32Snapshot"))
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        processes: list[ProcessInfo] = []
        if not kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            return []
        while True:
            name = entry.szExeFile
            if name.lower() in WECHAT_PROCESS_NAMES:
                processes.append(ProcessInfo(pid=int(entry.th32ProcessID), name=name))
            if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                break
        return processes
    finally:
        kernel32.CloseHandle(snapshot)


def enable_debug_privilege() -> None:
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    token = wintypes.HANDLE()
    TOKEN_ADJUST_PRIVILEGES = 0x0020
    TOKEN_QUERY = 0x0008
    SE_PRIVILEGE_ENABLED = 0x00000002

    class LUID(ctypes.Structure):
        _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]

    class TOKEN_PRIVILEGES(ctypes.Structure):
        _fields_ = [("PrivilegeCount", wintypes.DWORD), ("Luid", LUID), ("Attributes", wintypes.DWORD)]

    if not advapi32.OpenProcessToken(
        kernel32.GetCurrentProcess(),
        TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY,
        ctypes.byref(token),
    ):
        return
    try:
        luid = LUID()
        if not advapi32.LookupPrivilegeValueW(None, "SeDebugPrivilege", ctypes.byref(luid)):
            return
        privileges = TOKEN_PRIVILEGES(1, luid, SE_PRIVILEGE_ENABLED)
        advapi32.AdjustTokenPrivileges(token, False, ctypes.byref(privileges), 0, None, None)
    finally:
        kernel32.CloseHandle(token)


def last_error_message(api_name: str) -> str:
    code = ctypes.get_last_error()
    return f"{api_name} failed with Windows error {code}"
