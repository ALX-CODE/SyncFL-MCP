"""Select the actual ComposeWithLLM script; never guess FL's last-run script."""

from __future__ import annotations

import asyncio
import json
import re
import time

from fl_studio_mcp.utils.interaction_policy import require_desktop_input
from fl_studio_mcp.utils.native_windows import point_belongs_to, unique_control, windows
from fl_studio_mcp.utils.paths import get_fl_settings_dir
from fl_studio_mcp.utils.screen_ocr import read_region
from fl_studio_mcp.utils.studio_loading import call
from fl_studio_mcp.utils.windows_workflows import WindowsFL, native


def selected_identity():
    channel = call("channels.selectedChannel", True, 0, True)
    if type(channel) is not int or channel < 0:
        raise RuntimeError("Select a Channel Rack instrument first")
    mains = [w for w in windows() if w["class"] == "TFruityLoopsMainForm"]
    if len(mains) != 1:
        raise RuntimeError("Expected exactly one FL project for piano editing")
    return (
        channel,
        call("channels.getChannelName", channel, True),
        call("patterns.patternNumber", write=True),
        mains[0]["handle"],
        mains[0]["title"],
    )


def check_caption(text, name):
    value = re.sub(r"\s+", " ", text).strip()
    expected = re.sub(r"\s+", " ", name).strip()
    parts = value.split("Piano roll -", 1)
    # OCR sometimes joins adjacent words in FL's small title font. Channel index
    # is checked natively as well; only whitespace may differ in this visual check.
    if len(parts) != 2 or re.sub(r"\s", "", parts[1].strip(" <>›«»|•")) != re.sub(
        r"\s", "", expected
    ):
        raise RuntimeError(f"Visible piano-roll channel does not match {name!r}: {value!r}")


def caption_region(bounds, scale):
    x, y, width, _ = bounds
    # FL26 puts the caption after the fixed toolbar. Including those icons can
    # make WinRT stop reading at "Piano", even when the full label is visible.
    left = round(350 * scale)
    return [
        x + left,
        y,
        min(width - left - round(40 * scale), round(850 * scale)),
        round(70 * scale),
    ]


def compose_point(ocr):
    lines = ocr["lines"]
    matches = [line for line in lines if line["text"].strip() == "ComposeWithLLM"]
    # WinRT OCR can read the final small word as "agam"; the stable prefix
    # still proves this is the Scripts menu when the script label is unique.
    if len(matches) != 1 or not any(
        line["text"].strip().casefold().startswith("run last script") for line in lines
    ):
        raise RuntimeError("Cannot uniquely read ComposeWithLLM in the piano-roll Tools menu")
    x, y, width, height = matches[0]["bounds"]
    return round(x + width / 2), round(y + height / 2)


async def _dispatch():
    scripts = get_fl_settings_dir() / "Piano roll scripts"
    for name in ("ComposeWithLLM.pyscript", "mcp_note_protocol.py"):
        if not (scripts / name).is_file():
            raise RuntimeError(f"Piano bridge component is missing: {scripts / name}")
    identity = selected_identity()
    envelope = json.loads((scripts / "mcp_request.json").read_text(encoding="utf-8"))
    if not isinstance(envelope, dict) or envelope.get("target_identity") != list(identity):
        raise RuntimeError("Piano request is not bound to this channel, pattern and project")
    async with WindowsFL() as fl:
        await fl._project_guard()
        if call("transport.isPlaying"):
            raise RuntimeError("Stop playback before editing the piano roll")
        if call("ui.isInPopupMenu"):
            raise RuntimeError("Close the active FL popup before piano-roll work")
        await fl.win("window_management", action="activate", handle=fl.handle)
        # F7 refreshes the piano roll to the selected channel. showWindow(3) alone
        # can merely reveal the previous channel's roll on FL 2026.
        await fl.key("f7")
        call("ui.showWindow", 3, write=True)
        call("ui.setFocused", 3, write=True)
        roll = unique_control(fl.handle, "TEventEditForm", "Piano roll -")
        x, y, width, height = roll["bounds"]
        scale = roll["dpi"] / 96
        # WinRT OCR can return no lines for a very short (26px) image. Capture
        # extra vertical context, but consider only text within the title strip.
        # Exclude right-edge window buttons that OCR can merge into the caption.
        header = await read_region(fl, caption_region(roll["bounds"], scale), scale=3)
        caption = " ".join(
            line["text"] for line in header["lines"] if line["bounds"][1] < y + 26 * scale
        )
        try:
            check_caption(caption, identity[1])
        except RuntimeError:
            # F7 can reopen the previous channel's roll after a native channel
            # selection. Close that roll, reselect the bound target and open it
            # afresh; still require the exact visible caption before any script.
            if call("ui.isInPopupMenu"):
                raise
            call("ui.hideWindow", 3, write=True)
            native("channels.selectOne", {"index": identity[0]})
            if selected_identity() != identity:
                raise RuntimeError("Piano target changed while reopening the roll")
            call("ui.showWindow", 3, write=True)
            call("ui.setFocused", 3, write=True)
            roll = unique_control(fl.handle, "TEventEditForm", "Piano roll -")
            x, y, width, height = roll["bounds"]
            scale = roll["dpi"] / 96
            header = await read_region(
                fl, caption_region(roll["bounds"], scale), scale=3
            )
            caption = " ".join(
                line["text"] for line in header["lines"] if line["bounds"][1] < y + 26 * scale
            )
            check_caption(caption, identity[1])
        point = (round(x + 35 * scale), round(y + 12 * scale))
        if not point_belongs_to(point, roll["handle"]):
            raise RuntimeError("Piano-roll Tools button is obscured")
        await fl.click(point[0] - x, point[1] - y, handle=roll["handle"])
        if not call("ui.isInPopupMenu"):
            raise RuntimeError("Piano-roll Tools menu did not open")
        try:
            deadline = time.monotonic() + 3
            while True:
                menu = await read_region(
                    fl, [x, y, min(width, round(800 * scale)), min(height, round(800 * scale))],
                    scale=2,
                )
                try:
                    point = compose_point(menu)
                    break
                except RuntimeError:
                    if time.monotonic() >= deadline:
                        raise
                    if selected_identity() != identity or not call("ui.isInPopupMenu"):
                        raise RuntimeError("Piano target or menu changed while observing labels")
                    await asyncio.sleep(0.1)
            if selected_identity() != identity or not call("ui.isInPopupMenu"):
                raise RuntimeError("Piano target or menu changed before script selection")
            await fl._project_guard()
            if selected_identity() != identity or not call("ui.isInPopupMenu"):
                raise RuntimeError("Piano target or menu changed before script selection")
            current = json.loads((scripts / "mcp_request.json").read_text(encoding="utf-8"))
            if current != envelope:
                raise RuntimeError("Piano request changed before script selection")
            if not point_belongs_to(point, fl.handle):
                raise RuntimeError("ComposeWithLLM menu item is obscured")
            # One dispatch only. The bridge correlates and verifies the response.
            await fl.click(point[0] - x, point[1] - y, handle=roll["handle"])
            return True
        except Exception:
            # Cleanup only the menu we opened, never replay a script selection.
            if call("ui.isInPopupMenu") and selected_identity() == identity:
                call("ui.closeActivePopupMenu", write=True)
            raise


def dispatch_compose():
    """Caller owns GUI then piano locks and has published one correlated request."""
    return asyncio.run(_dispatch())


def prepare_piano(channel=None, expected_name=None):
    """Open the target roll and verify one read-only request, preserving notes."""
    from fl_studio_mcp.utils.piano_bridge import piano_session

    if channel is not None and (type(channel) is not int or channel < 0):
        raise ValueError("channel must be a nonnegative global index")
    require_desktop_input("Preparing the ComposeWithLLM piano roll")
    with piano_session() as bridge:
        bridge.recover_expired_read()
        if bridge.read_queue() != []:
            raise RuntimeError("Unresolved piano request; inspect it before preparing another roll")
        if channel is not None:
            if channel >= call("channels.channelCount", True):
                raise ValueError("channel does not exist")
            actual = call("channels.getChannelName", channel, True)
            if expected_name is not None and actual != expected_name:
                raise ValueError("Channel name differs from expected_name")
            native("channels.selectOne", {"index": channel})
        identity = selected_identity()
        if channel is not None and identity[0] != channel:
            raise RuntimeError("Selected channel index differs from the requested target")
        if expected_name is not None and identity[1] != expected_name:
            raise ValueError("Selected channel differs from expected_name")
        result = bridge.exchange()
        return {
            "success": True,
            "ready": True,
            "script": "ComposeWithLLM",
            "channel": identity[0],
            "channel_name": identity[1],
            "pattern": identity[2],
            "note_count": result["state"]["noteCount"],
            "revision": result["state"]["revision"],
            "request_id": result["id"],
            "verification": "correlated_read_only_score",
        }
