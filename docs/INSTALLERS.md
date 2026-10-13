# Controller installation entry points

For the normal Windows Codex setup, use `scripts/setup_laptop.ps1` as described
in [getting started](GETTING-STARTED.md). The root `install.ps1` and `install.sh` retain
their Claude configuration step; it now previews a `syncfl` entry without changing
client files. Pass `-SkipClaude` on Windows or `--skip-claude` on macOS/Linux to
skip the preview. No additional client is configured automatically.

Apply a reviewed client configuration with the repository Python:

```powershell
.\.venv\Scripts\python.exe scripts\register_claude.py --client desktop --apply
```

Use `--client code` for Claude Code, or `--config` for an explicit file. Existing
unrelated entries are preserved, invalid JSON is refused, and changed files get
an exact backup before atomic replacement. A conflicting `syncfl` entry requires
`--replace`; previews and repeated identical updates do not write anything.

Both root installers use `scripts/install_fl_integration.py` to install the file
list defined by `scripts/doctor.py`. This includes the controller, extended and
production commands, native catalog, piano script, and note-protocol companion.
The helper checks source syntax, takes the GUI/controller/piano bridge locks,
refuses pending piano work, retains changed destination files in
`Settings/MCP-install-backups/<id>`, and verifies every installed hash plus the
controller digest. Unrelated settings files are preserved. Identical files are
left in place.

The helper can run independently using an already prepared repository Python:

```powershell
.\.venv\Scripts\python.exe scripts\install_fl_integration.py --check
.\.venv\Scripts\python.exe scripts\install_fl_integration.py --apply
```

`--check` is the default and does not create files. Pass `--settings-dir` with an
existing absolute `FL Studio/Settings` directory, or `--fl-user-data` with the
Image-Line root containing `FL Studio`. Otherwise the shared path resolver uses
`FL_USER_DATA_FOLDER`, the known Windows relocated Documents folder, or the
platform default. An absent Settings directory is refused; launch FL once or
correct the supplied path before installing.

The locks exclude cooperating clients while files are replaced. They do not
reload Python already running inside FL. Finish active operations first; after
installation, reload the MIDI controller or restart FL to load the new code.
Use `scripts/doctor.py --live` separately when ready to verify the digest actually
loaded by FL. A disk hash match does not establish that live state.

On Windows, native ALX FL Bridge is the default. The root installer checks the
bundled runtime hashes offline. Server startup creates/reuses the two endpoints;
enable **ALX FL Bridge (FL Studio)** as FL's input with **FL Studio MCP Controller**.
The installer does not install a driver/service, elevate, stop processes, or
create MIDI endpoints. If the native Windows prerequisites are unavailable,
configure an existing alternative using `FL_MCP_MIDI_PORT`. A loopMIDI port is
an optional fallback and requires its separate process to remain running.
See the [native runtime requirements](../native/midi_bridge/README.md).

On macOS, enable IAC Driver in Audio MIDI Setup and choose its receiving bus in
FL. Linux/Wine requires an existing MIDI route and separate host qualification;
the Windows native helper is not used. `install.sh` refuses Windows shells and
directs them to `install.ps1`.
