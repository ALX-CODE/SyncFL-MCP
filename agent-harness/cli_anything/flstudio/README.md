# Optional FL Studio CLI

Install the main package/environment first, then install the harness from the repository root:

```powershell
uv pip install --python .venv/Scripts/python.exe --no-deps --editable ./agent-harness
cli-anything-flstudio --help
```

The entry point is `cli-anything-flstudio`, installed in this environment's `Scripts` directory. Run `.\.venv\Scripts\cli-anything-flstudio.exe --help` if that directory is not on your PATH. A `flstudio` shortcut is not installed by this package. The harness also requires Click (`click>=8`, declared in its setup metadata and included by the main environment).

Use `--help` for current commands and subcommands. The CLI shares native transport, target checks and background policy with the server. GUI-backed commands need explicit interactive mode. Native calls and batches can partially apply; inspect the result before recovery.

See the repository [architecture](../../../docs/ARCHITECTURE.md), [configuration](../../../docs/CONFIGURATION.md) and [development guide](../../../docs/DEVELOPMENT.md). Tool counts and live qualifications belong to the current server reference, not an old CLI snapshot.
