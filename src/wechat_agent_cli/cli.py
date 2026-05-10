from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .copying import copy_databases
from .dashboard import run_dashboard_server
from .decrypt import decrypt_databases
from .key_extract import extract_wechat_key
from .keys import (
    extract_key_with_command,
    fingerprint_key,
    load_database_keys,
    load_key,
    save_database_keys,
    save_key,
)
from .scanner import scan_environment
from .verify import verify_databases
from .workspace import default_workspace, ensure_workspace


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    except Exception as exc:
        if getattr(args, "json", False):
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        else:
            print(f"Error: {exc}", file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wechat-agent",
        description="Discover, copy, decrypt, and verify local Windows WeChat databases.",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=default_workspace(),
        help="Runtime workspace directory. Defaults to .wechat-agent in the current directory.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    scan = subparsers.add_parser("scan", help="Scan processes and candidate WeChat database files.")
    add_scan_args(scan)
    scan.set_defaults(func=cmd_scan)

    key = subparsers.add_parser("key", help="Save or extract a database key.")
    key.add_argument("--profile", default="default", help="Profile name for persisted key lookup.")
    key.add_argument("--key", dest="manual_key", help="64- or 96-character hexadecimal database key.")
    key.add_argument(
        "--auto",
        action="store_true",
        help="Scan running Weixin/WeChat process memory and validate the key with a raw database copy.",
    )
    key.add_argument(
        "--raw-dir",
        type=Path,
        help="Raw copied database directory or file used to validate auto-extracted keys.",
    )
    key.add_argument(
        "--pid",
        action="append",
        type=int,
        default=[],
        help="Specific Weixin/WeChat process id to scan. Can be passed multiple times.",
    )
    key.add_argument(
        "--max-region-mb",
        type=int,
        default=64,
        help="Largest memory region to scan during --auto. Defaults to 64 MB.",
    )
    key.add_argument(
        "--max-candidates",
        type=int,
        default=2000,
        help="Maximum key pointer candidates to validate during --auto.",
    )
    key.add_argument(
        "--external-cmd",
        help="Trusted local command that prints a 64-character hexadecimal key.",
    )
    key.add_argument("--json", action="store_true", help="Emit JSON output.")
    key.set_defaults(func=cmd_key)

    copy_cmd = subparsers.add_parser("copy", help="Hot-copy database files and sidecars.")
    add_scan_args(copy_cmd)
    copy_cmd.add_argument(
        "--source",
        action="append",
        type=Path,
        default=[],
        help="Specific database file to copy. Can be passed multiple times.",
    )
    copy_cmd.add_argument(
        "--account",
        action="append",
        default=[],
        help="Copy only databases for this wxid account. Can be passed multiple times.",
    )
    copy_cmd.add_argument(
        "--category",
        action="append",
        default=[],
        help="Copy only this db_storage category, such as message/contact/session.",
    )
    copy_cmd.add_argument(
        "--name",
        action="append",
        default=[],
        help="Copy only database names matching this pattern, such as message_*.db.",
    )
    copy_cmd.add_argument(
        "--core",
        action="store_true",
        help="Copy core login/message/contact/session databases.",
    )
    copy_cmd.add_argument("--run-id", help="Workspace run id. Defaults to current timestamp.")
    copy_cmd.set_defaults(func=cmd_copy)

    decrypt = subparsers.add_parser("decrypt", help="Decrypt copied databases.")
    decrypt.add_argument("--input", required=True, type=Path, help="Input database file or directory.")
    decrypt.add_argument("--output", type=Path, help="Output directory. Defaults to sibling decrypted/.")
    decrypt.add_argument("--profile", default="default", help="Saved key profile.")
    decrypt.add_argument("--key", dest="manual_key", help="64-character hexadecimal database key.")
    decrypt.add_argument(
        "--provider-cmd",
        help=(
            "External decrypt command. The command receives WECHAT_AGENT_INPUT, "
            "WECHAT_AGENT_OUTPUT, and WECHAT_AGENT_DB_KEY in its environment."
        ),
    )
    decrypt.add_argument("--json", action="store_true", help="Emit JSON output.")
    decrypt.set_defaults(func=cmd_decrypt)

    verify = subparsers.add_parser("verify", help="Verify SQLite files and detect WeChat schemas.")
    verify.add_argument("--input", required=True, type=Path, help="SQLite database file or directory.")
    verify.add_argument("--json", action="store_true", help="Emit JSON output.")
    verify.set_defaults(func=cmd_verify)

    serve = subparsers.add_parser("serve", help="Start the local Dashboard REST API.")
    serve.add_argument(
        "--decrypted-dir",
        type=Path,
        help="Directory containing decrypted SQLite databases. Defaults to the latest work/*/decrypted directory.",
    )
    serve.add_argument("--host", default="127.0.0.1", help="Host to bind. Defaults to 127.0.0.1.")
    serve.add_argument("--port", type=int, default=8765, help="Port to bind. Defaults to 8765.")
    serve.set_defaults(func=cmd_serve)

    return parser


def add_scan_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--data-dir",
        action="append",
        type=Path,
        default=[],
        help="Root directory to scan. Can be passed multiple times.",
    )
    parser.add_argument(
        "--max-depth",
        type=int,
        default=7,
        help="Maximum recursive depth for data directory discovery.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON output.")


def cmd_scan(args: argparse.Namespace) -> int:
    result = scan_environment(data_dirs=args.data_dir, max_depth=args.max_depth)
    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(f"Platform: {result.platform}")
        print(f"Running WeChat processes: {len(result.processes)}")
        for process in result.processes:
            print(f"  - {process['name']} pid={process.get('pid', 'unknown')}")
        print(f"Candidate roots: {len(result.roots)}")
        for root in result.roots:
            print(f"  - {root}")
        print(f"Database candidates: {len(result.databases)}")
        for database in result.databases:
            labels = [database.family]
            if database.account:
                labels.append(f"account={database.account}")
            if database.category:
                labels.append(f"category={database.category}")
            print(f"  - [{', '.join(labels)}] {database.path}")
    return 0


def cmd_key(args: argparse.Namespace) -> int:
    ensure_workspace(args.workspace, update_gitignore=True)
    selected_sources = [bool(args.manual_key), bool(args.external_cmd), bool(args.auto)]
    if sum(selected_sources) > 1:
        raise ValueError("Use only one of --key, --external-cmd, or --auto.")
    public_details: dict = {}
    if args.manual_key:
        key = args.manual_key
        source = "manual"
    elif args.auto:
        extraction = extract_wechat_key(
            workspace=args.workspace,
            raw_dir=args.raw_dir,
            pids=args.pid,
            max_region_size=args.max_region_mb * 1024 * 1024,
            max_candidates=args.max_candidates,
        )
        key = extraction.key_hex
        if not key:
            raise ValueError("Auto extraction did not return any usable key.")
        source = "auto"
        public_details = extraction.to_public_dict()
    elif args.external_cmd:
        key = extract_key_with_command(args.external_cmd)
        source = "external"
    else:
        existing = load_key(args.workspace, args.profile)
        if not existing:
            raise ValueError("No saved key. Pass --key or --external-cmd.")
        key = existing
        source = "saved"

    saved = source != "saved"
    if saved:
        if args.auto and public_details.get("database_key_count"):
            save_database_keys(args.workspace, args.profile, extraction.database_keys, source=source)
        else:
            save_key(args.workspace, args.profile, key, source=source)

    payload = {
        "ok": True,
        "profile": args.profile,
        "source": source,
        "saved": saved,
        "fingerprint": fingerprint_key(key),
    }
    payload.update(
        {field: value for field, value in public_details.items() if field not in {"ok", "fingerprint"}}
    )
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"Key profile '{args.profile}' ready. Fingerprint: {payload['fingerprint']}")
    return 0


def cmd_copy(args: argparse.Namespace) -> int:
    ensure_workspace(args.workspace, update_gitignore=True)
    result = copy_databases(
        workspace=args.workspace,
        sources=args.source,
        data_dirs=args.data_dir,
        max_depth=args.max_depth,
        run_id=args.run_id,
        accounts=args.account,
        categories=args.category,
        names=args.name,
        core=args.core,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"Run: {result['run_id']}")
        print(f"Raw output: {result['raw_dir']}")
        for item in result["databases"]:
            status = "ok" if item["ok"] else "failed"
            print(f"  - {status}: {item['source']} -> {item.get('dest', item.get('error'))}")
    return 0 if all(item["ok"] for item in result["databases"]) else 1


def cmd_decrypt(args: argparse.Namespace) -> int:
    ensure_workspace(args.workspace, update_gitignore=True)
    key = args.manual_key or load_key(args.workspace, args.profile)
    database_keys = {} if args.manual_key else load_database_keys(args.workspace, args.profile)
    result = decrypt_databases(
        input_path=args.input,
        output_dir=args.output,
        key=key,
        database_keys=database_keys,
        provider_cmd=args.provider_cmd,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"Decrypted output: {result['output_dir']}")
        for item in result["databases"]:
            status = "ok" if item["ok"] else "failed"
            method = item.get("method", "none")
            print(f"  - {status} ({method}): {item['source']} -> {item.get('dest', item.get('error'))}")
    return 0 if all(item["ok"] for item in result["databases"]) else 1


def cmd_verify(args: argparse.Namespace) -> int:
    result = verify_databases(args.input)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for item in result["databases"]:
            status = "ok" if item["ok"] else "failed"
            print(f"  - {status}: {item['path']}")
            if item["ok"]:
                print(f"    schema: {item['schema_family']}; tables: {item['table_count']}")
            else:
                print(f"    error: {item['error']}")
    return 0 if all(item["ok"] for item in result["databases"]) else 1


def cmd_serve(args: argparse.Namespace) -> int:
    decrypted_dir = args.decrypted_dir or latest_decrypted_dir(args.workspace)
    run_dashboard_server(decrypted_dir=decrypted_dir, host=args.host, port=args.port)
    return 0


def latest_decrypted_dir(workspace: Path) -> Path:
    work_dir = workspace / "work"
    candidates = [
        path / "decrypted"
        for path in work_dir.iterdir()
        if (path / "decrypted").is_dir()
    ] if work_dir.exists() else []
    if not candidates:
        raise ValueError("No decrypted directory found. Run decrypt first or pass --decrypted-dir.")
    return max(candidates, key=lambda path: path.stat().st_mtime)


if __name__ == "__main__":
    raise SystemExit(main())
