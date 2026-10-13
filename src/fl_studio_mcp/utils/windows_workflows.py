"""Verified FL custom-control recipes using the user's updated Windows MCP.

Geometry is deliberately guarded: a moved/resized mixer fails closed instead of
clicking remembered screen positions. Native readback is the completion test.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from fl_studio_mcp.utils.interaction_policy import check_windows_call, require_desktop_input
from fl_studio_mcp.utils.midi_connection import get_connection
from fl_studio_mcp.utils.project_windows import ProjectWindows
from fl_studio_mcp.utils.protocol import transaction_lock
from fl_studio_mcp.utils.windows_server import resolve_windows_server

_workflow_lock = asyncio.Lock()


def native(action, params=None):
    result = get_connection().send_command(action, params or {}, 15)
    if not result.get("success"):
        raise RuntimeError(result.get("error", str(result)))
    return result


def flattened(nodes):
    for n in nodes:
        yield n
        yield from flattened(n.get("children", []))


def matches_plugin(item, requested):
    # Verified FabFilter picker/internal naming difference, including copies whose
    # user name resets to the internal product name. Do not strip arbitrary vendors.
    aliases = {requested}
    if requested == "LittleAlterBoy":
        aliases.add("Little AlterBoy")
    if requested.startswith("FabFilter "):
        aliases.add(requested[len("FabFilter ") :])
    return bool(aliases.intersection({item.get("name"), item.get("user_name")}))


class WindowsFL(ProjectWindows):
    def __init__(self, *, read_only=False):
        self.read_only = read_only
        executable = resolve_windows_server()
        self.client = Client(StdioTransport(command=executable, args=[], keep_alive=False))
        self.handle = None

    async def __aenter__(self):
        if self.read_only:
            check_windows_call("window_management", {"action": "find"}, read_only=True)
        else:
            require_desktop_input("This Windows FL workflow")
        await self.client.__aenter__()
        result = await self.win("window_management", action="find", processName="FL64")
        main = [
            w for w in result.get("windows", []) if w.get("className") == "TFruityLoopsMainForm"
        ]
        if len(main) != 1:
            raise RuntimeError("Expected one FL Studio main window")
        self.handle = main[0]["handle"]
        self.project_title = main[0]["title"]
        return self

    async def __aexit__(self, *args):
        return await self.client.__aexit__(*args)

    async def win(self, name, **params):
        check_windows_call(name, params, read_only=self.read_only)
        result = await self.client.call_tool(name, params, raise_on_error=False)
        data = next((json.loads(c.text) for c in result.content if c.type == "text"), {})
        if data.get("success") is False or result.is_error:
            raise RuntimeError(f"Windows {name}: {data}")
        return data

    async def key(self, key, handle=None, modifiers=None):
        return await self.win(
            "keyboard_control",
            action="press",
            key=key,
            windowHandle=handle or self.handle,
            modifiers=modifiers,
        )

    async def click(self, x, y, handle=None, action="click"):
        return await self.win(
            "mouse_control",
            action=action,
            x=x,
            y=y,
            windowHandle=handle or self.handle,
            expectedProcessName="FL64",
        )

    async def dialog(self, title, timeout=5):
        deadline = time.monotonic() + timeout
        skipped_new_project = False
        while time.monotonic() < deadline:
            result = await self.win("window_management", action="find", processName="FL64")
            matches = [w for w in result.get("windows", []) if w["title"] == title]
            if len(matches) == 1:
                return matches[0]
            new_project = [w for w in result.get("windows", [])
                           if w.get("className") == "TNewProjForm" and w["title"] == "Save as"]
            if title == "Save As" and len(new_project) == 1 and not skipped_new_project:
                await self._skip_new_project_dialog(new_project[0])
                skipped_new_project = True
                deadline = time.monotonic() + timeout
            await asyncio.sleep(0.1)
        raise RuntimeError("Expected FL dialog did not appear: " + title)

    async def prepare_mixer(self, track):
        state = await self.win("window_management", action="activate", handle=self.handle)
        if state["window"]["title"] != self.project_title:
            raise RuntimeError("Project changed during workflow; no click dispatched")
        native(
            "native.call",
            {"function": "mixer.setActiveTrack", "args": [track], "allow_write": True},
        )
        native("native.call", {"function": "ui.showWindow", "args": [0], "allow_write": True})
        native("native.call", {"function": "ui.setFocused", "args": [0], "allow_write": True})
        focused = native("native.call", {"function": "ui.getFocused", "args": [0]})
        if not focused["value"]:
            raise RuntimeError("Mixer did not receive FL focus; no click dispatched")
        if state["window"]["bounds"][2:] != [1920, 1028]:
            raise RuntimeError(
                "FL window size differs from calibrated 1920x1028; recalibration required"
            )
        snapshot = await self.win("ui_snapshot", windowHandle=self.handle, mode="auto")
        mixers = [
            n
            for n in flattened(snapshot.get("tree", []))
            if n.get("name", "").startswith("Mixer -") and n.get("click")
        ]
        # Incremental snapshots can omit an unchanged mixer. Obtain a full view
        # only when it was omitted; absence is never treated as a geometry match.
        if not mixers:
            snapshot = await self.win("ui_snapshot", windowHandle=self.handle, mode="full")
            mixers = [
                n
                for n in flattened(snapshot.get("tree", []))
                if n.get("name", "").startswith("Mixer -") and n.get("click")
            ]
        if not mixers or mixers[0]["click"][:2] not in ([1085, 606], [1111, 464]):
            raise RuntimeError("Mixer moved/resized; recalibrate the custom-control profile")
        # Both layouts were visually calibrated at the same mixer size. Keep the
        # guard: a merely similar center does not establish the slot geometry.
        center = mixers[0]["click"][:2]
        self.mixer_offset = (center[0] - 1085, center[1] - 606)

    async def mixer_click(self, x, y, **kwargs):
        dx, dy = self.mixer_offset
        return await self.click(x + dx, y + dy, **kwargs)

    async def type_search(self, handle, text):
        # FL drops Unicode-injected spaces; real Space keystrokes are required.
        await self.key("a", handle, "ctrl")
        for i, word in enumerate(text.split(" ")):
            if i:
                await self.key("space", handle)
            if word:
                await self.win("keyboard_control", action="type", windowHandle=handle, text=word)

    async def add_plugin(self, track, slot, name, row=0):
        if not 0 <= slot <= 9 or not name.strip() or not 0 <= row < 20:
            raise ValueError("Invalid slot, plugin name or result row")
        before = native("mixer.inspect", {"tracks": [track], "routes": False})["tracks"][0][
            "slots"
        ][slot]
        if before["occupied"]:
            if matches_plugin(before, name):
                return {"success": True, "already_present": True, **before}
            raise ValueError("Slot occupied; use explicit replace with expected_plugin")
        await self.prepare_mixer(track)
        await self.mixer_click(1450, 381 + slot * 29)
        # The huge custom menu tracks the pointer; Home/Enter can race its hover
        # selection. Click the observed More plugins row in this layout profile.
        await asyncio.sleep(0.2)
        await self.click(1127, 24)
        picker = await self.dialog("Select effect plugin")
        h = picker["handle"]
        if picker["bounds"][2:] != [500, 1012]:
            raise RuntimeError("Plugin picker size changed; recalibration required")
        await self.click(263, 978, h)
        await self.type_search(h, name)
        await asyncio.sleep(0.2)
        await self.click(130, 63 + row * 19, h, "double_click")
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            result = get_connection().send_command(
                "mixer.inspect", {"tracks": [track], "routes": False}, 2
            )
            if result.get("success"):
                item = result["tracks"][0]["slots"][slot]
                if item["occupied"]:
                    if not matches_plugin(item, name):
                        raise RuntimeError(
                            "Loaded plugin differs from request; inspect before continuing: "
                            + str(item)
                        )
                    return {"success": True, "track": track, **item}
            await asyncio.sleep(0.15)
        raise RuntimeError("Plugin loading not verified; inspect FL, do not retry blindly")

    async def remove_plugin(self, track, slot, expected_plugin):
        if not 0 <= slot <= 9:
            raise ValueError("Slot must be between 0 and 9")
        before = native("mixer.inspect", {"tracks": [track], "routes": False})["tracks"][0][
            "slots"
        ][slot]
        if not before["occupied"]:
            return {"success": True, "already_empty": True}
        if before["name"] != expected_plugin:
            raise ValueError("Plugin identity changed; removal rejected")
        await self.prepare_mixer(track)
        await self.mixer_click(1392, 381 + slot * 29)
        await self.key("home")
        await self.key("down")
        await self.key("enter")
        confirm = await self.dialog("Confirm")
        # This workflow has just invoked Delete on the identity-checked slot.
        await self.key("enter", confirm["handle"])
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            result = get_connection().send_command(
                "mixer.inspect", {"tracks": [track], "routes": False}, 2
            )
            if result.get("success"):
                after = result["tracks"][0]["slots"][slot]
                if not after["occupied"]:
                    return {"success": True, "removed": before, "after": after}
            await asyncio.sleep(0.15)
        raise RuntimeError("Removal outcome unverified; inspect slot before retry")

    async def ensure_tracks(self, last_insert):
        current = native("native.call", {"function": "mixer.getTrackInfo", "args": [2]})["value"]
        if current >= last_insert:
            return {"success": True, "last_insert": current, "added": 0}
        if not current < last_insert <= 125:
            raise ValueError("Supported target is up to insert 125")
        await self.prepare_mixer(current)
        await self.mixer_click(683, 322, action="right_click")
        await self.key("home")
        await self.key("down")
        await self.key("enter")
        prompt = await self.dialog("Enter the number of mixer tracks to add")
        await self.win(
            "keyboard_control",
            action="type",
            windowHandle=prompt["handle"],
            text=str(last_insert - current),
            clearFirst=True,
        )
        await self.key("enter", prompt["handle"])
        after = native("native.call", {"function": "mixer.getTrackInfo", "args": [2]})["value"]
        if after != last_insert:
            raise RuntimeError(
                f"Insert creation readback failed: expected {last_insert}, got {after}"
            )
        return {"success": True, "last_insert": after, "added": after - current}

    async def load_chain(self, items, receipt_file=None):
        """Keep Windows MCP alive across many plugin loads; journal each verified slot."""
        results = []
        for item in items:
            result = await self.add_plugin(
                item["track"], item["slot"], item["name"], item.get("row", 0)
            )
            if "wet" in item:
                native(
                    "plugins.wet",
                    {"track": item["track"], "slot": item["slot"], "value": item["wet"]},
                )
            results.append(result)
            if receipt_file:
                Path(receipt_file).write_text(
                    json.dumps(
                        {"completed": len(results), "total": len(items), "results": results},
                        indent=2,
                    )
                )
        return {"success": True, "completed": len(results), "results": results}


async def run_workflow(operation, **kwargs):
    async with _workflow_lock:
        with transaction_lock(get_connection()._hardware_dir / "mcp-gui.lock", 5):
            context = WindowsFL(read_only=True) if operation == "render_status" else WindowsFL()
            async with context as fl:
                return await getattr(fl, operation)(**kwargs)
