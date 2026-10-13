"""Automatic script selection guards using fake FL state, never desktop input."""

import asyncio
import itertools
import json
import time
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from fl_studio_mcp.utils import piano_bridge
from fl_studio_mcp.utils import piano_ready as ready
from fl_studio_mcp.utils.interaction_policy import require_desktop_input


@pytest.mark.parametrize("caption", [
    "Piano roll - MCP Test Synth", "  Piano roll -  MCP  Test Synth   ",
    "Piano roll - MCP Test Synth ›",
    "C Q Piano roll - MCP Test Synth •",
])
def test_caption_requires_exact_visible_channel(caption):
    ready.check_caption(caption, "MCP Test Synth")


@pytest.mark.parametrize("caption", [
    "Piano roll - Other Synth", "MCP Test Synth", "Piano roll - MCP Test Synth 2", "",
])
def test_wrong_or_unreadable_caption_is_rejected(caption):
    with pytest.raises(RuntimeError, match="does not match"):
        ready.check_caption(caption, "MCP Test Synth")


def menu_lines(*labels):
    return {"lines": [{"text": label, "bounds": [100, 300 + index * 25, 150, 20]}
                      for index, label in enumerate(labels)]}


def test_menu_requires_exact_unique_script_and_tools_context():
    assert ready.compose_point(menu_lines("Run last script again", "ComposeWithLLM")) == (175, 335)
    assert ready.compose_point(menu_lines("Run last script agam", "ComposeWithLLM")) == (175, 335)


@pytest.mark.parametrize("labels", [
    ("ComposeWithLLM",), ("Run last script again", "ComposeWithLLM copy"),
    ("Run last script again", "ComposeWithLLM", "ComposeWithLLM"),
    ("Run last script again", "OtherScript"), (),
])
def test_unknown_or_ambiguous_menu_is_rejected(labels):
    with pytest.raises(RuntimeError, match="uniquely read"):
        ready.compose_point(menu_lines(*labels))


@pytest.fixture
def selector(tmp_path, monkeypatch):
    scripts = tmp_path / "Piano roll scripts"
    scripts.mkdir()
    for filename in ("ComposeWithLLM.pyscript", "mcp_note_protocol.py"):
        (scripts / filename).write_text("fixture")
    monkeypatch.setattr(ready, "get_fl_settings_dir", lambda: tmp_path)
    state = SimpleNamespace(identity=(3, "MCP Test Synth", 1, "100", "Test.flp - FL Studio"),
                            popup=False, playing=False)
    request = {"protocol": 3, "id": "a" * 32, "requests": [],
               "expires_at": time.time() + 45, "target_identity": list(state.identity)}
    request_path = scripts / "mcp_request.json"
    request_path.write_text(json.dumps(request))
    identity = Mock(side_effect=lambda: state.identity)
    monkeypatch.setattr(ready, "selected_identity", identity)

    def call(function, *args, **kwargs):
        if function == "transport.isPlaying":
            return state.playing
        if function == "ui.isInPopupMenu":
            return state.popup
        if function == "ui.closeActivePopupMenu":
            state.popup = False
        return None

    native_call = Mock(side_effect=call)
    monkeypatch.setattr(ready, "call", native_call)
    native = Mock()
    monkeypatch.setattr(ready, "native", native)
    ownership = Mock(return_value=True)
    monkeypatch.setattr(ready, "point_belongs_to", ownership)
    roll = {"handle": "200", "bounds": [50, 100, 1200, 900], "dpi": 96}
    monkeypatch.setattr(ready, "unique_control", Mock(return_value=roll))
    visible_windows = [
        {"handle": "100", "pid": 77, "class": "TFruityLoopsMainForm",
         "parent": "0", "enabled": True, "bounds": [0, 0, 1920, 1080]},
        {"handle": "300", "pid": 77, "class": "TQuickPopupMenuWindow",
         "parent": "100", "enabled": True, "bounds": [50, 110, 450, 700]},
    ]
    monkeypatch.setattr(ready, "windows", lambda: visible_windows)
    header = {"lines": [
        {"text": "Piano roll - MCP Test Synth", "bounds": [60, 104, 300, 18]},
        {"text": "Unrelated lower toolbar text", "bounds": [60, 136, 300, 18]},
    ]}
    menu = menu_lines("Run last script again", "ComposeWithLLM")
    ocr = AsyncMock(side_effect=[header, menu])
    monkeypatch.setattr(ready, "read_region", ocr)

    class FakeFL:
        handle = "100"

        def __init__(self):
            self.win = AsyncMock(return_value={"success": True})
            self.key = AsyncMock()
            self._project_guard = AsyncMock()
            self.click = AsyncMock(side_effect=self.clicked)

        async def clicked(self, *args, **kwargs):
            state.popup = not state.popup

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    fl = FakeFL()
    monkeypatch.setattr(ready, "WindowsFL", lambda: fl)
    return SimpleNamespace(fl=fl, state=state, ocr=ocr, header=header, menu=menu,
                           call=native_call, native=native, identity=identity, ownership=ownership,
                           scripts=scripts, roll=roll, request=request, request_path=request_path,
                           visible_windows=visible_windows)


def dispatch():
    return asyncio.run(ready._dispatch())


def test_selector_refreshes_channel_opens_toggle_proof_roll_and_dispatches_once(selector):
    assert dispatch() is True
    selector.fl.key.assert_awaited_once_with("f7")
    assert any(call.args == ("ui.showWindow", 3) and call.kwargs == {"write": True}
               for call in selector.call.call_args_list)
    assert selector.ocr.await_args_list[0].args[1] == [400, 100, 810, 70]
    assert selector.ocr.await_args_list[0].kwargs == {"scale": 3}
    # The caption check includes the title strip only, despite the taller OCR image.
    assert selector.fl.click.await_count == 2
    assert selector.fl.click.await_args_list[0].args == (35, 12)
    assert selector.fl.click.await_args_list[1].args == (125, 235)
    assert selector.fl.click.await_args_list[1].kwargs == {"handle": "200"}
    assert not selector.state.popup
    assert selector.ownership.call_args_list[-1].args == ((175, 335), "300")


@pytest.mark.parametrize("field,value", [
    ("class", "OtherPopup"), ("parent", "999"), ("pid", 88), ("enabled", False),
    ("bounds", [900, 900, 100, 100]),
])
def test_popup_identity_mismatch_never_dispatches(selector, field, value):
    selector.visible_windows[1][field] = value
    with pytest.raises(RuntimeError, match="Cannot identify FL's piano-roll Tools popup"):
        dispatch()
    assert selector.fl.click.await_count == 1


def test_obscured_piano_roll_is_raised_before_tools_click(selector):
    selector.ownership.side_effect = [False, True, True]
    assert dispatch() is True
    assert any(call.kwargs == {"action": "activate", "handle": "200"}
               for call in selector.fl.win.await_args_list)


def test_obscured_popup_is_raised_before_script_selection(selector):
    selector.ownership.side_effect = [True, False, True]
    assert dispatch() is True
    assert any(call.kwargs == {"action": "activate", "handle": "300"}
               for call in selector.fl.win.await_args_list)


def test_caption_crop_excludes_toolbar_icons_at_scaled_and_negative_origins():
    assert ready.caption_region([262, 82, 1658, 946], 1) == [612, 82, 850, 70]
    assert ready.caption_region([-1920, 100, 1800, 900], 1.5) == [-1395, 100, 1215, 105]


def test_delayed_menu_is_observed_without_repeating_clicks(selector):
    selector.ocr.side_effect = [selector.header, {"lines": []}, selector.menu]
    assert dispatch() is True
    assert selector.fl.click.await_count == 2
    assert selector.ocr.await_count == 3
    assert not selector.state.popup


def test_missing_component_never_opens_fl(selector):
    (selector.scripts / "mcp_note_protocol.py").unlink()
    with pytest.raises(RuntimeError, match="component is missing"):
        dispatch()
    selector.fl.win.assert_not_awaited()
    selector.fl.click.assert_not_awaited()


@pytest.mark.parametrize("attribute,message", [
    ("playing", "Stop playback"), ("popup", "active FL popup"),
])
def test_playback_or_existing_popup_stops_before_ui(selector, attribute, message):
    setattr(selector.state, attribute, True)
    with pytest.raises(RuntimeError, match=message):
        dispatch()
    selector.fl.win.assert_not_awaited()
    selector.fl.key.assert_not_awaited()
    selector.fl.click.assert_not_awaited()


def test_wrong_visible_channel_never_clicks_tools(selector):
    selector.header["lines"][0]["text"] = "Piano roll - Other Synth"
    selector.ocr.side_effect = [selector.header, selector.header]
    with pytest.raises(RuntimeError, match="does not match"):
        dispatch()
    selector.fl.click.assert_not_awaited()


def test_stale_roll_is_reopened_on_bound_channel_before_script(selector):
    stale = {"lines": [{"text": "Piano roll - Other Synth", "bounds": [60, 104, 300, 18]}]}
    selector.ocr.side_effect = [stale, selector.header, selector.menu]
    assert dispatch() is True
    selector.native.assert_called_once_with("channels.selectOne", {"index": 3})
    assert any(call.args == ("ui.hideWindow", 3) for call in selector.call.call_args_list)
    assert selector.fl.click.await_count == 2


def test_obscured_tools_button_never_clicks(selector):
    selector.ownership.return_value = False
    with pytest.raises(RuntimeError, match="Tools button is obscured"):
        dispatch()
    selector.fl.click.assert_not_awaited()


def test_unknown_menu_never_selects_script_and_closes_owned_menu(selector):
    selector.ocr.side_effect = itertools.chain(
        [selector.header], itertools.repeat(menu_lines("Run last script again", "Other"))
    )
    with pytest.raises(RuntimeError, match="uniquely read"):
        dispatch()
    assert selector.fl.click.await_count == 1
    assert not selector.state.popup
    assert any(call.args == ("ui.closeActivePopupMenu",)
               for call in selector.call.call_args_list)


def test_changed_selection_after_ocr_never_clicks_or_closes_new_context(selector):
    async def read(fl, region, scale=1):
        if region[3] == 70:
            return selector.header
        selector.state.identity = (4, "Other Synth", 1)
        return selector.menu

    selector.ocr.side_effect = read
    with pytest.raises(RuntimeError, match="target or menu changed"):
        dispatch()
    assert selector.fl.click.await_count == 1
    assert selector.state.popup
    assert not any(call.args == ("ui.closeActivePopupMenu",)
                   for call in selector.call.call_args_list)


def test_changed_selection_during_final_project_guard_never_dispatches(selector):
    async def guard():
        if selector.fl._project_guard.await_count == 2:
            selector.state.identity = (4, "Other Synth", 1)

    selector.fl._project_guard.side_effect = guard
    with pytest.raises(RuntimeError, match="target or menu changed"):
        dispatch()
    assert selector.fl.click.await_count == 1


def test_menu_item_obscured_after_ocr_never_dispatches(selector):
    selector.ownership.side_effect = lambda point, handle: handle == "200"
    with pytest.raises(RuntimeError, match="menu item is obscured"):
        dispatch()
    assert selector.fl.click.await_count == 1 and not selector.state.popup


@pytest.fixture
def prepare(monkeypatch):
    # Isolate retired script mechanics; the public boundary test restores the guard.
    monkeypatch.setattr(ready, "require_desktop_input", lambda *args: None)
    monkeypatch.setenv("FL_MCP_INTERACTION_POLICY", "interactive")
    queue = []
    bridge = SimpleNamespace(
        recover_expired_read=Mock(return_value={"recovered": False, "reason": "empty"}),
        read_queue=Mock(return_value=queue), exchange=Mock(return_value={
        "id": "a" * 32, "state": {"noteCount": 0, "revision": "fresh-revision"}}))

    @contextmanager
    def session():
        yield bridge

    session_factory = Mock(side_effect=session)
    monkeypatch.setattr(piano_bridge, "piano_session", session_factory)
    identity = Mock(return_value=(3, "MCP Test Synth", 1, "100", "Test.flp - FL Studio"))
    monkeypatch.setattr(ready, "selected_identity", identity)
    native = Mock(return_value={})
    monkeypatch.setattr(ready, "native", native)
    call = Mock(side_effect=lambda function, *args, **kwargs:
                10 if function == "channels.channelCount" else "MCP Test Synth")
    monkeypatch.setattr(ready, "call", call)
    return SimpleNamespace(bridge=bridge, session=session_factory, identity=identity,
                           native=native, call=call, queue=queue)


def test_background_prepare_does_not_select_channel_or_touch_request_queue(prepare, monkeypatch):
    monkeypatch.setattr(ready, "require_desktop_input", require_desktop_input)
    monkeypatch.delenv("FL_MCP_INTERACTION_POLICY", raising=False)
    with pytest.raises(RuntimeError, match="unsupported in background"):
        ready.prepare_piano(3, "MCP Test Synth")
    prepare.session.assert_not_called()
    prepare.native.assert_not_called()
    prepare.call.assert_not_called()
    prepare.bridge.recover_expired_read.assert_not_called()
    prepare.bridge.exchange.assert_not_called()


@pytest.mark.parametrize("channel", [-1, True, 1.5, "3"])
def test_invalid_channel_index_never_enters_session(prepare, channel):
    with pytest.raises(ValueError, match="nonnegative global index"):
        ready.prepare_piano(channel)
    prepare.session.assert_not_called()
    prepare.native.assert_not_called()


def test_prepare_preserves_pending_queue_without_selecting_channel(prepare):
    pending = {"id": "existing", "requests": [{"action": "clear"}]}
    prepare.bridge.read_queue.return_value = pending
    with pytest.raises(RuntimeError, match="Unresolved piano request"):
        ready.prepare_piano(3, "MCP Test Synth")
    assert prepare.bridge.read_queue.return_value == pending
    prepare.call.assert_not_called()
    prepare.native.assert_not_called()
    prepare.bridge.exchange.assert_not_called()


def test_prepare_name_mismatch_never_changes_selection(prepare):
    with pytest.raises(ValueError, match="name differs"):
        ready.prepare_piano(3, "Other Synth")
    prepare.native.assert_not_called()
    prepare.bridge.exchange.assert_not_called()


def test_prepare_requires_requested_index_after_native_selection(prepare):
    # Duplicate channel names must not hide a selection failure.
    prepare.identity.return_value = (4, "MCP Test Synth", 1)
    with pytest.raises((ValueError, RuntimeError), match="[Cc]hannel|[Ss]elect"):
        ready.prepare_piano(3, "MCP Test Synth")
    prepare.bridge.exchange.assert_not_called()


def test_prepare_rejects_out_of_range_channel_before_native_selection(prepare):
    with pytest.raises((ValueError, RuntimeError), match="[Cc]hannel|[Rr]ange|index"):
        ready.prepare_piano(10)
    prepare.native.assert_not_called()
    prepare.bridge.exchange.assert_not_called()


def test_prepare_verifies_one_read_only_exchange_and_returns_identity(prepare):
    result = ready.prepare_piano(3, "MCP Test Synth")
    prepare.native.assert_called_once_with("channels.selectOne", {"index": 3})
    prepare.bridge.exchange.assert_called_once_with()
    assert result == {
        "success": True, "ready": True, "script": "ComposeWithLLM", "channel": 3,
        "channel_name": "MCP Test Synth", "pattern": 1, "note_count": 0,
        "revision": "fresh-revision", "request_id": "a" * 32,
        "verification": "correlated_read_only_score",
    }


@pytest.mark.parametrize("channel", [None, -1, True, "3"])
def test_selected_identity_rejects_invalid_native_channel(monkeypatch, channel):
    native = Mock(return_value=channel)
    monkeypatch.setattr(ready, "call", native)
    with pytest.raises(RuntimeError, match="Select a Channel Rack"):
        ready.selected_identity()
    native.assert_called_once_with("channels.selectedChannel", True, 0, True)


def test_unbound_legacy_pending_mutation_never_opens_roll(selector):
    request = dict(selector.request, requests=[{"action": "clear"}])
    del request["target_identity"]
    selector.request_path.write_text(json.dumps(request))
    before = selector.request_path.read_text()
    with pytest.raises(RuntimeError, match="not bound"):
        dispatch()
    selector.fl.win.assert_not_awaited()
    selector.fl.key.assert_not_awaited()
    selector.fl.click.assert_not_awaited()
    assert selector.request_path.read_text() == before


@pytest.mark.parametrize("field,value", [
    (0, 4), (1, "Other Synth"), (2, 2), (3, "101"), (4, "Other.flp - FL Studio"),
])
def test_request_bound_to_different_channel_pattern_or_project_never_dispatches(
    selector, field, value
):
    target = list(selector.state.identity)
    target[field] = value
    selector.request_path.write_text(json.dumps(dict(selector.request, target_identity=target)))
    with pytest.raises(RuntimeError, match="not bound"):
        dispatch()
    selector.fl.win.assert_not_awaited()
    selector.fl.key.assert_not_awaited()
    selector.fl.click.assert_not_awaited()


@pytest.mark.parametrize("field,value", [
    ("id", "b" * 32), ("requests", [{"action": "clear"}]), ("expires_at", 0),
])
def test_request_changed_after_menu_capture_is_not_dispatched(selector, field, value):
    async def guard():
        if selector.fl._project_guard.await_count == 2:
            selector.request_path.write_text(json.dumps(dict(selector.request, **{field: value})))

    selector.fl._project_guard.side_effect = guard
    with pytest.raises(RuntimeError, match="request changed"):
        dispatch()
    assert selector.fl.click.await_count == 1 and not selector.state.popup
    assert json.loads(selector.request_path.read_text())[field] == value


def test_prepare_attempts_safe_read_recovery_before_pending_queue_check(prepare):
    events = []

    def recover():
        events.append("recover")
        return {"recovered": True, "id": "expired"}

    def queue():
        events.append("queue")
        return []

    prepare.bridge.recover_expired_read.side_effect = recover
    prepare.bridge.read_queue.side_effect = queue
    assert ready.prepare_piano(3, "MCP Test Synth")["ready"]
    assert events == ["recover", "queue"]


def test_selected_identity_includes_exact_project_window_and_title(monkeypatch):
    values = {"channels.selectedChannel": 3, "channels.getChannelName": "MCP Test Synth",
              "patterns.patternNumber": 8}
    call = Mock(side_effect=lambda function, *args, **kwargs: values[function])
    monkeypatch.setattr(ready, "call", call)
    monkeypatch.setattr(ready, "windows", Mock(return_value=[
        {"class": "TFruityLoopsMainForm", "handle": "555", "title": "Bound.flp - FL Studio"},
        {"class": "TPluginForm", "handle": "444", "title": "Plugin"},
    ]))
    assert ready.selected_identity() == (3, "MCP Test Synth", 8, "555", "Bound.flp - FL Studio")


@pytest.mark.parametrize("count", [0, 2])
def test_selected_identity_rejects_missing_or_ambiguous_fl_project(monkeypatch, count):
    call = Mock(return_value=3)
    monkeypatch.setattr(ready, "call", call)
    monkeypatch.setattr(ready, "windows", Mock(return_value=[
        {"class": "TFruityLoopsMainForm", "handle": str(index), "title": "Test.flp"}
        for index in range(count)
    ]))
    with pytest.raises(RuntimeError, match="exactly one FL project"):
        ready.selected_identity()
    call.assert_called_once_with("channels.selectedChannel", True, 0, True)
