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
from .project_native import _window_context
from .protocol import transaction_lock


def _matches(info, state):
    return ([(c["id"], c["name"]) for c in info["channels"]]
            == [(c["index"], c["name"]) for c in state["context"]["channels"]]
            and info["ppq"] == state["values"]["ppq"]
            and abs(info["tempo"] - state["values"]["tempo_bpm"]) <= .002)


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
            # The Playlist is addressed explicitly; FLP delivery replaces the project.
            bridge._call(conn, "native.call", dict(
                function="ui.showWindow", args=[2], allow_write=True))
            targets = [row for row in native_windows.windows(main["handle"], pid=main["pid"])
                       if row["class"] == "TEventEditForm"
                       and row["title"].startswith("Playlist") and row["enabled"]]
            if len(targets) != 1:
                raise RuntimeError("Cannot identify the native Playlist drop target")
            target = targets[0]
            x, y, width, height = target["bounds"]
            point = [x + width * 3 // 4, y + height // 2]
            if not native_windows.point_belongs_to(point, target["handle"]):
                native_windows.expose_fl_window(main)
            if not native_windows.point_belongs_to(point, target["handle"]):
                raise RuntimeError("Playlist target remains covered; no project load dispatched")
            fresh = bridge._project(conn, None)
            if (fresh["revision"] != current["revision"] or fresh["dirty"]
                    or fresh["is_playing"] or fresh["is_recording"]
                    or fresh["context"] != current["context"]
                    or _window_context() != main or source.read_bytes() != data):
                raise RuntimeError("Project or source changed before native load")
            window = dict(main=main, mixer=target, point=point,
                          process=bridge._process_identity(main["pid"]))
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
                        break
                except bridge.ReadUnavailable:
                    pass
                time.sleep(.15)
            if not report["success"]:
                raise RuntimeError(
                    "Native project load is unverified; inspect receipt, do not retry")
        except Exception as exc:
            report.update(success=False, status="unverified", error=str(exc))
        bridge._persist(receipt, report)
        return report
