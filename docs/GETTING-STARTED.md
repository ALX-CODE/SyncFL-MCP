# Getting started with SyncFL

The server runs on the same computer as FL Studio. An MCP-compatible assistant connects over stdio; the server talks to FL through an installed controller and local MIDI/files. A GitHub checkout does not connect an assistant to a remote DAW.

SyncFL is the public product name; SyncFL-MCP is the repository name. The Python distribution/import (`fl-studio-mcp` / `fl_studio_mcp`), existing `fl_*` tools, `FL_MCP_*` settings and **FL Studio MCP Controller** name are preserved for compatibility. The branded console command is `syncfl-mcp`; the existing `fl-studio-mcp` command remains available.

## Requirements

| Component | Required for |
|---|---|
| Windows x64 and FL Studio | Primary tested live workflow; FL 26.1.4 is the recorded qualification build |
| Python 3.13 and [uv](https://docs.astral.sh/uv/getting-started/installation/) | Recommended setup from `uv.lock`; 3.12 and 3.14 are also checked in CI |
| Windows MIDI Services with loopback support, x64 VC++ runtime | Default native bridge; runtime probe must pass |
| An MCP client supporting local stdio | Assistant connection |
| Your own plugins and licenses | Third-party controls; no commercial plugin is bundled |
| Compatible Windows MCP executable | Interactive workflows and native-save window identity inspection |
| FFmpeg | Integrated loudness, true-peak and reference-level analysis |

Packaging declares Python 3.10+, but the recommended setup uses 3.13 and configuration helpers need 3.11+. Select 3.14 with `-PythonVersion 3.14`; Windows MIDI output uses a bundled native DLL and no longer needs RtMidi compilation. The pinned optional dependency set lacks a Python 3.15 wheel for `cbor2`, so 3.15 is not yet a recommended binary-only install. macOS IAC and Linux/Wine routes are retained but lack equivalent live qualification; Windows-only helpers are unavailable there.

## 1. Prepare the checkout

Use this repository's current `main` branch or extract a clean source release into a permanent folder. Client configuration points to this checkout; moving it later requires updating that configuration.

```powershell
git clone https://github.com/ALX-CODE/SyncFL-MCP.git
cd SyncFL-MCP
```

Install uv, open FL once, and find **Options → File settings → User data folder**. Finish active MCP work before upgrading controller files. You may close FL during installation and reopen it afterward.

## 2. Install dependencies and controller files

From the repository root:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup_laptop.ps1 -SkipCodex
```

This installs Python 3.13, synchronizes locked Serum/audio/development dependencies, verifies/creates native MIDI endpoints, deploys six FL integration files with backups, and runs the offline doctor. `-SkipCodex` leaves client configuration unchanged. Omit it to register this checkout in Codex.

The execution policy applies only to that process. No Windows service/driver is installed and no machine execution policy is changed.

For relocated data or an explicit Windows helper:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup_laptop.ps1 `
  -SkipCodex `
  -FlUserData 'D:\Music\Image-Line' `
  -WindowsServer 'C:\Tools\WindowsMCP\Sbroenne.WindowsMcp.exe'
```

`-FlUserData` is the **Image-Line root containing `FL Studio`**, not `Settings`. The Windows helper is optional for native MIDI and offline work; supply an existing compatible installation. It is not bundled here.

If native MIDI is unavailable, select an existing configured output with `-MidiPort 'Exact Output Name'`. This is an explicit alternative, not automatic fallback. See the [MIDI bridge](../native/midi_bridge/README.md) for probing and requirements.

## 3. Select the FL controller

In **Options → MIDI settings**:

1. Under **Input**, enable **ALX FL Bridge (FL Studio)**.
2. Select **FL Studio MCP Controller** as the controller type.
3. Assign an unused MIDI port number.
4. Disable duplicate dedicated MCP controller assignments.

The assistant sends to **ALX FL Bridge (Send)**. FL receives on **ALX FL Bridge (FL Studio)**. Numeric suffixes can change after reboot; the server resolves the stable unique name.

After reboot or a MIDI-service restart, run the server/setup before opening FL so endpoints exist for its scan. If FL was already running, refresh MIDI devices or restart it after finishing work.

## 4. Connect a client

| Field | Value |
|---|---|
| Command | Absolute path to this checkout's `.venv\Scripts\python.exe` |
| Arguments | `-m`, `fl_studio_mcp.server` |
| Working directory | Absolute repository path |
| `FL_MCP_MIDI_PORT` | `ALX FL Bridge (Send)` |
| `FL_USER_DATA_FOLDER` | Your actual Image-Line root |
| `FL_MCP_INTERACTION_POLICY` | `background` |

[Configuration](CONFIGURATION.md) contains JSON and Codex TOML examples. Keep credentials and personal configuration outside the repository.

## 5. Verify before editing

```powershell
.\.venv\Scripts\python.exe scripts\doctor.py
.\.venv\Scripts\python.exe scripts\verify_mcp_runtime.py
```

With FL running and configured, add `--live` to either command. These read the controller without playing, changing notes or saving. If you configured custom paths in your client, pass the same values to the doctor or set them in the shell first; a terminal does not inherit another application's configuration.

A fresh server should expose **143 tools**, **workflow revision 12**, version **0.2.0**. The doctor verifies file hashes, loaded-controller identity and connectivity. It does not audition audio or certify every workflow on your machine.

In the assistant, inspect `fl_session_inspect` and `fl_workflow_catalog`. Keep the first session read-only, then use [workflows](WORKFLOWS.md) for a small edit in a disposable project.

## Update or remove

Finish bridge work, update the intended source revision, rerun setup, reload the FL controller, and reconnect the MCP client. Existing processes retain imported code. Changed FL files are backed up under `Settings/MCP-install-backups/<id>`; Codex registration preserves a configuration backup.

To stop using the integration, disable its input/controller in FL and remove the entry from your MCP client. After all work finishes, the integration's `Hardware/FLStudioMCP` folder and its two documented piano files can be removed. Preserve unrelated FL settings. Native endpoint removal is separately documented in the bridge README; do not remove an active pair.
