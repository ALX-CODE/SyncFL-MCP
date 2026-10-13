"""Offline/controller-double tests; no running FL Studio instance is touched."""

import hashlib
import json
import struct

import pytest
from test_plugin_state import Connection
from test_project_bundle import event, project, text

from fl_studio_mcp.utils import automation_targets as targets


class TargetConnection(Connection):
    def __init__(self, directory):
        super().__init__(directory)
        self.context["channels"] += [dict(index=1, name="Seed - Channel volume", type=5)]
        self.event_names = {
            0: ("Lead - Channel volume", "Channel volume"),
            1: ("Lead - Channel panning", "Channel panning"),
            32768: ("Lead - Volume", "Volume"),
            0x70001FC0 + 3 * 4194304: ("Lead FX - Volume", "Volume"),
            0x70001FC1 + 3 * 4194304: ("Lead FX - Panning", "Panning"),
            0x70001F40 + 3 * 4194304 + 4: ("Lead FX - To Reverb", "To Reverb"),
            0x70008000 + 3 * 4194304 + 2 * 65536: ("Lead FX - Test Synth - Volume", "Volume"),
        }
        self.route_active = 1
        self.route_level = 0.5
        self.base_override = None
        self.inspections = 0
        self.after_inspection = None

    def native(self, name, args):
        if name == "channels.getRecEventId":
            assert args == [0, True]
            return self.base_override if self.base_override is not None else 0
        if name == "mixer.getTrackPluginId":
            track, slot = args
            return track * 4194304 + slot * 65536
        if name == "mixer.getEventIDName":
            event_id, short = args
            return self.event_names.get(event_id, ("", ""))[short]
        if name == "mixer.getTrackName" and args == [4]:
            return "Reverb"
        if name == "mixer.getRouteSendActive":
            assert args == [3, 4]
            return self.route_active
        if name == "mixer.getRouteToLevel":
            assert args == [3, 4]
            return self.route_level
        return super().native(name, args)

    def send_command(self, action, params, timeout):
        result = super().send_command(action, params, timeout)
        if action == "production.inspect":
            self.inspections += 1
            if self.after_inspection:
                self.after_inspection(self)
        return result


@pytest.fixture
def conn(tmp_path):
    return TargetConnection(tmp_path)


def linked_project(links=None, version="26.1.2", curve_name="Seed - Channel volume"):
    points = b"\0" * 17 + struct.pack("<I", 2)
    points += struct.pack("<ddfI", 0, 0.5, 0, 0) + struct.pack("<ddfI", 4, 0.7, 0, 0)
    links = [struct.pack("<5I", 1 << 16, 0, 0, 8, 469)] if links is None else links
    return project(
        b"".join(event(227, link) for link in links)
        + event(64, struct.pack("<H", 1)) + text(203, curve_name)
        + event(234, points) + event(250, b"opaque plugin state unchanged"),
        version=version,
    ).replace(text(192, "Lead"), text(203, "Lead"))


def files(tmp_path, data=None):
    source = tmp_path / "source.flp"
    source.write_bytes(linked_project() if data is None else data)
    return source, tmp_path / "output.flp"


def retarget(conn, tmp_path, **overrides):
    source = tmp_path / "source.flp"
    if not source.exists():
        files(tmp_path)
    target = overrides.pop("target", dict(kind="mixer_volume", index=3))
    inspection = targets.inspect_target(target, connection=conn)
    args = dict(
        source=source, output=tmp_path / "output.flp",
        expected_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        automation_channel=1, expected_name="Seed - Channel volume", expected_event_id=0,
        target=target, expected_target_revision=inspection["revision"],
        expected_event_name=inspection["event_name"], connection=conn,
    )
    args.update(overrides)
    return targets.retarget_automation(**args)


@pytest.mark.parametrize("target,expected", [
    (dict(kind="channel_volume", index=0), 0),
    (dict(kind="channel_pan", index=0), 1),
    (dict(kind="generator_parameter", index=0, parameter=0), 32768),
    (dict(kind="mixer_volume", index=3), 0x70001FC0 + 3 * 4194304),
    (dict(kind="mixer_pan", index=3), 0x70001FC1 + 3 * 4194304),
    (dict(kind="mixer_send", index=3, destination=4), 0x70001F40 + 3 * 4194304 + 4),
    (dict(kind="effect_parameter", index=3, slot_index=2, parameter=0),
     0x70008000 + 3 * 4194304 + 2 * 65536),
])
def test_native_resolver_uses_global_and_exact_slot_identity(conn, target, expected):
    result = targets.inspect_target(target, connection=conn)
    assert result["event_id"] == expected
    assert result["event_name"] == conn.event_names[expected][0]
    assert result["project"]["session"] == "test-session"
    assert len(result["revision"]) == 64
    assert not conn.writes
    if target["kind"].endswith("parameter"):
        assert result["parameter_label_matches"]
        assert result["target"]["plugin"]["plugin"] == "Test Synth"


@pytest.mark.parametrize("target", [
    {}, dict(kind=[]), dict(kind="tempo", index=0), dict(kind="channel_pan", index=True),
    dict(kind="channel_pan", index=0, slot_index=-1),
    dict(kind="generator_parameter", index=0, parameter=10000),
    dict(kind="effect_parameter", index=3, parameter=0, slot_index=10),
    dict(kind="mixer_send", index=3, destination=3),
    dict(kind="mixer_send", index=3, destination=128),
    dict(kind="mixer_send", index=3),
])
def test_bad_targets_never_contact_controller(conn, target):
    with pytest.raises(ValueError):
        targets.inspect_target(target, connection=conn)
    assert not conn.calls


@pytest.mark.parametrize("base", [True, 0.0, "0", -1, 2**32])
def test_native_event_base_requires_exact_uint(conn, base):
    conn.base_override = base
    with pytest.raises(ValueError, match="recording-event base"):
        targets.inspect_target(dict(kind="channel_pan", index=0), connection=conn)


@pytest.mark.parametrize("active", [False, 0, 1.0, "1", 2, None])
def test_send_requires_actual_enabled_route(conn, active):
    conn.route_active = active
    with pytest.raises(ValueError, match="enabled"):
        targets.inspect_target(dict(kind="mixer_send", index=3, destination=4), connection=conn)


def test_empty_event_label_does_not_resolve(conn):
    conn.event_names.clear()
    with pytest.raises(ValueError, match="label"):
        targets.inspect_target(dict(kind="mixer_pan", index=3), connection=conn)


@pytest.mark.parametrize("labels", [
    ("Unknown event (#20401FC0)", "Unknown event (#20401FC0)"),
    ("Insert 1 - Volume", "Unknown event (#20401FC0)"),
    ("  UNKNOWN EVENT (#20401FC0)", "Volume"),
])
def test_unknown_native_event_labels_never_resolve_or_authorize_retarget(conn, tmp_path, labels):
    conn.event_names[0x70001FC0 + 3 * 4194304] = labels
    with pytest.raises(ValueError, match="FL did not resolve"):
        retarget(conn, tmp_path, apply=True)
    assert not (tmp_path / "output.flp").exists()
    assert not (tmp_path / "output.automation-retarget.json").exists()
    assert not conn.writes


@pytest.mark.parametrize("labels", [
    ("Insert 1 - ", "Insert 1 - "),
    ("Lead FX -", "Lead FX -"),
    ("Lead FX - To Reverb", "Lead FX - "),
    ("Lead FX - Send", "Send"),
    ("Lead FX - To Another Track", "To Another Track"),
])
def test_send_labels_require_complete_destination_identity(conn, tmp_path, labels):
    conn.event_names[0x70001F40 + 3 * 4194304 + 4] = labels
    with pytest.raises(ValueError, match="send event label"):
        retarget(conn, tmp_path, target=dict(kind="mixer_send", index=3, destination=4),
                 apply=True)
    assert not (tmp_path / "output.flp").exists()
    assert not conn.writes


def test_target_change_between_callbacks_rejected(conn):
    conn.after_inspection = lambda c: c.context.update(project_generation=c.inspections)
    with pytest.raises(RuntimeError, match="changed"):
        targets.inspect_target(dict(kind="channel_pan", index=0), connection=conn)


def test_missing_or_changed_signature_rejected(conn):
    conn.catalog["mixer.getEventIDName"]["arguments"] = ["index"]
    with pytest.raises(RuntimeError, match="signature"):
        targets.inspect_target(dict(kind="channel_pan", index=0), connection=conn)


def test_saved_link_inspector_returns_compact_curve_and_hash(tmp_path):
    source, _ = files(tmp_path)
    result = targets.inspect_links(source, channel=1)
    assert result["matched"] == 1
    assert result["links"][0]["raw"] == struct.pack("<5I", 1 << 16, 0, 0, 8, 469).hex()
    assert result["automation"][0]["point_count"] == 2
    assert "points" not in result["automation"][0]
    assert targets.inspect_links(source, event_id=999)["matched"] == 0


@pytest.mark.parametrize("data", [
    linked_project(links=[b"unknown"]), linked_project(version="25.2.1"),
    linked_project() + b"trailing junk",
])
def test_unknown_file_layout_is_never_silently_skipped(tmp_path, data):
    source, _ = files(tmp_path, data)
    with pytest.raises(ValueError):
        targets.inspect_links(source)


def test_preview_does_not_write_any_output_or_call_live_mutation(conn, tmp_path):
    report = retarget(conn, tmp_path)
    assert report["status"] == "preflighted"
    assert not (tmp_path / "output.flp").exists()
    assert not (tmp_path / "output.automation-retarget.json").exists()
    assert not conn.writes


@pytest.mark.parametrize("target", [
    dict(kind="channel_pan", index=0),
    dict(kind="generator_parameter", index=0, parameter=0),
    dict(kind="mixer_volume", index=3),
    dict(kind="mixer_pan", index=3),
    dict(kind="mixer_send", index=3, destination=4),
    dict(kind="effect_parameter", index=3, slot_index=2, parameter=0),
])
def test_apply_changes_only_event_id_bytes_and_archives_context(conn, tmp_path, target):
    source, output = files(tmp_path)
    original = source.read_bytes()
    report = retarget(conn, tmp_path, target=target, apply=True)
    assert report["success"] and report["status"] == "saved_link_verified"
    assert source.read_bytes() == original
    result = output.read_bytes()
    assert len(result) == len(original)
    _, _, before = targets._saved(original)
    _, _, after = targets._saved(result)
    differing = [(old, new) for old, new in zip(before, after) if old != new]
    assert len(differing) == 1
    old, new = differing[0]
    assert old[0] == new[0] == 227
    assert old[1][:8] == new[1][:8] and old[1][12:] == new[1][12:]
    saved = json.loads((tmp_path / "output.automation-retarget.json").read_text())
    assert saved["target_inspection"]["project"]["session"] == "test-session"
    assert saved["target_inspection"]["event_name"] == report["target_inspection"]["event_name"]
    assert saved["verified_in_fl"] is False
    assert not conn.writes


@pytest.mark.parametrize("changes", [
    dict(expected_sha256="a" * 64), dict(expected_target_revision="a" * 64),
    dict(expected_name="Renamed"), dict(expected_event_id=1),
    dict(expected_event_name="Wrong target"), dict(automation_channel=True),
    dict(apply="yes"),
])
def test_stale_or_malformed_apply_leaves_source_and_outputs_untouched(conn, tmp_path, changes):
    source, output = files(tmp_path)
    before = source.read_bytes()
    args = dict(apply=True)
    args.update(changes)
    with pytest.raises(ValueError):
        retarget(conn, tmp_path, **args)
    assert source.read_bytes() == before
    assert not output.exists() and not output.with_suffix(".automation-retarget.json").exists()


@pytest.mark.parametrize("words", [
    (1 << 16 | 1, 0, 0, 8, 469), (1 << 16, 0, 0, 9, 469),
])
def test_unqualified_link_flags_or_controller_index_rejected(conn, tmp_path, words):
    files(tmp_path, linked_project([struct.pack("<5I", *words)]))
    with pytest.raises(ValueError):
        retarget(conn, tmp_path, apply=True)


def test_one_to_many_automation_links_are_refused(conn, tmp_path):
    files(tmp_path, linked_project([
        struct.pack("<5I", 1 << 16, 0, 0, 8, 469),
        struct.pack("<5I", 1 << 16, 0, 1, 8, 469),
    ]))
    with pytest.raises(ValueError, match="exactly one"):
        retarget(conn, tmp_path, apply=True)


def test_occupied_destination_is_refused(conn, tmp_path):
    files(tmp_path, linked_project([
        struct.pack("<5I", 1 << 16, 0, 0, 8, 469),
        struct.pack("<5I", 2 << 16, 0, 0x70001FC0 + 3 * 4194304, 8, 469),
    ]))
    with pytest.raises(ValueError, match="already has"):
        retarget(conn, tmp_path, apply=True)


def test_plugin_event_label_must_match_exposed_parameter(conn, tmp_path):
    conn.event_names[32768] = ("Lead - Wrong", "Wrong")
    with pytest.raises(ValueError, match="short event label"):
        retarget(conn, tmp_path, target=dict(kind="generator_parameter", index=0, parameter=0),
                 apply=True)


def test_stopped_host_and_matching_saved_roster_required(conn, tmp_path):
    conn.playing = True
    with pytest.raises(ValueError, match="stopped"):
        retarget(conn, tmp_path, apply=True)
    conn.playing = False
    conn.context["channels"][1]["name"] = "Different clip"
    with pytest.raises(ValueError, match="rosters differ"):
        retarget(conn, tmp_path, apply=True)


@pytest.mark.parametrize("suffix", [".flp", ".automation-retarget.json"])
def test_existing_output_or_receipt_is_preserved(conn, tmp_path, suffix):
    path = tmp_path / ("output" + suffix)
    path.write_text("original")
    with pytest.raises(ValueError, match="new files"):
        retarget(conn, tmp_path, apply=True)
    assert path.read_text() == "original"


def test_retarget_noop_refused(conn, tmp_path):
    with pytest.raises(ValueError, match="already has"):
        retarget(conn, tmp_path, target=dict(kind="channel_volume", index=0), apply=True)


def test_real_fl2614_native_created_link_record(tmp_path):
    # Captured from root's disposable FL26.1.4.5589 fixture on 2026-09-29:
    # global channel 1 volume, controller 0 of new automation channel 3.
    raw = bytes.fromhex("00000300000000000000010008000000d5010000")
    source, _ = files(tmp_path, linked_project([raw]))
    result = targets.inspect_links(source)
    link = result["links"][0]
    assert (link["channel"], link["controller_index"], link["event_id"], link["flags"]) == (
        3, 0, 65536, 8,
    )
    assert link["raw"] == raw.hex()


def test_real_fl2614_mixer_namespace_is_not_legacy_manual_namespace(tmp_path):
    # Captured native Insert 1 volume fixture; the native track base is 0x400000.
    raw = bytes.fromhex("0000050000000000c01f407008000000d5010000")
    source, _ = files(tmp_path, linked_project([raw]))
    link = targets.inspect_links(source)["links"][0]
    assert link["event_id"] == 0x70401FC0
    assert link["event_id"] == targets._MIXER_VOLUME + 0x00400000
    assert link["event_id"] != 536879040 + 0x00400000
    assert (link["channel"], link["controller_index"], link["flags"]) == (5, 0, 8)


def test_source_race_before_write_does_not_create_output(conn, tmp_path, monkeypatch):
    source, output = files(tmp_path)
    original_inspect = targets._inspect_target
    count = 0

    def inspect(*args):
        nonlocal count
        count += 1
        result = original_inspect(*args)
        if count == 3:
            source.write_bytes(linked_project(curve_name="Another clip"))
        return result

    monkeypatch.setattr(targets, "_inspect_target", inspect)
    with pytest.raises(ValueError, match="Source changed before"):
        retarget(conn, tmp_path, apply=True)
    assert not output.exists() and not output.with_suffix(".automation-retarget.json").exists()


def test_target_race_before_write_does_not_create_output(conn, tmp_path, monkeypatch):
    _, output = files(tmp_path)
    original_inspect = targets._inspect_target
    count = 0

    def inspect(*args):
        nonlocal count
        count += 1
        if count == 3:
            conn.track_name = "A different track"
        return original_inspect(*args)

    monkeypatch.setattr(targets, "_inspect_target", inspect)
    with pytest.raises(RuntimeError, match="Native target changed before"):
        retarget(conn, tmp_path, apply=True)
    assert not output.exists() and not output.with_suffix(".automation-retarget.json").exists()


def test_output_race_is_receipted_without_retry_or_overwrite(conn, tmp_path, monkeypatch):
    _, output = files(tmp_path)
    original_open = type(output).open
    writes = []

    def racing_open(path, mode="r", *args, **kwargs):
        if path == output and mode == "xb":
            writes.append(path)
            with original_open(path, "wb") as stream:
                stream.write(b"external writer")
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(type(output), "open", racing_open)
    report = retarget(conn, tmp_path, apply=True)
    assert not report["success"] and report["status"] == "needs_inspection"
    assert len(writes) == 1
    assert output.read_bytes() == b"external writer"
    saved = json.loads(output.with_suffix(".automation-retarget.json").read_text())
    assert saved["status"] == "needs_inspection"


def test_file_size_bound_checked_before_read(tmp_path, monkeypatch):
    source, _ = files(tmp_path)
    monkeypatch.setattr(targets, "MAX_FILE_BYTES", 10)
    with pytest.raises(ValueError, match="at most"):
        targets.inspect_links(source)
