# SyncFL configuration

SyncFL is backend-only. There is no execution-mode setting. Legacy
`FL_MCP_INTERACTION_POLICY` values are ignored and removed when registering clients.

The server uses local stdio. Configure an absolute Python path and repository working directory. An HTTP URL is not provided by this project.

The default `discovery` tool mode advertises 32 core production tools and four discovery helpers. Supported backend tools
remain available through exact-name calls or `fl_discover` → `fl_describe` → `fl_call`.
Set `FL_MCP_TOOL_MODE=full` to restore the complete initial tool list. This setting
only changes schema visibility; it cannot enable desktop input. See [tool discovery](TOOL-DISCOVERY.md).

## Generic MCP / Claude-style JSON

Replace these paths with your checkout and FL data root. Merge the entry into the client's existing configuration; preserve unrelated entries.

```json
{
  "mcpServers": {
    "fl-studio": {
      "command": "C:\\MusicTools\\SyncFL-MCP\\.venv\\Scripts\\python.exe",
      "args": ["-m", "fl_studio_mcp.server"],
      "cwd": "C:\\MusicTools\\SyncFL-MCP",
      "env": {
        "FL_USER_DATA_FOLDER": "D:\\Music\\Image-Line",
        "FL_MCP_MIDI_PORT": "ALX FL Bridge (Send)"
      }
    }
  }
}
```

Clients differ in configuration location and working-directory support. Without `cwd`, use command `uv` and arguments `run`, `--directory`, your absolute repository path, `--no-sync`, `syncfl-mcp`. Synchronize dependencies first. This avoids relying on the client's launch directory. The server identifies itself as SyncFL; existing client entry names remain compatible.

The root `install.ps1` / `install.sh` retain legacy Claude configuration helpers. Manual configuration makes the environment and policy explicit. Use `-SkipClaude` / `--skip-claude` for FL-file installation without client changes.

## Codex TOML

```toml
[mcp_servers.fl_studio]
command = 'C:\MusicTools\SyncFL-MCP\.venv\Scripts\python.exe'
args = ['-m', 'fl_studio_mcp.server']
cwd = 'C:\MusicTools\SyncFL-MCP'

[mcp_servers.fl_studio.env]
FL_USER_DATA_FOLDER = 'D:\Music\Image-Line'
FL_MCP_MIDI_PORT = 'ALX FL Bridge (Send)'
```

The registration helper checks or applies a backed-up update:

```powershell
.\.venv\Scripts\python.exe scripts\register_codex.py --check
.\.venv\Scripts\python.exe scripts\register_codex.py --apply `
  --fl-user-data 'D:\Music\Image-Line' --midi-port 'ALX FL Bridge (Send)'
```

It honors `CODEX_HOME`, otherwise the user's `.codex/config.toml`. Use `--config` for another file. It preserves unrelated entries and refuses structures it cannot safely update.

## Environment

| Variable | Meaning |
|---|---|
| `FL_USER_DATA_FOLDER` | Image-Line root containing `FL Studio`; otherwise platform path discovery |
| `FL_MCP_MIDI_PORT` | Explicit output; default Windows route uses the owned ALX endpoint |
| `FL_MCP_TOOL_MODE` | `discovery` by default; `full` advertises every underlying tool schema |
| `FL_MCP_WINDOWS_SERVER` | Absolute compatible Windows MCP executable for UI/window inspection |
| `FL_STUDIO_MCP_FFMPEG` | Explicit FFmpeg executable; otherwise PATH resolution |

Reconnect after changes. A variable set in a terminal does not update an already-running desktop client.

## Input policy and extras

Desktop input is unavailable. Unsupported operations do not switch modes or fall back to external mouse/keyboard tools.

The `serum` extra adds native preset decoding/building. The `audio` extra adds numeric/audio libraries and Windows loopback capture. FFmpeg and Windows MCP are separate system tools. `setup_laptop.ps1` installs all Python extras used by the Windows workflow; minimal manual environments may lack optional tool dependencies.

Do not expose this local control server directly to the internet. The client is a trusted caller with project, file and DAW access. See [security](../SECURITY.md).
