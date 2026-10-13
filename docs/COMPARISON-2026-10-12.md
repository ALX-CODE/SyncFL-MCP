# FL Studio MCP landscape review — October 12, 2026

## Conclusion

**We believe SyncFL is one of the most comprehensive FL Studio MCP toolkits available as of October 12, 2026.** Its strength is the combination of live session control, guarded production workflows, independently qualified plugin mappings, Serum 2 tools, saved-project editing, audio measurements and source-preserving packaging.

This review **does not establish that SyncFL has the most tools, contains every competing feature, or is the single most capable implementation**. Other projects expose larger catalogs or deeper features in particular areas. Those findings are retained below rather than hidden behind a ranking.

The comparison uses SyncFL source baseline `08e118a2cd1dcba4e9bd786ade4c03651bd90877`. This documentation update does not add runtime capabilities.

## What the SyncFL numbers mean

| Measure | Verified scope |
|---|---|
| **139 MCP tools** | Registered tools in the [generated reference](reference/TOOLS.md); one tool can contain multiple operations |
| **11 named plugin profiles** | Exact exposed layouts; Serum 2 has a separate integration |
| **1,921 named plugin controls** | Includes repeated band controls and normalized-only controls |
| **802 qualified mappings** | 379 numeric plus 423 menu mappings across nine profiles; not every named control is calibrated |
| **6,496 independent holdout checks** | Recorded host-display checks for retained mappings; not proof of audible DSP behavior |
| **323 native catalog entries** | An additional discovery surface, not 323 extra MCP tools or 323 individually qualified operations |
| **2,244 tests passed, 2 skipped** | Source-only packaging baseline on Python 3.13.13; tests are not a comparative music-quality score |

Sources: [capabilities](CAPABILITIES.md), [plugin guide](PLUGIN-CONTROL-GUIDE.md), [calibration record](plugin-calibration-evidence.json), [native catalog](../fl_controller/native_catalog.json), [validation](VALIDATION.md).

## Search and review coverage

- **179 public repository candidates** from five paginated GitHub queries plus the supplied Glama-only candidate. Private search results were excluded.
- **157 repository trees screened**, representing **98 distinct full-tree hashes**. Forks and unchanged trees are not counted as independent implementations.
- **2,680 distinct text blobs** retrieved, hash-checked and statically indexed, spanning **5,538 repository/file records**. This is not a claim of a line-by-line audit of every file.
- **23 targeted project reviews**, including all four originally supplied projects, broader descendants, native SDK approaches, offline editors and compact domain-tool implementations.
- Official **Image-Line Gopher** and **SnappySnap** documentation was checked separately. No competitor code was installed, imported or executed; no competitor was live-benchmarked.

The [machine-readable review record](comparison-2026-10-12.json) lists every candidate, its classification, available commit/tree IDs, search queries, review depth and selected evidence paths. All five GitHub search responses reported `incomplete_results: false`; that means those queries completed, not that GitHub indexes every implementation in existence. None of the 157 retrieved trees was truncated, and the selected text downloads had no errors.

The search covered `flstudio mcp`, `fl-studio mcp`, the phrase `"FL Studio" "MCP"`, `fls-pilot` and `kuruitsugizaki`, including forks. Web and directory searches were used to cross-check discoverability. Unrelated finance/Flow Studio products, empty or unavailable repositories, SDK-only projects and implementations without established FL-specific code were not promoted into feature competitors.

## The original four

| Project | Source/documentation finding | SyncFL comparison |
|---|---|---|
| [karl-andres/fl-studio-mcp](https://github.com/karl-andres/fl-studio-mcp/blob/202b7a28a4dc725350b68064d1be4e1eab852847/README.md) | Transport, mixer, channels, generic plugin parameters and piano-roll scripts | SyncFL also provides guarded scenes, qualified named controls, project/audio workflows and the supported Windows MIDI Services route. Its current control implementation is independently replaced; see [source provenance](SOURCE-PROVENANCE.md). |
| [rosasynthesiz/flstudio-mcp](https://github.com/rosasynthesiz/flstudio-mcp/blob/ba94a6d2d2cc40bbf0d8a30c2b1fd89b4c66cb80/README.md) | 67-tool production surface, Mix Doctor, rollback-oriented edits, composition, chains and audio analysis | SyncFL has broader project/file/Serum and qualified plugin-control coverage; rosa's tempo/key and transcription work remains relevant |
| [thunderdew-dawn/fls-pilot](https://github.com/thunderdew-dawn/fls-pilot/blob/21288eb4856ca80d49b5f79893c4af7072a363ee/README.md) | Consolidated domain tools, routing, organization, knowledgebase and export-readiness checks | Real production-workflow overlap; legacy decorators are removed during registration, so raw source counts misrepresent its active tool list |
| [ohhalim/flstudio-mcp](https://github.com/ohhalim/flstudio-mcp/blob/d518dec361700d886ffc74eb94d388636a2eeed8/README.md) | Earlier MIDI/composition and retrieval-assisted integration | A narrower control surface than SyncFL; useful historical context, not an equivalent live production suite |

## Broader field

These observations describe available source and documentation at pinned commits. “Implemented” here means a concrete code path was found; host support and musical quality still need independent testing.

| Project or family | Material finding |
|---|---|
| [cagerber/flstudio-mcp](https://github.com/cagerber/flstudio-mcp/blob/5031731d5cf9d8323585e293c7fb6ff1fb4c85dd/README.md) | README advertises **194 tools**, with extensive REC-event, channel, mixer and UI wrappers. Some tools intentionally return host-API-unavailable reports. Its existence alone prevents a largest-tool-count claim for SyncFL. |
| [geezoria/FLStudioMCP](https://github.com/geezoria/FLStudioMCP/blob/3aa5f39c7321a00976c4a8b5c75c71365f439d53/README.md); [danishaft/FLStudioMCP](https://github.com/danishaft/FLStudioMCP/blob/2e524a5b6ba6c67c55c1be0b693802d5d0b2fd70/README.md) | Broad granular API wrappers, generators, voice/audio tooling; the danishaft server also registers offline FLP inspection/editing operations. |
| [marconadas/MacFLStudioMCP](https://github.com/marconadas/MacFLStudioMCP/blob/f595f93bc1d6290fd71db4ac8dfa95a95ef75237/README.md); [calvinw/MacFLStudioMCP](https://github.com/calvinw/MacFLStudioMCP/blob/bc082550c09fc8fdab58ffaede39c4d8423f207c/README.md); [nymphaea-a/fLMCP](https://github.com/nymphaea-a/fLMCP/blob/df93a7eaa52bac697d6fba0e8a6d9e89cfb92d1f/README.md) | macOS/file-IPC and guarded descendants with substantial native wrapper coverage. Some source modules are unregistered; for example marconadas disables its generator module. A declaration count is not the loaded interface. |
| [Zierax/fl-studio-mcp](https://github.com/Zierax/fl-studio-mcp/blob/e97e4818a70970469fbde97f6cb73a402b2e3a40/README.md) | Extensive native wrappers, REC events, API discovery and raw dispatch. A broad low-level API is useful but is not equivalent to independently verified higher-level workflows. |
| [Goldwep/FLStudio-MCP-Goldwep](https://github.com/Goldwep/FLStudio-MCP-Goldwep/blob/68cd3996fe1c0d9fa3e4ffc5ca105315d147e07f/README.md) | **110 tools**, file-IPC, composition and offline project intelligence. Published live checks are partial, and plugin loading/general live clip placement remain bounded. |
| [synopsys0/postfader-fl-studio-mcp](https://github.com/synopsys0/postfader-fl-studio-mcp/blob/dec5985377b7cae9808f2e76189e4c096f51059a/README.md) | **85 tools** with production runs and rendered-audio analysis. Spectral/reference readiness and vocal/instrumental masking go beyond SyncFL's present level/loudness comparison. |
| [whatanaiceguy/kuruitsugizaki](https://github.com/whatanaiceguy/kuruitsugizaki/blob/c23ff52ae075a0ee39b84e5e7e122f31db80e859/README.md) | **13 grouped tools** cover offline project authoring, presets and raw live execution. Its parameter index has **49 plugin entries, 48 with nonzero parameter rows**, plus broader preset-format support. This exceeds SyncFL's plugin breadth; sparse display sweeps are a different measure from our qualified unit conversions. |
| [Realynx/Fl-MCP](https://github.com/Realynx/Fl-MCP/blob/b4526a41c5f67dd3f83f588f5194e58ec98d6839/README.md) | **29 tools plus FruityLink SDK**: native note/clip authoring, generator/effect loading, linked automation and separate-desktop sessions. This is a deeper background-authoring route than SyncFL currently provides. Exact-build live evidence is the author's, not a test we reproduced. |
| [MadBlast0/Fl-Studio-MCP](https://github.com/MadBlast0/Fl-Studio-MCP/blob/735d1bc49265e08c589dbe0f7388db6e9bf3ba46/README.md) | **57 consolidated tools**, raw in-FL execution, REC events and generated Edison scripts. Edison-specific scripting is a useful extension beyond SyncFL's external WAV editors. |
| [KindaMAD-hav/fl-studio-mcp-KindaMAD](https://github.com/KindaMAD-hav/fl-studio-mcp-KindaMAD/blob/8ade2bc5a02f35bd0680101294fd341ec2dd0be5/README.md) | Song-spec and Windows-targeted automation extend the Karl foundation. Window-message automation and fully native, focus-independent execution must be assessed separately. |
| [dawhubapp/flstudio-mcp](https://github.com/dawhubapp/flstudio-mcp/blob/1e26e231d6a733df71635e1403d2d9a3fdc34922/README.md) | **Three grouped tools** encompass many live/offline operations and rendering. A clear example of why fewer tools need not mean fewer abilities. |
| [jihadkhawaja/flstudio-mcp](https://github.com/jihadkhawaja/flstudio-mcp/blob/6def9463d6fa8636bbc81010581318a39c47d3c4/README.md) | Music2DAW provides offline FLP rewriting, arranged MIDI export and plugin-state extraction through **five MCP entry points**. |
| [wyattowalsh/fl-mcp](https://github.com/wyattowalsh/fl-mcp/blob/83e1bd58a170e7d781d698f36f606ce07036b32d/README.md) | **216 internal operations**, with documentation explicitly separating **8 verified live** operations from **208 attemptable** operations and deterministic mock coverage. Credit that distinction; do not count mocks as live proof. |
| [vladvrx/MidiMCP](https://github.com/vladvrx/MidiMCP/blob/b79f5cba125f11eca11e383ae3fd98e8fcbf9462/README.md) | MIDI authoring, Serum 2 presets, rendering and reference-audio analysis overlap with SyncFL, with a narrower production focus. |
| [sadoway7/flaik](https://github.com/sadoway7/flaik/blob/648779b0d4ca34875295e9f32b5b9be610b83055/README.md) | Gopher-panel integration prototype. Its reported native tool catalog is not a reproduced external-control benchmark; live override verification is documented as blocked. |

Smaller implementations, setup variants and additional forks remain in the JSON corpus. They were screened rather than silently omitted from discovery counts. No “winner” is inferred from stars, downloads, code size or README confidence.

## Why tool counts cannot decide this

Different projects expose one function per knob, consolidate an entire domain behind one tool, retain aliases, register optional modules, return unsupported-operation reports, or include mock paths. Raw Python decorator counts even include modules the server never registers.

A concrete caution: [the vocal-chain cloning implementation](https://github.com/404kidwiz/fl-studio-mcp/blob/4bcd6febe12b52ecd962b6f46594b3bdbf2b80fa/src/fl_studio_mcp/tools/vocal_cloning.py) formats success messages without reading audio or applying a chain. That advertised ability was not credited. The same project also has real algorithmic composition code; this finding applies to the cited tool, not every feature in that repository.

We likewise do not inflate SyncFL's 139 tools with its 323 native catalog entries, 1,921 controls or 6,496 validation checks. Those are different measures. Nor does one generic raw-execution tool establish reliable access to every conceivable FL operation.

## Official and commercial routes

**Gopher already performs DAW actions.** Image-Line's [current control documentation](https://cluster.image-line.com/fl-studio-learning/fl-studio-online-manual/html/gopher/gopher_control.htm) describes experimental transport, channel, plugin, mixer and script operations with user approval. It would be inaccurate to describe Gopher as only a question-answering assistant. The documentation reviewed does not establish a stable, supported external MCP contract for third-party clients.

**SnappySnap is complementary competition for plugin control.** Its [official manual](https://electricsmudge.com/manual) documents MCP access to hosted-plugin parameters, snapshots, morphing and randomization. It hosts a plugin rather than exposing all of FL Studio's arrangement/mixer/project state. This was a documentation review, not an installed product test.

## What to improve next

| Priority | Evidence from the field | Adaptation and acceptance gate |
|---|---|---|
| Background note, clip and plugin authoring | FruityLink/FL MCP; current SyncFL gaps | Evaluate a build-specific native route in disposable sessions; require exact target identity, persisted readback and unchanged human foreground/input. Keep current prototypes experimental until this passes. |
| More named plugins and native presets | Kuru's broader maps and preset formats | Prioritize installed, actually used plugins. Capture exact exposed layouts locally, qualify units/enums with independent settled holdouts, and reject incompatible layouts. Do not import another machine's sparse samples as calibrated truth. |
| Better rendered-audio evidence | PostFader and Kuru | Add bounded spectral/stereo/transient and aligned vocal/instrumental analysis with reference-readiness checks. Validate against known audio fixtures and listening; do not present estimated analysis as recovered settings. |
| Host-native audio-script workflows | MadBlast's Edison route | Investigate generated Edison scripts with explicit activation boundaries, source preservation and verifiable outputs. Keep external WAV editing available where no host scripting is required. |
| Deeper offline project operations | Kuru, Music2DAW, dawhub and danishaft | Evaluate arranged MIDI export, semantic diff and additional supported event edits. Require preserved opaque data, source immutability and save/reopen comparison. |

These are review findings and acceptance criteria, **not implemented features or release promises**. Reuse requires a separate license review; the native SDK and FL-MCP adapter have distinct terms. No third-party code was copied into SyncFL during this review.

## Boundaries and refresh policy

“As of October 12, 2026” is a snapshot of the public material found during this review. It is not an exhaustive audit of every branch, historical release, private repository or commercial product. Search indexes can miss projects. Source inspection cannot prove installed compatibility, reliable concurrent human use, audio quality or universal feature parity.

SyncFL remains Windows-first. General background note writing, plugin loading and rendering are unfinished; Auto-Tune Pro and Decapitator do not have qualified physical-unit calibration. See the [capability limits](CAPABILITIES.md) and [roadmap](ROADMAP.md).

Refresh the discovery corpus, pinned commits, registration paths, substantive gaps and SyncFL evidence before strengthening or redating the README claim. Do not replace this conclusion with “#1,” “the most capable,” “every feature,” or “fully headless” without a defined, independently reproducible comparison that supports it.
