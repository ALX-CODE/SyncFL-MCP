"""Offline installation audit; opt into a bounded read-only FL MIDI handshake."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.metadata
import json
import os
import sys
from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parents[1]
DEPLOYED_FILES = (
    ("fl_controller/device_FLStudioMCP.py", "Hardware/FLStudioMCP/device_FLStudioMCP.py"),
    ("fl_controller/mcp_extended.py", "Hardware/FLStudioMCP/mcp_extended.py"),
    ("fl_controller/mcp_production.py", "Hardware/FLStudioMCP/mcp_production.py"),
    ("fl_controller/native_catalog.json", "Hardware/FLStudioMCP/native_catalog.json"),
    ("scripts/ComposeWithLLM.pyscript", "Piano roll scripts/ComposeWithLLM.pyscript"),
    ("src/fl_studio_mcp/utils/note_protocol.py", "Piano roll scripts/mcp_note_protocol.py"),
)


def windows_server():
    from fl_studio_mcp.utils.windows_server import resolve_windows_server

    try:
        return Path(resolve_windows_server())
    except RuntimeError:
        return None


def paths():
    from fl_studio_mcp.utils.paths import get_fl_settings_dir

    settings = get_fl_settings_dir().resolve()
    windows = windows_server()
    return {"root": str(ROOT), "settings": str(settings),
            "fl_user_data": str(settings.parent.parent),
            "windows_server": str(windows) if windows and windows.is_file() else None}


def controller_source_hash():
    """Match the digest captured by the controller when FL loads its source."""
    digest = hashlib.sha256()
    for name in ("device_FLStudioMCP.py", "mcp_extended.py", "mcp_production.py",
                 "native_catalog.json"):
        digest.update(name.encode("utf-8"))
        digest.update((ROOT / "fl_controller" / name).read_bytes())
    return digest.hexdigest()


def audit(live=False):
    report = {"paths": paths(), "checks": [], "warnings": [], "live_requested": live}
    checks = report["checks"]
    checks.append({"name": "python_tooling_supported", "ok": sys.version_info[:2] >= (3, 11),
                   "actual": sys.version.split()[0]})
    if sys.version_info[:2] not in ((3, 12), (3, 13), (3, 14)):
        report["warnings"].append(
            "The installer recommends Python 3.13 and CI covers 3.12-3.14; "
            "this runtime needs independent dependency and import checks."
        )
    declared = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"]["version"]
    try:
        installed = importlib.metadata.version("fl-studio-mcp")
        from fl_studio_mcp import __version__

        checks.append({"name": "build_version", "ok": installed == declared == __version__,
                       "declared": declared, "installed": installed, "module": __version__})
    except importlib.metadata.PackageNotFoundError:
        checks.append({"name": "build_version", "ok": False})
    modules = ["fastmcp", "mido", "zstandard", "cbor2", "numpy", "scipy",
               "soundfile", "PIL", "fl_studio_mcp.server"]
    if sys.platform == "win32":
        modules.extend(["pynput", "pyaudiowpatch"])
    else:
        modules.append("rtmidi")
    for name in modules:
        try:
            importlib.import_module(name)
            checks.append({"name": f"import:{name}", "ok": True})
        except Exception as exc:
            checks.append({"name": f"import:{name}", "ok": False,
                           "error_type": type(exc).__name__})
    from fl_studio_mcp.utils import serum_presets

    for name in ("kick", "pad"):
        try:
            serum_presets.read_preset(serum_presets.DATA / f"{name}.SerumPreset")
            checks.append({"name": f"bundled_serum_blueprint:{name}", "ok": True})
        except Exception as exc:
            checks.append({"name": f"bundled_serum_blueprint:{name}", "ok": False,
                           "error_type": type(exc).__name__})
    settings = Path(report["paths"]["settings"])
    for source, destination in DEPLOYED_FILES:
        source_path, installed_path = ROOT / source, settings / destination
        matches = (installed_path.is_file() and source_path.is_file()
                   and hashlib.sha256(installed_path.read_bytes()).digest()
                   == hashlib.sha256(source_path.read_bytes()).digest())
        checks.append({"name": f"deployed:{destination}", "ok": matches})
    if sys.platform == "win32":
        from fl_studio_mcp.utils.native_midi import verify_runtime

        try:
            verify_runtime()
            checks.append({"name": "native_midi_runtime_integrity", "ok": True})
        except (OSError, ValueError) as exc:
            checks.append({"name": "native_midi_runtime_integrity", "ok": False,
                           "error_type": type(exc).__name__})
        from fl_studio_mcp.utils.native_midi_transport import NativeMidiBackend

        try:
            backend = NativeMidiBackend()
            outputs = backend.list_outputs()
            checks.append({"name": "native_midi_transport", "ok": True,
                           "output_count": len(outputs), "abi_version": 65536})
        except (OSError, ValueError, RuntimeError) as exc:
            checks.append({"name": "native_midi_transport", "ok": False,
                           "error_type": type(exc).__name__})
    report["windows_gui_helpers"] = (
        "available" if report["paths"]["windows_server"]
        else "optional observation helper missing; set FL_MCP_WINDOWS_SERVER for UI snapshots"
    )
    if live:
        from fl_studio_mcp.utils.midi_connection import MIDIConnection

        connection = MIDIConnection()
        try:
            ping = connection.send_command("bridge.ping", timeout=5)
            expected_hash = controller_source_hash()
            checks.append({"name": "live_loaded_controller_source", "ok": (
                ping.get("success") is True
                and ping.get("controller_source_sha256") == expected_hash
            ), "expected": expected_hash, "actual": ping.get("controller_source_sha256")})
            result = connection.send_command("native.call", {
                "function": "channels.channelCount", "args": [], "allow_write": False,
            }, timeout=5)
            checks.append({"name": "live_controller_handshake", "ok": bool(result.get("success")),
                           "channel_count": result.get("value")})
            report["midi_transport"] = connection.transport_kind
        except Exception as exc:
            checks.append({"name": "live_controller_handshake", "ok": False,
                           "error_type": type(exc).__name__})
        finally:
            connection.disconnect()
    report["success"] = all(item["ok"] for item in checks)
    report["scope"] = (
        "Offline checks plus controller handshake; GUI/audio not exercised" if live
        else "Local installation and MIDI enumeration only; no FL handshake, MIDI send or audio"
    )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--paths-only", action="store_true")
    parser.add_argument("--fl-user-data", help="Image-Line root, not Settings")
    parser.add_argument("--midi-port", help="ALX FL Bridge (Send), or an exact alternative output")
    args = parser.parse_args()
    if args.fl_user_data:
        os.environ["FL_USER_DATA_FOLDER"] = args.fl_user_data
    if args.midi_port:
        os.environ["FL_MCP_MIDI_PORT"] = args.midi_port
    report = paths() if args.paths_only else audit(args.live)
    print(json.dumps(report, indent=2))
    return 0 if args.paths_only or report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
