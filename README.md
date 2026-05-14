# WeChat Agent CLI

Windows-first CLI for discovering local WeChat data, copying database files safely, managing database keys, decrypting through SQLCipher or an external provider, and verifying exported SQLite databases.

## Quick Start

```powershell
python -m pip install -e .
wechat-agent scan
wechat-agent key --key <64_HEX_KEY>
wechat-agent copy
wechat-agent decrypt --input .wechat-agent\work\<run-id>\raw
wechat-agent verify --input .wechat-agent\work\<run-id>\decrypted
```

The CLI writes runtime artifacts under `.wechat-agent/`, which is ignored by Git. Full keys are never printed to stdout; persisted keys are stored in `.wechat-agent/secrets.json`.

## Commands

- `scan`: detects running WeChat processes and candidate database files.
- `key`: saves a manual key or extracts one from an external command that prints a 64-character hex key.
- `copy`: hot-copies `.db`, `-wal`, and `-shm` files into a timestamped workspace.
- `decrypt`: copies plain SQLite databases as-is, or decrypts encrypted databases with the built-in native decryptor.
- `verify`: opens SQLite files and reports tables, columns, and likely WeChat schema type.
- `serve`: starts the local read-only Dashboard REST API.

## External Key Extraction

The project intentionally does not vendor a fragile or unpublished memory scanner. If you already use a trusted local extraction tool, wire it in with:

```powershell
wechat-agent key --external-cmd "your-tool.exe --print-key"
```

The command must print at least one 64-character hexadecimal key. The CLI stores the first valid key.

## Automatic Windows Key Extraction

For Windows Weixin 4.x, run the terminal as Administrator and keep Weixin logged in:

```powershell
wechat-agent key --auto
```

For Weixin 4.1.x, keys may be per-database raw keys instead of one account-wide key. In that case the command validates keys against the latest `.wechat-agent\work\<run-id>\raw` copy and saves a key map in `.wechat-agent\secrets.json`. The terminal output only shows fingerprints.

If multiple processes are running, target the main process from `scan` or `Get-Process Weixin`:

```powershell
wechat-agent key --auto --pid 10980 --max-candidates 5000
```

## Data Directory Discovery

On Windows, `scan` checks common WeChat/Weixin locations and the Weixin 4.x config files under:

```powershell
%APPDATA%\Tencent\xwechat\config\*.ini
```

Those files often point to a custom storage root such as `D:\document\wechat`. If automatic discovery still misses your data, pass the directory from Weixin's file-management settings:

```powershell
wechat-agent scan --data-dir D:\document\wechat
```

## Account-Scoped Copy

When `scan` shows multiple accounts, copy one account at a time:

```powershell
wechat-agent copy --account wxid_y048bdl6tkmy22 --core
```

`--core` copies the login, message, contact, session, message_resource, and hardlink categories. The last two help the dashboard resolve local image files. You can narrow further:

```powershell
wechat-agent copy --account wxid_y048bdl6tkmy22 --category message --name message_*.db
```

## SQLCipher

Encrypted databases are decrypted by the built-in native Python pipeline on Windows. It uses Windows CNG for AES-256-CBC and validates every SQLCipher page HMAC before writing output, so `sqlcipher.exe` is no longer required for Weixin 4.x raw keys.

For older database families, `decrypt` can still fall back to a SQLCipher CLI executable if one is available on `PATH`. The SQLCipher fallback uses SQLCipher 4 compatible PRAGMA values by default:

- `cipher_page_size = 4096`
- `kdf_iter = 256000`
- `cipher_hmac_algorithm = HMAC_SHA512`
- `cipher_kdf_algorithm = PBKDF2_HMAC_SHA512`

If your database family needs a different decryptor, use `--provider-cmd` and consume these environment variables:

- `WECHAT_AGENT_INPUT`
- `WECHAT_AGENT_OUTPUT`
- `WECHAT_AGENT_DB_KEY`

## Dashboard API

After decrypting, start the local API:

```powershell
wechat-agent serve --decrypted-dir .wechat-agent\work\20260510-000628\decrypted
```

The server binds to `127.0.0.1:8765` by default and reads decrypted SQLite files in read-only mode.

To keep the Dashboard data fresh while the API is running, enable automatic sync:

```powershell
wechat-agent serve --decrypted-dir .wechat-agent\work\20260510-000628\decrypted --auto-sync --sync-interval 60
```

With `--auto-sync`, the API keeps serving read-only requests while a background worker periodically hot-copies the live WeChat core databases, decrypts them into a new `.wechat-agent\work\autosync-*\decrypted` directory, then switches the API to that new decrypted set after the message/contact/session databases are available. By default auto-sync copies only Dashboard core categories; pass `--sync-all` to copy every discovered database, or use `--data-dir`, `--account`, `--category`, and `--name` to narrow the scan.

Inspect sync state with:

```powershell
Invoke-RestMethod http://127.0.0.1:8765/api/sync/status
```

Available endpoints:

- `GET /api/health`: database availability and paths.
- `GET /api/sync/status`: automatic sync status when `serve --auto-sync` is enabled.
- `GET /api/overview`: chat, message, contact, and session counts.
- `GET /api/contacts?q=&limit=&offset=`: contact list with display names.
- `GET /api/sessions?limit=&offset=`: recent sessions joined with contacts.
- `GET /api/chats?q=&limit=&offset=`: message chat tables mapped from `Name2Id.user_name`.
- `GET /api/messages?chat=&q=&type=&before=&after=&limit=&include_content=`: message query across one chat or all chats.
- `POST /api/summary`: summarize text messages for one chat and time range through the OpenAI Responses API.

Message summaries read OpenAI settings from `.wechat-agent/openai-responses.json` first, then `config/openai-responses.json`. Fill in:

```json
{
  "url": "https://api.openai.com/v1/responses",
  "api_key": "replace-with-your-openai-api-key",
  "model": "gpt-5.5",
  "stream": false
}
```

The fixed summary prompt lives at `src/wechat_agent_cli/prompts/wechat_message_summary.md`.

For Weixin 4.1 message databases, chat message tables are mapped as `Msg_` + `md5(username)`, using the decrypted `Name2Id` table.
