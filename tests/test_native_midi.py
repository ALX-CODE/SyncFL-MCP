import hashlib
import json
import subprocess
from types import SimpleNamespace

import pytest

from fl_studio_mcp.utils import native_midi as native


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    files = {}
    for name in native.RUNTIME_FILES:
        content = name.encode()
        (tmp_path / name).write_bytes(content)
        files[name] = {"sha256": hashlib.sha256(content).hexdigest()}
    (tmp_path / "manifest.json").write_text(
        json.dumps(dict(schema_version=1, files=files)), encoding="utf-8"
    )
    monkeypatch.setattr(native, "RUNTIME", tmp_path)
    monkeypatch.setattr(native.sys, "platform", "win32")
    monkeypatch.delenv("FL_MCP_MIDI_PORT", raising=False)
    return tmp_path


def response(**overrides):
    return dict(transport_available=True, present=True, created=False, removed=False,
                output_name="ALX FL Bridge (Send)", input_name="ALX FL Bridge (FL Studio)",
                association_id="test-pair", **overrides)


def test_verified_runtime_runs_bounded_hidden_ensure(runtime, monkeypatch):
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout=json.dumps(response()), stderr="")

    monkeypatch.setattr(native.subprocess, "run", run)
    assert native.initialize_native_midi()["success"] is True
    assert calls[0][0] == [str(runtime / native.RUNTIME_FILES[0]), "ensure", "--json"]
    assert calls[0][1]["timeout"] == 12
    assert "creationflags" in calls[0][1]


def test_status_never_ensures(runtime, monkeypatch):
    def run(args, **kwargs):
        assert args[1] == "status"
        return SimpleNamespace(returncode=0, stdout=json.dumps(response()), stderr="")

    monkeypatch.setattr(native.subprocess, "run", run)
    assert native.native_midi_status()["success"] is True


@pytest.mark.parametrize("bad", [None, [], {}, {"schema_version": 2, "files": {}}])
def test_invalid_manifest_never_launches(runtime, monkeypatch, bad):
    (runtime / "manifest.json").write_text(json.dumps(bad), encoding="utf-8")
    monkeypatch.setattr(native.subprocess, "run", lambda *a, **k: pytest.fail("must not launch"))
    assert native.initialize_native_midi()["success"] is False


@pytest.mark.parametrize("name", native.RUNTIME_FILES)
def test_tampered_binary_never_launches(runtime, monkeypatch, name):
    (runtime / name).write_bytes(b"changed")
    monkeypatch.setattr(native.subprocess, "run", lambda *a, **k: pytest.fail("must not launch"))
    result = native.initialize_native_midi()
    assert result["success"] is False
    assert "hash mismatch" in result["error"]


@pytest.mark.parametrize("value", [None, [], {}, {"transport_available": "true"}])
def test_bad_helper_response_fails(runtime, monkeypatch, value):
    monkeypatch.setattr(native.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=0, stdout=json.dumps(value), stderr=""
    ))
    assert native.native_midi_status()["success"] is False


def test_wrong_endpoint_identity_fails(runtime, monkeypatch):
    value = response()
    value["output_name"] = "another device"
    monkeypatch.setattr(native.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=0, stdout=json.dumps(value), stderr=""
    ))
    assert native.native_midi_status()["success"] is False


def test_timeout_is_explicit(runtime, monkeypatch):
    def run(*a, **k):
        raise subprocess.TimeoutExpired("helper", 12)

    monkeypatch.setattr(native.subprocess, "run", run)
    assert native.initialize_native_midi()["success"] is False


def test_explicit_legacy_selection_skips_native_launch(runtime, monkeypatch):
    monkeypatch.setenv("FL_MCP_MIDI_PORT", "FL Studio MCP 1")
    monkeypatch.setattr(native.subprocess, "run", lambda *a, **k: pytest.fail("must not launch"))
    assert native.initialize_native_midi()["enabled"] is False


def test_nonwindows_skips_launch(runtime, monkeypatch):
    monkeypatch.setattr(native.sys, "platform", "darwin")
    monkeypatch.setattr(native.subprocess, "run", lambda *a, **k: pytest.fail("must not launch"))
    assert native.initialize_native_midi()["enabled"] is False
