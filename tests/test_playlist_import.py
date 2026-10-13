"""Guarded first Audio Clip import contracts; no live FL or desktop calls."""

import copy
import json
import struct
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf
from test_playlist_file import clip
from test_project_bundle import event, project, text

from fl_studio_mcp.utils import playlist_import as pi


@pytest.fixture
def anyio_backend():
    return "asyncio"


# Real, full untrimmed Audio Clip from recording-qualified.flp, FL26.1.4.5589.
# Tests adjust explicit identity/placement fields; production never synthesizes it.
AUDIO_CLIP = bytes.fromhex(
    "00000000005006005b050000ef0100007800400040648080000080bf000080bf050000000000000000000000"
    "00000000000000000000803f0000000000000000000000000000f03f00000000ffffffff0000000000000000"
)


def saved_project(asset, imported=False):
    original = bytearray(clip())
    original[32:36] = (1).to_bytes(4, "little")
    original[19] = 0x80
    metadata = event(21, b"\0") + event(213, b"existing plugin payload")
    metadata += event(227, b"existing automation link") + event(234, b"curve")
    records = bytes(original)
    if imported:
        audio = bytearray(AUDIO_CLIP)
        struct.pack_into("<H", audio, 6, 1)
        metadata += (event(64, struct.pack("<H", 1)) + event(21, b"\x04")
                     + text(203, "Imported take") + text(196, str(asset)))
        records += bytes(audio)
    return project(metadata, playlist=records, version="26.1.4.5589")


@pytest.fixture
def files(tmp_path):
    asset = tmp_path / "take.wav"
    sf.write(asset, np.zeros((480, 2), dtype=np.float32), 48000, subtype="FLOAT")
    before, after = tmp_path / "before.flp", tmp_path / "after.flp"
    before.write_bytes(saved_project(asset))
    after.write_bytes(saved_project(asset, True))
    return asset, pi._snapshot(before), pi._snapshot(after)


def test_saved_native_import_proves_source_and_exactly_one_new_channel_clip(files):
    asset, before, after = files
    arrangement, row = pi._imported_clip(before, after, asset, pi.file_hash(asset))
    assert arrangement == 0 and row["kind"] == "audio" and row["channel"] == 1
    assert row["offset_bytes"] == "000080bf000080bf"


@pytest.mark.parametrize("change,match", [
    ("curve", "automation curves"), ("link", "automation links"),
    ("plugin", "plugin payloads"), ("name", "channel identities"),
    ("source", "sample path"), ("type", "native Audio Clip"),
    ("extra", "exactly one clip"), ("missing", "beyond qualified"),
    ("trim", "full untrimmed"),
])
def test_native_import_rejects_unexpected_content_changes(files, change, match):
    asset, before, after = files
    after = copy.deepcopy(after)
    if change == "curve":
        after["automation"] = [b"different"]
    elif change == "link":
        after["automation_links"] = [b"different"]
    elif change == "plugin":
        after["plugin_payloads"] = [b"different"]
    elif change == "name":
        after["channels"][0]["name"] = "different"
    elif change == "source":
        other = asset.with_name("different.wav")
        other.write_bytes(asset.read_bytes())
        after["channels"][1]["sample_paths"] = [str(other)]
    elif change == "type":
        after["channels"][1]["type"] = 0
    elif change == "extra":
        after["records"][0].append(after["records"][0][-1])
        after["arrangements"][0]["clips"].append(after["arrangements"][0]["clips"][-1])
    elif change == "missing":
        after["records"][0].pop(0)
        after["arrangements"][0]["clips"].pop(0)
    elif change == "trim":
        after["arrangements"][0]["clips"][-1]["offset_bytes"] = "0000000000000000"
    with pytest.raises(RuntimeError, match=match):
        pi._imported_clip(before, after, asset, pi.file_hash(asset))


def test_native_import_requires_audio_hash_to_still_match(files):
    asset, before, after = files
    with pytest.raises(RuntimeError, match="Audio source changed"):
        pi._imported_clip(before, after, asset, "0" * 64)


def test_roundtrip_accepts_only_captured_clip_normalization_and_reports_it(files):
    _, _, before = files
    after = copy.deepcopy(before)
    for i, record in enumerate(after["records"][0]):
        raw = bytearray(record)
        raw[32:36] = (11 + i).to_bytes(4, "little")
        if i == 0:
            raw[19] ^= 0x80
        after["records"][0][i] = bytes(raw)
    report = pi._roundtrip(before, after)
    assert len(report["clips"]) == 2
    assert report["clips"][0]["changed_byte_offsets"] == [19, 32]
    assert report["clips"][1]["changed_byte_offsets"] == [32]
    assert report["clips"][1]["u32_at32_after"] == 12


@pytest.mark.parametrize("offset,bit", [(19, 1), (20, 1), (35, 1), (80, 1)])
def test_roundtrip_rejects_unknown_audio_clip_changes(files, offset, bit):
    _, _, before = files
    after = copy.deepcopy(before)
    raw = bytearray(after["records"][0][1])
    raw[offset] ^= bit
    after["records"][0][1] = bytes(raw)
    if offset == 35:  # Offset32 is allowed only when the replacements stay unique.
        first = bytearray(after["records"][0][0])
        first[32:36] = raw[32:36]
        after["records"][0][0] = bytes(first)
    with pytest.raises(RuntimeError, match="beyond qualified|not unique"):
        pi._roundtrip(before, after)


def test_roundtrip_allows_captured_nonclip_fields_but_never_plugin_changes(files):
    _, _, before = files
    before = copy.deepcopy(before)
    before["nonplaylist"] += [(143, bytes.fromhex("0a000000")), (237, bytes(16))]
    after = copy.deepcopy(before)
    after["nonplaylist"][-2] = (143, bytes.fromhex("02000000"))
    after["nonplaylist"][-1] = (237, bytes(8) + b"12345678")
    result = pi._roundtrip(before, after)
    assert [v["event_id"] for v in result["nonplaylist"]] == [143, 237]
    after["plugin_payloads"] = [b"changed"]
    with pytest.raises(RuntimeError, match="plugin payloads"):
        pi._roundtrip(before, after)


def test_roundtrip_rejects_unqualified_nonclip_event_change(files):
    _, _, before = files
    after = copy.deepcopy(before)
    after["nonplaylist"][0] = (199, b"different")
    with pytest.raises(RuntimeError, match="unqualified event199"):
        pi._roundtrip(before, after)


@pytest.fixture
def geometry(monkeypatch):
    form = dict(handle="20", bounds=[262, 82, 1658, 946], pid=10, parent="1",
                dpi=96, enabled=True, title="Playlist -", **{"class": "TEventEditForm"})
    children = [dict(handle=str(30 + i), bounds=bounds, pid=10, parent="20", dpi=96,
                     enabled=True, title="", **{"class": cls})
                for i, (cls, bounds) in enumerate([
                    ("TWPControl", [423, 106, 112, 41]),
                    ("TQuickScroller", [534, 106, 1366, 21]),
                    ("TNumBar", [1899, 126, 21, 902]),
                    ("TNumBar", [402, 161, 16, 847]),
                ])]
    monkeypatch.setattr(pi, "unique_control", lambda *a: form)
    monkeypatch.setattr(pi, "windows", lambda *a: children)
    return SimpleNamespace(handle="1"), form, children


def test_surface_uses_discovered_native_anchors(geometry):
    fl, _, _ = geometry
    result = pi._surface(fl)
    assert result["header_bounds"] == [423, 147, 112, 861]
    assert result["grid_left"] == 535


@pytest.mark.parametrize("fault", ["dpi", "title", "parent", "moved", "duplicate"])
def test_surface_fails_closed_when_profile_changes(geometry, fault):
    fl, form, children = geometry
    if fault == "dpi":
        form["dpi"] = 144
    elif fault == "title":
        form["title"] = "Other editor"
    elif fault == "parent":
        children[0]["parent"] = "999"
    elif fault == "moved":
        children[0]["bounds"][0] += 1
    else:
        children.append(copy.deepcopy(children[0]))
    with pytest.raises(RuntimeError, match="verified"):
        pi._surface(fl)


@pytest.mark.anyio
async def test_empty_row_uses_ocr_and_native_identity_without_tick_geometry(
    files, geometry, monkeypatch,
):
    _, saved, _ = files
    fl, _, _ = geometry

    async def ocr(*args, **kwargs):
        return {"lines": [dict(text="Track 1", bounds=[425, 155, 45, 12]),
                          dict(text="Track 8", bounds=[425, 450, 45, 12])]}

    monkeypatch.setattr(pi, "read_region", ocr)
    monkeypatch.setattr(pi, "_pixels", lambda *a: "pixels")
    monkeypatch.setattr(pi, "point_belongs_to", lambda *a: True)
    monkeypatch.setattr(pi, "_native", lambda name, track: f"Track {track}"
                        if name.endswith("getTrackName") else 0)
    target = await pi._empty_target(fl, saved)
    assert target["track"] == 8 and target["point"] == (551, 456)
    assert target["header_sha256"] == "pixels"


@pytest.mark.anyio
@pytest.mark.parametrize("fault", ["pixels", "muted", "name", "ownership", "duplicate"])
async def test_empty_row_refuses_unverified_screen_or_identity(
    files, geometry, monkeypatch, fault,
):
    _, saved, _ = files
    fl, _, _ = geometry
    lines = [dict(text="Track 8", bounds=[425, 450, 45, 12])]
    if fault == "duplicate":
        lines *= 2

    async def ocr(*args, **kwargs):
        return {"lines": lines}

    captures = iter(["first", "second"] if fault == "pixels" else ["same", "same"])
    monkeypatch.setattr(pi, "read_region", ocr)
    monkeypatch.setattr(pi, "_pixels", lambda *a: next(captures))
    monkeypatch.setattr(pi, "point_belongs_to", lambda *a: fault != "ownership")
    monkeypatch.setattr(pi, "_native", lambda name, track:
                        ("wrong" if fault == "name" else f"Track {track}")
                        if name.endswith("getTrackName") else int(fault == "muted"))
    with pytest.raises(RuntimeError, match="rows moved|No visible"):
        await pi._empty_target(fl, saved)


class FakeFL:
    def __init__(self, asset):
        self.handle = "1"
        self.asset = asset
        self.imported = False
        self.dirty = False
        self.playing = False
        self.revision_extra = ""
        self.opened = None
        self.saves = []
        self.drops = 0
        self.opens = 0

    def state(self):
        return dict(revision=f"rev{int(self.imported)}{int(self.dirty)}{self.revision_extra}",
                    dirty=self.dirty, is_playing=self.playing, is_recording=False,
                    context=dict(project_generation=1, channel_generation=int(self.imported),
                                 channels=self.channels()), values=dict(ppq=96, tempo_bpm=125))

    def channels(self):
        result = [dict(index=0, name="Lead", target_fx=0)]
        if self.imported:
            result.append(dict(index=1, name="Imported take", target_fx=0))
        return result

    async def _project_guard(self):
        return None

    async def _project_focus(self):
        return None

    async def save_project_as(self, path):
        self.saves.append(Path(path).name)
        data = self.opened.read_bytes() if self.opened else saved_project(self.asset, self.imported)
        Path(path).write_bytes(data)
        self.dirty = False
        return dict(success=True, path=str(path), sha256=pi.file_hash(path))


@pytest.fixture
def workflow(files, tmp_path, monkeypatch):
    asset, _, _ = files
    fl = FakeFL(asset)
    surface = dict(form={"handle": "20"}, header_bounds=[1, 2, 3, 4])
    monkeypatch.setattr(pi, "_state", fl.state)
    monkeypatch.setattr(pi, "_surface", lambda *a: copy.deepcopy(surface))
    monkeypatch.setattr(pi, "_pixels", lambda *a: "pixels")
    monkeypatch.setattr(pi, "_channel_values", fl.channels)
    monkeypatch.setattr(pi, "_native", lambda *a: 4)

    async def target(*args):
        return dict(surface=surface, track=8, point=(55, 100), header_sha256="pixels")

    async def transfer(_fl, _asset, _point, _handle, guard):
        guard()
        fl.drops += 1
        fl.imported = True
        fl.dirty = True
        return dict(delivery_confirmed=True)

    async def open_project(_fl, source, digest, revision, recovery, receipt, apply):
        assert revision == fl.state()["revision"] and digest == pi.file_hash(source)
        fl.opens += 1
        fl.opened = Path(source)
        return dict(success=True, status="verified")

    monkeypatch.setattr(pi, "_empty_target", target)
    monkeypatch.setattr(pi, "transfer_file", transfer)
    monkeypatch.setattr(pi, "open_project", open_project)
    args = dict(path=str(asset), expected_audio_sha256=pi.file_hash(asset),
                expected_revision=fl.state()["revision"], directory=str(tmp_path / "work"),
                position_ticks=3072, track=8)
    return fl, args


@pytest.mark.anyio
async def test_preview_does_not_save_drop_open_or_create_directory(workflow):
    fl, args = workflow
    result = await pi.audio_clip_add(fl, **args)
    assert result["success"] and not result["verified"]
    assert result["status"] == "preflighted"
    assert not Path(args["directory"]).exists()
    assert not fl.saves and fl.drops == fl.opens == 0


@pytest.mark.anyio
async def test_apply_places_one_native_import_and_verifies_independent_save(workflow):
    fl, args = workflow
    result = await pi.audio_clip_add(fl, **args, apply=True)
    assert result["success"] and result["verified"] and result["status"] == "verified"
    assert result["success_scope"] == "audio_clip_placement"
    assert result["clip"]["position_ticks"] == 3072 and result["clip"]["track"] == 8
    assert fl.drops == fl.opens == 1
    assert fl.saves == ["before.flp", "imported.flp", "verified.flp"]
    directory = Path(args["directory"])
    assert (directory / "before.flp").read_bytes() == saved_project(fl.asset)
    assert json.loads((directory / "audio-clip-import.json").read_text())["verified"] is True


@pytest.mark.anyio
@pytest.mark.parametrize("fault", ["hash", "revision", "playing", "directory", "position"])
async def test_preflight_failure_never_dispatches(workflow, fault):
    fl, args = workflow
    if fault == "hash":
        args["expected_audio_sha256"] = "0" * 64
    elif fault == "revision":
        args["expected_revision"] = "stale"
    elif fault == "playing":
        fl.playing = True
    elif fault == "directory":
        Path(args["directory"]).mkdir()
    else:
        args["position_ticks"] = True
    with pytest.raises(ValueError):
        await pi.audio_clip_add(fl, **args, apply=True)
    assert not fl.saves and fl.drops == fl.opens == 0


@pytest.mark.anyio
async def test_playback_started_after_preview_is_checked_before_save(workflow, monkeypatch):
    fl, args = workflow
    calls = 0

    def state():
        nonlocal calls
        calls += 1
        result = fl.state()
        result["is_playing"] = calls > 1
        return result

    monkeypatch.setattr(pi, "_state", state)
    result = await pi.audio_clip_add(fl, **args, apply=True)
    assert result["status"] == "needs_inspection" and not result["success"]
    assert not fl.saves and fl.drops == fl.opens == 0


@pytest.mark.anyio
async def test_uncertain_delivery_is_not_repeated_or_claimed_success(workflow, monkeypatch):
    fl, args = workflow

    async def uncertain(*_):
        fl.drops += 1
        return dict(delivery_confirmed=False)

    monkeypatch.setattr(pi, "transfer_file", uncertain)
    result = await pi.audio_clip_add(fl, **args, apply=True)
    assert not result["success"] and not result["verified"]
    assert result["status"] == "needs_inspection" and "unconfirmed" in result["error"]
    assert fl.drops == 1 and fl.opens == 0 and fl.saves == ["before.flp"]


@pytest.mark.anyio
async def test_failed_reload_keeps_recovery_and_does_not_repeat(workflow, monkeypatch):
    fl, args = workflow

    async def failed(*a, **kw):
        fl.opens += 1
        return dict(success=False, status="needs_inspection")

    monkeypatch.setattr(pi, "open_project", failed)
    result = await pi.audio_clip_add(fl, **args, apply=True)
    assert not result["success"] and not result["verified"]
    assert fl.drops == fl.opens == 1
    assert Path(result["receipt_file"]).is_file()
    assert (Path(args["directory"]) / "before.flp").is_file()


@pytest.mark.anyio
async def test_project_change_after_file_edit_prevents_open(workflow, monkeypatch):
    fl, args = workflow
    edit = pi.edit_playlist

    def changed(*a, **kw):
        result = edit(*a, **kw)
        fl.revision_extra = "other-client"
        return result

    monkeypatch.setattr(pi, "edit_playlist", changed)
    result = await pi.audio_clip_add(fl, **args, apply=True)
    assert not result["success"] and "open not dispatched" in result["error"]
    assert fl.drops == 1 and fl.opens == 0


@pytest.mark.anyio
async def test_geometry_change_in_final_drop_guard_prevents_delivery(workflow, monkeypatch):
    fl, args = workflow

    async def changed(_fl, _asset, _point, _handle, guard):
        monkeypatch.setattr(pi, "_pixels", lambda *a: "changed")
        guard()
        fl.drops += 1

    monkeypatch.setattr(pi, "transfer_file", changed)
    result = await pi.audio_clip_add(fl, **args, apply=True)
    assert not result["success"] and "row view changed" in result["error"]
    assert fl.drops == fl.opens == 0
