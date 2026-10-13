"""Dedicated MIDI endpoint selection and bounded protocol-2 transactions."""

from __future__ import annotations

import copy
import json
import math
import os
import re
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from fl_studio_mcp.utils.interaction_policy import native_access
from fl_studio_mcp.utils.native_interaction_policy import check_native_command
from fl_studio_mcp.utils.protocol import atomic_json, poll_delay, transaction_lock

MANAGED_PORT_BASE = "ALX FL Bridge (Send)"


class _MidoBackend:
    """Non-Windows transport; Windows uses our native WinMM implementation."""

    kind = "mido"

    def list_outputs(self):
        import mido

        return list(mido.get_output_names())

    def open_output(self, name):
        import mido

        port = mido.open_output(name)

        class Output:
            @property
            def closed(self):
                return port.closed

            def close(self):
                port.close()

            def send_wakeup(self):
                port.send(mido.Message("note_on", note=127, velocity=127))

        return Output()


def _create_backend():
    if sys.platform == "win32":
        from fl_studio_mcp.utils.native_midi_transport import NativeMidiBackend

        return NativeMidiBackend()
    return _MidoBackend()


def _is_managed_port(name: str) -> bool:
    return re.fullmatch(re.escape(MANAGED_PORT_BASE) + r"(?: [0-9]+)?", name) is not None


def _get_fl_hardware_dir() -> Path:
    from fl_studio_mcp.utils.paths import get_fl_settings_dir

    directory = get_fl_settings_dir().joinpath("Hardware", "FLStudioMCP")
    directory.mkdir(exist_ok=True, parents=True)
    return directory


def _choose_endpoint(ports: list[str], requested: str | None) -> str:
    if requested and requested != MANAGED_PORT_BASE:
        candidates = [name for name in ports if name == requested]
    else:
        candidates = list(filter(_is_managed_port, ports))
        if not candidates and not requested:
            candidates = [
                name
                for name in ports
                if any(marker in name for marker in ("FL Studio MCP", "FLStudioMCP", "IAC"))
            ]
    if len(candidates) != 1:
        raise ValueError("Set FL_MCP_MIDI_PORT to one exact dedicated virtual MIDI port")
    return candidates[0]


class MIDIConnection:
    """A lazily opened output port and its correlated file mailbox.

    Sending a command never retries a MIDI message: lost acknowledgements after
    dispatch have an unknown outcome and must be reconciled by the caller.
    """

    TRIGGER_NOTE = 127

    def __init__(self) -> None:
        self._hardware_dir = _get_fl_hardware_dir()
        self._command_file, self._response_file = (
            self._hardware_dir / filename for filename in ("mcp_command.json", "mcp_response.json")
        )
        self._connected, self._port, self._port_name, self._error = False, None, None, None
        self._backend = None
        self._connection_lock = threading.RLock()

    def _get_backend(self):
        with self._connection_lock:
            if self._backend is None:
                self._backend = _create_backend()
            return self._backend

    @property
    def is_connected(self) -> bool:
        # RtMidi overrides Mido's safe send() and can terminate the process
        # when called after close. Never pass it a known closed handle.
        return (self._port is not None and self._connected
                and not getattr(self._port, "closed", False))

    @property
    def connection_error(self) -> str | None:
        return self._error

    @property
    def transport_kind(self) -> str:
        return "windows_midi_services" if _is_managed_port(self._port_name or "") else "midi"

    def connect(self) -> bool:
        # Concurrent first callers must not open two handles and orphan one.
        with self._connection_lock:
            return self._connect()

    def _connect(self) -> bool:
        if self.is_connected:
            return True
        try:
            backend = self._get_backend()
            endpoint = _choose_endpoint(
                backend.list_outputs(), os.getenv("FL_MCP_MIDI_PORT")
            )
            opened = backend.open_output(endpoint)
        except Exception as exc:
            self._error = str(exc)
            return False
        self._port, self._port_name = opened, endpoint
        self._connected, self._error = True, None
        return True

    def disconnect(self) -> None:
        with self._connection_lock:
            previous = self._port
            self._connected, self._port, self._port_name = False, None, None
        if previous is not None:
            try:
                previous.close()
            except Exception as exc:
                self._error = "MIDI port close failed: " + str(exc)

    def ensure_connected(self) -> None:
        if not (self.is_connected or self.connect()):
            raise RuntimeError(self._error or "MIDI endpoint unavailable")

    def send_command(
        self, action: str, params: dict[str, Any] | None = None, timeout: float = 2.0
    ) -> dict[str, Any]:
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be a positive finite number")
        payload = copy.deepcopy(params if params is not None else {})
        check_native_command(action, payload)
        begin = time.monotonic()
        stop = begin + timeout
        expiry = time.time() + timeout
        self.ensure_connected()
        identifier = uuid.uuid4().hex
        attempted = False

        def budget() -> float:
            left = stop - time.monotonic()
            if left <= 0:
                raise TimeoutError("Command expired before dispatch")
            return left

        try:
            with transaction_lock(self._hardware_dir / "mcp.lock", budget()):
                budget()
                if not self.is_connected:
                    self.disconnect()
                    raise RuntimeError("MIDI output closed before dispatch; no command sent")
                check_native_command(action, payload)
                envelope = dict(
                    protocol=2, id=identifier, action=action, params=payload, expires_at=expiry
                )
                if native_access() == "full":
                    envelope["access"] = "full"
                atomic_json(self._command_file, envelope)
                budget()
                check_native_command(action, payload)
                attempted = True
                try:
                    self._port.send_wakeup()
                except Exception as exc:
                    # The native ABI distinguishes failure before dispatch from
                    # uncertain delivery. Other backends remain conservative.
                    attempted = getattr(exc, "delivery_attempted", True) is not False
                    # A failed/stale OS handle must not poison future requests.
                    # This request remains uncertain: close, never replay it.
                    self.disconnect()
                    self._error = str(exc)
                    raise
                return self._wait_for_response(timeout, identifier, deadline=stop, started=begin)
        except Exception as exc:
            return dict(
                success=False,
                id=identifier,
                error=str(exc),
                outcome="unknown" if attempted else "not_dispatched",
            )

    def _wait_for_response(
        self,
        timeout: float,
        request_id: str,
        *,
        deadline: float | None = None,
        started: float | None = None,
    ) -> dict[str, Any]:
        begin = time.monotonic() if started is None else started
        stop = begin + timeout if deadline is None else deadline
        while time.monotonic() < stop:
            try:
                message = json.loads(self._response_file.read_text(encoding="utf-8"))
            except (OSError, ValueError, UnicodeError):
                message = None
            if isinstance(message, dict):
                valid = (
                    type(message.get("protocol")) is int
                    and message["protocol"] == 2
                    and message.get("id") == request_id
                    and type(message.get("success")) is bool
                )
                if valid and time.monotonic() < stop:
                    return dict(message, round_trip_ms=round((time.monotonic() - begin) * 1000, 1))
            remaining = stop - time.monotonic()
            if remaining > 0:
                time.sleep(min(remaining, poll_delay(time.monotonic() - begin)))
        return dict(
            success=False,
            id=request_id,
            outcome="unknown",
            error=(
                f"Timeout waiting for FL Studio response after {timeout}s. "
                "Do not blindly retry a mutation; inspect state and receipts. "
                "Check that the protocol-2 controller is installed and reloaded."
            ),
        )

    def get_status(self) -> dict[str, Any]:
        try:
            ports = self._get_backend().list_outputs()
        except Exception as exc:
            ports = []
            self._error = self._error or str(exc)
        return dict(
            connected=self.is_connected,
            port_name=self._port_name,
            transport_kind=self.transport_kind,
            midi_backend=(self._backend.kind if self._backend is not None else
                          "native_winmm" if sys.platform == "win32" else "mido"),
            available_ports=ports,
            command_file=str(self._command_file),
            response_file=str(self._response_file),
            error=self.connection_error,
        )


_connection: MIDIConnection | None = None
_owner_lock = threading.RLock()


def get_connection() -> MIDIConnection:
    global _connection
    with _owner_lock:
        session = _connection
        if session is None:
            session = MIDIConnection()
            _connection = session
        return session


def reset_connection() -> None:
    global _connection
    with _owner_lock:
        old_session, _connection = _connection, None
        if old_session is not None:
            old_session.disconnect()
