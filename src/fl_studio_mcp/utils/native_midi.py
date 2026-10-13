"""Start the owned Windows MIDI endpoint pair without a resident broker process."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from fl_studio_mcp.utils.midi_connection import MANAGED_PORT_BASE, _is_managed_port

_BUNDLED_RUNTIME = Path(__file__).resolve().parents[1] / "_native" / "midi_bridge"
# A present but damaged bundle must fail verification, never fall back to another build.
RUNTIME = (
    _BUNDLED_RUNTIME
    if _BUNDLED_RUNTIME.exists()
    else Path(__file__).resolve().parents[3] / "native" / "midi_bridge" / "runtime"
)
RUNTIME_FILES = ("alx-midi-service.exe", "Windows.Devices.Midi2.dll", "Windows.Devices.Midi2.pri")


def verify_runtime(directory=None):
    """Check the exact shipped runtime before loading any native executable/DLL."""
    root = Path(directory) if directory is not None else RUNTIME
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8-sig"))
    if (not isinstance(manifest, dict) or manifest.get("schema_version") != 1
            or not isinstance(manifest.get("files"), dict)):
        raise ValueError("Unsupported native MIDI runtime manifest")
    for name in RUNTIME_FILES:
        entry = manifest["files"].get(name)
        expected = entry.get("sha256") if isinstance(entry, dict) else None
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected):
            raise ValueError(f"Missing native MIDI runtime hash: {name}")
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != expected.lower():
            raise ValueError(f"Native MIDI runtime hash mismatch: {name}")
    return root / RUNTIME_FILES[0]


def native_midi_status(*, ensure=False):
    """Observe or ensure only our stable endpoint pair; never install drivers."""
    if sys.platform != "win32":
        return dict(success=True, enabled=False, reason="Windows-only endpoint manager")
    try:
        executable = verify_runtime()
        completed = subprocess.run(
            [str(executable), "ensure" if ensure else "status", "--json"],
            capture_output=True, text=True, timeout=12, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"Native MIDI helper failed ({completed.returncode}): "
                f"{completed.stderr.strip()[:500]}"
            )
        result = json.loads(completed.stdout)
        if not isinstance(result, dict) or any(
            type(result.get(key)) is not bool
            for key in ("transport_available", "present", "created", "removed")
        ):
            raise ValueError("Invalid native MIDI helper response")
        if result["present"] and (
            result.get("output_name") != MANAGED_PORT_BASE
            or result.get("input_name") != "ALX FL Bridge (FL Studio)"
            or not isinstance(result.get("association_id"), str)
            or not result["association_id"]
        ):
            raise ValueError("Native MIDI helper returned the wrong endpoint pair")
        if not result["transport_available"] or (ensure and not result["present"]):
            raise RuntimeError("Windows MIDI Services loopback transport is unavailable")
        result.update(success=True, enabled=True)
        return result
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        return dict(success=False, enabled=True, error=str(exc))


def initialize_native_midi():
    """Run at server start/reconnect; preserve explicitly selected legacy ports."""
    requested = os.environ.get("FL_MCP_MIDI_PORT")
    if requested and not _is_managed_port(requested):
        return dict(success=True, enabled=False, reason="Explicit alternative MIDI port")
    return native_midi_status(ensure=True)
