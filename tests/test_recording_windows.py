"""Owned recording filename dialogs and no-input failure boundaries."""

import copy
import json
from pathlib import Path

import pytest
from test_recording_state import Connection

from fl_studio_mcp.utils import recording_windows as rw
from fl_studio_mcp.utils.recording_state import inspect_recording


@pytest.fixture
def anyio_backend():
    return "asyncio"


class RecordingConnection(Connection):
    selected = 1
    focused = True

    def value(self, name, args):
        if name == "mixer.trackNumber":
            return self.selected
        if name == "ui.getFocused":
            return self.focused
        return super().value(name, args)

    def send_command(self, action, params, timeout):
        if action == "native.call":
            self.writes.append(copy.deepcopy(params))
            if params["function"] == "mixer.setActiveTrack":
                self.selected = params["args"][0]
            else:
                assert params["function"] in ("ui.showWindow", "ui.setFocused")
            return dict(success=True, value=None)
        return super().send_command(action, params, timeout)


class FL:
    def __init__(self, conn):
        self.conn = conn
        self.handle = "100"
        self.project_title = "Fixture"
        self.dialog_open = False
        self.dialog_title = "Record Vocal to"
        self.dialog_owner = "100"
        self.dialog_class = "#32770"
        self.calls = []
        self.typed = None
        self.on_type = None
        self.fail_confirmation = False
        self.ignore_confirmation = False
        self.fields = [dict(type="Edit", name="File name:", id="field", enabled=True)]
        self.mixer = dict(handle="200", **{"class": "TFXForm"}, title="Mixer - Vocal",
                          bounds=[475, 370, 1089, 677], pid=123, parent="100",
                          enabled=True, dpi=96)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def _project_guard(self, **kwargs):
        return dict(handle="100"), dict(pid=123)

    async def _project_windows(self):
        return [dict(handle="300", title=self.dialog_title, className=self.dialog_class)]

    async def _project_dialog_snapshot(self, dialog):
        return self.fields

    async def _cancel_project_dialog(self, dialog):
        self.calls.append(("cancel", {}))
        self.dialog_open = False

    async def dialog(self, title):
        return dict(handle="300", title=self.dialog_title, className=self.dialog_class)

    async def click(self, *point, **kwargs):
        self.calls.append(("click", dict(point=point, **kwargs)))
        self.dialog_open = True

    async def win(self, name, **params):
        self.calls.append((name, params))
        if name == "keyboard_control":
            assert params["action"] == "type"
            self.typed = params["text"]
            if self.on_type:
                self.on_type(self)
        if name == "ui_batch":
            steps = json.loads(params["steps"])
            if steps[-1].get("key") == "enter":
                assert steps[0] == dict(action="wait_text", elementId="field",
                                        text=self.typed, match="exact", timeoutMs=5000)
                assert steps[-1]["expectedElementId"] == "field"
                self.dialog_open = False
                if not self.ignore_confirmation:
                    self.conn.filenames[1] = self.typed
                    self.conn.armed[1] = True
                if self.fail_confirmation:
                    raise TimeoutError("confirmation response unknown")
            else:
                assert steps == [dict(action="key", key="a", modifiers="ctrl",
                                      expectedElementId="field")]
        return dict(success=True, tree=[])

    def state(self, handle):
        return dict(exists=self.dialog_open, enabled=True, visible=True,
                    root_owner=self.dialog_owner, pid=123)


def headers(mixer):
    x, y = mixer["bounds"][:2]
    words = [dict(text="Wide", bounds=[x + 145, y + 8, 28, 12])]
    words += [dict(text=str(i), bounds=[x + 124 + (i - 1) * 44, y + 30, 6, 12])
              for i in range(1, 17)]
    return dict(lines=[dict(words=words)])


@pytest.fixture
def rig(tmp_path, monkeypatch):
    conn = RecordingConnection(tmp_path)
    fl = FL(conn)
    monkeypatch.setattr(rw, "WindowsFL", lambda: fl)
    monkeypatch.setattr(rw, "unique_control", lambda *args: copy.deepcopy(fl.mixer))
    monkeypatch.setattr(rw, "_window_state", fl.state)
    monkeypatch.setattr(rw, "point_belongs_to", lambda *args: True)

    async def ocr(*args, **kwargs):
        return headers(fl.mixer)

    monkeypatch.setattr(rw, "read_region", ocr)
    return conn, fl


async def configure(rig, **changes):
    conn, _ = rig
    revision = inspect_recording([1], conn)["tracks"][0]["revision"]
    values = dict(track=1, expected_name="Vocal", expected_revision=revision,
                  recording_file=str(conn._hardware_dir / "take.wav"), apply=True,
                  receipt_file=str(conn._hardware_dir / "receipt.json"), connection=conn)
    return await rw.configure_recording_file(**{**values, **changes})


@pytest.mark.anyio
async def test_preflight_uses_no_windows_input_or_files(rig):
    report = await configure(rig, apply=False)
    conn, fl = rig
    assert report["success_scope"] == "preflight" and not report["gui_profile_verified"]
    assert not conn.writes and not fl.calls
    assert not (conn._hardware_dir / "receipt.json").exists()


@pytest.mark.anyio
async def test_real_keyboard_configuration_requires_native_path_and_arm_readbacks(rig):
    conn, fl = rig
    report = await configure(rig)
    assert report["success"] and report["status"] == "verified"
    assert report["success_scope"] == "recording_file_configuration"
    assert report["filename_verified"] and len(report["readbacks"]) == 2
    assert report["arm_changed"] and report["target_armed"]
    assert not report["recorded_take_verified"] and not report["input_configuration_verified"]
    assert report["relative_disk_point"] == [117, 586]
    assert not Path(report["recording_file"]).exists()
    assert not any(name == "ui_type" for name, _ in fl.calls)
    assert [row["function"] for row in conn.writes] == [
        "mixer.setActiveTrack", "ui.showWindow", "ui.setFocused"]
    assert json.loads(Path(report["receipt_file"]).read_text())["filename_verified"]


@pytest.mark.anyio
@pytest.mark.parametrize("change", ["title", "dpi", "width", "pid", "disabled"])
async def test_unknown_mixer_identity_or_geometry_never_clicks(rig, change):
    _, fl = rig
    if change == "title":
        fl.mixer["title"] = "Mixer - Different"
    elif change == "dpi":
        fl.mixer["dpi"] = 120
    elif change == "width":
        fl.mixer["bounds"][2] += 1
    elif change == "pid":
        fl.mixer["pid"] = 999
    else:
        fl.mixer["enabled"] = False
    report = await configure(rig)
    assert not report["success"] and report["status"] == "not_applied"
    assert not any(name == "click" for name, _ in fl.calls)


@pytest.mark.anyio
async def test_occluded_disk_control_never_clicks(rig, monkeypatch):
    monkeypatch.setattr(rw, "point_belongs_to", lambda *args: False)
    report = await configure(rig)
    assert "obscured" in report["error"] and not rig[1].dialog_open


@pytest.mark.anyio
async def test_failed_header_check_keeps_ocr_diagnostics_before_any_disk_click(rig, monkeypatch):
    async def missing_number(*args, **kwargs):
        result = headers(rig[1].mixer)
        result["lines"][0]["words"] = result["lines"][0]["words"][:2]
        result["lines"][0]["words"][1]["text"] = "I"
        return result

    monkeypatch.setattr(rw, "read_region", missing_number)
    report = await configure(rig)
    assert not report["success"] and "header/scroll" in report["error"]
    assert report["header_observation"]["lines"][0]["words"][1]["text"] == "I"
    saved = json.loads(Path(report["receipt_file"]).read_text())
    assert saved["header_observation"] == report["header_observation"]
    assert saved["mixer"] == rig[1].mixer
    assert not any(name == "click" for name, _ in rig[1].calls)


@pytest.mark.anyio
@pytest.mark.parametrize("change", ["title", "owner", "class", "ambiguous_field"])
async def test_dialog_must_be_exact_owned_and_unambiguous_before_typing(rig, change):
    _, fl = rig
    if change == "title":
        fl.dialog_title = "Record Different to"
    elif change == "owner":
        fl.dialog_owner = "999"
    elif change == "class":
        fl.dialog_class = "Foreign"
    else:
        fl.fields *= 2
    report = await configure(rig)
    assert not report["success"] and fl.typed is None
    if change == "ambiguous_field":
        assert report["dialog_cancelled"]


@pytest.mark.anyio
@pytest.mark.parametrize("change", ["project", "selected", "playing", "occupied"])
async def test_changes_after_typing_block_filename_confirmation(rig, change):
    conn, fl = rig

    def mutate(_):
        if change == "project":
            conn.context["project_generation"] += 1
        elif change == "selected":
            conn.selected = 2
        elif change == "playing":
            conn.playing = True
        else:
            Path(fl.typed).write_bytes(b"preserve")

    fl.on_type = mutate
    report = await configure(rig)
    assert not report["success"] and not report["filename_verified"]
    assert "confirm_filename" not in [d["operation"] for d in report["dispatches"]]
    assert not conn.armed[1]


@pytest.mark.anyio
async def test_unknown_confirmation_is_not_replayed_or_cancelled(rig):
    _, fl = rig
    fl.fail_confirmation = True
    report = await configure(rig)
    assert report["status"] == "unknown" and not report["success"]
    assert [d["operation"] for d in report["dispatches"]].count("confirm_filename") == 1
    assert not any(name == "cancel" for name, _ in fl.calls)


@pytest.mark.anyio
async def test_visible_filename_input_is_not_native_configuration_proof(rig):
    rig[1].ignore_confirmation = True
    report = await configure(rig)
    assert report["status"] == "unknown" and not report["filename_verified"]
    assert "did not verify" in report["error"]


@pytest.mark.anyio
@pytest.mark.parametrize("change", ["playing", "recording", "stale", "occupied", "no_receipt"])
async def test_invalid_preconditions_fail_before_any_input(rig, change):
    conn, fl = rig
    kwargs = {}
    if change == "playing":
        conn.playing = True
    elif change == "recording":
        conn.recording = True
    elif change == "stale":
        kwargs["expected_revision"] = "0" * 64
    elif change == "occupied":
        (conn._hardware_dir / "take.wav").write_bytes(b"preserve")
    else:
        kwargs["receipt_file"] = None
    with pytest.raises(ValueError):
        await configure(rig, **kwargs)
    assert not conn.writes and not fl.calls


def test_track_headers_bind_the_qualified_disk_column(rig):
    mixer = rig[1].mixer
    assert rw._headers(headers(mixer), mixer, 16) == (777, 586)
    wrong = headers(mixer)
    wrong["lines"][0]["words"][1]["text"] = "17"
    with pytest.raises(RuntimeError, match="header/scroll"):
        rw._headers(wrong, mixer, 1)
    duplicate = headers(mixer)
    duplicate["lines"][0]["words"].append(duplicate["lines"][0]["words"][1])
    with pytest.raises(RuntimeError, match="header/scroll"):
        rw._headers(duplicate, mixer, 1)
    narrow = headers(mixer)
    narrow["lines"][0]["words"][0]["text"] = "Narrow"
    with pytest.raises(RuntimeError, match="Wide"):
        rw._headers(narrow, mixer, 1)


def test_live_ocr_double_digit_headers_establish_the_same_unscrolled_grid(rig):
    mixer = rig[1].mixer
    observed = headers(mixer)
    words = observed["lines"][0]["words"]
    observed["lines"][0]["words"] = [words[i] for i in (0, 12, 13, 14, 15, 16)]
    observed["lines"][0]["words"][3]["text"] = "u"  # Literal live OCR; never assumed to be 14.
    assert rw._headers(observed, mixer, 1) == (117, 586)
    assert rw._headers(observed, mixer, 16) == (777, 586)
    # A horizontally scrolled viewport reports different numeric columns.
    shifted = copy.deepcopy(observed)
    shifted["lines"][0]["words"][1]["bounds"][0] -= 44
    with pytest.raises(RuntimeError, match="header/scroll"):
        rw._headers(shifted, mixer, 1)
    sparse = copy.deepcopy(observed)
    sparse["lines"][0]["words"] = sparse["lines"][0]["words"][:3]
    with pytest.raises(RuntimeError, match="too few anchors"):
        rw._headers(sparse, mixer, 1)
