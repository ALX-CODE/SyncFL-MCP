"""File loading through the native OLE bridge, without a desktop-input client.

Reuse the established identity/routing verification, replacing its input transport
and target preparation. A receipt is reserved before every one-shot native drop.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path

from . import background_loading as bridge
from . import native_windows
from .midi_connection import get_connection
from .project_native import _window_context
from .studio_loading import StudioLoading, all_channels, call, channel_identity, rack_drop_target


class BackendStudioLoading(StudioLoading):
    def __init__(self):
        # Deliberately do not construct WindowsFL or start a Windows input helper.
        self.conn = get_connection()
        self.receipts = []
        self.outcome = None

    async def __aenter__(self):
        self.window = _window_context()
        self.handle = self.window["handle"]
        self.project_title = self.window["title"]
        self.before = bridge._project(self.conn, None)
        ping = bridge._call(self.conn, "bridge.ping", {})
        if ping.get("project_native", {}).get("controller_pid") != self.window["pid"]:
            raise RuntimeError("Controller and FL window refer to different processes")
        self.context = {k: self.before["context"][k] for k in ("session", "project_generation")}
        await self.guard()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        for path, report in self.receipts:
            report.update(status="unverified" if exc else "verified", success=exc is None)
            if self.outcome is not None:
                report["readback"] = self.outcome
            if exc:
                report["error"] = str(exc)
            bridge._persist(path, report)
        return False

    async def _project_guard(self, **kwargs):
        current = bridge._project(self.conn, None)
        if (_window_context() != self.window
                or any(current["context"][key] != value for key, value in self.context.items())):
            raise RuntimeError("Project changed during backend loading; inspect the receipt")
        return self.window, current

    async def guard(self):
        _, state = await self._project_guard()
        if state["is_playing"] or state["is_recording"] or call("ui.isInPopupMenu"):
            raise RuntimeError("Stop transport and resolve FL popups before backend loading")

    async def reveal_target(self, control, point, label):
        if not native_windows.point_belongs_to(point, control["handle"]):
            native_windows.expose_fl_window(self.window)
        if not native_windows.point_belongs_to(point, control["handle"]):
            raise RuntimeError(label + " remains covered; no file dispatched")

    async def rack_target(self):
        await self.guard()
        call("ui.showWindow", 1, write=True)
        rack = native_windows.unique_control(self.handle, "TStepSeqForm", "Channel rack")
        point = rack_drop_target(rack, native_windows.windows(rack["handle"]))
        await self.reveal_target(rack, point, "Channel Rack add area")
        return rack, point

    async def effect_target(self, track, slot, require_empty=True):
        from .studio_loading import mixer_drop_target, native

        if type(track) is not int:
            raise ValueError("Mixer track must be an existing integer index")
        self.validate_track(track)
        if type(slot) is not int or not 0 <= slot <= 9:
            raise ValueError("Mixer slot must be 0..9")
        before = native("mixer.inspect", {"tracks": [track], "routes": False})["tracks"][0]
        if require_empty and before["slots"][slot]["occupied"]:
            raise ValueError("Mixer slot is occupied; select an empty slot")
        await self.guard()
        call("mixer.setTrackNumber", track, write=True)
        call("ui.showWindow", 0, write=True)
        mixer = native_windows.unique_control(self.handle, "TFXForm")
        if call("mixer.trackNumber") != track or mixer["title"] != "Mixer - " + before["name"]:
            raise RuntimeError("Native mixer selection did not match the requested target")
        point = mixer_drop_target(mixer, native_windows.windows(mixer["handle"]), slot)
        await self.reveal_target(mixer, point, "Mixer effect slot")
        return mixer, point, before

    async def transfer(self, path, control, point, validate_target=None):
        await self.guard()
        path = Path(path)
        if path.suffix.lower() not in {".wav", ".fst"}:
            raise ValueError("Backend loading currently accepts WAV samples and FST presets")
        if path.stat().st_size > bridge.MAX_ASSET_BYTES:
            raise ValueError("Backend asset exceeds the 64 MiB bound")
        executable = bridge.verify_runtime()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        receipt = self.conn._hardware_dir / ("backend-load-" + uuid.uuid4().hex + ".json")
        report = dict(success=False, status="reserved", path=str(path), asset_sha256=digest,
                      desktop_input=False, experimental=True, target=control, point=list(point))
        with receipt.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        self.receipts.append((receipt, report))
        await self._project_guard()
        if validate_target:
            validate_target()
        current = native_windows.unique_control(self.handle, control["class"], control["title"])
        if current != control or not native_windows.point_belongs_to(point, control["handle"]):
            raise RuntimeError("Backend drop target changed; no file dispatched")
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise RuntimeError("Asset changed before dispatch")
        window = dict(main=self.window, mixer=control, point=list(point),
                      process=bridge._process_identity(self.window["pid"]))
        report.update(status="dispatching", input_before=bridge._safe_input_snapshot())
        bridge._persist(receipt, report)
        await self.guard()
        if validate_target:
            validate_target()
        report["cli"] = bridge._dispatch(executable, dict(window=window), path)
        report["input_after"] = bridge._safe_input_snapshot()
        report["status"] = "awaiting_readback"
        bridge._persist(receipt, report)
        if not bridge._confirmed(report["cli"]):
            raise RuntimeError(
                f"Native file delivery is unverified; do not retry. Receipt: {receipt}")
        return dict(delivery_confirmed=True, method="native_ole", desktop_input=False,
                    receipt_file=str(receipt))

    async def new_channel(self, *args, **kwargs):
        result = await super().new_channel(*args, **kwargs)
        first = all_channels()
        await self._project_guard()
        if channel_identity(all_channels()) != channel_identity(first):
            raise RuntimeError("Channel state changed during independent backend readback")
        result.update(desktop_input=False, experimental=True)
        self.outcome = result
        return result

    async def add_sample(self, path, name=None, mixer_track=None):
        from .studio_loading import asset_path

        path = asset_path(path, {".wav"})
        return await self.new_channel(path, None, name=name, mixer_track=mixer_track, sample=True)
