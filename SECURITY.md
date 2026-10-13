# Security and privacy

## Trust boundary

This is a local DAW-control server for a trusted MCP client. A caller can inspect local projects and files and request supported edits. Background mode blocks desktop input; it is not a security sandbox for arbitrary untrusted callers. Do not expose the server as an unauthenticated internet service.

The server uses local stdio, MIDI and filesystem messages. It does not require a cloud account. Your selected AI client may send tool results, paths, project metadata or audio-related information to its model provider according to that client's settings. Local transport alone does not make the whole assistant workflow offline.

Receipts, diagnostics and captures may contain personal paths, plugin names, project names or musical data. Keep them outside version control and redact them before sharing. Never upload commercial sessions, credentials or plugin license material with a bug report.

## Reporting a vulnerability

Use GitHub's private vulnerability-reporting feature if it is enabled for the published repository. Otherwise contact a listed maintainer privately through their public profile and ask for a secure reporting route. Do not disclose exploit details or secrets in a public issue.

Include the affected commit/version, component, reproducible steps, impact and a minimal redacted test case. Report third-party host/plugin vulnerabilities to the appropriate vendor as well. No response-time guarantee is currently established.

## Supported changes

Security fixes target the current maintained source branch. Older private snapshots and experimental native prototypes should not be assumed supported releases. Changes must preserve transport correlation, path boundaries, fail-closed background policy and explicit uncertain outcomes.
