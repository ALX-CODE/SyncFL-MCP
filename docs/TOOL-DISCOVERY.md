# Tool discovery

SyncFL defaults to six advertised entry tools instead of injecting all underlying
schemas into every new conversation. The complete toolset remains callable.

| Entry tool | Purpose |
|---|---|
| `fl_build_info` | Identify the loaded build before relying on a capability |
| `fl_session_inspect` | Obtain fresh live project/transport observations |
| `fl_discover` | Find up to five task-related summaries by default; maximum ten |
| `fl_describe` | Fetch exact original contracts for one to five tool names |
| `fl_workflows` | List a compact workflow/domain index, or inspect one workflow |
| `fl_call` | Execute one original tool with its original arguments and guards |

For example, discover `lower the backing vocals by 2 dB`, describe
`fl_production_inspect` and `fl_mixer_scene`, then execute the needed inspections
through `fl_call`. Build the scene from those fresh observations. Schemas, target
indices, units, defaults and preflight requirements come from the underlying
tools; the router neither invents arguments nor decides whether a write is authorized.

Discovery ranks exact tool identifiers first, curated musical workflow terms next,
and BM25 over current tool contracts last. It returns inspection/status companions
alongside candidate writes, including alternative routes when useful. Named plugin
controls handle supported physical units; legacy setters are not interchangeable.
All 146 tools have an on-disk domain and usage note in `discovery_catalog.json`.
Tests check this index against actual registrations, so new tools cannot silently
go missing from the audited inventory. Unrecognized language can still retrieve
irrelevant candidates: inspect contracts and use a domain filter to narrow them.

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
complete original schema list. Use `discovery` (the default) to restore six entry
tools. Mode selection does not change `FL_MCP_INTERACTION_POLICY` or grant desktop
input. Existing processes and conversations keep their loaded catalogs until the
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
