"""Fail-closed GUI fallback tests; no desktop input is sent by this suite."""

import asyncio
import base64
import copy
import json
import struct
import zlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastmcp import Client, FastMCP
from mcp.types import ImageContent
from pydantic import ValidationError

from fl_studio_mcp.tools import studio_ui as tool_module
from fl_studio_mcp.utils import studio_ui as ui
from fl_studio_mcp.utils.interaction_policy import require_desktop_input

IMAGE = ImageContent(type="image", data="aW1hZ2U=", mimeType="image/png")


@pytest.fixture
def rig(tmp_path, monkeypatch):
    # Isolate retired action internals; production boundary is tested below.
    monkeypatch.setattr(ui, "require_desktop_input", lambda *args: None)
    monkeypatch.setenv("FL_MCP_INTERACTION_POLICY", "interactive")
    main = dict(handle="1", title="Song - FL Studio", bounds=[100, 200, 800, 600],
                pid=42, parent="0", enabled=True, dpi=96, **{"class": "TFruityLoopsMainForm"})
    child = dict(handle="2", title="Plugin", bounds=[120, 220, 400, 300],
                 pid=42, parent="1", enabled=True, dpi=96, **{"class": "PluginForm"})
    project = dict(success=True, context=dict(session="session-1", project_generation=0,
                                            channel_generation=0, pattern=1),
                   values=dict(tempo_bpm=120, ppq=96), dirty=False, is_playing=False,
                   is_recording=False, controller_build="build")
    node = dict(id="helper-1:1", name="Volume", type="Slider", enabled=True,
                click=[250, 350, 0], value="50")
    state = SimpleNamespace(main=main, child=child, project=project, tree=[node],
                            focus=dict(foreground="1", focus="2"), calls=[], writes=[],
                            snapshots=0, screenshot=True, fail_dispatch=False, incremental=False,
                            block_point=None, image=IMAGE, focus_after_snapshot=None,
                            dispatch_response=dict(success=True), captures=0)

    def native_windows(parent=None, pid=None):
        return copy.deepcopy([child] if parent == "1" else [] if parent else [main])

    async def call_tool(name, params, **kwargs):
        state.captures += 1
        assert name == "screenshot_control"
        assert params["windowHandle"] in ("1", "2")
        assert params["target"] == "window" and params["annotate"] is False
        return SimpleNamespace(is_error=False, content=[state.image] if state.screenshot else [])

    class FakeFL:
        handle = "1"
        client = SimpleNamespace(call_tool=call_tool)

        def __init__(self, *, read_only=False):
            self.read_only = read_only

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def win(self, name, **kwargs):
            state.calls.append((name, kwargs))
            if name == "ui_snapshot":
                state.snapshots += 1
                if state.focus_after_snapshot:
                    state.focus = state.focus_after_snapshot
                if state.incremental and kwargs["mode"] == "auto":
                    return dict(success=True, kind="unchanged")
                tree = copy.deepcopy(state.tree)
                for node in tree:
                    node["id"] = f"helper-{state.snapshots}:1"
                return dict(success=True, kind="full", tree=tree)
            state.writes.append((name, kwargs))
            if state.fail_dispatch:
                raise TimeoutError("input sent, response lost")
            return copy.deepcopy(state.dispatch_response)

    def send(action, params, timeout):
        assert action == "production.inspect" and params == {"scope": "project"}
        return copy.deepcopy(project)

    state.conn = SimpleNamespace(_hardware_dir=tmp_path, send_command=send)
    state.receipt = str(tmp_path / "action.json")
    ui._observations.clear()
    monkeypatch.setattr(ui, "WindowsFL", FakeFL)
    monkeypatch.setattr(ui, "windows", native_windows)
    monkeypatch.setattr(ui, "_focus_state", lambda: copy.deepcopy(state.focus))
    monkeypatch.setattr(ui, "point_belongs_to", lambda point, handle: point != state.block_point)
    return state


def inspect(rig, **kwargs):
    return asyncio.run(ui.inspect_ui(connection=rig.conn, **kwargs))


def act(rig, revision, action=None, apply=True, **kwargs):
    return asyncio.run(ui.action_ui(
        kwargs.pop("window_handle", "1"), revision,
        action or dict(kind="click", selector=dict(name="Volume")),
        "Read exact native parameter value in a fresh callback", apply,
        kwargs.pop("receipt_file", rig.receipt), connection=rig.conn, **kwargs))


def test_read_only_inspection_allows_playback_and_returns_images(rig):
    rig.project["is_playing"] = True
    report, images = inspect(rig, include_screenshot=True)
    assert report["status"] == "observed" and report["screenshot_included"]
    assert len(report["revision"]) == 64 and report["target"]["handle"] == "1"
    assert report["project"]["is_playing"] is True and images == [IMAGE]
    assert not rig.writes


def test_missing_screenshot_does_not_create_observation(rig):
    rig.screenshot = False
    with pytest.raises(RuntimeError, match="screenshot"):
        inspect(rig, include_screenshot=True)
    assert not ui._observations and not rig.writes


def test_incremental_snapshot_is_replaced_by_complete_tree(rig):
    rig.incremental = True
    report, _ = inspect(rig)
    assert report["snapshot"]["kind"] == "full"
    assert [params["mode"] for name, params in rig.calls] == ["auto", "full"]


def test_semantic_preflight_resolves_new_helper_ids_and_never_dispatches(rig):
    before, _ = inspect(rig)
    result = act(rig, before["revision"], apply=False)
    assert result["status"] == "preflighted" and result["success"]
    assert result["success_scope"] == "preflight"
    assert not result["musical_state_verified"] and not rig.writes
    assert not Path(rig.receipt).exists()


def test_background_inspection_and_action_preview_do_not_dispatch(rig, monkeypatch):
    monkeypatch.delenv("FL_MCP_INTERACTION_POLICY", raising=False)
    before, _ = inspect(rig, include_screenshot=True)
    result = act(rig, before["revision"], apply=False)
    assert result["success"] and result["status"] == "preflighted"
    assert result["requires_desktop_input"] is True and result["would_be_blocked"] is True
    assert not rig.writes and not Path(rig.receipt).exists()


def test_background_apply_rejected_before_observation_receipt_or_dispatch(rig, monkeypatch):
    monkeypatch.setattr(ui, "require_desktop_input", require_desktop_input)
    before, _ = inspect(rig)
    calls = list(rig.calls)
    monkeypatch.delenv("FL_MCP_INTERACTION_POLICY", raising=False)
    with pytest.raises(RuntimeError, match="unsupported in background mode"):
        act(rig, before["revision"])
    assert rig.calls == calls and not rig.writes and not Path(rig.receipt).exists()
    assert before["revision"] in ui._observations


def test_apply_dispatches_once_retains_receipt_and_requires_new_observation(rig):
    before, _ = inspect(rig)
    result = act(rig, before["revision"])
    assert result["status"] == "dispatched_unverified"
    assert result["success"] and result["success_scope"] == "input_delivery"
    assert not result["verified"] and not result["musical_state_verified"]
    assert len(rig.writes) == 1 and rig.writes[0][0] == "ui_click"
    assert rig.writes[0][1]["elementId"] == "helper-2:1"
    assert json.loads(Path(rig.receipt).read_text())["status"] == "dispatched_unverified"
    with pytest.raises(ValueError, match="consumed"):
        act(rig, before["revision"], receipt_file=str(Path(rig.receipt).with_name("other.json")))
    assert len(rig.writes) == 1


@pytest.mark.parametrize("change", ["project", "session", "title", "bounds", "tree", "focus"])
def test_stale_observation_never_dispatches(rig, change):
    before, _ = inspect(rig)
    if change == "project":
        rig.project["context"]["project_generation"] += 1
    elif change == "session":
        rig.project["context"]["session"] = "new-session"
    elif change == "title":
        rig.child["title"] = "Another plugin"
    elif change == "bounds":
        rig.main["bounds"][0] += 1
    elif change == "tree":
        rig.tree[0]["value"] = "51"
    else:
        rig.focus["focus"] = "1"
    with pytest.raises(ValueError, match="changed"):
        act(rig, before["revision"])
    assert not rig.writes and not Path(rig.receipt).exists()


def test_expired_observation_never_dispatches(rig):
    before, _ = inspect(rig)
    ui._observations[before["revision"]]["created"] -= ui.OBSERVATION_TTL + 1
    with pytest.raises(ValueError, match="expired"):
        act(rig, before["revision"])
    assert not rig.writes


def test_foreign_or_different_window_is_rejected(rig):
    with pytest.raises(ValueError, match="FL process"):
        inspect(rig, window_handle="999")
    before, _ = inspect(rig)
    with pytest.raises(ValueError, match="foreign"):
        act(rig, before["revision"], window_handle="2")
    assert not rig.writes


def test_process_membership_is_rechecked_before_snapshot(rig):
    before, _ = inspect(rig, window_handle="2")
    rig.child["handle"] = "3"
    with pytest.raises(ValueError, match="FL process"):
        act(rig, before["revision"], window_handle="2")
    assert not rig.writes


def test_ambiguous_semantic_selector_rejects_instead_of_using_first_match(rig):
    rig.tree.append({**rig.tree[0], "id": "duplicate"})
    before, _ = inspect(rig)
    with pytest.raises(ValueError, match="found 2"):
        act(rig, before["revision"])
    assert not rig.writes


def test_duplicate_non_actionable_label_does_not_hide_unique_actionable_match(rig):
    rig.tree = [dict(id="browser-pane", name="Browser", type="Pane", enabled=True,
                     click=[127, 555, 0], children=[
                         dict(id="browser-label", name="Browser", type="Pane", enabled=True)])]
    before, _ = inspect(rig)
    result = act(rig, before["revision"],
                 dict(kind="click", selector=dict(name="Browser", control_type="Pane")),
                 apply=False)
    assert result["status"] == "preflighted"
    assert not rig.writes and not Path(rig.receipt).exists()


@pytest.mark.parametrize("missing", ["click", "id", "enabled"])
def test_selector_rejects_when_no_matching_element_is_actionable(missing):
    node = dict(id="label", name="Browser", type="Pane", enabled=True, click=[127, 555, 0])
    del node[missing]
    with pytest.raises(ValueError, match="actionable.*found 0"):
        ui._select([node], dict(name="Browser", control_type="Pane"))


def test_disabled_duplicate_does_not_make_enabled_target_ambiguous():
    node = dict(id="active", name="Browser", type="Pane", enabled=True, click=[127, 555, 0])
    disabled = {**node, "id": "disabled", "enabled": False}
    assert ui._select([disabled, node], dict(name="Browser", control_type="Pane")) is node


@pytest.mark.parametrize("recording", [False, True])
def test_transport_prevents_all_edits(rig, recording):
    rig.project["is_recording" if recording else "is_playing"] = True
    before, _ = inspect(rig)
    with pytest.raises(ValueError, match="Stop playback"):
        act(rig, before["revision"])
    assert not rig.writes


def test_coordinate_fallback_needs_visual_observation(rig):
    before, _ = inspect(rig)
    with pytest.raises(ValueError, match="include_screenshot"):
        act(rig, before["revision"], dict(kind="click", x=10, y=10))
    assert not rig.writes


def test_coordinates_are_window_relative_and_checked_against_absolute_screen(rig):
    before, _ = inspect(rig, include_screenshot=True)
    result = act(rig, before["revision"], dict(kind="click", x=10, y=20, button="right"))
    assert result["status"] == "dispatched_unverified"
    name, args = rig.writes[0]
    assert name == "mouse_control" and args["action"] == "right_click"
    assert (args["x"], args["y"]) == (10, 20) and args["expectedProcessName"] == "FL64"


@pytest.mark.parametrize("action", [dict(kind="click", x=10, y=20),
                                    dict(kind="drag", x=10, y=20, end_x=30, end_y=20),
                                    dict(kind="scroll", x=10, y=20, direction="down")])
def test_pixel_only_change_blocks_coordinate_actions(rig, action):
    before, _ = inspect(rig, include_screenshot=True)
    rig.image = ImageContent(type="image", data="bmV3LXBpeGVscw==", mimeType="image/png")
    with pytest.raises(ValueError, match="screenshot changed"):
        act(rig, before["revision"], action)
    assert not rig.writes and not Path(rig.receipt).exists()


def test_semantic_action_does_not_require_unchanged_pixels(rig):
    before, _ = inspect(rig, include_screenshot=True)
    rig.screenshot = False  # No second screenshot should be requested.
    result = act(rig, before["revision"])
    assert result["status"] == "dispatched_unverified" and len(rig.writes) == 1


def test_focus_stolen_mid_observation_rejects_without_dispatch(rig):
    before, _ = inspect(rig)
    rig.focus_after_snapshot = dict(foreground="99", focus="99")
    with pytest.raises(RuntimeError, match="changed during observation"):
        act(rig, before["revision"])
    assert not rig.writes and not Path(rig.receipt).exists()


@pytest.mark.parametrize("kind", ["coordinate", "semantic", "drag"])
def test_occlusion_anywhere_along_action_prevents_input(rig, kind):
    before, _ = inspect(rig, include_screenshot=True)
    if kind == "coordinate":
        rig.block_point = (110, 220)
        action = dict(kind="click", x=10, y=20)
    elif kind == "semantic":
        rig.block_point = (250, 350)
        action = dict(kind="click", selector=dict(name="Volume"))
    else:
        rig.block_point = (115, 220)  # Middle of path, not either endpoint.
        action = dict(kind="drag", x=10, y=20, end_x=20, end_y=20)
    with pytest.raises(RuntimeError, match="occluded"):
        act(rig, before["revision"], action)
    assert not rig.writes and not Path(rig.receipt).exists()


def test_outside_window_coordinate_is_rejected(rig):
    before, _ = inspect(rig, include_screenshot=True)
    with pytest.raises(ValueError, match="outside"):
        act(rig, before["revision"], dict(kind="click", x=800, y=20))
    assert not rig.writes


def test_keyboard_requires_target_to_own_focus(rig):
    rig.focus = dict(foreground="99", focus="99")
    before, _ = inspect(rig)
    with pytest.raises(ValueError, match="keyboard focus"):
        act(rig, before["revision"], dict(kind="key", key="enter"))
    assert not rig.writes


def test_keyboard_dispatch_is_one_explicit_key(rig):
    before, _ = inspect(rig)
    act(rig, before["revision"], dict(kind="key", key="tab", shift=True))
    assert rig.writes == [("keyboard_control", dict(windowHandle="1", action="press",
                                                  key="tab", modifiers="shift"))]


def test_timeout_records_unknown_and_never_retries(rig):
    before, _ = inspect(rig)
    rig.fail_dispatch = True
    result = act(rig, before["revision"])
    assert result["status"] == "unknown" and not result["success"]
    assert len(rig.writes) == 1
    assert json.loads(Path(rig.receipt).read_text())["status"] == "unknown"


def test_helper_without_positive_acknowledgement_is_unknown(rig):
    before, _ = inspect(rig)
    rig.dispatch_response = {}
    result = act(rig, before["revision"])
    assert result["status"] == "unknown" and not result["success"]
    assert result["success_scope"] is None and len(rig.writes) == 1


def test_failed_receipt_update_does_not_claim_success_or_retry(rig, monkeypatch):
    before, _ = inspect(rig)

    def fail_write(*args):
        raise OSError("receipt storage unavailable")

    monkeypatch.setattr(ui, "atomic_json", fail_write)
    result = act(rig, before["revision"])
    assert result["status"] == "unknown" and not result["success"]
    assert result["success_scope"] is None and len(rig.writes) == 1
    assert "receipt storage" in result["receipt_persistence_error"]
    assert json.loads(Path(rig.receipt).read_text())["status"] == "dispatching"


def test_receipt_collision_prevents_dispatch_and_preserves_file(rig):
    Path(rig.receipt).write_text("original")
    before, _ = inspect(rig)
    with pytest.raises(FileExistsError):
        act(rig, before["revision"])
    assert not rig.writes and Path(rig.receipt).read_text() == "original"


@pytest.mark.parametrize("action", [
    dict(kind="key", key="f4", modifiers="alt"), dict(kind="key", key="win"),
    dict(kind="type", text="hello\nworld"), dict(kind="type", text="ok", clear_first=1),
    dict(kind="click", x=True, y=1), dict(kind="click", x=-1, y=1),
    dict(kind="click", x=1, y=1, selector=dict(name="Volume")),
    dict(kind="click", selector={}), dict(kind="click", selector=dict(elementId="old")),
    dict(kind="scroll", x=1, y=1, direction="down", amount=100),
    dict(kind="arbitrary_tool", name="app"),
])
def test_invalid_actions_fail_without_observation_or_input(rig, action):
    with pytest.raises(ValidationError):
        act(rig, "0" * 64, action)
    assert not rig.calls and not rig.writes


def test_tool_exposes_native_image_blocks_and_structured_report(monkeypatch):
    async def fake_inspect(*args, **kwargs):
        assert kwargs == dict(include_ocr=False)
        return dict(success=True, status="observed", revision="a" * 64), [IMAGE]

    monkeypatch.setattr(tool_module, "inspect_ui", fake_inspect)
    mcp = FastMCP("studio-ui-test")
    tool_module.register_studio_ui_tools(mcp)

    async def run():
        async with Client(mcp) as client:
            return await client.call_tool("fl_ui_inspect", {"include_screenshot": True})

    result = asyncio.run(run())
    assert result.structured_content["status"] == "observed"
    assert any(block.type == "image" and block.data == IMAGE.data for block in result.content)


def test_popup_click_preserves_foreground_owner_without_activating_popup(rig):
    rig.child["class"] = "TQuickPopupMenuWindow"
    before, _ = inspect(rig, window_handle="2", include_screenshot=True)
    result = act(rig, before["revision"], dict(kind="click", x=30, y=40),
                 window_handle="2")
    assert result["status"] == "dispatched_unverified"
    tool, args = rig.writes[-1]
    assert tool == "ui_batch" and args["windowHandle"] == "1"
    assert json.loads(args["steps"]) == [dict(
        action="mouse", mouseAction="click", x=50, y=60,
        expectedProcessName="FL64", expectedWindowTitle=rig.main["title"])]


def test_popup_with_different_foreground_owner_never_dispatches(rig):
    rig.child["class"] = "TQuickPopupMenuWindow"
    rig.focus["foreground"] = "2"
    before, _ = inspect(rig, window_handle="2", include_screenshot=True)
    with pytest.raises(ValueError, match="foreground main owner"):
        act(rig, before["revision"], dict(kind="click", x=30, y=40), window_handle="2")
    assert not rig.writes


def png_image(width=800, height=600):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(
            ">I", zlib.crc32(kind + data))

    data = b"\x89PNG\r\n\x1a\n"
    data += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    data += chunk(b"IDAT", zlib.compress((b"\x00" + b"\xff" * width * 3) * height))
    data += chunk(b"IEND", b"")
    return ImageContent(type="image", data=base64.b64encode(data).decode(), mimeType="image/png")


@pytest.fixture
def ocr_rig(rig, monkeypatch):
    rig.image = png_image()
    rig.ocr_result = dict(success=True, width=800, height=600, text="Cutoff", lines=[
        dict(text="Cutoff", bounds=[20, 30, 50, 15], words=[
            dict(text="Cutoff", bounds=[20, 30, 50, 15])])])
    rig.ocr_calls = []
    rig.on_ocr = None

    async def recognize(path):
        rig.ocr_calls.append(path)
        assert path.read_bytes() == base64.b64decode(rig.image.data)
        if rig.on_ocr:
            rig.on_ocr()
        return copy.deepcopy(rig.ocr_result)

    monkeypatch.setattr(ui, "_ocr_file", recognize)
    return rig


def test_ocr_uses_exact_capture_without_returning_image_or_coordinate_authority(ocr_rig):
    rig = ocr_rig
    # Preserve the pre-existing positional connection argument.
    report, images = asyncio.run(ui.inspect_ui("1", False, 6, rig.conn, include_ocr=True))
    assert report["ocr_included"] and not report["screenshot_included"] and images == []
    assert report["screenshot_sha256"] is None
    assert rig.captures == 1 and len(rig.ocr_calls) == 1 and not rig.ocr_calls[0].exists()
    assert report["ocr"]["coordinate_space"] == "window_relative"
    assert report["ocr"]["lines"][0]["words"][0]["bounds"] == [20, 30, 50, 15]
    assert report["ocr"]["image_size"] == [800, 600]
    assert not report["ocr"]["semantic_target_verified"]
    assert not ui._observations[report["revision"]]["screenshot"]
    with pytest.raises(ValueError, match="include_screenshot"):
        act(rig, report["revision"], dict(kind="click", x=25, y=35))
    assert not rig.writes


def test_ocr_and_native_vision_share_one_capture(ocr_rig):
    report, images = inspect(ocr_rig, include_ocr=True, include_screenshot=True)
    assert images == [ocr_rig.image] and report["screenshot_included"]
    assert report["ocr"]["text"] == "Cutoff" and ocr_rig.captures == 1
    assert not ocr_rig.writes


@pytest.mark.parametrize("kind", ["png_size", "ocr_size", "invalid_base64", "non_png",
                                  "wrong_type", "failed_ocr", "missing_lines", "missing_text",
                                  "invalid_line", "invalid_word", "outside_line", "outside_word",
                                  "nan_bounds", "negative_bounds", "zero_bounds"])
def test_invalid_ocr_or_image_geometry_never_creates_revision(ocr_rig, kind):
    rig = ocr_rig
    line = rig.ocr_result["lines"][0]
    if kind == "png_size":
        rig.image = png_image(799, 600)
    elif kind == "ocr_size":
        rig.ocr_result["width"] = 799
    elif kind == "invalid_base64":
        rig.image = ImageContent(type="image", data="!", mimeType="image/png")
    elif kind == "non_png":
        rig.image = IMAGE
    elif kind == "wrong_type":
        rig.image = ImageContent(type="image", data=rig.image.data, mimeType="image/jpeg")
    elif kind == "failed_ocr":
        rig.ocr_result["success"] = False
    elif kind == "missing_lines":
        rig.ocr_result["lines"] = None
    elif kind == "missing_text":
        del rig.ocr_result["text"]
    elif kind == "invalid_line":
        line["text"] = False
    elif kind == "invalid_word":
        line["words"][0]["text"] = 5
    elif kind == "outside_line":
        line["bounds"] = [790, 30, 50, 15]
    elif kind == "outside_word":
        line["words"][0]["bounds"] = [20, 595, 50, 15]
    elif kind == "nan_bounds":
        line["bounds"][0] = float("nan")
    elif kind == "negative_bounds":
        line["bounds"][0] = -1
    else:
        line["bounds"][2] = 0
    with pytest.raises(RuntimeError, match="OCR"):
        inspect(rig, include_ocr=True)
    assert not ui._observations and not rig.writes


@pytest.mark.parametrize("changed", ["focus", "project", "census"])
def test_changes_during_ocr_reject_the_whole_observation(ocr_rig, changed):
    rig = ocr_rig

    def change():
        if changed == "focus":
            rig.focus = dict(foreground="99", focus="99")
        elif changed == "project":
            rig.project["context"]["project_generation"] += 1
        else:
            rig.main["bounds"][0] += 1

    rig.on_ocr = change
    with pytest.raises(RuntimeError, match="changed during observation"):
        inspect(rig, include_ocr=True)
    assert not ui._observations and not rig.writes
    assert not rig.ocr_calls[0].exists()


@pytest.mark.parametrize("limit", ["lines", "words", "characters"])
def test_ocr_output_bounds_are_explicit_and_report_truncation(ocr_rig, monkeypatch, limit):
    rig = ocr_rig
    rig.ocr_result["lines"] *= 3
    limits = dict(ui.OCR_LIMITS)
    limits[limit] = 12 if limit == "characters" else 1
    monkeypatch.setattr(ui, "OCR_LIMITS", limits)
    report, _ = inspect(rig, include_ocr=True)
    ocr = report["ocr"]
    assert ocr["truncated"] and ocr["total_lines"] == 3 and ocr["total_words"] == 3
    assert ocr["included_words"] == 1 and len(ocr["lines"]) == 1
    assert ocr["text"] == "Cutoff" and ocr["limits"] == limits


def test_tool_forwards_ocr_option_and_returns_only_structured_text(monkeypatch):
    seen = []

    async def fake_inspect(*args, **kwargs):
        seen.append((args, kwargs))
        return dict(success=True, ocr=dict(text="Owner drawn label")), []

    monkeypatch.setattr(tool_module, "inspect_ui", fake_inspect)
    mcp = FastMCP("studio-ui-ocr-test")
    tool_module.register_studio_ui_tools(mcp)

    async def run():
        async with Client(mcp) as client:
            return await client.call_tool("fl_ui_inspect", {"include_ocr": True})

    result = asyncio.run(run())
    assert seen == [((None, False, 6), dict(include_ocr=True))]
    assert result.structured_content["ocr"]["text"] == "Owner drawn label"
    assert all(block.type == "text" for block in result.content)


def test_ocr_option_requires_real_boolean(rig):
    with pytest.raises(ValueError, match="boolean"):
        inspect(rig, include_ocr=1)
    assert not rig.calls and not rig.captures
