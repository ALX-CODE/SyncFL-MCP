#!/usr/bin/env bash
# Preview a SyncFL client entry. Pass --apply to publish it with a backup.
set -euo pipefail
base="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
interpreter="$base/../.venv/bin/python"
if [[ ! -x "$interpreter" ]]; then
    printf '%s\n' 'Run uv sync in the repository before configuring a client.' >&2
    exit 1
fi
exec "$interpreter" "$base/register_claude.py" "$@"
