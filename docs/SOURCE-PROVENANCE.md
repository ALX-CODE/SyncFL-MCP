# Application source provenance

The current SyncFL application source is maintained by ALX-CODE and nobibasedd.
Before this revision, parts of the application retained an earlier FL Studio
MCP implementation. On October 12, 2026, those retained implementations were
replaced rather than relabeled or stripped of comments.

The replacement covers the in-host controller and dispatch logic, connection
ownership and MIDI transactions, transport/mixer/channel/plugin tools, server
bootstrap and resources, desktop shortcut dispatch, package exports and Claude
configuration helpers. Existing independently implemented piano transactions,
advanced production modules and native bridges remain part of SyncFL.

The public tool names, input signatures, wire action names and host API calls
remain compatible where needed. They are interface contracts. The replacement
uses a different implementation: grouped native handlers, shared validation and
result handling, one connection owner, bounded correlated transactions, and
configuration previews with preserved backups. Renaming symbols alone was not
used as evidence of a rewrite.

## Review and verification

The source review compared the current source against 41 commits and 60
distinct source-file versions across both reviewed predecessor histories
(tips `202b7a28a4dc725350b68064d1be4e1eab852847` and
`54467fd93b56a2ceb7e8e586e1c215b234843ebb`). The comparison found no matching
nontrivial function bodies after removing docstrings and no matching windows of
ten significant source lines after excluding comments, imports and blank lines.
Remaining short similarities were inspected as interface signatures, standard
Python/shell syntax, host API calls and formatting.

These checks support the source review; a zero-match score alone is not a proof
of independent authorship or a substitute for understanding the implementation.
The behavior checks cover request correlation, expiry, safe failure reporting,
index/value bounds, host signatures, complete controller loading, client-config
preservation and registration of all 139 MCP tools. Live plugin qualification
remains subject to the conditions in [validation](VALIDATION.md).

The final local offline runs passed 2,294 tests with two skips on Python 3.13.13
and 2,290 tests with six skips on Python 3.14.6. Public signatures for all 47
rewritten basic tools match their previous interface. The generated reference
and public documentation checks pass. These results do not mean the replacement
controller has been reloaded and requalified in every live FL installation.

## Licenses and historical revisions

The current application license names SyncFL contributors. Older Git revisions
retain the copyright notices applicable to the code they contain. Competitor
links in the dated feature review identify research sources, not current source
dependencies or maintainers.

Microsoft Windows MIDI Services and other dependencies keep their own required
notices. See [NOTICE.md](../NOTICE.md) and the native bridge notices. This source
replacement does not claim ownership of those components, FL Studio or plugins.
