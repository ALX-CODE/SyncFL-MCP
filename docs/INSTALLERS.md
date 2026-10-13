# Installing SyncFL on Windows

**Double-click `Install.cmd`** in a permanent extracted source folder and choose your assistant. Or run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -Client codex
```

The client choices are `codex`, `claude-desktop`, `claude-code` and `none`. The command-line default is `none`, which prints manual connection fields. `-ClientConfig` chooses an explicit configuration file. Client changes are backed up; unrelated entries and unspecified environment settings are preserved.

## What setup does

1. Reuses uv, or downloads the pinned official uv 0.13.0 installer and checks its SHA-256 before execution. The bootstrap uses a user-local tools folder without modifying PATH or shell profiles. `-NoBootstrap` disables this download.
2. Prepares Python 3.13 and synchronizes locked audio/Serum runtime dependencies with `--no-dev`. Normal users do not install test tools or FL API stubs. `-SkipDependencies` reuses an existing environment.
3. Delegates FL file deployment to `scripts/install_fl_integration.py`, which checks source syntax, takes GUI/controller/piano bridge locks, refuses pending work, backs up changed files and verifies installed hashes. Unrelated FL settings are preserved.
4. Registers only the selected assistant using the absolute environment Python, then runs the offline doctor. It does not reload FL, send MIDI commands, move the mouse or type into another application.

The source ZIP is small; Python and dependencies are additional first-install downloads. Plugins, licenses, songs and raw calibration recordings are not bundled. The package uses local stdio and requires no SyncFL cloud account.

## Developer and CLI installation

Contributors can run **`Develop.cmd`** to clone or reuse a local Git checkout and
prepare the editable development environment. It preserves existing branches and
changes. Offline setup skips FL deployment and client registration by default.
See [developer setup](DEVELOPER-SETUP.md) for private-repository access and the
command-line bootstrap.

For an existing checkout, `install.ps1 -Development -SkipFlIntegration` installs
the development dependencies and CLI without requiring FL Studio. Use
`install.ps1 -Cli` to add just the CLI to the normal runtime installation. The
branded command is `.\.venv\Scripts\syncfl.exe --help`; no PATH changes are made.

## Preview and custom paths

```powershell
.\install.ps1 -Plan -Client codex
.\install.ps1 -Client claude-desktop -FlUserData 'D:\Music\Image-Line'
```

`-FlUserData` is the existing Image-Line root containing `FL Studio`, not its `Settings` folder. `-MidiPort` selects an existing alternative MIDI output; omission preserves client configuration. `-WindowsServer` points to an optional compatible Windows MCP executable. The native bridge still requires supported Windows MIDI Services and Microsoft's x64 VC++ runtime.

The legacy `scripts/setup_laptop.ps1` delegates to this installer. It retains its Codex default and `-SkipCodex` switch; there is one FL deployment implementation.

## Finish in FL Studio

Start the selected assistant/SyncFL first, then open FL. Under **Options → MIDI settings**, enable **ALX FL Bridge (FL Studio)** and select **FL Studio MCP Controller**. Reload the controller after upgrades and reconnect the assistant. Endpoint creation occurs during server startup, not offline installation.

For independent verification:

```powershell
.\.venv\Scripts\python.exe scripts\install_fl_integration.py --check
.\.venv\Scripts\python.exe scripts\doctor.py --live
```

Run the live check after the controller is configured. It verifies connectivity and loaded source identity, not audible DSP behavior. See [getting started](GETTING-STARTED.md) and [troubleshooting](TROUBLESHOOTING.md).

On macOS, `install.sh` retains the IAC Driver setup path and Claude configuration preview. Linux/Wine needs an existing MIDI route and separate host qualification. The Windows installer is the primary tested flow.
