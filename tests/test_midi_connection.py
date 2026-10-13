import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from fl_studio_mcp.utils import midi_connection


class Clock:
    def __init__(self):
        self.now = 10.0
        self.on_sleep = None
        self.sleeps = []

    def monotonic(self):
        return self.now

    def time(self):
        return 10000.0 + self.now - 10.0

    def sleep(self, seconds):
        assert seconds > 0
        self.sleeps.append(seconds)
        self.now += seconds
        if self.on_sleep:
            self.on_sleep()


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(midi_connection, "time", clock)
    monkeypatch.setattr(midi_connection, "_get_fl_hardware_dir", lambda: tmp_path)
    monkeypatch.setitem(
        sys.modules,
        "mido",
        SimpleNamespace(Message=lambda kind, **kwargs: {"kind": kind, **kwargs}),
    )
    lock_timeouts = []

    @contextmanager
    def lock(path, timeout):
        assert path == tmp_path / "mcp.lock"
        lock_timeouts.append(timeout)
        yield

    monkeypatch.setattr(midi_connection, "transaction_lock", lock)
    conn = midi_connection.MIDIConnection()
    conn._connected = True
    sends = []

    def send(message):
        sends.append(message)

    conn._port = SimpleNamespace(send=send)
    conn._port.send_wakeup = lambda: conn._port.send(
        {"kind": "note_on", "note": 127, "velocity": 127})

    def reply(**values):
        request = json.loads(conn._command_file.read_text(encoding="utf-8"))
        response = {"protocol": 2, "id": request["id"], "success": True, **values}
        conn._response_file.write_text(json.dumps(response), encoding="utf-8")

    return SimpleNamespace(
        conn=conn, clock=clock, sends=sends, lock_timeouts=lock_timeouts, reply=reply
    )


@pytest.fixture
def midi_backend(tmp_path, monkeypatch):
    monkeypatch.delenv("FL_MCP_MIDI_PORT", raising=False)
    monkeypatch.setattr(midi_connection, "_get_fl_hardware_dir", lambda: tmp_path)
    ports, opened, closed = [], [], []

    def open_output(name):
        opened.append(name)
        return SimpleNamespace(close=lambda: closed.append(name))

    monkeypatch.setattr(midi_connection, "_create_backend", lambda: SimpleNamespace(
        kind="fake", list_outputs=lambda: list(ports), open_output=open_output))
    return SimpleNamespace(
        conn=midi_connection.MIDIConnection(), ports=ports, opened=opened, closed=closed
    )


@pytest.mark.parametrize("suffix", ["", " 0", " 1", " 37", " 1000"])
def test_default_prefers_unique_managed_endpoint(midi_backend, suffix):
    endpoint = midi_connection.MANAGED_PORT_BASE + suffix
    midi_backend.ports[:] = ["FLStudioMCP 0", "IAC Driver Bus 1", endpoint]

    assert midi_backend.conn.connect() is True

    assert midi_backend.opened == [endpoint]
    assert midi_backend.conn.transport_kind == "windows_midi_services"
    assert midi_backend.conn.get_status()["transport_kind"] == "windows_midi_services"


def test_concurrent_connect_opens_only_one_handle(midi_backend, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    opened = []

    def open_output(name):
        opened.append(name)
        entered.set()
        assert release.wait(3)
        return SimpleNamespace(closed=False, close=lambda: None)

    monkeypatch.setattr(midi_connection, "_create_backend", lambda: SimpleNamespace(
        kind="fake", list_outputs=lambda: [midi_connection.MANAGED_PORT_BASE],
        open_output=open_output))
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(midi_backend.conn.connect)
        try:
            assert entered.wait(3)
            second = pool.submit(midi_backend.conn.connect)
        finally:
            release.set()
        assert first.result() and second.result()
    assert opened == [midi_connection.MANAGED_PORT_BASE]


def test_windows_backend_does_not_fall_back_on_load_failure(tmp_path, monkeypatch):
    from fl_studio_mcp.utils import native_midi_transport

    monkeypatch.setattr(midi_connection, "_get_fl_hardware_dir", lambda: tmp_path)
    monkeypatch.setattr(midi_connection.sys, "platform", "win32")

    def unavailable():
        raise RuntimeError("Verified native runtime is unavailable")

    monkeypatch.setattr(native_midi_transport, "NativeMidiBackend", unavailable)
    conn = midi_connection.MIDIConnection()
    assert not conn.connect()
    assert conn._port is None and not conn.is_connected
    assert "runtime is unavailable" in conn.connection_error


def test_explicit_managed_base_tracks_changing_backend_index(midi_backend, monkeypatch):
    base = midi_connection.MANAGED_PORT_BASE
    monkeypatch.setenv("FL_MCP_MIDI_PORT", base)
    for index in [1, 8, 2]:
        endpoint = f"{base} {index}"
        midi_backend.ports[:] = ["FL Studio MCP", endpoint]
        assert midi_backend.conn.connect() is True
        assert midi_backend.opened[-1] == endpoint
        midi_backend.conn.disconnect()
        assert midi_backend.closed[-1] == endpoint
        assert midi_backend.conn.transport_kind == "midi"


@pytest.mark.parametrize("configured", [False, True])
@pytest.mark.parametrize("suffixes", [["", " 1"], [" 1", " 2"]])
def test_ambiguous_managed_endpoints_never_fall_back_to_legacy(
    midi_backend, monkeypatch, configured, suffixes
):
    base = midi_connection.MANAGED_PORT_BASE
    if configured:
        monkeypatch.setenv("FL_MCP_MIDI_PORT", base)
    midi_backend.ports[:] = ["FL Studio MCP", *[base + suffix for suffix in suffixes]]

    assert midi_backend.conn.connect() is False

    assert midi_backend.opened == []
    assert "one exact" in midi_backend.conn.connection_error


@pytest.mark.parametrize("endpoint", ["FL Studio MCP", "FLStudioMCP 0", "IAC Driver Bus 1"])
def test_explicit_legacy_endpoint_is_honored_over_managed(midi_backend, monkeypatch, endpoint):
    monkeypatch.setenv("FL_MCP_MIDI_PORT", endpoint)
    midi_backend.ports[:] = [midi_connection.MANAGED_PORT_BASE + " 1", endpoint]

    assert midi_backend.conn.connect() is True

    assert midi_backend.opened == [endpoint]
    assert midi_backend.conn.get_status()["transport_kind"] == "midi"


def test_explicit_backend_name_is_an_exact_selection(midi_backend, monkeypatch):
    base = midi_connection.MANAGED_PORT_BASE
    monkeypatch.setenv("FL_MCP_MIDI_PORT", base + " 2")
    midi_backend.ports[:] = [base + " 1", base + " 2"]

    assert midi_backend.conn.connect() is True

    assert midi_backend.opened == [base + " 2"]


@pytest.mark.parametrize(
    "endpoint",
    [
        "ALX FL Bridge (Send) extra",
        "ALX FL Bridge (Send) 1 extra",
        "ALX FL Bridge (Send) 1.0",
        "ALX FL Bridge (Send) -1",
        "ALX FL Bridge (Send)  1",
        "ALX FL Bridge (Send) \u0661",
        "ALX FL Bridge (Send)1",
        "ALX FL Bridge (Send) ",
        "Other ALX FL Bridge (Send) 1",
        "ALX FL Bridge (Receive) 1",
    ],
)
def test_similarly_named_ports_are_not_managed(midi_backend, monkeypatch, endpoint):
    midi_backend.ports[:] = [endpoint]

    assert midi_backend.conn.connect() is False
    assert midi_backend.opened == []

    monkeypatch.setenv("FL_MCP_MIDI_PORT", midi_connection.MANAGED_PORT_BASE)
    assert midi_backend.conn.connect() is False
    assert midi_backend.opened == []


def test_missing_exact_explicit_legacy_name_does_not_fall_back(midi_backend, monkeypatch):
    monkeypatch.setenv("FL_MCP_MIDI_PORT", "FLStudioMCP")
    midi_backend.ports[:] = ["FLStudioMCP 1", midi_connection.MANAGED_PORT_BASE + " 1"]

    assert midi_backend.conn.connect() is False
    assert midi_backend.opened == []


@pytest.mark.parametrize("endpoint", ["FL Studio MCP", "FLStudioMCP 0", "IAC Driver Bus 1"])
def test_unique_legacy_fallback_still_connects(midi_backend, endpoint):
    midi_backend.ports[:] = ["Unrelated endpoint", endpoint]

    assert midi_backend.conn.connect() is True

    assert midi_backend.opened == [endpoint]
    assert midi_backend.conn.transport_kind == "midi"


def test_success_preserves_payload_and_command_envelope(bridge):
    def send(message):
        bridge.sends.append(message)
        bridge.reply(value=[1, 2])

    bridge.conn._port.send = send

    result = bridge.conn.send_command("mixer.inspect", {"index": 2}, timeout=1)

    command = json.loads(bridge.conn._command_file.read_text(encoding="utf-8"))
    assert command == {
        "protocol": 2,
        "id": result["id"],
        "action": "mixer.inspect",
        "params": {"index": 2},
        "expires_at": 10001,
    }
    assert result == {
        "protocol": 2,
        "id": command["id"],
        "success": True,
        "value": [1, 2],
        "round_trip_ms": 0.0,
    }
    assert bridge.sends == [{"kind": "note_on", "note": 127, "velocity": 127}]


def test_failed_send_invalidates_handle_without_replaying_request(bridge):
    closed = []

    def failed_send(message):
        bridge.sends.append(message)
        raise RuntimeError("Device disconnected")

    bridge.conn._port.send = failed_send
    bridge.conn._port.close = lambda: closed.append(True)
    result = bridge.conn.send_command("mixer.setTrackVolume", {"track": 1, "volume": .5})

    assert result["outcome"] == "unknown"
    assert "Device disconnected" in result["error"]
    assert not bridge.conn.is_connected and bridge.conn._port is None
    assert bridge.conn.connection_error == "Device disconnected"
    assert closed == [True] and len(bridge.sends) == 1


def test_closed_port_is_not_reported_as_connected(midi_backend):
    midi_backend.ports[:] = [midi_connection.MANAGED_PORT_BASE]
    assert midi_backend.conn.connect()
    old = midi_backend.conn._port
    old.closed = True
    assert not midi_backend.conn.is_connected
    assert midi_backend.conn.connect()
    assert midi_backend.conn._port is not old
    assert len(midi_backend.opened) == 2


def test_port_closed_while_waiting_never_calls_native_send(bridge, monkeypatch):
    @contextmanager
    def closed_during_wait(path, timeout):
        bridge.conn._port.closed = True
        yield

    monkeypatch.setattr(midi_connection, "transaction_lock", closed_during_wait)
    result = bridge.conn.send_command("bridge.ping")
    assert result["outcome"] == "not_dispatched"
    assert not bridge.sends and not bridge.conn._command_file.exists()


def test_native_ui_policy_blocks_before_connection_queue_or_midi(bridge, monkeypatch):
    monkeypatch.delenv("FL_MCP_INTERACTION_POLICY", raising=False)
    monkeypatch.setattr(bridge.conn, "ensure_connected", lambda: pytest.fail("must not connect"))
    with pytest.raises(RuntimeError, match="unsupported in background"):
        bridge.conn.send_command("native.call", {"function": "ui.setFocused", "args": [0]})
    assert not bridge.conn._command_file.exists()
    assert bridge.sends == [] and bridge.lock_timeouts == []



def test_policy_change_while_waiting_for_lock_stops_before_queue_or_trigger(bridge, monkeypatch):
    monkeypatch.setenv("FL_MCP_INTERACTION_POLICY", "interactive")
    with pytest.raises(RuntimeError, match="backend-only"):
        bridge.conn.send_command("native.call", {"function": "ui.setFocused", "args": [0]})
    assert not bridge.conn._command_file.exists()
    assert bridge.sends == []



def test_policy_rechecked_after_publication_before_midi_trigger(bridge, monkeypatch):
    monkeypatch.setenv("FL_MCP_INTERACTION_POLICY", "interactive")
    with pytest.raises(RuntimeError, match="backend-only"):
        bridge.conn.send_command("native.call", {"function": "ui.setFocused", "args": [0]})
    assert not bridge.conn._command_file.exists()
    assert bridge.sends == []


def test_policy_and_publication_use_private_copy_of_nested_params(bridge, monkeypatch):
    monkeypatch.delenv("FL_MCP_INTERACTION_POLICY", raising=False)
    params = {"steps": [{"action": "native.call", "params": {"function": "ui.getFocused"}}]}

    @contextmanager
    def changed_caller_data(path, timeout):
        params["steps"][0]["params"]["function"] = "ui.setFocused"
        yield

    def send(message):
        bridge.sends.append(message)
        bridge.reply()

    bridge.conn._port.send = send
    monkeypatch.setattr(midi_connection, "transaction_lock", changed_caller_data)
    assert bridge.conn.send_command("bridge.batch", params)["success"]
    command = json.loads(bridge.conn._command_file.read_text())
    assert command["params"]["steps"][0]["params"]["function"] == "ui.getFocused"
    assert params["steps"][0]["params"]["function"] == "ui.setFocused"


def test_lock_wait_uses_the_same_response_deadline(bridge, monkeypatch):
    @contextmanager
    def slow_lock(path, timeout):
        bridge.lock_timeouts.append(timeout)
        bridge.clock.now += 0.75
        yield

    monkeypatch.setattr(midi_connection, "transaction_lock", slow_lock)

    result = bridge.conn.send_command("mixer.setTrackVolume", timeout=1)

    assert bridge.clock.now == pytest.approx(11.0)
    assert sum(bridge.clock.sleeps) == pytest.approx(0.25)
    assert bridge.lock_timeouts == [1.0]
    assert result["outcome"] == "unknown"
    assert len(bridge.sends) == 1


def test_lock_expiry_never_publishes_or_dispatches(bridge, monkeypatch):
    @contextmanager
    def expired_lock(path, timeout):
        bridge.clock.now += timeout
        yield

    monkeypatch.setattr(midi_connection, "transaction_lock", expired_lock)

    result = bridge.conn.send_command("mixer.setTrackVolume", timeout=1)

    assert result["outcome"] == "not_dispatched"
    assert "expired" in result["error"]
    assert not bridge.conn._command_file.exists()
    assert bridge.sends == []


def test_connect_time_reduces_lock_budget(bridge, monkeypatch):
    def connect():
        bridge.clock.now += 0.4

    monkeypatch.setattr(bridge.conn, "ensure_connected", connect)
    bridge.conn._port.send = lambda message: bridge.reply()

    result = bridge.conn.send_command("transport.inspect", timeout=1)

    assert bridge.lock_timeouts == [pytest.approx(0.6)]
    assert result["success"] is True
    assert result["round_trip_ms"] == 400.0


def test_connect_expiry_does_not_acquire_lock(bridge, monkeypatch):
    def connect():
        bridge.clock.now += 1

    monkeypatch.setattr(bridge.conn, "ensure_connected", connect)

    result = bridge.conn.send_command("transport.inspect", timeout=1)

    assert result["outcome"] == "not_dispatched"
    assert bridge.lock_timeouts == []
    assert bridge.sends == []


def test_write_time_reduces_response_budget(bridge, monkeypatch):
    original_write = midi_connection.atomic_json

    def slow_write(path, command):
        original_write(path, command)
        bridge.clock.now += 0.8

    monkeypatch.setattr(midi_connection, "atomic_json", slow_write)

    result = bridge.conn.send_command("transport.inspect", timeout=1)

    assert result["outcome"] == "unknown"
    assert bridge.clock.now == pytest.approx(11.0)
    assert sum(bridge.clock.sleeps) == pytest.approx(0.2)


def test_expiry_during_write_never_sends_trigger(bridge, monkeypatch):
    original_write = midi_connection.atomic_json

    def expired_write(path, command):
        original_write(path, command)
        bridge.clock.now += 1

    monkeypatch.setattr(midi_connection, "atomic_json", expired_write)

    result = bridge.conn.send_command("mixer.setTrackVolume", timeout=1)

    assert result["outcome"] == "not_dispatched"
    assert bridge.sends == []


def test_trigger_send_error_is_unknown_and_never_retried(bridge):
    def failed_send(message):
        bridge.sends.append(message)
        raise OSError("port disconnected during send")

    bridge.conn._port.send = failed_send

    result = bridge.conn.send_command("mixer.setTrackVolume", timeout=1)

    assert result["outcome"] == "unknown"
    assert "port disconnected" in result["error"]
    assert len(bridge.sends) == 1


def test_native_failure_before_send_is_not_dispatched(bridge):
    from fl_studio_mcp.utils.native_midi_transport import NativeMidiError

    def failed_send():
        raise NativeMidiError("closed native session", status=6, delivery_attempted=False)

    bridge.conn._port.send_wakeup = failed_send
    result = bridge.conn.send_command("mixer.setTrackVolume", timeout=1)

    assert result["outcome"] == "not_dispatched"
    assert bridge.conn._command_file.exists()
    assert not bridge.conn.is_connected
    assert bridge.sends == []


def test_lock_error_does_not_dispatch(bridge, monkeypatch):
    @contextmanager
    def failed_lock(path, timeout):
        raise TimeoutError("Another FL client holds the bridge lock")
        yield

    monkeypatch.setattr(midi_connection, "transaction_lock", failed_lock)

    result = bridge.conn.send_command("mixer.setTrackVolume", timeout=1)

    assert result["outcome"] == "not_dispatched"
    assert not bridge.conn._command_file.exists()
    assert bridge.sends == []


@pytest.mark.parametrize(
    "invalid",
    [
        None,
        [],
        "response",
        1,
        False,
        {},
        {"protocol": 1, "success": True},
        {"protocol": 2.0, "success": True},
        {"protocol": "2", "success": True},
        {"protocol": 2, "success": 1},
        {"protocol": 2, "success": "true"},
        {"protocol": 2, "success": None},
        {"protocol": 2, "success": True, "id": "stale-request"},
        {"protocol": 2, "success": True, "id": None},
    ],
)
def test_invalid_or_stale_responses_are_ignored_until_valid(bridge, invalid):
    def send(message):
        bridge.sends.append(message)
        request = json.loads(bridge.conn._command_file.read_text(encoding="utf-8"))
        response = {"id": request["id"], **invalid} if isinstance(invalid, dict) else invalid
        bridge.conn._response_file.write_text(json.dumps(response), encoding="utf-8")

    bridge.conn._port.send = send
    bridge.clock.on_sleep = lambda: bridge.reply(value="fresh")

    result = bridge.conn.send_command("transport.inspect", timeout=1)

    assert result["success"] is True
    assert result["value"] == "fresh"
    assert result["round_trip_ms"] == 20.0
    assert len(bridge.sends) == 1


@pytest.mark.parametrize("invalid", [b'{"protocol":', b"\xff\xfe\x00"])
def test_malformed_json_or_encoding_is_ignored(bridge, invalid):
    def send(message):
        bridge.conn._response_file.write_bytes(invalid)

    bridge.conn._port.send = send
    bridge.clock.on_sleep = bridge.reply

    result = bridge.conn.send_command("transport.inspect", timeout=1)

    assert result["success"] is True
    assert result["round_trip_ms"] == 20.0


def test_matching_error_response_is_preserved(bridge):
    bridge.conn._port.send = lambda message: bridge.reply(success=False, error="refused")

    result = bridge.conn.send_command("mixer.setTrackVolume", timeout=1)

    assert result["success"] is False
    assert result["error"] == "refused"
    assert "outcome" not in result


def test_stale_response_alone_times_out_without_retry(bridge):
    bridge.conn._response_file.write_text(
        json.dumps({"protocol": 2, "id": "old", "success": True}), encoding="utf-8"
    )

    result = bridge.conn.send_command("mixer.setTrackVolume", timeout=0.05)

    assert result["success"] is False
    assert result["outcome"] == "unknown"
    assert bridge.clock.now == pytest.approx(10.05)
    assert len(bridge.sends) == 1
    assert "Do not blindly retry" in result["error"]


def test_read_finishing_after_deadline_does_not_claim_success(bridge):
    class SlowResponse:
        def read_text(self, encoding):
            bridge.clock.now += 0.6
            request = json.loads(bridge.conn._command_file.read_text(encoding="utf-8"))
            return json.dumps({"protocol": 2, "id": request["id"], "success": True})

    bridge.conn._response_file = SlowResponse()

    result = bridge.conn.send_command("mixer.setTrackVolume", timeout=0.5)

    assert result["outcome"] == "unknown"
    assert "round_trip_ms" not in result
    assert bridge.clock.sleeps == []


@pytest.mark.parametrize("timeout", [0, -1, True, None, "1", float("nan"), float("inf")])
def test_invalid_timeout_is_rejected_before_dispatch(bridge, timeout):
    with pytest.raises(ValueError, match="positive finite"):
        bridge.conn.send_command("mixer.setTrackVolume", timeout=timeout)

    assert not bridge.conn._command_file.exists()
    assert bridge.sends == []
