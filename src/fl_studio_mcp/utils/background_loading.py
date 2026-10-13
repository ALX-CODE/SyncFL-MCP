"""Experimental, selected-track-only OLE effect loading without desktop input.

The private OLE interface is not a supported Image-Line API. A callback result
never establishes completion; later native snapshots must identify the effect.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import subprocess
import time
import uuid
from pathlib import Path

from fl_studio_mcp.utils import native_windows
from fl_studio_mcp.utils.midi_connection import get_connection
from fl_studio_mcp.utils.protocol import transaction_lock
from fl_studio_mcp.utils.studio_loading import asset_path, mixer_drop_target

_BUNDLED_RUNTIME = Path(__file__).resolve().parents[1] / "_native" / "background_bridge"
# A present but damaged bundle must fail verification, never fall back to another build.
RUNTIME = (
    _BUNDLED_RUNTIME
    if _BUNDLED_RUNTIME.exists()
    else Path(__file__).resolve().parents[3] / "native" / "background_bridge" / "runtime"
)
RUNTIME_FILES = ("alx-background-bridge.exe", "alx-background-hook.dll")
MANIFEST_SHA256 = "1d7d8973e50a964293f9e32277c825aa3e69aa3611d5ad4680da59057b4cb3f9"
MAX_ASSET_BYTES = 64 * 1024 * 1024
LIMITATIONS = [
    "Experimental private OleDropTargetInterface; not a supported Image-Line API.",
    "Only the currently selected, visibly matching mixer track is supported. No track selection.",
    "No mouse, key, focus, activation, physical drop, retry or rollback fallback.",
    "The shared lock excludes cooperating clients, not manual edits. Guards are not atomic in FL.",
    "Slot names cannot detect every same-name replacement or prove plugin DSP or licensing.",
    "A timeout is unverified; an already-running callback cannot be cancelled safely.",
]


class ReadUnavailable(RuntimeError):
    """A native callback is busy, unavailable or timed out; read-only retry is safe."""


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def verify_runtime(directory=None):
    """Verify the pinned manifest and both exact binaries before starting the helper."""
    root = (Path(directory) if directory is not None else RUNTIME).resolve(strict=True)
    manifest_path = root / "manifest.json"
    data = manifest_path.read_bytes()
    if len(data) > 32768 or hashlib.sha256(data).hexdigest() != MANIFEST_SHA256:
        raise ValueError("Background runtime manifest differs from the qualified pinned build")
    manifest = json.loads(data)
    if (
        manifest.get("schema_version") != 1
        or manifest.get("architecture") != "x64"
        or manifest.get("experimental") is not True
        or set(manifest.get("files", {})) != set(RUNTIME_FILES)
    ):
        raise ValueError("Invalid background runtime manifest")
    for name in RUNTIME_FILES:
        path, entry = root / name, manifest["files"][name]
        if path.is_symlink() or not path.is_file() or not isinstance(entry, dict):
            raise ValueError(f"Invalid background runtime file: {name}")
        size, expected = entry.get("size"), entry.get("sha256")
        if (
            type(size) is not int
            or not 0 < size <= 16 * 1024 * 1024
            or not isinstance(expected, str)
            or not re.fullmatch("[a-f0-9]{64}", expected)
            or path.stat().st_size != size
            or hashlib.sha256(path.read_bytes()).hexdigest() != expected
        ):
            raise ValueError(f"Background runtime hash/size mismatch: {name}")
    return root / RUNTIME_FILES[0]


def _process_identity(pid):
    if os.name != "nt":
        raise RuntimeError("Background loading requires Windows x64")
    from ctypes import wintypes as w

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    kernel.OpenProcess.restype = w.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [
        w.HANDLE,
        w.DWORD,
        w.LPWSTR,
        ctypes.POINTER(w.DWORD),
    ]
    kernel.QueryFullProcessImageNameW.restype = w.BOOL
    kernel.GetProcessTimes.argtypes = [w.HANDLE] + [ctypes.POINTER(w.FILETIME)] * 4
    kernel.GetProcessTimes.restype = w.BOOL
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.CloseHandle.restype = w.BOOL
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        buffer, size = ctypes.create_unicode_buffer(4096), w.DWORD(4096)
        times = [w.FILETIME() for _ in range(4)]
        if not kernel.QueryFullProcessImageNameW(
            handle, 0, buffer, ctypes.byref(size)
        ) or not kernel.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
            raise ctypes.WinError(ctypes.get_last_error())
        image = Path(buffer.value)
        if not image.is_absolute() or image.name.casefold() != "fl64.exe":
            raise RuntimeError("Target process is not an absolute FL64.exe image")
        return dict(
            image=str(image), created=(times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
        )
    finally:
        kernel.CloseHandle(handle)


def _window_state(slot):
    rows = native_windows.windows()
    mains = [row for row in rows if row["class"] == "TFruityLoopsMainForm"]
    if len(mains) != 1:
        raise RuntimeError("Expected exactly one visible FL main window")
    main = mains[0]
    if not main.get("enabled") or any(
        row["pid"] == main["pid"] and row["class"] == "#32770" for row in rows
    ):
        raise ReadUnavailable("FL has an active modal operation")
    children = native_windows.windows(main["handle"], pid=main["pid"])
    mixers = [row for row in children if row["class"] == "TFXForm"]
    if len(mixers) != 1 or not mixers[0].get("enabled"):
        raise RuntimeError("Expected exactly one enabled visible FL mixer")
    mixer = mixers[0]
    point = mixer_drop_target(mixer, native_windows.windows(mixer["handle"], pid=main["pid"]), slot)
    x, y, width, height = mixer["bounds"]
    if not (x <= point[0] < x + width and y <= point[1] < y + height):
        raise RuntimeError("Computed effect slot lies outside the mixer")
    keys = ("handle", "pid", "title", "bounds", "dpi", "enabled")
    return dict(
        main={key: main[key] for key in keys},
        mixer={key: mixer[key] for key in keys},
        point=list(point),
        process=_process_identity(main["pid"]),
    )


def _input_snapshot():
    if os.name != "nt":
        return dict(available=False)
    from ctypes import wintypes as w

    user = ctypes.WinDLL("user32", use_last_error=True)
    user.GetCursorPos.argtypes, user.GetCursorPos.restype = [ctypes.POINTER(w.POINT)], w.BOOL
    user.GetForegroundWindow.restype = w.HWND
    user.GetCapture.restype = w.HWND
    point = w.POINT()
    valid = bool(user.GetCursorPos(ctypes.byref(point)))
    return dict(
        available=True,
        cursor=[point.x, point.y] if valid else None,
        foreground=int(user.GetForegroundWindow() or 0),
        capture=int(user.GetCapture() or 0),
        note="Observations only; user input can change these values. No restoration.",
    )


def _safe_input_snapshot():
    try:
        return _input_snapshot()
    except Exception as exc:
        # Diagnostics must never discard a callback result or prevent its journal.
        return dict(available=False, error=str(exc))


def _call(conn, action, params, deadline=None):
    timeout = 3 if deadline is None else min(3, deadline - time.monotonic())
    if timeout <= 0:
        raise ReadUnavailable("Readback deadline expired")
    try:
        result = conn.send_command(action, params, timeout)
    except (TimeoutError, OSError) as exc:
        raise ReadUnavailable(str(exc)) from exc
    if not isinstance(result, dict) or result.get("success") is not True:
        raise ReadUnavailable(f"Native {action} unavailable: {str(result)[:1000]}")
    return result


def _project(conn, deadline):
    result = _call(conn, "production.inspect", {"scope": "project"}, deadline)
    context = result.get("context")
    if (
        not isinstance(context, dict)
        or not isinstance(context.get("session"), str)
        or not context["session"]
        or any(
            type(context.get(key)) is not int or context[key] < 0
            for key in ("project_generation", "channel_generation")
        )
        or not re.fullmatch("[a-f0-9]{64}", str(result.get("revision", "")))
        or any(type(result.get(key)) is not bool for key in ("is_playing", "is_recording"))
    ):
        raise ValueError("Incomplete native project identity or transport state")
    return result


def _tracks(rows):
    if not isinstance(rows, list) or not 1 <= len(rows) <= 256:
        raise ValueError("Incomplete native mixer catalog")
    result = []
    for index, track in enumerate(rows):
        if (
            not isinstance(track, dict)
            or track.get("index") != index
            or type(track.get("index")) is not int
            or not isinstance(track.get("name"), str)
            or not isinstance(track.get("slots"), list)
            or len(track["slots"]) != 10
        ):
            raise ValueError("Incomplete native mixer track identity")
        slots = []
        for number, item in enumerate(track["slots"]):
            if (
                not isinstance(item, dict)
                or type(item.get("slot")) is not int
                or item["slot"] != number
                or type(item.get("occupied")) is not bool
            ):
                raise ValueError("Incomplete native effect slot identity")
            entry = dict(slot=number, occupied=item["occupied"])
            if item["occupied"]:
                if (
                    any(
                        not isinstance(item.get(key), str) or len(item[key]) > 4096
                        for key in ("name", "user_name")
                    )
                    or not item["name"]
                ):
                    raise ValueError("Incomplete native effect names")
                entry.update(name=item["name"], user_name=item["user_name"])
            slots.append(entry)
        result.append(dict(index=index, name=track["name"], slots=slots))
    return result


def _native_values(conn, specs, deadline):
    rows = _call(
        conn,
        "bridge.batch",
        {
            "dry_run": False,
            "steps": [
                dict(action="native.call", params=dict(function=name, args=args))
                for name, args in specs
            ],
        },
        deadline,
    ).get("results")
    if (
        not isinstance(rows, list)
        or len(rows) != len(specs)
        or any(
            not isinstance(row, dict)
            or "value" not in row
            or row.get("error")
            or row.get("success") is False
            for row in rows
        )
    ):
        raise ValueError("Incomplete native mixer batch")
    return [row["value"] for row in rows]


def _inspect(conn, track, slot, deadline=None):
    if deadline is None:
        deadline = time.monotonic() + 30
    window = _window_state(slot)
    ping = _call(conn, "bridge.ping", {}, deadline)
    if (
        ping.get("project_native", {}).get("controller_pid") != window["main"]["pid"]
        or type(ping.get("project_native", {}).get("controller_pid")) is not int
        or type(ping.get("safe_to_edit")) not in (int, bool)
        or ping["safe_to_edit"] not in (0, 1, False, True)
    ):
        raise ValueError("Controller process identity or edit safety is unavailable")
    project = _project(conn, deadline)
    selection_calls = [("mixer.trackNumber", []), ("ui.isInPopupMenu", [])]
    maximum, selected, popup = _native_values(
        conn, [("mixer.getTrackInfo", [2]), *selection_calls], deadline
    )
    if type(maximum) is not int or not 0 <= maximum <= 255:
        raise ValueError("Mixer catalog exceeds the 256-track bound")
    rows = []
    # A fully occupied track costs at most 61 native calls. Four tracks keep
    # each callback below 256 calls instead of scanning 1260 slots in one turn.
    for start in range(0, maximum + 1, 4):
        batch = _call(
            conn,
            "mixer.inspect",
            {
                "tracks": list(range(start, min(start + 4, maximum + 1))),
                "routes": False,
            },
            deadline,
        ).get("tracks")
        if not isinstance(batch, list) or len(batch) != min(4, maximum + 1 - start):
            raise ValueError("Incomplete bounded mixer read")
        rows.extend(batch)
    tracks = _tracks(rows)
    if (
        type(selected) is not int
        or not 0 <= selected < len(tracks)
        or type(popup) not in (int, bool)
        or popup not in (0, 1, False, True)
    ):
        raise ValueError("Invalid native selected track or popup state")
    if track >= len(tracks):
        raise ValueError("Mixer track does not exist")
    if _native_values(conn, selection_calls, deadline) != [selected, popup]:
        raise RuntimeError("Mixer selection or popup changed during inspection")
    later = _project(conn, deadline)
    if (
        later["revision"] != project["revision"]
        or later["context"] != project["context"]
        or _window_state(slot) != window
    ):
        raise RuntimeError("Project or mixer geometry changed during inspection")
    reasons = []
    if (
        project["is_playing"]
        or project["is_recording"]
        or later["is_playing"]
        or later["is_recording"]
    ):
        reasons.append("Stop playback and recording before loading")
    if not ping["safe_to_edit"] or popup:
        reasons.append("Resolve the active FL popup or modal operation")
    if selected != track:
        reasons.append(
            "Only the currently selected mixer track is supported; no track change is sent"
        )
    if window["mixer"]["title"] != "Mixer - " + tracks[track]["name"]:
        reasons.append("Visible mixer title does not match the exact native target track name")
    if tracks[track]["slots"][slot]["occupied"]:
        reasons.append("Target effect slot is occupied")
    identity = dict(
        window=window,
        project=project["context"],
        native_revision=project["revision"],
        selected_track=selected,
        mixer_tracks=tracks,
        track=track,
        slot=slot,
    )
    return dict(
        success=True,
        experimental=True,
        ready=not reasons,
        reasons=reasons,
        revision=_digest(identity),
        limitations=LIMITATIONS,
        **identity,
    )


def _target(track, slot):
    if (
        type(track) is not int
        or not 0 <= track <= 255
        or type(slot) is not int
        or not 0 <= slot <= 9
    ):
        raise ValueError("track must be 0..255 and effect slot must be 0..9")


def inspect_background_effect(track, slot, connection=None):
    _target(track, slot)
    conn = connection if connection is not None else get_connection()
    with transaction_lock(conn._hardware_dir / "mcp-gui.lock", 5):
        result = _inspect(conn, track, slot)
        try:
            result["runtime"] = dict(verified=True, executable=str(verify_runtime()))
        except (ValueError, OSError) as exc:
            result["runtime"] = dict(verified=False, error=str(exc))
            result["ready"] = False
            result["reasons"].append("Background runtime did not verify")
        return result


def _persist(path, report):
    """Flush each receipt transition before proceeding to any next step."""
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _dispatch(executable, before, path):
    window = before["window"]
    args = [
        str(executable),
        "--pid",
        str(window["main"]["pid"]),
        "--hwnd",
        window["mixer"]["handle"],
        "--expected-exe",
        window["process"]["image"],
        "--timeout-ms",
        "5000",
        "--drop",
        "--x",
        str(window["point"][0]),
        "--y",
        str(window["point"][1]),
        "--file",
        str(path),
    ]
    try:
        completed = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=7,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if len(completed.stdout) > 65536 or len(completed.stderr) > 65536:
            return dict(state="unknown", error="Oversized helper response")
        payload = json.loads(completed.stdout)
        if (
            not isinstance(payload, dict)
            or payload.get("experimental") is not True
            or payload.get("state") not in {"dropped", "unknown", "rejected", "unsupported"}
            or any(
                type(payload.get(key)) is not int
                for key in ("error", "query_hr", "enter_hr", "over_hr", "drop_hr", "effect")
            )
            or type(payload.get("memory_valid")) is not bool
        ):
            raise ValueError("Invalid background helper response")
        return dict(returncode=completed.returncode, result=payload, stderr=completed.stderr)
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return dict(state="unknown", error=str(exc))


def _validate_dispatch_state(conn, before):
    """Refresh the actual target after the longer all-track scan and journal flush.

    These checks narrow the race window. They cannot make the external private
    OLE call atomic with native selection, project or manual UI changes.
    """
    deadline = time.monotonic() + 6
    project = _project(conn, deadline)
    if project["revision"] != before["native_revision"] or project["context"] != before["project"]:
        raise RuntimeError("Project changed immediately before dispatch; no drop sent")
    track = before["track"]
    rows = _call(
        conn,
        "bridge.batch",
        {
            "dry_run": False,
            "steps": [
                dict(action="mixer.inspect", params=dict(tracks=[track], routes=False)),
                *[
                    dict(action="native.call", params=dict(function=name, args=[]))
                    for name in (
                        "mixer.trackNumber",
                        "transport.isPlaying",
                        "transport.isRecording",
                        "general.safeToEdit",
                        "ui.isInPopupMenu",
                    )
                ],
            ],
        },
        deadline,
    ).get("results")
    if (
        not isinstance(rows, list)
        or len(rows) != 6
        or any(
            not isinstance(row, dict) or row.get("error") or row.get("success") is False
            for row in rows
        )
    ):
        raise ValueError("Incomplete immediate target readback")
    current = rows[0].get("tracks")
    if not isinstance(current, list) or len(current) != 1:
        raise ValueError("Incomplete immediate mixer target")
    catalog = list(before["mixer_tracks"])
    catalog[track] = current[0]
    if _tracks(catalog)[track] != before["mixer_tracks"][track]:
        raise RuntimeError("Target slots changed immediately before dispatch; no drop sent")
    selected, playing, recording, safe, popup = [row.get("value") for row in rows[1:]]
    flags = (playing, recording, safe, popup)
    if (
        type(selected) is not int
        or selected != track
        or any(type(flag) not in (bool, int) or flag not in (0, 1) for flag in flags)
        or playing
        or recording
        or not safe
        or popup
    ):
        raise RuntimeError("Selection or edit safety changed immediately before dispatch")
    if _window_state(before["slot"]) != before["window"]:
        raise RuntimeError("Window geometry changed immediately before dispatch; no drop sent")


def _confirmed(cli):
    result = cli.get("result", {})
    return (
        cli.get("returncode") == 0
        and result.get("state") == "dropped"
        and result.get("memory_valid") is True
        and result.get("effect") == 1
        and all(
            type(result.get(key)) is int and result[key] >= 0
            for key in ("query_hr", "enter_hr", "over_hr", "drop_hr")
        )
    )


def _compare(before, after, expected_plugin):
    if (
        after["project"] != before["project"]
        or after["native_revision"] != before["native_revision"]
        or after["window"] != before["window"]
        or after["selected_track"] != before["selected_track"]
    ):
        return "changed", "Project, selection or mixer geometry changed after dispatch"
    track, slot = before["track"], before["slot"]
    old, new = before["mixer_tracks"], after["mixer_tracks"]
    if len(old) != len(new):
        return "changed", "Mixer track count changed after dispatch"
    for left, right in zip(old, new):
        if left["name"] != right["name"]:
            return "changed", "Mixer track name changed after dispatch"
        for a, b in zip(left["slots"], right["slots"]):
            if (left["index"], a["slot"]) != (track, slot) and a != b:
                return "changed", "Another effect slot changed; inspect all mixer tracks"
    actual = new[track]["slots"][slot]
    if not actual["occupied"]:
        return "pending", "Target remains empty"
    if actual["name"] != expected_plugin:
        return "changed", "Target plugin differs from the exact expected native name"
    return "matched", "Exact target plugin and every other mixer slot verified"


def load_background_effect(
    track,
    slot,
    path,
    expected_plugin,
    expected_revision,
    expected_window_title,
    apply=False,
    receipt_file=None,
    connection=None,
):
    _target(track, slot)
    if type(apply) is not bool:
        raise ValueError("apply must be boolean")
    if not isinstance(expected_revision, str) or not re.fullmatch(
        "[a-f0-9]{64}", expected_revision
    ):
        raise ValueError("Use the revision from fl_background_effect_inspect")
    if (
        not isinstance(expected_plugin, str)
        or not expected_plugin.strip()
        or len(expected_plugin) > 4096
        or not isinstance(expected_window_title, str)
        or not expected_window_title
        or len(expected_window_title) > 2048
    ):
        raise ValueError("Provide exact nonempty native plugin and FL window names")
    asset = asset_path(path, {".fst"})
    if asset.stat().st_size > MAX_ASSET_BYTES:
        raise ValueError("Plugin preset exceeds the 64 MiB bound")
    asset_hash = hashlib.sha256(asset.read_bytes()).hexdigest()
    receipt = Path(receipt_file) if receipt_file else None
    if apply and (
        receipt is None or not receipt.is_absolute() or receipt.suffix.lower() != ".json"
    ):
        raise ValueError("Apply requires a new absolute .json receipt_file")
    conn = connection if connection is not None else get_connection()
    with transaction_lock(conn._hardware_dir / "mcp-gui.lock", 5):
        before = _inspect(conn, track, slot)
        if before["revision"] != expected_revision:
            raise ValueError("Background loading revision changed; inspect again")
        if before["window"]["main"]["title"] != expected_window_title:
            raise ValueError("FL project window title changed")
        if not before["ready"]:
            raise RuntimeError("; ".join(before["reasons"]))
        executable = verify_runtime()
        report = dict(
            success=not apply,
            status="preflighted",
            experimental=True,
            operation_id=uuid.uuid4().hex,
            before=before,
            path=str(asset),
            asset_sha256=asset_hash,
            expected_plugin=expected_plugin,
            desktop_input=False,
            track_selection_changed=False,
            limitations=LIMITATIONS,
            recovery="Inspect native state; never replay an uncertain drop.",
        )
        if not apply:
            return report
        report.update(success=False, status="reserved", receipt_file=str(receipt))
        with receipt.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            fresh = _inspect(conn, track, slot)
            if fresh["revision"] != before["revision"] or not fresh["ready"]:
                raise RuntimeError("Target changed after receipt reservation; no drop dispatched")
            if hashlib.sha256(asset.read_bytes()).hexdigest() != asset_hash:
                raise RuntimeError("Preset changed before dispatch")
            executable = verify_runtime()
            report.update(status="dispatching", input_before=_safe_input_snapshot())
            _persist(receipt, report)
            _validate_dispatch_state(conn, fresh)
            report["cli"] = _dispatch(executable, fresh, asset)
            report.update(status="awaiting_readback")
            _persist(receipt, report)  # Preserve callback outcome before any busy native read.
            report["input_after_dispatch"] = _safe_input_snapshot()
            report["readbacks"] = []
            deadline, consecutive = time.monotonic() + 30, 0
            for attempt in range(12):
                time.sleep(0.25 if attempt == 0 else 0.5)
                try:
                    after = _inspect(conn, track, slot, deadline)
                except ReadUnavailable as exc:
                    report["readbacks"].append(dict(unavailable=str(exc)))
                    if time.monotonic() >= deadline:
                        break
                    continue
                state, message = _compare(before, after, expected_plugin)
                report["readbacks"].append(dict(revision=after["revision"], result=state))
                report["after"] = after
                if state == "changed":
                    report["error"] = message
                    break
                consecutive = consecutive + 1 if state == "matched" else 0
                if consecutive == 2:
                    report.update(
                        success=_confirmed(report["cli"]),
                        status="verified" if _confirmed(report["cli"]) else "unverified",
                        verification=message,
                    )
                    break
            else:
                report["error"] = (
                    "Native effect readback did not settle within the bounded attempts"
                )
            if report["status"] == "awaiting_readback":
                report.update(status="unverified", success=False)
        except Exception as exc:
            report.update(success=False, status="unverified", error=str(exc))
        report["input_after"] = _safe_input_snapshot()
        try:
            _persist(receipt, report)
        except Exception as exc:
            report.update(success=False, status="unverified", receipt_persistence_error=str(exc))
        return report
