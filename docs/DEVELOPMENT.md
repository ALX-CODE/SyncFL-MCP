# Development

Start a focused branch from `main` and keep live DAW tests separate from ordinary code/test work. Source changes do not reload a running MCP server or FL controller.

## Environment and tests

```powershell
uv sync --locked --python 3.13 --extra serum --extra audio --extra dev
uv pip install --python .venv/Scripts/python.exe --no-deps --editable ./agent-harness
uv run --no-sync python -m pytest tests -q
uv run --no-sync python -m pytest agent-harness/cli_anything/flstudio/tests -q
uv run --no-sync ruff check src tests scripts/generate_tool_docs.py scripts/check_public_docs.py scripts/export_public_release.py
uv run --no-sync python -m compileall -q src fl_controller scripts
```

CI runs the locked suite on Python 3.12, 3.13 and 3.14 and builds/tests the C++ MIDI transport separately. The recommended local baseline is 3.13. Windows installations use the bundled native transport and do not build RtMidi. Python 3.15 is not yet qualified with the pinned optional dependency set.

The main suite uses fake hosts/isolated files and does not require an active FL instance. Some optional packaging/capture cases skip when their prerequisites are absent. Native components have separate CMake/CTest suites; read their build instructions before downloading SDK dependencies.

Legacy FL scripts execute with host-injected globals, so a blanket lint command over every historical file is not equivalent to the maintained source/test checks.

## Keep documentation coupled to code

```powershell
uv run --no-sync python scripts/generate_tool_docs.py
uv run --no-sync python scripts/generate_tool_docs.py --check
uv run --no-sync python scripts/check_public_docs.py
```

The generator lists tools from an in-memory MCP client without connecting to FL or executing a tool. It records descriptions, schemas and source links. The checker validates local links and example argument schemas, and ensures public documentation only links to included source files. Do not hand-edit generated reference files.

If adding a tool, define clear input units/indices, classify its read/offline/input behavior in the operator catalog, and explain output evidence and unknown outcomes. Update the capability/workflow guides when user-visible behavior changes.

## Tests that prove useful behavior

Validate full preflight before mutation, exact target matching, malformed inputs, partial outcomes, timeouts after dispatch, refusal of stale revisions, and source preservation. Mocked success only tests the wrapper; it is not live FL qualification.

For live tests, use a disposable saved project, stop playback unless the test explicitly needs it, capture before/after state, and keep a new receipt per mutation. Check foreground/input behavior when claiming background support. Restore test state through explicit verified operations. Never run live mutation tests against a collaborator's active project.

## Packaging and releasing

`pyproject.toml` packages the Python source and three pinned native runtime folders. Runtime hash checks must continue to pass after packaging. Build and inspect an actual wheel after packaging changes.

Keep production material outside this source repository. Use the [source export process](PUBLIC-RELEASE.md); it has an explicit file allowlist, local-content checks and per-file hashes. Never merge private development history or copy its `.git` directory into this repository.
