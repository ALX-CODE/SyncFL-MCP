"""One-shot native FLP loading with a saved recovery copy and no desktop input."""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

from . import background_loading as bridge
from . import native_windows
from .background_piano import _file, _save_and_read
from .midi_connection import get_connection
from .project_bundle import _inspect, _parse
from .project_native import _window_context
from .protocol import transaction_lock


def _matches(info, state):
    return ([(c["id"], c["name"]) for c in info["channels"]]
            == [(c["index"], c["name"]) for c in state["context"]["channels"]]
            and info["ppq"] == state["values"]["ppq"]
            and abs(info["tempo"] - state["values"]["tempo_bpm"]) <= .002)


def _load(conn, main, before, source, data, info, executable, receipt, report):
    """Deliver an already prepared FLP once and independently observe its load."""
    # A project belongs to FL's desktop. The Playlist accepts clips, and can
    # decline an FLP even when its OLE callback itself succeeds.
    for panel in range(4):
        bridge._call(conn, "native.call", dict(
            function="ui.hideWindow", args=[panel], allow_write=True))
    native_windows.expose_fl_window(main)
    # Panel visibility/layout updates can follow the controller acknowledgement.
    deadline = time.monotonic() + 3
    while True:
        targets = [row for row in native_windows.windows(main["handle"], pid=main["pid"])
                   if row["class"] == "TWPControl" and row["parent"] == main["handle"]
                   and row["bounds"][2] > 0 and row["bounds"][3] > 0 and row["enabled"]]
        targets.sort(key=lambda row: row["bounds"][2] * row["bounds"][3], reverse=True)
        if (targets and (len(targets) == 1 or targets[0]["bounds"][2] * targets[0]["bounds"][3]
                         > targets[1]["bounds"][2] * targets[1]["bounds"][3])):
            desktop = targets[0]
            x, y, width, height = desktop["bounds"]
            point = [x + width * 3 // 4, y + height // 2]
            if native_windows.point_belongs_to(point, desktop["handle"], exact=True):
                break
        if time.monotonic() >= deadline:
            raise RuntimeError(
                "FL desktop remains covered or ambiguous; no project load dispatched")
        time.sleep(.05)
    fresh = bridge._project(conn, None)
    if (fresh["revision"] != before["revision"] or fresh["dirty"]
            or fresh["is_playing"] or fresh["is_recording"]
            or fresh["context"] != before["context"]
            or _window_context() != main or source.read_bytes() != data):
        raise RuntimeError("Project or source changed before native load")
    window = dict(main=main, mixer=main, point=point,
                  process=bridge._process_identity(main["pid"]))
    report["drop_target"] = dict(window=main, desktop=desktop, point=point)
    report.update(status="dispatching", dispatched=True)
    bridge._persist(receipt, report)
    report["cli"] = bridge._dispatch(executable, dict(window=window), source)
    report["status"] = "awaiting_readback"
    bridge._persist(receipt, report)
    deadline = time.monotonic() + 30
    consecutive = 0
    while time.monotonic() < deadline:
        try:
            after = bridge._project(conn, deadline)
            now = _window_context()
            matched = (now["pid"] == main["pid"]
                       and now["title"].split(" - FL Studio", 1)[0].casefold()
                       == source.name.casefold()
                       and after["context"]["session"] == before["context"]["session"]
                       and after["context"]["project_generation"]
                       > before["context"]["project_generation"]
                       and _matches(info, after))
            consecutive = consecutive + 1 if matched else 0
            if consecutive >= 2:
                if source.read_bytes() != data:
                    raise RuntimeError("Source changed during project loading")
                report.update(success=True, status="verified", after=after,
                              verification="generation_title_channels_tempo_ppq_source_hash",
                              absolute_path_verified=False)
                return
        except bridge.ReadUnavailable:
            pass
        time.sleep(.15)
    raise RuntimeError("Native project load is unverified; inspect receipt, do not retry")


def _clean_blank(conn, main, state, expected_revision):
    if state["revision"] != expected_revision:
        raise ValueError("Project revision changed; inspect again")
    context = state["context"]
    if (main["title"] != context["program_title"] or context.get("project_title")
            or state["dirty"] != 0 or state["is_playing"] or state["is_recording"]
            or context["channels"] != [dict(index=0, name="Sampler", type=0)]):
        raise ValueError("Creating a project requires the clean, unnamed Empty template; "
                         "existing or modified projects are preserved")
    count = bridge._call(conn, "native.call", dict(function="patterns.patternCount", args=[]))
    if count.get("value") != 0:
        raise ValueError("The unnamed project contains patterns; it is not an empty project")
    ping = bridge._call(conn, "bridge.ping", {})
    if (ping.get("project_native", {}).get("controller_pid") != main["pid"]
            or not ping.get("safe_to_edit")):
        raise RuntimeError("The controller must belong to the idle, safe FL process")
    if not ping.get("project_native", {}).get("force_save_supported"):
        raise RuntimeError("Reload the updated controller before creating a blank project")


def create_blank_project(path, expected_revision, receipt_file=None, apply=False,
                         connection=None):
    """Bootstrap FL's clean Empty template into a named, native-saved FL26 project."""
    if type(apply) is not bool:
        raise ValueError("apply must be boolean")
    destination = Path(path)
    if (not destination.is_absolute() or destination.suffix.lower() != ".flp"
            or destination.exists()):
        raise ValueError("Use a NEW absolute .flp path; existing projects are never overwritten")
    receipt = Path(receipt_file) if receipt_file else None
    if apply and (receipt is None or not receipt.is_absolute() or receipt.exists()
                  or receipt.suffix.lower() != ".json"):
        raise ValueError("Apply requires a NEW absolute .json receipt_file")
    conn = connection or get_connection()
    with transaction_lock(conn._hardware_dir / "mcp-gui.lock", 5):
        main, before = _window_context(), bridge._project(conn, None)
        _clean_blank(conn, main, before, expected_revision)
        image = Path(bridge._process_identity(main["pid"])["image"])
        template = image.parent / "Data" / "Templates" / "Empty" / "Empty.flp"
        if template.is_symlink() or not 22 <= template.stat().st_size <= 512 * 1024 * 1024:
            raise ValueError("FL's installed Empty template is unavailable or invalid")
        data = template.read_bytes()
        info = _inspect(data)
        _, events, _ = _parse(data)
        if (info["channels"] != [dict(id=0, name="Sampler")]
                or info["patterns"] or info["sample_references"]
                or any(e.payload for e in events if e.id in (223, 224, 233))
                or not _matches(info, before)):
            raise ValueError("The installed Empty template does not match this clean blank project")
        executable = bridge.verify_runtime()
        report = dict(success=not apply, status="preflighted", desktop_input=False,
                      experimental=True, project_path=str(destination), template=str(template),
                      template_sha256=hashlib.sha256(data).hexdigest(), before=before,
                      dispatched=False, operation="create_blank_project")
        if not apply:
            return report
        receipt.parent.mkdir(parents=True, exist_ok=True)
        with receipt.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("xb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            # Recheck after filesystem work, before replacing even a clean blank.
            _clean_blank(conn, _window_context(), bridge._project(conn, None), expected_revision)
            _load(conn, main, before, destination, data, info, executable, receipt, report)
            loaded = bridge._project(conn, None)
            loaded_window = _window_context()
            if (loaded["context"] != report["after"]["context"] or loaded["dirty"] != 0
                    or loaded["is_playing"] or loaded["is_recording"]
                    or loaded_window["pid"] != main["pid"]
                    or loaded_window["title"].split(" - FL Studio", 1)[0].casefold()
                    != destination.name.casefold()):
                raise RuntimeError(
                    "New project changed before initialization; no pattern edit sent")
            report.update(success=False, status="initializing_pattern")
            bridge._persist(receipt, report)
            bridge._call(conn, "native.call", dict(
                function="patterns.setPatternName", args=[1, "Main"], allow_write=True))
            current = bridge._project(conn, None)
            expected_context = dict(loaded["context"], pattern=1, pattern_name="Main")
            if current["context"] != expected_context:
                raise RuntimeError("The initial pattern was not materialized; no save sent")
            saved, saved_info = _save_and_read(conn, dict(
                context=current["context"], window=_window_context(), path=str(destination)), data,
                force=True)
            if (not saved_info["version"].startswith("26.") or not _matches(saved_info, before)
                    or any(p["notes"] for p in saved_info["patterns"]) or saved_info["clips"]):
                raise RuntimeError("The new FL26 blank project was not independently verified")
            final = bridge._project(conn, None)
            if (final["context"] != current["context"] or final["dirty"] != 0
                    or final["is_playing"] or final["is_recording"]
                    or _window_context() != loaded_window or destination.read_bytes() != saved):
                raise RuntimeError("New project changed during final save verification")
            report.update(success=True, status="verified", after=final,
                          sha256=hashlib.sha256(saved).hexdigest(), version=saved_info["version"],
                          verification="native_load_then_native_save_and_empty_flp_readback")
        except Exception as exc:
            report.update(success=False, status="unverified", error=str(exc))
        bridge._persist(receipt, report)
        return report


def open_backend_project(source, expected_sha256, expected_revision, recovery_path,
                         receipt_file=None, apply=False, current_project_path=None,
                         connection=None):
    if type(apply) is not bool:
        raise ValueError("apply must be boolean")
    source, data, info = _file(source)
    if hashlib.sha256(data).hexdigest() != expected_sha256:
        raise ValueError("Source hash changed; inspect it again")
    recovery = Path(recovery_path)
    if not recovery.is_absolute() or recovery.suffix.lower() != ".flp" or recovery.exists():
        raise ValueError("Recovery requires a NEW absolute .flp path")
    receipt = Path(receipt_file) if receipt_file else None
    if apply and (receipt is None or not receipt.is_absolute() or receipt.exists()
                  or receipt.suffix.lower() != ".json"):
        raise ValueError("Apply requires a NEW absolute .json receipt_file")
    if not current_project_path:
        raise ValueError("Provide current_project_path for a verified native recovery save. "
                         "The current project must already be named and saved.")
    active, old_data, _ = _file(current_project_path)
    if active == source or recovery.resolve() in (source, active):
        raise ValueError("Source, current project and new recovery copy must be distinct")
    conn = connection or get_connection()
    with transaction_lock(conn._hardware_dir / "mcp-gui.lock", 5):
        main = _window_context()
        before = bridge._project(conn, None)
        ping = bridge._call(conn, "bridge.ping", {})
        if ping.get("project_native", {}).get("controller_pid") != main["pid"]:
            raise RuntimeError("Controller and project window refer to different processes")
        if before["revision"] != expected_revision:
            raise ValueError("Project revision changed; inspect again")
        if main["title"].split(" - FL Studio", 1)[0].casefold() != active.name.casefold():
            raise ValueError("Current project path differs from the active named project")
        if before["is_playing"] or before["is_recording"] or not ping.get("safe_to_edit"):
            raise RuntimeError("Stop transport and resolve modal operations before loading")
        executable = bridge.verify_runtime()
        report = dict(success=not apply, status="preflighted", desktop_input=False,
                      experimental=True, source=str(source), current_project_path=str(active),
                      recovery_path=str(recovery), before=before,
                      source_sha256=expected_sha256, dispatched=False)
        if not apply:
            return report
        with receipt.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            saved, saved_info = _save_and_read(
                conn, dict(context=before["context"], window=main, path=str(active)), old_data)
            current = bridge._project(conn, None)
            if not _matches(saved_info, current) or current["context"] != before["context"]:
                raise RuntimeError("Saved recovery source does not match the active project")
            with recovery.open("xb") as stream:
                stream.write(saved)
                stream.flush()
                os.fsync(stream.fileno())
            report.update(status="recovery_verified",
                          recovery_sha256=hashlib.sha256(saved).hexdigest())
            bridge._persist(receipt, report)
            _load(conn, main, current, source, data, info, executable, receipt, report)
        except Exception as exc:
            report.update(success=False, status="unverified", error=str(exc))
        bridge._persist(receipt, report)
        return report
