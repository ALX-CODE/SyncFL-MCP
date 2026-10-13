"""Create and verify FL26 automation for a visible Channel Rack volume/pan knob."""

import asyncio
import json
import struct
import time
import uuid
from pathlib import Path

from .automation_file import inspect_automation
from .native_windows import point_belongs_to, unique_control, windows
from .project_bundle import _inspect, _parse
from .project_lifecycle import _state, _stopped
from .protocol import atomic_json
from .screen_ocr import read_region
from .studio_loading import StudioLoading, call, channel_drop_target


def saved_links(path):
    """FL26 link records observed with native-created channel volume/pan automation.

    Keep every opaque byte in the report. No link bytes are written here.
    """
    result = []
    for event in _parse(Path(path).read_bytes())[1]:
        if event.id == 227 and len(event.payload) == 20:
            controller, opaque, event_id, flags, tail = struct.unpack("<5I", event.payload)
            result.append(
                dict(
                    channel=controller >> 16,
                    controller_index=controller & 65535,
                    event_id=event_id,
                    flags=flags,
                    raw=event.payload.hex(),
                )
            )
    return result


async def _menu_label(fl, menu, label):
    # A freshly allocated popup HWND can precede its first painted text frame.
    # Poll observations only; never reopen or re-click the menu to refresh it.
    last = None
    for attempt in range(4):
        current = [w for w in windows(pid=menu["pid"]) if w["handle"] == menu["handle"]]
        if current != [menu]:
            raise RuntimeError("Owned menu changed while waiting for its labels")
        last = await read_region(fl, menu["bounds"], scale=4)
        matches = [
            line
            for line in last["lines"]
            if " ".join(line["text"].split()).casefold() == label.casefold()
        ]
        if len(matches) == 1:
            return last, matches[0]
        if len(matches) > 1:
            break
        await asyncio.sleep(0.1)
    raise RuntimeError(f"{label} was not uniquely observed: {last.get('text', '')}")


async def _all_group(fl, rack):
    """Select the inspected All group and prove the resulting global-index map."""
    if rack["dpi"] != 96 or rack["bounds"][2] < 300:
        raise RuntimeError("Channel Rack group geometry is unsupported")
    point = rack["bounds"][0] + 145, rack["bounds"][1] + 14
    if not point_belongs_to(point, rack["handle"]):
        raise RuntimeError("Channel group selector is obscured")
    await fl.win("ui_snapshot", windowHandle=rack["handle"], mode="auto")
    await fl.win(
        "mouse_control",
        action="click",
        windowHandle=rack["handle"],
        x=145,
        y=14,
        expectedProcessName="FL64",
    )
    menus = [
        w
        for w in windows(pid=rack["pid"])
        if w["class"] == "TQuickPopupMenuWindow" and w["parent"] == str(fl.handle)
    ]
    if len(menus) != 1:
        raise RuntimeError("Expected one owned Channel Rack group menu")
    menu = menus[0]
    observed, match = await _menu_label(fl, menu, "All")
    x, y, w, h = match["bounds"]
    point = round(x + w / 2), round(y + h / 2)
    if not point_belongs_to(point, menu["handle"]):
        raise RuntimeError("Channel group menu became obscured")
    main = [w for w in windows(pid=rack["pid"]) if w["handle"] == str(fl.handle)][0]
    await fl.win(
        "ui_batch",
        windowHandle=fl.handle,
        stopOnError=True,
        steps=json.dumps(
            [
                dict(
                    action="mouse",
                    mouseAction="click",
                    x=point[0] - main["bounds"][0],
                    y=point[1] - main["bounds"][1],
                    expectedProcessName="FL64",
                    expectedWindowTitle=fl.project_title,
                )
            ]
        ),
    )
    await asyncio.sleep(0.1)
    count = call("channels.channelCount", False)
    mapping = [call("channels.getChannelIndex", i) for i in range(count)]
    if mapping != list(range(call("channels.channelCount", True))):
        raise RuntimeError("All channel group mapping did not verify")
    return observed


async def create_channel_automation(
    fl, channel, expected_name, parameter, expected_revision, directory, apply=False
):
    if parameter not in ("volume", "pan"):
        raise ValueError("Supported Channel Rack automation parameters: volume, pan")
    if type(apply) is not bool or type(channel) is not int or channel < 0:
        raise ValueError("apply must be boolean and channel a nonnegative global index")
    directory = Path(directory)
    if not directory.is_absolute() or directory.exists():
        raise ValueError("Use an absolute NEW automation output directory")
    directory = directory.resolve()
    before = _state()
    _stopped(before)
    if before["revision"] != expected_revision:
        raise ValueError("Project revision changed; inspect again")
    channels = before["context"]["channels"]
    if channel >= len(channels) or channels[channel]["name"] != expected_name:
        raise ValueError("Global channel index/name changed")
    if channels[channel]["type"] == 5:
        raise ValueError("Automating an automation channel is not supported")
    await fl._project_guard()
    event_id = call("channels.getRecEventId", channel, True) + (1 if parameter == "pan" else 0)
    # FL's rack indices depend on the displayed group. Never treat them as global.
    count = call("channels.channelCount", False)
    mapping = [call("channels.getChannelIndex", i) for i in range(count)]
    report = dict(
        success=not apply,
        status="preflighted",
        operation="create_channel_automation",
        directory=str(directory),
        operation_id=uuid.uuid4().hex,
        before=before,
        channel=channel,
        expected_name=expected_name,
        parameter=parameter,
        target_event_id=event_id,
        will_select_all_group=channel not in mapping,
        limitations=[
            "Creates volume/pan automation for visible Channel Rack channels.",
            "FL chooses initial Playlist placement/range from the current selection.",
            "Use saved curve tools and reopen the output for exact point edits.",
        ],
    )
    if not apply:
        return report
    directory.mkdir()
    receipt = directory / "automation-job.json"
    report.update(success=False, status="saving_recovery")
    atomic_json(receipt, report)
    try:
        report["recovery"] = await fl.save_project_as(directory / "before.flp")
        if not _inspect((directory / "before.flp").read_bytes())["version"].startswith("26.1."):
            raise ValueError("Automation link verification requires tested FL26.1 layout")
        if any(link["event_id"] == event_id for link in saved_links(directory / "before.flp")):
            raise ValueError("This target already has a saved controller link; create was not sent")
        current = _state()
        if current["context"] != before["context"] or current["dirty"]:
            raise RuntimeError("Project changed during checkpoint; create was not sent")
        await StudioLoading.guard(fl)
        call("ui.showWindow", 1, write=True)
        call("ui.setFocused", 1, write=True)
        rack = unique_control(fl.handle, "TStepSeqForm", "Channel rack")
        if rack["dpi"] != 96:
            raise RuntimeError("Automation knob profile requires verified 96 DPI Channel Rack")
        count = call("channels.channelCount", False)
        mapping = [call("channels.getChannelIndex", i) for i in range(count)]
        if channel not in mapping:
            report["all_group_evidence"] = await _all_group(fl, rack)
            rack = unique_control(fl.handle, "TStepSeqForm", "Channel rack")
            count = call("channels.channelCount", False)
            mapping = [call("channels.getChannelIndex", i) for i in range(count)]
        button = channel_drop_target(rack, windows(rack["handle"]), mapping.index(channel), count)
        point = button[0] - (98 if parameter == "volume" else 114), button[1]
        if not point_belongs_to(point, rack["handle"]):
            raise RuntimeError("Target knob is obscured")
        if _state()["context"] != before["context"]:
            raise RuntimeError("Target channel identity changed")
        await fl.win("ui_snapshot", windowHandle=rack["handle"], mode="auto")
        await fl.win(
            "mouse_control",
            action="right_click",
            windowHandle=rack["handle"],
            x=point[0] - rack["bounds"][0],
            y=point[1] - rack["bounds"][1],
            expectedProcessName="FL64",
        )
        menus = [
            w
            for w in windows(pid=rack["pid"])
            if w["class"] == "TQuickPopupMenuWindow" and w["parent"] == str(fl.handle)
        ]
        if len(menus) != 1:
            raise RuntimeError("Expected one owned channel parameter menu")
        menu = menus[0]
        observed, match = await _menu_label(fl, menu, "Create automation clip")
        report["menu_evidence"] = observed
        x, y, w, h = match["bounds"]
        point = round(x + w / 2), round(y + h / 2)
        if not point_belongs_to(point, menu["handle"]):
            raise RuntimeError("Automation creation menu became obscured")
        if _state()["context"] != before["context"]:
            raise RuntimeError("Project identity changed while the menu was open")
        report.update(status="creating", menu_evidence=observed)
        atomic_json(receipt, report)
        # Verify foreground instead of activating the popup, which closes FL menus.
        main = [w for w in windows(pid=rack["pid"]) if w["handle"] == str(fl.handle)][0]
        report["delivery"] = await fl.win(
            "ui_batch",
            windowHandle=fl.handle,
            stopOnError=True,
            operationId="automation-create-" + report["operation_id"],
            steps=json.dumps(
                [
                    dict(
                        action="mouse",
                        mouseAction="click",
                        x=point[0] - main["bounds"][0],
                        y=point[1] - main["bounds"][1],
                        expectedProcessName="FL64",
                        expectedWindowTitle=fl.project_title,
                    )
                ]
            ),
        )
        name = expected_name + (
            " - Channel volume" if parameter == "volume" else " - Channel panning"
        )
        report["readbacks"] = []
        consecutive = 0
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                after = _state()
            except RuntimeError as exc:
                if "unsafe to edit" not in str(exc).lower():
                    raise
                report["readbacks"].append(dict(transient_busy=str(exc)))
                await asyncio.sleep(0.1)
                continue
            report["readbacks"].append(after)
            _stopped(after)
            if any(
                after["context"][k] != before["context"][k]
                for k in ("session", "project_generation")
            ):
                raise RuntimeError("Live project changed during automation creation")
            added = after["context"]["channels"]
            if (
                len(added) == len(channels) + 1
                and added[:-1] == channels
                and added[-1]["type"] == 5
                and added[-1]["name"] == name
            ):
                consecutive += 1
                if consecutive >= 2:
                    break
            else:
                consecutive = 0
            await asyncio.sleep(0.1)
        else:
            raise RuntimeError(
                "New automation channel was not observed; never repeat creation blindly"
            )
        new = added[-1]
        if added[:-1] != channels or new["type"] != 5 or new["name"] != name:
            raise RuntimeError("Created automation channel identity differs from target")
        report["saved"] = await fl.save_project_as(directory / "created.flp")
        curves = inspect_automation(directory / "created.flp")
        links = [
            link
            for link in saved_links(directory / "created.flp")
            if link["event_id"] == event_id
            and link["channel"] == new["index"]
            and link["controller_index"] == 0
        ]
        curve = [
            a for a in curves["automation"] if a["channel"] == new["index"] and a["name"] == name
        ]
        if len(links) != 1 or len(curve) != 1:
            raise RuntimeError(
                "Saved automation curve/target link could not be independently verified"
            )
        report.update(
            success=True,
            status="verified",
            automation=curve[0],
            link=links[0],
            source_sha256=curves["sha256"],
            after=after,
        )
    except Exception as exc:
        report.update(status="needs_inspection", error=str(exc))
    atomic_json(receipt, report)
    return report
