"""File-backed instrument, sample and effect loading with native FL readback."""

from __future__ import annotations

import asyncio
import struct
import time
from pathlib import Path

from fl_studio_mcp.utils.asset_catalog import resolve_plugin
from fl_studio_mcp.utils.native_windows import point_belongs_to, unique_control, windows
from fl_studio_mcp.utils.protocol import transaction_lock
from fl_studio_mcp.utils.windows_workflows import WindowsFL, _workflow_lock, matches_plugin, native

SAMPLE_EXTENSIONS = {".wav", ".aif", ".aiff", ".flac", ".mp3", ".ogg", ".wv"}


def asset_path(value, extensions):
    path = Path(value).expanduser()
    if not path.is_absolute() or path.suffix.lower() not in extensions:
        raise ValueError(f"Provide an absolute file path with extension: {sorted(extensions)}")
    path = path.resolve(strict=True)
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError("Asset must be a nonempty regular file")
    if path.suffix.lower() == ".fst":
        with path.open("rb") as source:
            header = source.read(18)
        if (
            len(header) < 18
            or header[:4] != b"FLhd"
            or header[14:18] != b"FLdt"
            or struct.unpack_from("<I", header, 4)[0] != 6
            or struct.unpack_from("<H", header, 8)[0] != 0x30
        ):
            raise ValueError(
                "Expected an FL plugin .fst preset; full mixer-track presets are unsupported"
            )
    return path


def call(function, *args, write=False):
    return native(
        "native.call",
        {
            "function": function,
            "args": list(args),
            "allow_write": write,
        },
    )["value"]


def all_channels():
    return native("channels.getAll")["channels"]


def channel_identity(items):
    return [(c["index"], c["name"], c["target_fx_track"]) for c in items]


def rack_drop_target(rack, children):
    x, y, width, height = rack["bounds"]
    scale = rack["dpi"] / 96
    panels = [
        c
        for c in children
        if c["class"] == "TVectorPanel"
        and c["bounds"][2] > width * 0.8
        and 18 * scale <= c["bounds"][3] <= 40 * scale
        and c["bounds"][1] > y + height - 45 * scale
    ]
    if len(panels) != 1:
        raise RuntimeError("Cannot identify the Channel Rack's empty add area")
    px, py, pw, ph = panels[0]["bounds"]
    return (round(px + min(145 * scale, pw * 0.3)), round(py + ph * 0.5))


def mixer_drop_target(mixer, children, slot):
    if type(slot) is not int or not 0 <= slot <= 9:
        raise ValueError("Mixer slot must be 0..9")
    scale = mixer["dpi"] / 96
    # FL26 exposes the ten-slot stack at 291 or 300 pixels high at 96 DPI.
    panels = [
        c
        for c in children
        if c["class"] == "TVectorPanel"
        and 210 * scale <= c["bounds"][2] <= 230 * scale
        and 290 * scale <= c["bounds"][3] <= 310 * scale
    ]
    if len(panels) != 1:
        raise RuntimeError("Cannot identify the visible ten-slot mixer panel")
    x, y, width, height = panels[0]["bounds"]
    return (round(x + width * 0.35), round(y + scale + (slot + 0.5) * (height - 2 * scale) / 10))


def channel_drop_target(rack, children, relative_index, visible_count):
    scale = rack["dpi"] / 96
    panels = [
        c
        for c in children
        if c["class"] == "TVectorPanel"
        and c["bounds"][2] > rack["bounds"][2] * 0.8
        and c["bounds"][1] < rack["bounds"][1] + 40 * scale
        and c["bounds"][3] >= (visible_count + 1) * 30 * scale
    ]
    if len(panels) != 1:
        raise RuntimeError(
            "Channel Rack rows are scrolled or clipped; enlarge the rack to show this group"
        )
    x, y, width, height = panels[0]["bounds"]
    return round(x + 144 * scale), round(y + (32 + relative_index * 30) * scale)


def plugin_state(index, slot=-1):
    return {
        "name": call("plugins.getPluginName", index, slot, False, True),
        "params": native(
            "plugins.getParams",
            {"index": index, "slot_index": slot, "use_global": True, "max_params": 512},
        )["params"],
    }


class StudioLoading(WindowsFL):
    async def reveal_target(self, control, point, label):
        if point_belongs_to(point, control["handle"]):
            return
        await self.win("window_management", action="activate", handle=control["handle"])
        deadline = time.monotonic() + 1.0
        while not point_belongs_to(point, control["handle"]):
            if time.monotonic() >= deadline:
                raise RuntimeError(label + " is obscured")
            await asyncio.sleep(0.05)

    async def guard(self):
        await self._project_guard()
        if call("transport.isPlaying"):
            raise RuntimeError("Stop playback before loading files or plugins")
        if call("ui.isInPopupMenu"):
            raise RuntimeError("An FL popup menu is active; close it before loading")
        await self.win("window_management", action="activate", handle=self.handle)

    async def rack_target(self):
        await self.guard()
        call("ui.showWindow", 1, write=True)
        call("ui.setFocused", 1, write=True)
        rack = unique_control(self.handle, "TStepSeqForm", "Channel rack")
        point = rack_drop_target(rack, windows(rack["handle"]))
        await self.reveal_target(rack, point, "Channel Rack add area")
        return rack, point

    async def transfer(self, path, control, point, validate_target=None):
        from fl_studio_mcp.utils.file_transfer import transfer_file

        await self._project_guard()

        def validate():
            mains = [w for w in windows() if w["handle"] == self.handle]
            if (
                len(mains) != 1
                or mains[0]["title"] != self.project_title
                or not mains[0]["enabled"]
                or call("transport.isPlaying")
            ):
                raise RuntimeError("FL project context changed before transfer")
            if validate_target is not None:
                validate_target()

        return await transfer_file(self, path, point, control["handle"], validate)

    def validate_track(self, track):
        if track is not None:
            if type(track) is not int or not 0 <= track <= call("mixer.getTrackInfo", 2):
                raise ValueError("Mixer track does not exist")

    async def new_channel(self, path, expected_name, *, name=None, mixer_track=None, sample=False):
        self.validate_track(mixer_track)
        if name is not None and (not isinstance(name, str) or not name.strip()):
            raise ValueError("Channel name must be nonempty")
        before = all_channels()
        rack, point = await self.rack_target()
        if channel_identity(all_channels()) != channel_identity(before):
            raise RuntimeError("Channels changed before file transfer")

        def validate_target():
            if channel_identity(all_channels()) != channel_identity(before):
                raise RuntimeError("Channels changed before file transfer")

        delivery = await self.transfer(path, rack, point, validate_target)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            after = all_channels()
            if len(after) == len(before) + 1:
                if channel_identity(after[:-1]) != channel_identity(before):
                    raise RuntimeError("Existing channels changed during loading; inspect FL")
                added = after[-1]
                actual = (
                    call("channels.getChannelType", added["index"], True)
                    if sample
                    else call("plugins.getPluginName", added["index"], -1, 0, True)
                )
                if sample and (actual != 0 or added["name"].casefold() != path.stem.casefold()):
                    raise RuntimeError("Added sampler identity differs from the requested file")
                if (
                    not sample
                    and expected_name
                    and str(actual).casefold() != expected_name.casefold()
                ):
                    raise RuntimeError(
                        f"Expected plugin {expected_name!r}, got {actual!r}; inspect FL"
                    )
                if not sample and not actual:
                    raise RuntimeError("New channel does not expose a loaded instrument")
                if name is not None:
                    native("channels.setName", {"index": added["index"], "name": name})
                if mixer_track is not None:
                    native(
                        "channels.routeToMixer",
                        {
                            "channel_index": added["index"],
                            "mixer_track": mixer_track,
                        },
                    )
                final_channels = all_channels()
                if len(final_channels) != len(before) + 1 or channel_identity(
                    final_channels[:-1]
                ) != channel_identity(before):
                    raise RuntimeError("Channels changed during rename or routing; inspect FL")
                final = final_channels[-1]
                if name is not None and final["name"] != name:
                    raise RuntimeError("Channel rename readback failed")
                if mixer_track is not None and final["target_fx_track"] != mixer_track:
                    raise RuntimeError("Channel routing readback failed")
                await self._project_guard()
                return {
                    "success": True,
                    "path": str(path),
                    "channel": final,
                    "plugin": actual,
                    "transfer": delivery,
                    "verification": "native_channel_count_identity_and_routing",
                }
            if len(after) != len(before):
                raise RuntimeError("Unexpected channel count after drop; inspect FL, do not retry")
            await asyncio.sleep(0.15)
        raise RuntimeError("File was dispatched but channel creation is unverified; do not retry")

    async def add_sample(self, path, name=None, mixer_track=None):
        path = asset_path(path, SAMPLE_EXTENSIONS)
        return await self.new_channel(path, None, name=name, mixer_track=mixer_track, sample=True)

    async def add_instrument(self, name=None, path=None, channel_name=None, mixer_track=None):
        if path is None:
            if not name:
                raise ValueError("Provide an exact instrument name or .fst path")
            resolved = resolve_plugin(name, "generator")
            path, name = resolved["path"], resolved["name"]
        path = asset_path(path, {".fst"})
        return await self.new_channel(path, name, name=channel_name, mixer_track=mixer_track)

    async def effect_target(self, track, slot, require_empty=True):
        if type(track) is not int:
            raise ValueError("Mixer track must be an existing integer index")
        self.validate_track(track)
        if type(slot) is not int or not 0 <= slot <= 9:
            raise ValueError("Mixer slot must be 0..9")
        before = native("mixer.inspect", {"tracks": [track], "routes": False})["tracks"][0]
        if require_empty and before["slots"][slot]["occupied"]:
            raise ValueError("Mixer slot is occupied; select an empty slot")
        await self.guard()
        call("mixer.setActiveTrack", track, write=True)
        call("ui.showWindow", 0, write=True)
        call("ui.setFocused", 0, write=True)
        mixer = unique_control(self.handle, "TFXForm")
        point = mixer_drop_target(mixer, windows(mixer["handle"]), slot)
        await self.reveal_target(mixer, point, "Mixer effect slot")
        return mixer, point, before

    async def add_effect(self, track, slot, name=None, path=None):
        if path is None:
            if not name:
                raise ValueError("Provide an exact effect name or .fst path")
            resolved = resolve_plugin(name, "effect")
            path, name = resolved["path"], resolved["name"]
        path = asset_path(path, {".fst"})
        mixer, point, before = await self.effect_target(track, slot)

        def validate_target():
            current = native("mixer.inspect", {"tracks": [track], "routes": False})["tracks"][0]
            if current["slots"] != before["slots"] or call("mixer.trackNumber") != track:
                raise RuntimeError("Mixer target changed before file transfer")

        delivery = await self.transfer(path, mixer, point, validate_target)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            after = native("mixer.inspect", {"tracks": [track], "routes": False})["tracks"][0]
            item = after["slots"][slot]
            if item["occupied"]:
                if name and not matches_plugin(item, name):
                    raise RuntimeError(f"Loaded effect identity differs from {name!r}: {item}")
                if [s for i, s in enumerate(before["slots"]) if i != slot] != [
                    s for i, s in enumerate(after["slots"]) if i != slot
                ]:
                    raise RuntimeError("Another mixer slot changed during loading; inspect FL")
                await self._project_guard()
                return {
                    "success": True,
                    "path": str(path),
                    "track": track,
                    "slot": item,
                    "transfer": delivery,
                    "verification": "native_slot_identity",
                }
            await asyncio.sleep(0.15)
        raise RuntimeError("Effect loading outcome unverified; inspect the slot before retrying")

    async def add_plugin(self, track, slot, name, result_row=0):
        if result_row != 0:
            raise ValueError("Use fl_effect_add with an exact .fst path instead of picker rows")
        return await self.add_effect(track, slot, name=name)

    async def load_preset(
        self, path, target="new_channel", channel=None, track=None, slot=None, expected_name=None
    ):
        path = asset_path(path, {".fst"})
        if target == "new_channel":
            if any(v is not None for v in (channel, track, slot)):
                raise ValueError("new_channel does not accept channel/track/slot")
            return await self.new_channel(path, expected_name)
        if target == "mixer":
            if channel is not None or track is None or slot is None:
                raise ValueError("mixer requires track and slot only")
            mixer, point, before = await self.effect_target(track, slot, require_empty=False)
            occupied = before["slots"][slot]["occupied"]
            if not occupied:
                return await self.add_effect(track, slot, None, str(path))
            if not expected_name or expected_name != before["slots"][slot]["name"]:
                raise ValueError("Existing effect requires its exact current expected_name")
            prior_state = plugin_state(track, slot)

            def validate_target():
                now = native("mixer.inspect", {"tracks": [track], "routes": False})["tracks"][0]
                if now["slots"] != before["slots"] or call("mixer.trackNumber") != track:
                    raise RuntimeError("Mixer preset target changed before transfer")

            delivery = await self.transfer(path, mixer, point, validate_target)
            after_state = await self.settled_plugin(track, slot, delivery)
            after = native("mixer.inspect", {"tracks": [track], "routes": False})["tracks"][0]
            if [s for i, s in enumerate(before["slots"]) if i != slot] != [
                s for i, s in enumerate(after["slots"]) if i != slot
            ]:
                raise RuntimeError("Another effect changed during preset loading; inspect FL")
            await self._project_guard()
            return {
                "success": True,
                "path": str(path),
                "track": track,
                "slot": slot,
                "before": prior_state,
                "after": after_state,
                "transfer": delivery,
                "parameters_changed": prior_state != after_state,
                "verification": "ole_delivery_and_native_plugin_state",
            }
        if target == "channel":
            if track is not None or slot is not None or type(channel) is not int:
                raise ValueError("channel requires a global channel index only")
            before = all_channels()
            matches = [item for item in before if item["index"] == channel]
            if len(matches) != 1 or not expected_name or matches[0]["name"] != expected_name:
                raise ValueError("Existing channel requires its exact current expected_name")
            prior = matches[0]
            rack, _ = await self.rack_target()
            count = call("channels.channelCount", False)
            mapping = [call("channels.getChannelIndex", i) for i in range(count)]
            if channel not in mapping:
                raise ValueError("Target channel is outside the visible Channel Rack group")
            point = channel_drop_target(
                rack, windows(rack["handle"]), mapping.index(channel), count
            )
            if call("plugins.isValid", channel, -1, True):
                prior_state = plugin_state(channel)
            elif call("channels.getChannelType", channel, True) == 0:
                # A native Sampler has no plugin parameter interface to inspect.
                prior_state = {"name": prior["name"], "params": [], "channel_type": 0}
            else:
                raise ValueError("Target channel has no inspectable plugin or native Sampler")

            def validate_target():
                if channel_identity(all_channels()) != channel_identity(before):
                    raise RuntimeError("Channel preset target changed before transfer")

            delivery = await self.transfer(path, rack, point, validate_target)
            after_state = await self.settled_plugin(channel, -1, delivery)
            after = all_channels()
            if len(after) != len(before) or channel_identity(
                [c for c in after if c["index"] != channel]
            ) != channel_identity([c for c in before if c["index"] != channel]):
                raise RuntimeError("Unexpected channels changed during preset loading; inspect FL")
            # A channel preset may carry a name and routing. Preserve the target's
            # project role while replacing its requested sound.
            native("channels.setName", {"index": channel, "name": prior["name"]})
            native(
                "channels.routeToMixer",
                {"channel_index": channel, "mixer_track": prior["target_fx_track"]},
            )
            final = all_channels()
            if channel_identity(final) != channel_identity(before):
                raise RuntimeError("Channel identity/routing restoration did not verify")
            await self._project_guard()
            return {
                "success": True,
                "path": str(path),
                "channel": channel,
                "before": prior_state,
                "after": after_state,
                "transfer": delivery,
                "parameters_changed": prior_state != after_state,
                "verification": "ole_delivery_and_native_plugin_state",
            }
        raise ValueError("Preset target must be new_channel, channel or mixer")

    async def settled_plugin(self, index, slot, delivery):
        if not delivery.get("delivery_confirmed"):
            raise RuntimeError("Preset transfer was not confirmed; inspect target before retrying")
        deadline = time.monotonic() + 20
        previous = None
        while time.monotonic() < deadline:
            state = plugin_state(index, slot)
            if state["name"] and previous == state:
                return state
            previous = state
            await asyncio.sleep(0.2)
        raise RuntimeError("Preset's native plugin state did not settle; inspect before retrying")


async def run_file_workflow(operation, **kwargs):
    from fl_studio_mcp.utils.backend_studio_loading import BackendStudioLoading
    from fl_studio_mcp.utils.midi_connection import get_connection

    async with _workflow_lock:
        with transaction_lock(get_connection()._hardware_dir / "mcp-gui.lock", 5):
            async with BackendStudioLoading() as fl:
                result = await getattr(fl, operation)(**kwargs)
                result.update(desktop_input=False, experimental=True)
                fl.outcome = result
                return result
