import ctypes as ct
import hashlib
import importlib
import json
from types import SimpleNamespace

import pytest

from fl_studio_mcp.utils import native_midi_transport as native


class Function:
    def __init__(self, call):
        self.call = call

    def __call__(self, *args):
        return self.call(*args)


def output(buffer, capacity, required, data):
    required._obj.value = len(data)
    if capacity < len(data):
        return 2
    ct.memmove(buffer, data, len(data))
    return 0


@pytest.fixture
def dll(monkeypatch):
    state = SimpleNamespace(opens=[], sends=[], closes=[], send_status=0,
                            attempted=1, name=b"Dedicated MIDI", abi=65536,
                            names=["Dedicated MIDI", "MIDI \u97f3\u697d"], native_code=91)
    fake = SimpleNamespace()

    def function(name, call):
        setattr(fake, "syncfl_midi_" + name, Function(call))

    def list_outputs(buffer, capacity, required, count):
        count._obj.value = len(state.names)
        data = b"".join(x.encode() + b"\0" for x in state.names) + b"\0"
        return output(buffer, capacity, required, data)

    def open_output(name, flags, session):
        state.opens.append((name, flags))
        session._obj.value = 7
        return 0

    def send(session, attempted):
        state.sends.append(session)
        attempted._obj.value = state.attempted
        return state.send_status

    def close(session):
        state.closes.append(session)
        return 0

    def last_error(code, buffer, capacity, required):
        code._obj.value = state.native_code
        return output(buffer, capacity, required, b"Native failure\0")

    function("abi_version", lambda: state.abi)
    function("list_outputs", list_outputs)
    function("resolve_output", lambda name, flags, b, n, r: output(b, n, r, name + b"\0"))
    function("open_output", open_output)
    function("session_name", lambda session, b, n, r: output(b, n, r, state.name + b"\0"))
    function("send_wakeup", send)
    function("close", close)
    function("last_error", last_error)
    monkeypatch.setattr(native.sys, "platform", "win32")
    monkeypatch.setattr(native, "verify_runtime", lambda: native.Path("verified.dll"))
    loaded = []
    monkeypatch.setattr(native.ct, "CDLL", lambda path, **kw: loaded.append((path, kw)) or fake)
    return state, fake, loaded


def test_import_is_lazy_even_without_a_runtime(monkeypatch):
    with monkeypatch.context() as scope:
        scope.setattr(ct, "CDLL", lambda *a, **kw: pytest.fail("import loaded a DLL"))
        importlib.reload(native)


def test_platform_rejected_before_loading(monkeypatch):
    monkeypatch.setattr(native.sys, "platform", "linux")
    monkeypatch.setattr(ct, "CDLL", lambda *a, **kw: pytest.fail("must not load"))
    with pytest.raises(RuntimeError, match="Windows x64"):
        native.NativeMidiBackend()


def test_loading_uses_explicit_safe_search_and_cdecl_signatures(dll):
    _, fake, loaded = dll
    native.NativeMidiBackend()
    assert loaded[0][1] == {"winmode": 0x900}
    assert fake.syncfl_midi_open_output.argtypes[-1] == ct.POINTER(ct.c_uint64)
    assert fake.syncfl_midi_open_output.restype == ct.c_int32
    assert fake.syncfl_midi_list_outputs.argtypes[1] == ct.c_size_t


def test_incompatible_abi_rejected(dll):
    state, _, _ = dll
    state.abi = 2
    with pytest.raises(RuntimeError, match="ABI"):
        native.NativeMidiBackend()


def test_enumeration_and_resolution_are_read_only_unicode_safe(dll):
    state, _, _ = dll
    backend = native.NativeMidiBackend()
    assert backend.list_outputs() == state.names
    assert backend.resolve_output("MIDI \u97f3\u697d") == "MIDI \u97f3\u697d"
    state.names = []
    assert backend.list_outputs() == []
    assert state.opens == state.sends == state.closes == []


@pytest.mark.parametrize("name", ["", None, 7, "a\0b", "x" * 32, "\U0001f3b5" * 16, "\ud800"])
def test_invalid_name_never_opens(dll, name):
    state, _, _ = dll
    with pytest.raises((ValueError, UnicodeError)):
        native.NativeMidiBackend().open_output(name)
    assert state.opens == []


def test_one_send_and_idempotent_close(dll):
    state, _, _ = dll
    port = native.NativeMidiBackend().open_output("Dedicated MIDI")
    assert port.name == "Dedicated MIDI" and not port.closed
    port.send_wakeup()
    port.close()
    port.close()
    assert port.closed and state.sends == [7] and state.closes == [7]
    with pytest.raises(native.NativeMidiError) as error:
        port.send_wakeup()
    assert not error.value.delivery_attempted and state.sends == [7]


@pytest.mark.parametrize("attempted", [0, 1])
def test_failed_send_is_never_retried_and_reports_attempt(dll, attempted):
    state, _, _ = dll
    state.send_status, state.attempted = 8, attempted
    port = native.NativeMidiBackend().open_output("Dedicated MIDI")
    with pytest.raises(native.NativeMidiError) as error:
        port.send_wakeup()
    assert error.value.status == 8 and error.value.native_code == 91
    assert error.value.delivery_attempted is bool(attempted)
    assert str(error.value) == "Native failure" and state.sends == [7]
    port.close()


def test_mismatched_opened_name_is_closed_without_sending(dll):
    state, _, _ = dll
    state.name = b"Wrong port"
    with pytest.raises(RuntimeError, match="different output"):
        native.NativeMidiBackend().open_output("Dedicated MIDI")
    assert state.closes == [7] and state.sends == []


def test_unbounded_and_continually_growing_reads_are_rejected(dll):
    backend = native.NativeMidiBackend()

    def huge(buffer, size, required):
        required._obj.value = native.MAX_BUFFER + 1
        return 2

    with pytest.raises(RuntimeError, match="buffer limit"):
        backend._buffer(huge)

    def grows(buffer, size, required):
        required._obj.value = size + 1
        return 2

    with pytest.raises(RuntimeError, match="kept changing"):
        backend._buffer(grows)


def test_runtime_integrity_has_no_fallback(tmp_path):
    files = {}
    for name in native.RUNTIME_FILES:
        data = b"synthetic test binary"
        (tmp_path / name).write_bytes(data)
        files[name] = {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
    manifest = {"schema_version": 1, "architecture": "x64", "abi_version": 65536,
                "files": files}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    assert native.verify_runtime(tmp_path) == tmp_path / native.RUNTIME_FILES[0]
    (tmp_path / native.RUNTIME_FILES[0]).write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="integrity"):
        native.verify_runtime(tmp_path)


def test_missing_runtime_is_an_error_before_loading(monkeypatch, tmp_path):
    monkeypatch.setattr(native, "RUNTIME", tmp_path)
    monkeypatch.setattr(native.sys, "platform", "win32")
    monkeypatch.setattr(ct, "CDLL", lambda *a, **kw: pytest.fail("must verify first"))
    with pytest.raises(FileNotFoundError):
        native.NativeMidiBackend()
