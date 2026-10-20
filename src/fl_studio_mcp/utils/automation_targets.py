"""Resolve native automation identities and retarget one existing saved link.

No automation curve, plugin state, or live DAW control is written here. Event
arithmetic uses the qualified FL26.1 namespace and is always accompanied by
the host's own event labels. A rewritten copy still needs host qualification.
"""

import hashlib
import json
import math
import os
import struct
from pathlib import Path

from . import plugin_state
from .automation_file import _events, _points
from .midi_connection import get_connection
from .protocol import atomic_json, transaction_lock

MAX_FILE_BYTES = 256 * 1024 * 1024
MAX_LINKS = 10000
_KINDS = {
    "channel_volume", "channel_pan", "mixer_volume", "mixer_pan", "mixer_send",
    "generator_parameter", "effect_parameter",
}
# FL26.1.4.5589 native-created Insert 1 volume saves event 0x70401FC0,
# while getTrackPluginId(1, 0) returns 0x00400000. The online manual and
# API-37 stubs still use the obsolete 0x20000000 mixer namespace. Never fall
# back to those constants when FL cannot resolve the resulting event labels.
_MIXER_VOLUME = 0x70001FC0
_MIXER_PAN = 0x70001FC1
_MIXER_SEND = 0x70001F40
_GENERATOR_PARAMETER = 0x8000
_EFFECT_PARAMETER = 0x70008000
_SIGNATURES = {
    "channels.getRecEventId": ["index", "useGlobalIndex"],
    "channels.getChannelName": ["index", "useGlobalIndex"],
    "mixer.getTrackPluginId": ["index", "plugIndex"],
    "mixer.getTrackInfo": ["mode"],
    "mixer.getTrackName": ["index"],
    "mixer.getEventIDName": ["index", "shortname"],
    "mixer.getRouteSendActive": ["index", "destIndex"],
    "mixer.getRouteToLevel": ["index", "destIndex"],
}
LIMITATIONS = [
    "Mixer/effect event mapping uses the qualified FL26.1 namespace; unresolved host labels "
    "are rejected rather than falling back to obsolete published constants.",
    "Event labels and exposed parameter names must be reviewed; event arithmetic alone is not "
    "proof that a parameter is automatable.",
    "Names and generations cannot detect every same-name plugin replacement.",
    "A matching saved channel roster does not prove arbitrary file/plugin contents match the "
    "live project. Supply a fresh saved copy of the inspected project.",
    "A saved link edit does not prove audible automation. Reopen the new copy, save again, "
    "inspect its links and audition or render before calling the result verified in FL.",
]


def _integer(value, label, high=0xFFFFFFFF):
    if type(value) is not int or not 0 <= value <= high:
        raise ValueError(f"{label} must be an integer in 0..{high}")
    return value


def _text(value, label):
    if not isinstance(value, str) or not value.strip() or len(value) > 4096:
        raise ValueError(f"{label} must be nonempty text (at most 4096 characters)")
    return value


def _event_label(value, label):
    _text(value, label)
    if value.strip().casefold().startswith("unknown event"):
        raise ValueError(f"FL did not resolve the automation target: {value}")
    return value


def _sha(value, label):
    if not isinstance(value, str) or len(value) != 64 or any(
        c not in "0123456789abcdef" for c in value
    ):
        raise ValueError(f"{label} must be an inspected SHA256 revision")


def _validate_target(target):
    if (not isinstance(target, dict) or not isinstance(target.get("kind"), str)
            or target["kind"] not in _KINDS):
        raise ValueError("Unsupported automation target kind")
    kind = target["kind"]
    required = {"kind", "index"}
    if kind.endswith("parameter"):
        required.add("parameter")
    if kind == "effect_parameter":
        required.add("slot_index")
    if kind == "mixer_send":
        required.add("destination")
    if set(target) != required:
        raise ValueError(f"Target fields must be exactly {sorted(required)}")
    _integer(target["index"], "index", 65535)
    if "parameter" in target:
        _integer(target["parameter"], "parameter", plugin_state.MAX_PARAMETERS - 1)
    if "slot_index" in target:
        _integer(target["slot_index"], "slot_index", 9)
    if "destination" in target:
        _integer(target["destination"], "destination", 127)
        if target["index"] == target["destination"]:
            raise ValueError("A send cannot target its own mixer track")


def _capabilities(conn, target):
    native = plugin_state._call(conn, "bridge.capabilities", {}).get("native", {})
    needed = {"mixer.getEventIDName"}
    if target["kind"].startswith(("channel", "generator")):
        needed |= {"channels.getRecEventId", "channels.getChannelName"}
    else:
        needed |= {"mixer.getTrackPluginId", "mixer.getTrackInfo", "mixer.getTrackName"}
    if target["kind"] == "mixer_send":
        needed |= {"mixer.getRouteSendActive", "mixer.getRouteToLevel"}
    for name in needed:
        spec = native.get(name, {}) if isinstance(native, dict) else {}
        if (not isinstance(spec, dict) or spec.get("arguments") != _SIGNATURES[name]
                or spec.get("kind") != "read"):
            raise RuntimeError(f"Missing or unsupported advertised native signature: {name}")
    if target["kind"].endswith("parameter"):
        plugin_state._capabilities(conn, target.get("slot_index", -1))


def _resolve(conn, target):
    project, transport = plugin_state._project(conn)
    kind, index = target["kind"], target["index"]
    identity = dict(target)
    plugin = None
    if kind.endswith("parameter"):
        plugin = plugin_state._read_state(conn, index, target.get("slot_index", -1))
        if plugin["project"] != project or plugin["transport"] != transport:
            raise RuntimeError("Project changed during target inspection")
        parameter = target["parameter"]
        if parameter >= len(plugin["parameters"]):
            raise ValueError("Exposed plugin parameter does not exist")
        row = plugin["parameters"][parameter]
        _text(row["name"], "Exposed parameter name")
        identity.update(
            plugin=plugin["target"], parameter_name=row["name"],
            parameter_value=row["value"], parameter_display=row["display"],
            layout_sha256=plugin["layout_sha256"],
        )
    if kind.startswith(("channel", "generator")):
        if index >= len(project["channels"]):
            raise ValueError("Global channel does not exist")
        channel = project["channels"][index]
        if channel.get("type") == 5:
            raise ValueError("An automation channel cannot be an automation target")
        name, base = plugin_state._native(conn, [
            ("channels.getChannelName", [index, True]),
            ("channels.getRecEventId", [index, True]),
        ])
        if name != channel["name"]:
            raise RuntimeError("Global channel identity changed")
        identity["name"] = name
        offset = (_GENERATOR_PARAMETER + target["parameter"]
                  if kind == "generator_parameter" else int(kind == "channel_pan"))
    else:
        maximum = plugin_state._native(conn, [("mixer.getTrackInfo", [2])])[0]
        _integer(maximum, "Native maximum mixer track", 65535)
        if index > maximum or target.get("destination", 0) > maximum:
            raise ValueError("Mixer track does not exist")
        name, base = plugin_state._native(conn, [
            ("mixer.getTrackName", [index]),
            ("mixer.getTrackPluginId", [index, target.get("slot_index", 0)]),
        ])
        identity["name"] = _text(name, "Mixer track name")
        if kind == "mixer_send":
            destination = target["destination"]
            dest_name, active, level = plugin_state._native(conn, [
                ("mixer.getTrackName", [destination]),
                ("mixer.getRouteSendActive", [index, destination]),
                ("mixer.getRouteToLevel", [index, destination]),
            ])
            if not (active is True or (type(active) is int and active == 1)):
                raise ValueError("Send route must already be enabled")
            if type(level) not in (int, float) or not math.isfinite(level) or not 0 <= level <= 1:
                raise RuntimeError("Invalid normalized send level")
            identity.update(destination_name=_text(dest_name, "Destination name"), send_level=level)
            offset = _MIXER_SEND + destination
        elif kind == "effect_parameter":
            offset = _EFFECT_PARAMETER + target["parameter"]
        else:
            offset = _MIXER_VOLUME if kind == "mixer_volume" else _MIXER_PAN
    _integer(base, "Native recording-event base")
    event_id = _integer(base + offset, "Resolved event ID")
    full, short = plugin_state._native(conn, [
        ("mixer.getEventIDName", [event_id, 0]),
        ("mixer.getEventIDName", [event_id, 1]),
    ])
    _event_label(full, "Native full event label")
    _event_label(short, "Native short event label")
    if kind == "mixer_send":
        # FL can return a nonempty track prefix with no resolved control suffix
        # (observed "Insert 1 - "). That is not a named send destination.
        for label in (full, short):
            leaf = label.rsplit(" - ", 1)[-1].strip()
            if not leaf or label.strip().endswith(" -"):
                raise ValueError("Native send event label is incomplete; target is unresolved")
        if identity["destination_name"] not in full.rsplit(" - ", 1)[-1]:
            raise ValueError("Native send event label does not identify the destination")
    after_project, after_transport = plugin_state._project(conn)
    if after_project != project or after_transport != transport:
        raise RuntimeError("Project or transport changed during target inspection")
    result = dict(
        project=project, target=identity, event_id=event_id, event_name=full,
        event_short_name=short, transport=transport,
        event_mapping=dict(native_base=base, target_offset=offset, profile="FL26.1"),
    )
    if plugin is not None:
        result["plugin_revision"] = plugin["revision"]
        result["parameter_label_matches"] = short == identity["parameter_name"]
    return result


def _inspect_target(conn, target):
    _capabilities(conn, target)
    before = _resolve(conn, target)
    after = _resolve(conn, target)
    if before != after:
        raise RuntimeError("Automation target changed between independent observations")
    # Keep the full resolution here: retarget_automation compares the live
    # channel roster against a saved project. Only the tool boundary compacts.
    return dict(
        **after, revision=plugin_state._digest(after), success=True,
        status="native_target_resolved", limitations=list(LIMITATIONS),
    )


def inspect_target(target, connection=None):
    _validate_target(target)
    target = dict(target)
    conn = connection if connection is not None else get_connection()
    with transaction_lock(conn._hardware_dir / "mcp-gui.lock", 5):
        return _inspect_target(conn, target)


def _read_file(source):
    source = Path(source)
    if not source.is_absolute() or source.suffix.lower() != ".flp":
        raise ValueError("source must be an absolute .flp path")
    source = source.resolve(strict=True)
    if not source.is_file() or source.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("Source must be a regular FLP of at most 256 MiB")
    with source.open("rb") as stream:
        data = stream.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("Source exceeded 256 MiB while reading")
    return source, data


def _saved(data):
    ppq, start, events = _events(data)
    versions = [raw.rstrip(b"\0").decode("ascii") for eid, raw, _ in events if eid == 199]
    if len(versions) != 1 or not versions[0].startswith("26.1."):
        raise ValueError("Automation links require the qualified FL26.1 saved-project layout")
    links, curves, channels = [], [], {}
    channel = None
    for position, (eid, raw, _) in enumerate(events):
        if eid == 64:
            channel = int.from_bytes(raw, "little")
            if channel in channels:
                raise ValueError("Duplicate saved channel identity")
            channels[channel] = ""
        elif eid == 203 and channel is not None:
            if channels[channel]:
                raise ValueError("Duplicate saved channel name")
            channels[channel] = raw.decode("utf-16le").rstrip("\0")
        elif eid == 234:
            if channel is None or any(curve["channel"] == channel for curve in curves):
                raise ValueError("Unbound or ambiguous automation curve")
            points, _ = _points(raw)
            curves.append(dict(
                channel=channel, name=channels[channel], point_count=len(points),
                curve_sha256=hashlib.sha256(raw).hexdigest(),
            ))
        elif eid == 227:
            if len(raw) != 20:
                raise ValueError("Unsupported controller-link layout; refusing to skip it")
            if len(links) >= MAX_LINKS:
                raise ValueError("Too many controller links")
            controller, opaque, event_id, flags, tail = struct.unpack("<5I", raw)
            links.append(dict(
                event_position=position, channel=controller >> 16,
                controller_index=controller & 65535, event_id=event_id,
                flags=flags, raw=raw.hex(),
            ))
    for link in links:
        link["channel_name"] = channels.get(link["channel"])
        link["has_automation_curve"] = any(c["channel"] == link["channel"] for c in curves)
    return dict(
        sha256=hashlib.sha256(data).hexdigest(), version=versions[0], ppq=ppq,
        links=links, automation=curves,
        channels=[dict(index=index, name=name) for index, name in sorted(channels.items())],
    ), start, events


def inspect_links(source, channel=None, event_id=None, offset=0, limit=100):
    if channel is not None:
        _integer(channel, "channel", 65535)
    if event_id is not None:
        _integer(event_id, "event_id")
    _integer(offset, "offset", MAX_LINKS)
    _integer(limit, "limit", 500)
    if not limit:
        raise ValueError("limit must be 1..500")
    source, data = _read_file(source)
    saved, _, _ = _saved(data)
    matches = [link for link in saved["links"] if
               (channel is None or link["channel"] == channel)
               and (event_id is None or link["event_id"] == event_id)]
    page = matches[offset:offset + limit]
    return dict(
        success=True, source=str(source), sha256=saved["sha256"], version=saved["version"],
        ppq=saved["ppq"], links=page, matched=len(matches),
        next_offset=offset + limit if offset + limit < len(matches) else None,
        automation=[curve for curve in saved["automation"]
                    if any(link["channel"] == curve["channel"] for link in page)],
        loaded_in_fl=False,
    )


def retarget_automation(
    source, output, expected_sha256, automation_channel, expected_name, expected_event_id,
    target, expected_target_revision, expected_event_name, apply=False, connection=None,
):
    """Rewrite exactly one event-ID field, with a new receipt and source preserved."""
    if type(apply) is not bool:
        raise ValueError("apply must be boolean")
    _sha(expected_sha256, "expected_sha256")
    _sha(expected_target_revision, "expected_target_revision")
    _integer(automation_channel, "automation_channel", 65535)
    _integer(expected_event_id, "expected_event_id")
    _text(expected_name, "expected_name")
    _text(expected_event_name, "expected_event_name")
    _validate_target(target)
    target = dict(target)
    output = Path(output)
    if not output.is_absolute() or output.suffix.lower() != ".flp":
        raise ValueError("output must be an absolute NEW .flp path")
    output = output.resolve()
    receipt = output.with_suffix(".automation-retarget.json")
    if output.exists() or receipt.exists():
        raise ValueError("Output and automatic receipt must both be new files")
    source, data = _read_file(source)
    if source == output:
        raise ValueError("Source and output must differ")
    saved, start, events = _saved(data)
    if saved["sha256"] != expected_sha256:
        raise ValueError("Source revision changed; inspect again")
    curves = [curve for curve in saved["automation"] if curve["channel"] == automation_channel]
    selected = [link for link in saved["links"] if link["channel"] == automation_channel]
    if len(curves) != 1 or curves[0]["name"] != expected_name:
        raise ValueError("Exact automation channel/curve identity did not match")
    if (len(selected) != 1 or selected[0]["event_id"] != expected_event_id
            or selected[0]["controller_index"] != 0):
        raise ValueError("Automation must have exactly one matching controller-zero link")
    original = selected[0]
    if original["flags"] != 8:
        raise ValueError("Only qualified native-created automation link flags (8) are supported")
    conn = connection if connection is not None else get_connection()
    with transaction_lock(conn._hardware_dir / "mcp-gui.lock", 5):
        resolved = _inspect_target(conn, target)
        plugin_state._stopped(resolved)
        if resolved["revision"] != expected_target_revision:
            raise ValueError("Native automation target revision changed; inspect again")
        if resolved["event_name"] != expected_event_name:
            raise ValueError("Native event label does not match expected_event_name")
        if resolved.get("parameter_label_matches") is False:
            raise ValueError("Native short event label does not match the exposed parameter name")
        live_channels = [dict(index=c["index"], name=c["name"])
                         for c in resolved["project"]["channels"]]
        if live_channels != saved["channels"]:
            raise ValueError("Saved and live global channel rosters differ; save a fresh copy")
        event_id = resolved["event_id"]
        if any(link["event_id"] == event_id for link in saved["links"]):
            raise ValueError("Destination event already has a saved controller link")
        position = original["event_position"]
        eid, raw, encoded = events[position]
        replacement = raw[:8] + struct.pack("<I", event_id) + raw[12:]
        changed = encoded[:-20] + replacement
        body = b"".join(changed if i == position else entry[2] for i, entry in enumerate(events))
        output_data = data[:start] + body
        if len(output_data) != len(data):
            raise RuntimeError("Link retarget unexpectedly changed the file length")
        verified, _, output_events = _saved(output_data)
        if verified["automation"] != saved["automation"] or any(
            old != new for i, (old, new) in enumerate(zip(events, output_events)) if i != position
        ):
            raise RuntimeError("Unrelated saved data changed during retarget")
        new_link = [link for link in verified["links"] if link["event_position"] == position][0]
        if new_link["event_id"] != event_id or new_link["channel"] != automation_channel:
            raise RuntimeError("Rewritten target link did not verify")
        report = dict(
            success=True, status="preflighted", apply=apply, source=str(source),
            source_sha256=saved["sha256"], output=str(output), output_sha256=verified["sha256"],
            receipt_file=str(receipt), target_inspection=resolved, before_link=original,
            after_link=new_link, automation=curves[0], changed_field="event227.payload[8:12]",
            loaded_in_fl=False, verified_in_fl=False, limitations=list(LIMITATIONS),
        )
        if not apply:
            return report
        current = _inspect_target(conn, target)
        plugin_state._stopped(current)
        if current["revision"] != resolved["revision"]:
            raise RuntimeError("Native target changed before file write")
        if _read_file(source)[1] != data:
            raise ValueError("Source changed before file write")
        report.update(success=False, status="writing_copy")
        with receipt.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            with output.open("xb") as stream:
                stream.write(output_data)
                stream.flush()
                os.fsync(stream.fileno())
            if _read_file(output)[1] != output_data or _read_file(source)[1] != data:
                raise RuntimeError("Written output or source preservation did not verify")
            report.update(success=True, status="saved_link_verified")
        except Exception as exc:
            report.update(status="needs_inspection", error=str(exc))
        atomic_json(receipt, report)
        return report
