#!/bin/bash
# FL Studio MCP installation for macOS and Linux/Wine environments.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

case "${OSTYPE:-}" in
    msys*|win32*|cygwin*)
        echo 'On Windows use install.ps1; native ALX FL Bridge is the default MIDI route.'
        exit 1
        ;;
esac

SKIP_DEPENDENCIES=false
SKIP_CLAUDE=false
INSTALL_ARGS=(--apply)
while (($#)); do
    case "$1" in
        --skip-dependencies) SKIP_DEPENDENCIES=true; shift ;;
        --skip-claude) SKIP_CLAUDE=true; shift ;;
        --fl-user-data)
            if (($# < 2)); then echo '--fl-user-data requires an Image-Line root' >&2; exit 2; fi
            INSTALL_ARGS+=(--fl-user-data "$2"); shift 2 ;;
        *) echo "Unknown argument: $1" >&2; exit 2 ;;
    esac
done

echo '[1/3] Checking Python environment...'
if [[ "$SKIP_DEPENDENCIES" == false ]]; then
    if ! command -v uv >/dev/null 2>&1; then
        echo 'Install uv first: https://docs.astral.sh/uv/getting-started/installation/' >&2
        exit 1
    fi
    uv python install 3.12
    uv sync --locked --python 3.12
fi
PYTHON="$SCRIPT_DIR/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
    echo 'Repository Python is missing; rerun without --skip-dependencies.' >&2
    exit 1
fi

echo '[2/3] Installing and verifying the complete FL controller and piano files...'
"$PYTHON" "$SCRIPT_DIR/scripts/install_fl_integration.py" "${INSTALL_ARGS[@]}"

if [[ "$SKIP_CLAUDE" == false ]]; then
    echo '[3/3] Running the existing Claude MCP configuration helper...'
    bash "$SCRIPT_DIR/scripts/install_mcp_for_claude.sh"
fi

echo ''
echo 'FL files verified. Reload the controller or restart FL after current work finishes.'
if [[ "${OSTYPE:-}" == darwin* ]]; then
    echo 'In Audio MIDI Setup, enable IAC Driver and its bus.'
    echo 'In FL MIDI inputs, enable that IAC bus and select FL Studio MCP Controller.'
else
    echo 'Use an existing virtual MIDI route exposed to your FL/Wine environment.'
    echo 'Set FL_MCP_MIDI_PORT to its exact output name and configure the receiving input in FL.'
    echo 'Linux/Wine compatibility requires separate validation on your host.'
fi
echo 'A relocated FL data folder can be selected with --fl-user-data or FL_USER_DATA_FOLDER.'
echo 'For manual server startup: uv run fl-studio-mcp'
