"""Serum preset-loader regression tests without desktop or FL side effects."""

import asyncio
import math
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from fl_studio_mcp.utils import serum_windows as sw


def body():
    return {
        "Global0": {"plainParams": {"kParamMasterVolume": 0.16, "kParamMonoToggle": 1}},
        "Oscillator0": {
            "plainParams": {
                "kParamFine": -32,
                "kParamVolume": 0.5625,
                "kParamUnison": 7,
                "kParamDetune": 0.0625,
            }
        },
        "Env0": {
            "plainParams": {
                "kParamAttack": 0.0001,
                "kParamDecay": 0.27,
                "kParamSustain": 0,
                "kParamCurve2": 67,
            }
        },
        "Env1": {"plainParams": {"kParamRelease": 1.5}},
        "ModSlot0": {"source": [3, 0], "plainParams": {"kParamAmount": 25}},
    }


def test_expected_controls_uses_observed_physical_conversions_only():
    controls = sw.expected_controls(body())
    assert controls["master.volume_percent"] == pytest.approx(40)
    assert controls["voice.mono"] is True
    assert controls["osc_a.level_percent"] == pytest.approx(75)
    assert controls["osc_a.unison_normalized"] == pytest.approx(6 / 15)
    assert controls["osc_a.detune_normalized"] == pytest.approx(0.25)
    assert controls["env1.attack_ms"] == pytest.approx(0.1)
    assert controls["env2.release_ms"] == pytest.approx(1500)
    assert controls["mod1.amount_percent"] == 25
    assert "env1.hold_ms" not in controls


def test_sparse_presets_cannot_claim_verified_load():
    with pytest.raises(ValueError, match="enough mapped"):
        sw.expected_controls({})


def test_unrouted_modulation_not_counted():
    payload = body()
    payload["ModSlot0"].pop("source")
    assert "mod1.amount_percent" not in sw.expected_controls(payload)


@pytest.mark.parametrize(
    "key,display,expected,matches",
    [
        ("voice.mono", "On", True, True),
        ("voice.mono", "Off", True, False),
        ("master.volume_percent", "40% [-12.9 dB]", 40, True),
        ("osc_a.fine_cents", "-32", -32, True),
        ("env1.attack_ms", "0.1 ms", 0.1, True),
        ("env1.attack_ms", "0.2 ms", 0.1, False),
        ("env1.decay_ms", "270 ms", 270.4, True),
        ("env1.decay_ms", "270 ms", 271, False),
        ("env2.release_ms", "1.50 s", 1500, True),
        ("env2.release_ms", "1.50 s", 1510, False),
        ("env1.attack_ms", "270", 270, False),
        ("env1.sustain_normalized", "-inf dB", 0, True),
        ("env1.sustain_normalized", "--", 0, True),
        ("env1.sustain_normalized", "-inf dB", 0.5, False),
        ("env1.sustain_normalized", "-12.04 dB", 0.5, True),
        ("env1.sustain_normalized", "0 dB", 1, True),
        ("env1.sustain_normalized", "0.5", 0.5, False),
        ("osc_a.unison_normalized", "7", 6 / 15, True),
        ("osc_a.unison_normalized", "8", 6 / 15, False),
        ("osc_a.detune_normalized", "0.06", 0.25, True),
        ("osc_a.detune_normalized", "0.07", 0.25, False),
        ("mod1.amount_percent", "25%", 25, True),
        ("mod1.amount_percent", "not available", 25, False),
        ("mod1.amount_percent", "25%", math.nan, False),
    ],
)
def test_live_display_precision_and_units(key, display, expected, matches):
    assert sw.display_matches(key, display, expected) is matches


def test_missing_path_fails_before_connection_or_gui(tmp_path, monkeypatch):
    monkeypatch.setattr(sw, "get_connection", lambda: pytest.fail("No connection allowed"))
    with pytest.raises(FileNotFoundError):
        asyncio.run(sw.load_serum_preset(3, tmp_path / "missing.SerumPreset"))


def test_unreadable_preset_fails_before_connection_or_gui(tmp_path, monkeypatch):
    path = tmp_path / "bad.SerumPreset"
    path.write_bytes(b"bad")
    monkeypatch.setattr(sw, "get_connection", lambda: pytest.fail("No connection allowed"))
    monkeypatch.setattr(
        sw, "read_preset", lambda _: (_ for _ in ()).throw(PermissionError("locked"))
    )
    with pytest.raises(PermissionError):
        asyncio.run(sw.load_serum_preset(3, path))


@pytest.fixture
def loader(tmp_path, monkeypatch):
    path = tmp_path / "valid.SerumPreset"
    path.write_bytes(b"mock preset parser owns format validation")
    identity = {"channel": 3, "channel_name": "Serum 2"}
    editor = {"handle": "222", "title": "Serum 2", "bounds": [350, 250, 1200, 771]}
    events = []
    displays = {
        "master.volume_percent": "40%",
        "voice.mono": "On",
        "osc_a.fine_cents": "-32",
        "osc_a.level_percent": "75%",
        "osc_a.unison_normalized": "7",
        "osc_a.detune_normalized": "0.06",
        "env1.attack_ms": "0.1 ms",
        "env1.decay_ms": "270 ms",
        "env1.sustain_normalized": "-inf dB",
        "env1.decay_curve_percent": "67%",
        "env2.release_ms": "1.50 s",
        "mod1.amount_percent": "25%",
    }
    snapshot = {
        "identity": identity,
        "groups": {
            "controls": {key: {"display": value, "value": 0.999} for key, value in displays.items()}
        },
    }

    class FL:
        handle = "111"

        async def __aenter__(self):
            events.append("gui")
            return self

        async def __aexit__(self, *args):
            pass

        async def win(self, operation, **kwargs):
            events.append((operation, kwargs))
            if operation == "window_management" and kwargs["action"] == "find":
                return {"windows": [{"handle": self.handle, "bounds": [0, 0, 1920, 1080]}]}
            return {}

        async def click(self, *args, **kwargs):
            events.append("click")

        async def dialog(self, name):
            return {"handle": "333"}

        async def key(self, *args):
            pass

    monkeypatch.setattr(sw, "read_preset", lambda _: ({"presetName": "Test"}, body()))
    monkeypatch.setattr(sw, "get_connection", lambda: SimpleNamespace(_hardware_dir=tmp_path))
    monkeypatch.setattr(sw, "transaction_lock", lambda *_: nullcontext())
    monkeypatch.setattr(sw, "_workflow_lock", asyncio.Lock())
    monkeypatch.setattr(sw, "_stopped", lambda _: None)
    monkeypatch.setattr(sw, "_identity", lambda *_: identity.copy())
    monkeypatch.setattr(sw, "_native", lambda *args: None)
    monkeypatch.setattr(sw, "WindowsFL", FL)
    monkeypatch.setattr(sw, "find_editor", lambda *_: editor.copy())
    monkeypatch.setattr(sw, "_inspect_locked", lambda *_: snapshot)
    return SimpleNamespace(
        path=path, identity=identity, editor=editor, events=events, snapshot=snapshot
    )


def test_loader_uses_live_display_despite_stale_normalized_cache(loader):
    result = asyncio.run(sw.load_serum_preset(3, loader.path))
    assert result["success"]
    assert result["verified_controls"] == len(sw.expected_controls(body()))
    assert not result["mismatches"]
    assert loader.events.count("click") == 2


def test_loader_rejects_display_mismatch_even_if_cached_value_looks_right(loader):
    row = loader.snapshot["groups"]["controls"]["env1.decay_ms"]
    row.update(display="800 ms", value=270)
    result = asyncio.run(sw.load_serum_preset(3, loader.path))
    assert not result["success"]
    assert "env1.decay_ms" in result["mismatches"]
    assert result["mismatches"]["env1.decay_ms"]["actual_display"] == "800 ms"


def test_missing_control_is_unverified(loader):
    del loader.snapshot["groups"]["controls"]["env1.decay_ms"]
    result = asyncio.run(sw.load_serum_preset(3, loader.path))
    assert not result["success"]
    assert result["mismatches"]["env1.decay_ms"]["actual_display"] is None


def test_serum_unicode_infinity_sustain():
    assert sw.display_matches("env1.sustain_normalized", "-∞ dB", 0)
    assert not sw.display_matches("env1.sustain_normalized", "-∞ dB", .5)


def test_position_guard_prevents_menu_clicks(loader):
    loader.editor["bounds"][0] = 1000
    with pytest.raises(RuntimeError, match="exceed calibrated"):
        asyncio.run(sw.load_serum_preset(3, loader.path))
    assert "click" not in loader.events


def test_size_guard_failure_prevents_menu_clicks(loader, monkeypatch):
    def changed_size(*_):
        raise RuntimeError("Serum editor size differs from calibrated 1200x771; inspect UI")

    monkeypatch.setattr(sw, "find_editor", changed_size)
    with pytest.raises(RuntimeError, match="size differs"):
        asyncio.run(sw.load_serum_preset(3, loader.path))
    assert "click" not in loader.events


def test_identity_change_prevents_menu_clicks(loader, monkeypatch):
    identities = iter([loader.identity, {"channel": 3, "channel_name": "Other Serum"}])
    monkeypatch.setattr(sw, "_identity", lambda *_: next(identities))
    with pytest.raises(RuntimeError, match="moved or changed"):
        asyncio.run(sw.load_serum_preset(3, loader.path))
    assert "click" not in loader.events


def test_running_transport_fails_before_opening_gui(loader, monkeypatch):
    def playing(_):
        raise ValueError("Stop playback")

    monkeypatch.setattr(sw, "_stopped", playing)
    with pytest.raises(ValueError, match="Stop playback"):
        asyncio.run(sw.load_serum_preset(3, loader.path))
    assert "gui" not in loader.events


def test_identity_change_after_load_cannot_report_success(loader):
    loader.snapshot["identity"] = {"channel": 3, "channel_name": "Replaced"}
    with pytest.raises(RuntimeError, match="identity changed after"):
        asyncio.run(sw.load_serum_preset(3, loader.path))
