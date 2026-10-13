"""Lazy, hash-verified ctypes binding for SyncFL's Windows x64 MIDI transport.

Importing this module neither loads a DLL nor touches MIDI. The C ABI is cdecl;
all pointers, lengths and session tokens have explicit ctypes declarations.
"""

from __future__ import annotations

import ctypes as ct
import hashlib
import json
import re
import sys
import threading
from pathlib import Path

ABI_VERSION = 0x10000
BUFFER_TOO_SMALL = 2
MAX_BUFFER = 1024 * 1024
RUNTIME_FILES = ("syncfl_midi_transport.dll", "syncfl-midi.exe")
_BUNDLED = Path(__file__).resolve().parents[1] / "_native" / "midi_transport"
RUNTIME = (_BUNDLED if _BUNDLED.exists() else
           Path(__file__).resolve().parents[3] / "native" / "midi_transport" / "runtime")


class NativeMidiError(RuntimeError):
    def __init__(self, message, *, status, native_code=0, delivery_attempted=False):
        super().__init__(message)
        self.status = status
        self.native_code = native_code
        self.delivery_attempted = delivery_attempted


def verify_runtime(directory=None):
    root = Path(directory) if directory is not None else RUNTIME
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if (not isinstance(manifest, dict) or manifest.get("schema_version") != 1
            or manifest.get("architecture") != "x64"
            or manifest.get("abi_version") != ABI_VERSION
            or not isinstance(manifest.get("files"), dict)):
        raise ValueError("Unsupported native MIDI transport manifest")
    for name in RUNTIME_FILES:
        entry = manifest["files"].get(name)
        if (not isinstance(entry, dict)
                or not isinstance(entry.get("sha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])
                or type(entry.get("size")) is not int or entry["size"] <= 0):
            raise ValueError(f"Invalid native MIDI transport manifest entry: {name}")
        data = (root / name).read_bytes()
        if len(data) != entry["size"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise ValueError(f"Native MIDI transport integrity failure: {name}")
    return root / RUNTIME_FILES[0]


def _encoded_name(name):
    if not isinstance(name, str) or not name or "\0" in name:
        raise ValueError("MIDI output name must be nonempty text without NUL")
    # Validate before crossing the ABI; WinMM names hold 31 UTF-16 code units.
    if len(name.encode("utf-16-le")) > 62:
        raise ValueError("MIDI output name exceeds WinMM's 31-character limit")
    return name.encode("utf-8")


class NativeMidiBackend:
    """One resident library; independently owned output sessions."""

    kind = "native_winmm"

    def __init__(self):
        if sys.platform != "win32" or ct.sizeof(ct.c_void_p) != 8:
            raise RuntimeError("Native MIDI transport requires Windows x64")
        # Search only the DLL's own directory and System32, never cwd/PATH.
        self._dll = ct.CDLL(str(verify_runtime().resolve()), winmode=0x100 | 0x800)
        pointer = ct.POINTER
        signatures = {
            "abi_version": (ct.c_uint32, []),
            "list_outputs": (ct.c_int32, [ct.c_void_p, ct.c_size_t,
                                           pointer(ct.c_size_t), pointer(ct.c_uint32)]),
            "resolve_output": (ct.c_int32, [ct.c_char_p, ct.c_uint32, ct.c_void_p,
                                            ct.c_size_t, pointer(ct.c_size_t)]),
            "open_output": (ct.c_int32, [ct.c_char_p, ct.c_uint32, pointer(ct.c_uint64)]),
            "session_name": (ct.c_int32, [ct.c_uint64, ct.c_void_p, ct.c_size_t,
                                          pointer(ct.c_size_t)]),
            "send_wakeup": (ct.c_int32, [ct.c_uint64, pointer(ct.c_uint32)]),
            "close": (ct.c_int32, [ct.c_uint64]),
            "last_error": (ct.c_int32, [pointer(ct.c_uint32), ct.c_void_p,
                                        ct.c_size_t, pointer(ct.c_size_t)]),
        }
        for name, (result, args) in signatures.items():
            function = getattr(self._dll, "syncfl_midi_" + name)
            function.restype, function.argtypes = result, args
        if self._dll.syncfl_midi_abi_version() != ABI_VERSION:
            raise RuntimeError("Incompatible native MIDI transport ABI")

    def _error(self, status, *, attempted=False):
        # last_error is read-only: capture it before close or any other ABI call.
        code, required = ct.c_uint32(), ct.c_size_t()
        self._dll.syncfl_midi_last_error(ct.byref(code), None, 0, ct.byref(required))
        message = "Native MIDI transport failed"
        if 0 < required.value <= MAX_BUFFER:
            buffer = ct.create_string_buffer(required.value)
            if self._dll.syncfl_midi_last_error(
                ct.byref(code), buffer, len(buffer), ct.byref(required)
            ) == 0:
                message = buffer.value.decode("utf-8", errors="replace") or message
        return NativeMidiError(message, status=status, native_code=code.value,
                               delivery_attempted=attempted)

    def _buffer(self, call):
        # Enumeration may grow between the size query and read. Only read-only
        # buffer calls retry; open and send are never retried.
        size = 0
        for _ in range(4):
            required = ct.c_size_t()
            buffer = ct.create_string_buffer(size) if size else None
            status = call(buffer, size, ct.byref(required))
            if status == BUFFER_TOO_SMALL:
                if not 0 < required.value <= MAX_BUFFER:
                    raise RuntimeError("Native MIDI response exceeds its buffer limit")
                size = required.value
                continue
            if status:
                raise self._error(status)
            if buffer is None or not 0 < required.value <= size:
                raise RuntimeError("Invalid native MIDI buffer response")
            return buffer.raw[:required.value]
        raise RuntimeError("MIDI output list kept changing during enumeration")

    def list_outputs(self):
        count = ct.c_uint32()
        data = self._buffer(lambda b, n, r: self._dll.syncfl_midi_list_outputs(
            b, n, r, ct.byref(count)))
        if not data.endswith(b"\0") or count.value > 4096:
            raise RuntimeError("Invalid native MIDI output catalog")
        rows = data[:-1].split(b"\0") if count.value else []
        if len(rows) != count.value + (1 if count.value else 0) or (rows and rows[-1]):
            raise RuntimeError("Invalid native MIDI output count")
        return [row.decode("utf-8") for row in rows[:count.value]]

    def resolve_output(self, name, *, allow_decimal_suffix=False):
        if type(allow_decimal_suffix) is not bool:
            raise ValueError("allow_decimal_suffix must be boolean")
        encoded = _encoded_name(name)
        return self._buffer(lambda b, n, r: self._dll.syncfl_midi_resolve_output(
            encoded, int(allow_decimal_suffix), b, n, r)).removesuffix(b"\0").decode("utf-8")

    def open_output(self, name):
        encoded = _encoded_name(name)
        session = ct.c_uint64()
        status = self._dll.syncfl_midi_open_output(encoded, 0, ct.byref(session))
        if status:
            raise self._error(status)
        if not session.value:
            raise RuntimeError("Native MIDI open returned an invalid session")
        try:
            actual = self._buffer(lambda b, n, r: self._dll.syncfl_midi_session_name(
                session.value, b, n, r)).removesuffix(b"\0").decode("utf-8")
            if actual != name:
                raise RuntimeError("Native MIDI opened a different output name")
            return NativeMidiOutput(self, session.value, actual)
        except BaseException:
            self._dll.syncfl_midi_close(session.value)
            raise


class NativeMidiOutput:
    def __init__(self, backend, session, name):
        self._backend, self._session, self.name = backend, session, name
        self._lock = threading.Lock()

    @property
    def closed(self):
        with self._lock:
            return self._session == 0

    def send_wakeup(self):
        with self._lock:
            if not self._session:
                raise NativeMidiError("MIDI output is closed", status=6)
            attempted = ct.c_uint32()
            status = self._backend._dll.syncfl_midi_send_wakeup(
                self._session, ct.byref(attempted))
            if status:
                raise self._backend._error(status, attempted=bool(attempted.value))
            if attempted.value != 1:
                raise NativeMidiError("Native MIDI returned no delivery attempt", status=12)

    def close(self):
        with self._lock:
            session, self._session = self._session, 0
            if session:
                status = self._backend._dll.syncfl_midi_close(session)
                if status:
                    raise self._backend._error(status)
