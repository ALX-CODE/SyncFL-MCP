"""Configure one exact mixer WAV through an owned FL recording-file dialog.

The filename setter is not in FL's scripting API. The supported fallback uses
the visually qualified Wide mixer at 96 DPI, OCR-confirmed visible track headers,
an exact owned standard dialog, real keyboard input and native path readback.
It never starts recording or guesses an input device/channel.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from pathlib import Path

from .midi_connection import get_connection
from .native_windows import point_belongs_to, unique_control
from .project_windows import _window_state
from .protocol import atomic_json, transaction_lock
from .recording_jobs import _same_path
from .recording_state import (
    _call,
    _capabilities,
    _check_revision,
    _identity_matches,
    _read_native,
    _read_state,
    _stopped,
    _supported,
)
from .screen_ocr import read_region
from .windows_workflows import WindowsFL, _workflow_lock

PROFILE = "fl26-wide-1089x677-96dpi-visible-inserts-1-16"
LIMITATIONS = [
    "Supports only the qualified 1089x677 Wide mixer at 96 DPI with visible inserts 1..16.",
    "Selecting a recording file implicitly arms the target in FL; other arm states are unchanged.",
    "Filename setup does not configure or verify audio input, pickup, monitoring or latency.",
    "The native path is a proposal for the next recording; this operation creates no take.",
    "The shared GUI lease cannot prevent a person or legacy client from interleaving input.",
]


def _destination(value, suffix):
    path = Path(value)
    if (not path.is_absolute() or path.suffix.lower() != suffix
            or any(ord(char) < 32 or char in '\"*?<>|' for char in str(path))):
        raise ValueError(f"Provide an absolute new {suffix} path without invalid characters")
    path = path.resolve()
    if path.exists() or not path.parent.is_dir():
        raise ValueError(f"The {suffix} destination must be NEW, in an existing directory")
    return path


def _caps(conn):
    capabilities = _capabilities(conn)
    native = _call(conn, "bridge.capabilities", {})["native"]
    required = {
        "mixer.trackNumber": ([], "read"),
        "mixer.setActiveTrack": (["index"], "write"),
        "ui.showWindow": (["index"], "write"),
        "ui.setFocused": (["index"], "write"),
        "ui.getFocused": (["index"], "read"),
    }
    if not capabilities["recording_filename_supported"] or any(
        not _supported(native, name, args, kind) for name, (args, kind) in required.items()
    ):
        raise RuntimeError("Filename setup requires native filename, selected-track and focus APIs")
    return capabilities


def _guard(conn, capabilities, before, expected_revision=None):
    current = _read_state(conn, [before["tracks"][0]["track"]], capabilities)
    _stopped(current)
    if not _identity_matches(before, current):
        raise RuntimeError("Project or mixer target changed during recording-file setup")
    if expected_revision and current["tracks"][0]["revision"] != expected_revision:
        raise RuntimeError("Recording arm state changed during recording-file setup")
    return current


def _selected(conn, track, require_focus=False):
    calls = [("mixer.trackNumber", [])]
    if require_focus:
        calls.append(("ui.getFocused", [0]))
    values = _read_native(conn, calls)
    if type(values[0]) is not int or values[0] != track:
        raise RuntimeError("Requested mixer track is no longer selected")
    if require_focus and values[1] not in (True, 1):
        raise RuntimeError("Mixer focus was not verified")


def _profile(mixer, main_pid, track, expected_name):
    if (mixer["pid"] != main_pid or mixer["class"] != "TFXForm"
            or mixer["title"] != "Mixer - " + expected_name or not mixer["enabled"]
            or mixer["dpi"] != 96 or mixer["bounds"][2:] != [1089, 677]
            or not 1 <= track <= 16):
        raise RuntimeError("Unsupported mixer identity/layout; no disk-record control clicked")


def _headers(ocr, mixer, track):
    """Check mode and unscrolled track columns before using the qualified disk row."""
    x, y = mixer["bounds"][:2]
    words = [word for line in ocr["lines"] for word in line["words"]]
    modes = [word for word in words if word["text"] == "Wide"
             and x + 125 <= word["bounds"][0] <= x + 170
             and y <= word["bounds"][1] < y + 25]
    if len(modes) != 1:
        raise RuntimeError("Qualified Wide mixer mode was not uniquely observed")
    # Windows OCR skips FL's isolated single-digit glyphs. The qualified image
    # exposes 12, 13, 15 and 16 instead; their positions still establish the same
    # unscrolled 44-pixel grid. Never reinterpret a misread glyph (e.g. 'u') as 14.
    anchors = []
    for word in words:
        text = word["text"]
        left, top, width, _ = word["bounds"]
        if not (y + 25 <= top < y + 48 and text.isascii() and text.isdecimal()):
            continue
        index = int(text)
        expected_x = x + 127 + (index - 1) * 44
        if (not 1 <= index <= 16 or text != str(index) or index in anchors
                or abs(left + width / 2 - expected_x) > 6):
            raise RuntimeError("Mixer track header/scroll geometry did not verify")
        anchors.append(index)
    if len(anchors) < 3 or max(anchors) - min(anchors) < 2:
        raise RuntimeError("Mixer track header/scroll geometry did not verify: too few anchors")
    return 117 + (track - 1) * 44, 586


async def _dialog_guard(fl, dialog, title):
    main, native_main = await fl._project_guard(allow_dialog=dialog["handle"])
    current = [w for w in await fl._project_windows()
               if str(w["handle"]) == str(dialog["handle"])]
    state = _window_state(dialog["handle"])
    if (len(current) != 1 or current[0].get("title") != title
            or current[0].get("className") != "#32770"
            or not all(state[key] for key in ("exists", "visible", "enabled"))
            or state["pid"] != native_main["pid"]
            or state["root_owner"] != str(main["handle"])):
        raise RuntimeError("Recording filename dialog identity/ownership changed")


async def configure_recording_file(track, expected_name, expected_revision, recording_file,
                                   apply=False, receipt_file=None, connection=None):
    """Configure a new proposed WAV path; success requires two native readbacks."""
    _check_revision(expected_revision)
    if (type(track) is not int or not 1 <= track <= 16 or type(apply) is not bool
            or not isinstance(expected_name, str) or not expected_name
            or any(ord(c) < 32 for c in expected_name)):
        raise ValueError("Use a visible insert 1..16, its exact name and boolean apply")
    output = _destination(recording_file, ".wav")
    receipt = _destination(receipt_file, ".json") if receipt_file else None
    if apply and receipt is None:
        raise ValueError("Apply requires a new absolute JSON receipt_file")
    conn = connection if connection is not None else get_connection()
    async with _workflow_lock:
        with transaction_lock(conn._hardware_dir / "mcp-gui.lock", 5):
            capabilities = _caps(conn)
            before = _read_state(conn, [track], capabilities)
            _stopped(before)
            target = before["tracks"][0]
            if target["name"] != expected_name or target["revision"] != expected_revision:
                raise ValueError("Recording target/revision changed; inspect again")
            report = dict(
                success=not apply, success_scope="preflight" if not apply else None,
                status="preflighted", operation="configure_recording_file", dry_run=not apply,
                operation_id=uuid.uuid4().hex, recording_file=str(output), track=track,
                expected_name=expected_name, before=before, profile=PROFILE,
                gui_profile_verified=False, filename_verified=False, recorded_take_verified=False,
                input_configuration_verified=False, dispatches=[], limitations=list(LIMITATIONS),
                recovery="Inspect FL and fl_recording_inspect after any uncertain result. "
                "Do not replay filename confirmation or blindly toggle the arm state.",
            )
            if not apply:
                return report
            report["receipt_file"] = str(receipt)
            with receipt.open("x", encoding="utf-8") as stream:
                json.dump(report, stream, indent=2)
                stream.flush()
                os.fsync(stream.fileno())

            def stage(status, **changes):
                report.update(status=status, **changes)
                atomic_json(receipt, report)

            def intent(operation):
                report["dispatches"].append(dict(operation=operation, at=time.time()))
                stage(operation)

            confirmed = False
            dialog = None
            try:
                async with WindowsFL() as fl:
                    try:
                        _guard(conn, capabilities, before, expected_revision)
                        await fl._project_guard()
                        intent("focus_mixer")
                        await fl.win("window_management", action="activate", handle=fl.handle)
                        for function, args in (("mixer.setActiveTrack", [track]),
                                               ("ui.showWindow", [0]), ("ui.setFocused", [0])):
                            _guard(conn, capabilities, before, expected_revision)
                            _call(conn, "native.call", dict(function=function, args=args,
                                                            allow_write=True))
                        _selected(conn, track, require_focus=True)
                        _, main_state = await fl._project_guard()
                        mixer = unique_control(fl.handle, "TFXForm", "Mixer - " + expected_name)
                        _profile(mixer, main_state["pid"], track, expected_name)
                        # Inspect this unfamiliar owner-drawn window before coordinate fallback.
                        await fl.win("ui_snapshot", windowHandle=mixer["handle"], mode="auto")
                        bounds = mixer["bounds"]
                        ocr = await read_region(fl, [bounds[0] + 100, bounds[1], 745, 55], scale=4)
                        stage("header_observed", mixer=mixer, header_observation=ocr)
                        point = _headers(ocr, mixer, track)
                        fresh = unique_control(fl.handle, "TFXForm", "Mixer - " + expected_name)
                        if fresh != mixer or not point_belongs_to(
                            (bounds[0] + point[0], bounds[1] + point[1]), mixer["handle"]
                        ):
                            raise RuntimeError("Mixer moved or disk-record control became obscured")
                        _guard(conn, capabilities, before, expected_revision)
                        _selected(conn, track, require_focus=True)
                        await fl._project_guard()
                        stage("mixer_verified", gui_profile_verified=True, mixer=mixer,
                              header_observation=ocr, relative_disk_point=list(point))
                        intent("open_recording_dialog")
                        await fl.click(*point, handle=mixer["handle"], action="right_click")
                        title = "Record " + expected_name + " to"
                        dialog = await fl.dialog(title)
                        await _dialog_guard(fl, dialog, title)
                        nodes = await fl._project_dialog_snapshot(dialog)
                        fields = [node for node in nodes if node.get("type") == "Edit"
                                  and node.get("name") == "File name:" and node.get("id")
                                  and node.get("enabled", True)]
                        if len(fields) != 1:
                            raise RuntimeError("Cannot uniquely locate recording filename field")
                        field = fields[0]["id"]
                        _guard(conn, capabilities, before, expected_revision)
                        _selected(conn, track)
                        await _dialog_guard(fl, dialog, title)
                        intent("type_filename")
                        await fl.win("ui_batch", windowHandle=dialog["handle"], stopOnError=True,
                                     steps=json.dumps([dict(action="key", key="a", modifiers="ctrl",
                                                            expectedElementId=field)]))
                        # ValuePattern can change visible text but leave FL's old filename.
                        await fl.win("keyboard_control", windowHandle=dialog["handle"],
                                     action="type", text=str(output))
                        _guard(conn, capabilities, before, expected_revision)
                        _selected(conn, track)
                        await _dialog_guard(fl, dialog, title)
                        if output.exists():
                            raise FileExistsError("Recording output appeared before confirmation")
                        intent("confirm_filename")
                        confirmed = True  # Delivery can be unknown even when Windows times out.
                        await fl.win(
                            "ui_batch", windowHandle=dialog["handle"], stopOnError=True,
                            operationId="fl-recording-file-" + report["operation_id"],
                            steps=json.dumps([
                                dict(action="wait_text", elementId=field, text=str(output),
                                     match="exact", timeoutMs=5000),
                                dict(action="key", key="enter", expectedElementId=field),
                            ]),
                        )
                        deadline = time.monotonic() + 5
                        while _window_state(dialog["handle"])["exists"]:
                            if time.monotonic() >= deadline:
                                raise RuntimeError("Recording filename dialog did not close")
                            await asyncio.sleep(0.05)
                        await fl._project_guard()
                        readbacks = []
                        for _ in range(2):
                            current = _guard(conn, capabilities, before)
                            actual = current["tracks"][0]
                            if (not actual["armed"] or not actual["recording_filename"]
                                    or not _same_path(actual["recording_filename"], output)):
                                raise RuntimeError(
                                    "Native recording filename/armed state did not verify")
                            if output.exists():
                                raise RuntimeError(
                                    "Audio appeared during filename setup; inspect transport")
                            readbacks.append(current)
                            await asyncio.sleep(0.05)
                        stage("verified", success=True,
                              success_scope="recording_file_configuration",
                              filename_verified=True, target_armed=True,
                              arm_changed=not target["armed"], after=readbacks[-1],
                              readbacks=readbacks)
                    except Exception:
                        if dialog is not None and not confirmed:
                            try:
                                await _dialog_guard(fl, dialog, "Record " + expected_name + " to")
                                await fl._cancel_project_dialog(dialog)
                                report["dialog_cancelled"] = True
                            except Exception as cleanup:
                                report["dialog_cleanup_error"] = str(cleanup)
                        raise
            except Exception as exc:
                report.update(success=False, success_scope=None,
                              status="unknown" if confirmed else "not_applied", error=str(exc))
            try:
                atomic_json(receipt, report)
            except Exception as exc:
                report.update(success=False, status="unknown" if confirmed else "not_applied",
                              receipt_persistence_error=str(exc))
            return report
