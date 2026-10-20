# Tool discovery

SyncFL advertises 32 core production APIs with their complete schemas in every
new session, plus four discovery helpers. Loading sounds, samples, instruments,
synths, VST plugins and FST presets, writing notes, arranging, mixing, routing,
playing and saving are directly available. These do not depend on remembered
tool names or an initial search. The remaining specialist tools stay callable
and discoverable. Render status is available; starting/cancelling a render is
not implemented in this backend build.

| Entry tool | Purpose |
|---|---|
| `fl_build_info` | Identify the loaded build before relying on a capability |
| `fl_session_inspect` | Obtain fresh live project/transport observations |
| `fl_discover` | Find up to five task-related summaries by default; maximum ten |
| `fl_describe` | Fetch exact original contracts for one to five tool names |
| `fl_workflows` | List a compact workflow/domain index, or inspect one workflow |
| `fl_call` | Execute one original tool with its original arguments and guards |

For example, call `fl_production_inspect` and `fl_mixer_scene` directly to lower
backing vocals by 2 dB. Build the scene from those fresh observations. Schemas, target
indices, units, defaults and preflight requirements come from the underlying
tools; the router neither invents arguments nor decides whether a write is authorized.

Discovery ranks exact tool identifiers first, task-specific loading/selection routes
and curated musical workflow terms next,
and BM25 over current tool contracts last. It returns inspection/status companions
alongside candidate writes, including alternative routes when useful. Named plugin
controls handle supported physical units; legacy setters are not interchangeable.
All 157 tools have an on-disk domain and usage note in `discovery_catalog.json`.
Tests check this index against actual registrations, so new tools cannot silently
go missing from the audited inventory. Unrecognized language can still retrieve
irrelevant candidates: inspect contracts and use a domain filter to narrow them.

A clean unnamed blank project is initialized with `fl_project_create` before
adding sounds or notes. Discovery and session inspection point to this route;
the assistant should perform the first save rather than request a manual save.

For loading requests, use ordinary language such as `load Serum preset`, `add VST
instrument`, `load sounds plugins synths in backend mode`, or `switch to existing
synth`. Loading verbs and asset names are matched separately, including plurals.
Recognized loading requests return their relevant bundle without padding it with
unrelated parameter setters. The workflow index includes example tasks.

Native WAV/FST loaders and guarded FLP loading use the experimental OLE bridge.
Discovery only recommends available backend routes; no mode switch exists.
Direct live loading of native Serum preset files is unavailable; the generic
loader accepts existing FL FST presets. Reconnect after upgrading so the client
loads the new descriptions.

## Execution and recovery

The discovery facade is a separate in-process FastMCP proxy. Only the underlying
server owns the DAW lease. Installing a recursive call wrapper directly on that
server would acquire the non-reentrant lease twice and reject its own calls.

Each `fl_call` forwards exactly once. Original argument validation, interaction
policy, revisions, receipts, cancellation handling, content blocks, structured
results, error flags and metadata remain in place. Discovery reads need no DAW
lease. The generic executor is explicitly marked as potentially destructive;
clients must not treat it as a read-only discovery tool. No automatic retry or
fallback to UI is introduced. Direct calls to original tool names remain compatible.

This interface reduces initial tool context, not every source of long-chat drift.
Use scoped/paginated reads, reinspect after mutations, retain compact task state
with project identities and receipt references, and verify job completion. Dispatch
results remain complete so that errors and uncertainty cannot disappear in a
summary. Large plugin catalogs still require the tools' own filters and pagination.

## Full mode and activation

Set `FL_MCP_TOOL_MODE=full` in the server environment and reconnect to expose the
complete original schema list. Use `discovery` (the default) for core production
APIs plus bounded discovery. Schema visibility never grants desktop input. Existing processes and
conversations keep their loaded catalogs until the
client reconnects; source/config changes cannot remove old context retroactively.

The Codex registration helper preserves other settings and makes a backup:

```powershell
.\.venv\Scripts\python.exe scripts\register_codex.py --apply --tool-mode discovery
```

Verify both fresh stdio paths without changing a project:

```powershell
.\.venv\Scripts\python.exe scripts\verify_mcp_runtime.py --mode discovery
.\.venv\Scripts\python.exe scripts\verify_mcp_runtime.py --mode full
.\.venv\Scripts\python.exe -m pytest tests/test_discovery.py tests/test_tool_lease.py
```

Retrieval fixtures cover audited musical requests, exact-name access to every
underlying tool, schema equality, bounds, errors and busy/cancelled execution.
They are deterministic integration checks, not an end-to-end musical-quality or
model-selection benchmark. The source catalog remains the authoritative reference.

The previous six-tool facade hid every production action behind a search. This
hybrid keeps the common workflow visible while bounding specialist context. It
uses the same principle as FastMCP's
[always-visible tools](https://github.com/PrefectHQ/fastmcp/blob/main/docs/v3/servers/transforms/tool-search.mdx).
Tests create independent fresh clients and compare every core schema to its
original registration. A separate canary checks direct and proxied calls retain
the same lease, errors and single-dispatch behavior.
