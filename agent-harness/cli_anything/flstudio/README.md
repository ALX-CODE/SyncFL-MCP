# Optional FL Studio CLI

Install the main package/environment first, then install the harness from the repository root:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -Cli
.\.venv\Scripts\syncfl.exe --help
```

The branded command is `syncfl`, installed in this environment's `Scripts`
directory. `cli-anything-flstudio` remains an equivalent command. Activate the
environment to use either name without its full path. A `flstudio` shortcut is
not installed. The harness requires Click (`click>=8`, declared in its setup
metadata and included by the main environment). Contributor setup through
`Develop.cmd` installs the editable CLI automatically.

Use `--help` for current commands and subcommands. The CLI shares native transport, target checks and background policy with the server. GUI-backed commands need explicit interactive mode. Native calls and batches can partially apply; inspect the result before recovery.

See the repository [architecture](../../../docs/ARCHITECTURE.md), [configuration](../../../docs/CONFIGURATION.md) and [development guide](../../../docs/DEVELOPMENT.md). Tool counts and live qualifications belong to the current server reference, not an old CLI snapshot.
