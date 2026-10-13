"""Open/reopen a saved project once, retaining a verified recovery FLP first."""

import asyncio
import json
import os
import time
import uuid
from pathlib import Path

from .project_bundle import _inspect
from .project_windows import _flp_evidence, _path, _same_path, _window_state
from .protocol import atomic_json

OPEN_DIALOG_TIMEOUT = 30.0
OPEN_VERIFY_TIMEOUT = 30.0


def _state():
    from .windows_workflows import native

    return native("production.inspect", {"scope": "project"})


def _stopped(state):
    if state["is_playing"] or state["is_recording"]:
        raise ValueError("Stop playback and recording before changing projects")


async def _open_dialog(fl, source, expected_sha256, report, receipt):
    """Own one standard dialog and confirm once; never use retrying file_open."""
    delivery = report["open_delivery"]
    await fl.key("o", modifiers="ctrl")
    dialog = await fl.dialog("Open")
    main, state = await fl._project_guard(allow_dialog=dialog["handle"])
    owned = _window_state(dialog["handle"])
    if (
        dialog.get("className") != "#32770"
        or not owned["exists"] or not owned["visible"] or not owned["enabled"]
        or owned["pid"] != state["pid"]
        or owned["root_owner"] != str(main["handle"])
    ):
        raise RuntimeError("Open dialog is not the owned standard FL project dialog")
    nodes = await fl._project_dialog_snapshot(dialog)
    filenames = [node for node in nodes if node.get("type") == "Edit"
                 and node.get("name") == "File name:" and node.get("id")]
    if len(filenames) != 1:
        raise RuntimeError("Cannot uniquely locate Open filename field")
    field = filenames[0]["id"]
    delivery.update(phase="typing_path", dialog_handle=dialog["handle"], filename_element=field)
    atomic_json(receipt, report)
    await fl.win(
        "ui_batch", windowHandle=dialog["handle"], stopOnError=True,
        steps=json.dumps([dict(action="key", key="a", modifiers="ctrl", expectedElementId=field)]),
    )
    await fl.win("keyboard_control", windowHandle=dialog["handle"], action="type", text=str(source))
    await fl._project_guard(allow_dialog=dialog["handle"])
    if _flp_evidence(source)["sha256"] != expected_sha256:
        raise RuntimeError("Source changed before Open confirmation; no open confirmed")
    # Mark the uncertain boundary before sending one confirmation batch. If its
    # response is lost, subsequent code observes actual project state only.
    delivery.update(phase="confirming", confirmation_dispatched=True)
    atomic_json(receipt, report)
    delivery["response"] = await fl.win(
        "ui_batch", windowHandle=dialog["handle"], stopOnError=True,
        operationId="fl-open-confirm-" + report["operation_id"],
        steps=json.dumps([
            dict(action="wait_text", elementId=field, text=str(source), match="exact",
                 timeoutMs=5000),
            dict(action="key", key="enter", expectedElementId=field),
        ]),
    )
    delivery["phase"] = "confirmation_acknowledged"


async def open_project(
    fl, source, expected_sha256, expected_revision, recovery_path, receipt_file=None, apply=False
):
    if type(apply) is not bool:
        raise ValueError("apply must be boolean")
    source, recovery = _path(source), _path(recovery_path)
    evidence = _flp_evidence(source)
    if evidence["sha256"] != expected_sha256:
        raise ValueError("Source hash changed; inspect the saved project again")
    saved = _inspect(source.read_bytes())
    if _same_path(source, recovery) or recovery.exists():
        raise ValueError("Recovery path must be a NEW .flp distinct from the source")
    receipt = Path(receipt_file).resolve() if receipt_file else None
    if apply and (
        not receipt_file
        or not Path(receipt_file).is_absolute()
        or receipt.exists()
        or receipt in (source, recovery)
    ):
        raise ValueError("Apply requires a new absolute receipt_file distinct from FLPs")
    before = _state()
    _stopped(before)
    if before["revision"] != expected_revision:
        raise ValueError("Project revision changed; inspect production state again")
    await fl._project_guard()
    report = dict(
        success=not apply,
        status="preflighted",
        operation="project_open",
        operation_id=uuid.uuid4().hex,
        source=str(source),
        source_evidence=evidence,
        recovery_path=str(recovery),
        before=before,
        recovery="Open the verified recovery_path with a fresh project revision. "
        "Do not repeat a timed-out open without inspecting FL and this receipt.",
    )
    if not apply:
        return report
    report.update(success=False, status="saving_recovery", receipt_file=str(receipt))
    with receipt.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        report["checkpoint"] = await fl.save_project_as(recovery)
        report["status"] = "recovery_verified"
        atomic_json(receipt, report)
        checkpoint_state = _state()
        _stopped(checkpoint_state)
        # Save As must not switch the underlying project/channel identities.
        if checkpoint_state["context"] != before["context"] or checkpoint_state["dirty"]:
            raise RuntimeError("Project changed during recovery save; open was not dispatched")
        if _flp_evidence(source)["sha256"] != expected_sha256:
            raise RuntimeError("Source changed during recovery save; open was not dispatched")
        await fl._project_focus()
        current = _state()
        if current["context"] != checkpoint_state["context"] or current["dirty"]:
            raise RuntimeError("Project changed after checkpoint; open was not dispatched")
        _stopped(current)
        report["status"] = "opening"
        atomic_json(receipt, report)
        report["open_delivery"] = dict(
            method="owned_open_dialog", phase="opening_dialog", confirmation_dispatched=False,
        )
        atomic_json(receipt, report)
        try:
            await asyncio.wait_for(
                _open_dialog(fl, source, expected_sha256, report, receipt), OPEN_DIALOG_TIMEOUT,
            )
        except Exception as exc:
            report["open_delivery"]["error"] = str(exc) or type(exc).__name__
            atomic_json(receipt, report)
            if not report["open_delivery"]["confirmation_dispatched"]:
                raise
            # A confirmation can load FL before UIA/provider cleanup reports a
            # timeout. Keep the full independent checks below; never send again.
        deadline = time.monotonic() + OPEN_VERIFY_TIMEOUT
        while time.monotonic() < deadline:
            windows = await fl._project_windows()
            mains = [w for w in windows if str(w.get("handle")) == str(fl.handle)]
            if len(mains) == 1 and mains[0].get("title", "").startswith(source.name + " - "):
                fl.project_title = mains[0]["title"]
                await fl._project_guard()
                try:
                    after = _state()
                except RuntimeError as exc:
                    # Loading can publish the title before MIDI editing is safe.
                    # Retry observations only; file_open is never dispatched again.
                    if "unsafe to edit" not in str(exc).lower():
                        raise
                    report["last_readback_busy"] = str(exc)
                    await asyncio.sleep(0.1)
                    continue
                if (
                    after["context"]["session"] == before["context"]["session"]
                    and after["context"]["project_generation"]
                    > before["context"]["project_generation"]
                ):
                    break
            await asyncio.sleep(0.1)
        else:
            raise RuntimeError("New project generation/title did not settle; inspect FL")
        _stopped(after)
        report["active_project"] = await fl.project_info()
        actual = report["active_project"].get("path")
        if actual is None or not _same_path(_path(actual), source):
            raise RuntimeError("Loaded project path differs from requested source")
        if _flp_evidence(source)["sha256"] != expected_sha256:
            raise RuntimeError("Source changed during opening; loaded bytes are uncertain")
        if (
            after["values"]["ppq"] != saved["ppq"]
            or abs(after["values"]["tempo_bpm"] - saved["tempo"]) > 0.002
        ):
            raise RuntimeError("Loaded tempo/PPQ differs from the saved project")
        report.update(
            success=True,
            status="verified",
            after=after,
            verification=dict(
                exact_active_path=True,
                new_project_generation=True,
                source_hash_unchanged=True,
                tempo_ppq=True,
                delivery_error_reconciled="error" in report["open_delivery"],
            ),
        )
    except Exception as exc:
        report.update(
            success=False, status="needs_inspection", error=str(exc) or type(exc).__name__,
        )
    atomic_json(receipt, report)
    return report
