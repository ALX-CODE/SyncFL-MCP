"""Black-box checks for the replacement basic tools and controller routes."""

import ast
import json
import math
import runpy
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from fl_studio_mcp.tools import channels, mixer, plugins, transport
from fl_studio_mcp.utils import connection, midi_connection, tool_results


@pytest.fixture
def basic(monkeypatch):
    recorded = []
    reply = {"success": True, "value": 0.5}
    session = SimpleNamespace(
        send_command=lambda action, params: recorded.append((action, params)) or dict(reply)
    )
    monkeypatch.setattr(tool_results, "get_connection", lambda: session)
    functions = {}

    def tool():
        def decorate(fn):
            functions[fn.__name__] = fn
            return fn

        return decorate

    registry = SimpleNamespace(tool=tool)
    for module, name in (
        (channels, "channel"),
        (mixer, "mixer"),
        (plugins, "plugin"),
        (transport, "transport"),
    ):
        getattr(module, "register_" + name + "_tools")(registry)
    return SimpleNamespace(calls=recorded, reply=reply, tools=functions)


@pytest.mark.parametrize(
    "tool,kwargs",
    [
        ("fl_set_channel_volume", {"index": 0, "volume": True}),
        ("fl_set_track_volume", {"track": 0, "volume": float("nan")}),
        ("fl_set_track_pan", {"track": -1, "pan": 0}),
        ("fl_set_song_position", {"position": 0.5, "mode": True}),
        ("fl_set_plugin_param_value", {"param_index": 0, "value": True, "plugin_index": 0}),
        ("fl_set_plugin_param_value", {"param_index": 0, "value": float("inf"), "plugin_index": 0}),
        ("fl_set_track_color", {"track": 0, "red": 256, "green": 0, "blue": 0}),
        ("fl_set_step_sequence", {"channel": 0, "pattern": [True, 1]}),
        ("fl_trigger_note", {"channel": 0, "note": 60, "midi_channel": 16}),
    ],
)
def test_invalid_writes_do_not_reach_transport(basic, tool, kwargs):
    assert basic.tools[tool](**kwargs).startswith("Error:")
    assert not basic.calls


@pytest.mark.parametrize(
    "result",
    [
        {"success": False, "error": "No acknowledgement", "outcome": "unknown"},
        {"error": "Disconnected"},
        {"success": True, "error": "Host refused"},
    ],
)
def test_failed_mutation_is_never_reported_as_success(basic, result):
    basic.reply.clear()
    basic.reply.update(result)
    assert basic.tools["fl_set_track_volume"](0, 0.8).startswith("Error:")
    assert len(basic.calls) == 1


def test_existing_plugin_address_contract_and_readback(basic):
    basic.reply.update(name="Threshold", value=0.23, value_string="-18 dB")
    result = basic.tools["fl_set_plugin_param_value"](3, 0.23, 4, 2, False)
    assert basic.calls == [
        (
            "plugins.setParamValue",
            {
                "param_index": 3,
                "value": 0.23,
                "plugin_index": 4,
                "slot_index": 2,
                "use_global": False,
            },
        )
    ]
    assert "-18 dB" in result and "0.23" in result


def test_error_sentinel_contract_for_list_count_and_boolean(basic):
    basic.reply.clear()
    basic.reply.update(success=False, error="offline")
    assert basic.tools["fl_get_mixer_track_count"]() == -1
    assert basic.tools["fl_is_plugin_valid"](0) is False
    assert basic.tools["fl_get_all_channels"]() == [{"error": "offline"}]


def test_connection_api_has_one_owner(monkeypatch):
    session = object()
    monkeypatch.setattr(midi_connection, "get_connection", lambda: session)
    assert connection.get_connection() is session
    assert connection.FLConnection is midi_connection.MIDIConnection


def controller(**modules):
    source = Path(__file__).resolve().parents[1] / "fl_controller/device_FLStudioMCP.py"
    parsed = ast.parse(source.read_text(encoding="utf-8"))
    functions = ast.Module(
        body=[node for node in parsed.body if isinstance(node, ast.FunctionDef)], type_ignores=[]
    )
    namespace = dict(math=math, **modules)
    exec(compile(functions, str(source), "exec"), namespace)
    return namespace


def test_controller_rejects_out_of_range_target_before_write():
    writes = []
    ns = controller(
        channels=SimpleNamespace(channelCount=lambda global_index: 2),
        mixer=SimpleNamespace(
            getTrackInfo=lambda mode: 3, setTrackVolume=lambda *args: writes.append(args)
        ),
    )
    with pytest.raises(ValueError, match="host range"):
        ns["dispatch_command"]("mixer.setTrackVolume", {"track": 4, "volume": 0.8})
    assert not writes


def test_controller_preflights_entire_step_sequence():
    writes = []
    ns = controller(
        channels=SimpleNamespace(
            channelCount=lambda global_index: 2, setGridBit=lambda *args: writes.append(args)
        )
    )
    with pytest.raises(ValueError, match="boolean steps"):
        ns["dispatch_command"](
            "channels.setStepSequence", {"channel": 0, "pattern": [True, False, 1]}
        )
    assert not writes


def test_plugin_parameter_bounds_checked_before_write():
    writes = []
    ns = controller(
        channels=SimpleNamespace(channelCount=lambda global_index: 1),
        plugins=SimpleNamespace(
            getParamCount=lambda *args: 2, setParamValue=lambda *args: writes.append(args)
        ),
    )
    with pytest.raises(ValueError, match="exposed controls"):
        ns["dispatch_command"](
            "plugins.setParamValue", {"plugin_index": 0, "param_index": 2, "value": 0.5}
        )
    assert not writes


@pytest.mark.parametrize("kind", ["mixer", "channels"])
def test_color_writes_use_rgb_byte_order(kind):
    writes = []
    ns = controller(
        channels=SimpleNamespace(
            channelCount=lambda global_index: 2,
            setChannelColor=lambda *args: writes.append(args),
        ),
        mixer=SimpleNamespace(
            getTrackInfo=lambda mode: 2,
            setTrackColor=lambda *args: writes.append(args),
        ),
    )
    if kind == "mixer":
        ns["handle_mixer_get_track_info"] = lambda p: {"name": "Test"}
        action, field = "mixer.setTrackColor", "track"
        expected = (1, 0x12ABEF)
    else:
        ns["handle_channels_get_info"] = lambda p: {"name": "Test", "target_fx_track": 0}
        action, field = "channels.setColor", "index"
        expected = (1, 0x12ABEF, True)
    ns["dispatch_command"](action, {field: 1, "r": 0x12, "g": 0xAB, "b": 0xEF})
    assert writes == [expected]


def test_plugin_write_bypasses_pickup_and_preserves_local_indexing():
    state = {"value": 0.1}
    writes = []

    def apply(value, parameter, index, slot, pickup, global_index):
        writes.append((value, parameter, index, slot, pickup, global_index))
        state["value"] = value

    ns = controller(
        channels=SimpleNamespace(channelCount=lambda global_index: 2),
        plugins=SimpleNamespace(
            getParamCount=lambda *args: 4,
            getParamName=lambda *args: "Threshold",
            getParamValue=lambda *args: state["value"],
            getParamValueString=lambda *args: "-18 dB",
            setParamValue=apply,
        ),
    )
    result = ns["dispatch_command"](
        "plugins.setParamValue",
        {
            "plugin_index": 1,
            "param_index": 3,
            "value": 0.6,
            "slot_index": -1,
            "use_global": False,
        },
    )
    assert writes == [(0.6, 3, 1, -1, 0, False)]
    assert result["value"] == 0.6 and result["value_string"] == "-18 dB"


def test_complete_controller_bootstrap_and_midi_envelope(tmp_path, monkeypatch):
    """Load every companion, then run a real file-envelope callback with fake host APIs."""
    import sys

    names = "channels mixer plugins transport patterns playlist ui general arrangement midi".split()
    host = {name: SimpleNamespace() for name in names}
    host["general"].getVersion = lambda: 45
    for name, module in host.items():
        monkeypatch.setitem(sys.modules, name, module)
    source = Path(__file__).resolve().parents[1] / "fl_controller/device_FLStudioMCP.py"
    loaded = runpy.run_path(str(source))
    # Functions retain the exec namespace rather than runpy's returned copy.
    namespace = loaded["OnMidiMsg"].__globals__
    namespace["COMMAND_FILE"] = tmp_path / "request.json"
    namespace["RESPONSE_FILE"] = tmp_path / "reply.json"
    namespace["COMMAND_FILE"].write_text(
        json.dumps(
            {
                "protocol": 2,
                "id": "bootstrap-test",
                "expires_at": time.time() + 20,
                "action": "bridge.receipt",
                "params": {"id": "missing"},
            }
        )
    )
    event = SimpleNamespace(midiId=0x90, data1=127, data2=127, handled=False, pmeflags=8)
    loaded["OnMidiMsg"](event)
    result = json.loads(namespace["RESPONSE_FILE"].read_text())
    assert event.handled and result["success"] is True
    assert result["protocol"] == 2 and result["id"] == "bootstrap-test"
    assert result["available"] is False


def test_public_package_exports_preserve_existing_imports():
    import fl_studio_mcp.tools as tools
    import fl_studio_mcp.utils as utils

    assert utils.FLConnection is midi_connection.MIDIConnection
    assert utils.get_connection is connection.get_connection
    assert tools.register_transport_tools is transport.register_transport_tools
    assert tools.register_plugin_tools is plugins.register_plugin_tools
    with pytest.raises(AttributeError):
        getattr(utils, "does_not_exist")


def test_mac_trigger_does_not_report_a_failed_shortcut_as_success(monkeypatch):
    from fl_studio_mcp.utils import fl_trigger

    monkeypatch.setenv("FL_MCP_INTERACTION_POLICY", "interactive")
    monkeypatch.setattr(fl_trigger.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        fl_trigger.subprocess, "run", lambda *args, **kw: SimpleNamespace(returncode=1)
    )
    assert fl_trigger.FLStudioTrigger().trigger() is False


def test_windows_trigger_releases_modifiers_if_key_tap_fails(monkeypatch):
    import sys
    from contextlib import contextmanager

    from fl_studio_mcp.utils import fl_trigger

    held = []

    class Keyboard:
        @contextmanager
        def pressed(self, modifier):
            held.append(modifier)
            try:
                yield
            finally:
                held.remove(modifier)

        def tap(self, key):
            assert held == ["ctrl", "alt"]
            raise OSError("injected shortcut failure")

    monkeypatch.setenv("FL_MCP_INTERACTION_POLICY", "interactive")
    monkeypatch.setattr(fl_trigger.platform, "system", lambda: "Windows")
    monkeypatch.setattr(fl_trigger.FLStudioTrigger, "_focus_fl_studio_windows", lambda self: True)
    keyboard = SimpleNamespace(Controller=Keyboard, Key=SimpleNamespace(ctrl="ctrl", alt="alt"))
    monkeypatch.setitem(sys.modules, "pynput.keyboard", keyboard)
    assert fl_trigger.FLStudioTrigger().trigger() is False
    assert held == []


@pytest.mark.parametrize("delay", [True, -1, float("nan"), float("inf")])
def test_invalid_trigger_delay_never_injects_input(monkeypatch, delay):
    from fl_studio_mcp.utils import fl_trigger

    monkeypatch.setenv("FL_MCP_INTERACTION_POLICY", "interactive")
    trigger = fl_trigger.FLStudioTrigger()
    calls = []
    trigger._trigger_func = lambda: calls.append(True)
    with pytest.raises(ValueError):
        trigger.trigger(delay)
    assert not calls
