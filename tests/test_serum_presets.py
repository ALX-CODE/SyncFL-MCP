"""Native preset integrity and recipes, using captured Serum 2.1.2 templates."""

import copy
import hashlib
import json
import struct

import pytest

from fl_studio_mcp.utils import serum_presets as serum

cbor = pytest.importorskip("cbor2")
zstd = pytest.importorskip("zstandard")


@pytest.fixture
def captured():
    return serum.read_preset(serum.DATA / "kick.SerumPreset")


def packet(header, body=None, *, raw=None, payload=None, size=None, mode=2):
    header = copy.deepcopy(header)
    if raw is None:
        raw = cbor.dumps(body)
    if payload is None:
        payload = zstd.ZstdCompressor().compress(raw)
    if isinstance(header, dict):
        header["hash"] = hashlib.md5(payload).hexdigest()
    encoded = json.dumps(header).encode()
    return (
        serum.MAGIC
        + struct.pack("<Q", len(encoded))
        + encoded
        + struct.pack("<II", len(raw) if size is None else size, mode)
        + payload
    )


def put(tmp_path, data):
    path = tmp_path / "input.SerumPreset"
    path.write_bytes(data)
    return path


@pytest.mark.parametrize("name", ["kick", "pad"])
def test_captured_templates_and_checksum(name):
    path = serum.DATA / (name + ".SerumPreset")
    header, body = serum.read_preset(path)
    data = path.read_bytes()
    length = struct.unpack_from("<Q", data, 9)[0]
    assert hashlib.md5(data[25 + length :]).hexdigest() == header["hash"]
    assert header["productVersion"] == body["productVersion"] == "2.1.2"
    assert header["version"] == body["version"] == 10


def test_codec_preserves_unknown_nested_fields_and_input(captured, tmp_path):
    header, body = captured
    body["unknown_future_field"] = {"data": b"\x00\xff", "items": [1, 2.5, None, False]}
    original = copy.deepcopy((header, body))
    path = tmp_path / "roundtrip.SerumPreset"
    result = serum.write_preset(path, header, body)
    _, reloaded = serum.read_preset(path)
    assert reloaded == body
    assert (header, body) == original
    assert result["file_verified"] is True
    assert result["loaded_in_serum"] is False
    assert result["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_checksum_rejects_corruption(tmp_path):
    data = bytearray((serum.DATA / "kick.SerumPreset").read_bytes())
    data[-1] ^= 1
    with pytest.raises(ValueError, match="checksum"):
        serum.read_preset(put(tmp_path, data))


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"not a preset",
        b"X" * 50,
        serum.MAGIC + struct.pack("<Q", 0) + b"x" * 20,
        serum.MAGIC + struct.pack("<Q", 65537) + b"x" * 20,
    ],
)
def test_invalid_magic_and_header_length(tmp_path, data):
    with pytest.raises(ValueError):
        serum.read_preset(put(tmp_path, data))


@pytest.mark.parametrize(
    "key,value",
    [
        ("version", 4),
        ("productVersion", "2.1.3"),
        ("product", "Serum"),
        ("fileType", "NotAPreset"),
    ],
)
def test_unsupported_header_metadata(captured, tmp_path, key, value):
    header, body = captured
    header[key] = value
    with pytest.raises(ValueError):
        serum.read_preset(put(tmp_path, packet(header, body)))


def test_nonmap_header_rejected(captured, tmp_path):
    with pytest.raises(ValueError):
        serum.read_preset(put(tmp_path, packet([], captured[1])))


def test_header_body_identity_mismatch(captured, tmp_path):
    header, body = captured
    body["presetName"] = "different"
    with pytest.raises(ValueError, match="disagree"):
        serum.read_preset(put(tmp_path, packet(header, body)))


def test_nonmap_body_rejected(captured, tmp_path):
    with pytest.raises(ValueError, match="map"):
        serum.read_preset(put(tmp_path, packet(captured[0], ["unexpected"])))


@pytest.mark.parametrize(
    "options", [{"mode": 1}, {"size": 0}, {"size": serum.MAX_RAW + 1}, {"size": 17}]
)
def test_payload_mode_and_size_limits(captured, tmp_path, options):
    with pytest.raises(ValueError):
        serum.read_preset(put(tmp_path, packet(*captured, **options)))


def test_file_limit_before_decode(monkeypatch, tmp_path):
    monkeypatch.setattr(serum, "MAX_FILE", 10)
    with pytest.raises(ValueError, match="size"):
        serum.read_preset(put(tmp_path, b"x" * 11))


def test_unknown_frame_size_rejected(captured, tmp_path):
    raw = cbor.dumps(captured[1])
    payload = zstd.ZstdCompressor(write_content_size=False).compress(raw)
    with pytest.raises(ValueError):
        serum.read_preset(put(tmp_path, packet(captured[0], raw=raw, payload=payload)))


def test_trailing_compressed_frame_rejected(captured, tmp_path):
    raw = cbor.dumps(captured[1])
    payload = zstd.ZstdCompressor().compress(raw) + zstd.ZstdCompressor().compress(b"extra")
    with pytest.raises((ValueError, zstd.ZstdError)):
        serum.read_preset(put(tmp_path, packet(captured[0], raw=raw, payload=payload)))


def test_trailing_cbor_object_rejected(captured, tmp_path):
    raw = cbor.dumps(captured[1]) + cbor.dumps({"extra": True})
    with pytest.raises(ValueError):
        serum.read_preset(put(tmp_path, packet(captured[0], raw=raw)))


@pytest.mark.parametrize("recipe", ["kick", "808", "lead", "pad"])
def test_build_all_actual_recipes_roundtrip(recipe, tmp_path):
    path = tmp_path / (recipe + ".SerumPreset")
    result = serum.build_preset(recipe, path)
    header, body = serum.read_preset(path)
    assert result["success"] and result["file_verified"]
    assert result["recipe"] == recipe
    assert result["loaded_in_serum"] is False
    assert header["presetName"] == body["presetName"] == recipe
    assert body["presetAuthor"] == "SyncFL"
    if recipe == "lead":
        assert body["Oscillator0"]["plainParams"]["kParamUnison"] == 3
        assert body["Env0"]["plainParams"]["kParamAttack"] == pytest.approx(0.002)
    elif recipe == "808":
        assert body["Env0"]["plainParams"]["kParamDecay"] == pytest.approx(1.8)
        assert body["ModSlot0"]["plainParams"]["kParamAmount"] == 10
    elif recipe == "kick":
        original = serum.read_preset(serum.DATA / "kick.SerumPreset")[1]
        for key in ("Oscillator0", "Env0", "Env1", "ModSlot0", "FXRack0", "Global0"):
            assert body[key] == original[key]


def test_physical_recipe_overrides(tmp_path):
    path = tmp_path / "overrides.SerumPreset"
    serum.build_preset(
        "kick",
        path,
        {
            "master_percent": 25,
            "level_percent": 50,
            "fine_cents": -32,
            "amp_decay_ms": 270,
            "amp_sustain_percent": 70,
            "mono": True,
            "unison_voices": 7,
            "detune": 0.04,
            "distortion_drive_percent": 88,
        },
    )
    _, body = serum.read_preset(path)
    assert body["Global0"]["plainParams"]["kParamMasterVolume"] == 0.0625
    assert body["Oscillator0"]["plainParams"]["kParamVolume"] == 0.25
    assert body["Oscillator0"]["plainParams"]["kParamFine"] == -32
    assert body["Env0"]["plainParams"]["kParamDecay"] == 0.270
    assert body["Env0"]["plainParams"]["kParamSustain"] == 0.7


@pytest.mark.parametrize(
    "settings",
    [
        {"unknown": 1},
        {"amp_decay_ms": float("nan")},
        {"detune": float("inf")},
        {"master_percent": True},
        {"mono": 1},
        {"unison_voices": True},
        {"unison_voices": 2.5},
        {"unison_voices": 0},
        {"unison_voices": 17},
        {"amp_attack_ms": -1},
        {"amp_decay_ms": 32001},
        {"master_percent": 71},
        {"detune": -0.01},
        {"amp_sustain_percent": 101},
        {"fine_cents": -101},
        {"distortion_drive_percent": 101},
        {"distortion_drive_percent": False},
    ],
)
def test_invalid_overrides_do_not_create_files(settings, tmp_path):
    path = tmp_path / "bad.SerumPreset"
    with pytest.raises(ValueError):
        serum.build_preset("kick", path, settings)
    assert not path.exists()


@pytest.mark.parametrize("settings", [False, 0, [], [1], "bad"])
def test_settings_requires_mapping(settings, tmp_path):
    path = tmp_path / "bad.SerumPreset"
    with pytest.raises(ValueError):
        serum.build_preset("kick", path, settings)
    assert not path.exists()


@pytest.mark.parametrize("settings", [{"distortion_drive_percent": 30}, {"pitch_decay_ms": 300}])
def test_unsupported_template_effect_or_route(settings, tmp_path):
    path = tmp_path / "bad.SerumPreset"
    with pytest.raises(ValueError):
        serum.build_preset("pad", path, settings)
    assert not path.exists()


def test_output_no_overwrite(captured, tmp_path):
    path = tmp_path / "protected.SerumPreset"
    path.write_bytes(b"user data")
    with pytest.raises(FileExistsError):
        serum.write_preset(path, *captured)
    with pytest.raises(FileExistsError):
        serum.build_preset("kick", path)
    assert path.read_bytes() == b"user data"


def test_invalid_write_metadata_does_not_leave_file(captured, tmp_path):
    header, body = captured
    body["version"] = 4
    path = tmp_path / "bad.SerumPreset"
    with pytest.raises(ValueError):
        serum.write_preset(path, header, body)
    assert not path.exists()


def test_invalid_write_extension(captured, tmp_path):
    path = tmp_path / "bad.wav"
    with pytest.raises(ValueError):
        serum.write_preset(path, *captured)
    assert not path.exists()


@pytest.mark.parametrize(
    "key,value",
    [
        ("presetName", None),
        ("presetAuthor", False),
        ("presetDescription", []),
        ("tags", "Wavetable"),
        ("tags", [1]),
    ],
)
def test_invalid_write_metadata_types(captured, tmp_path, key, value):
    header, body = captured
    body[key] = value
    path = tmp_path / "bad.SerumPreset"
    with pytest.raises(ValueError):
        serum.write_preset(path, header, body)
    assert not path.exists()


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_arbitrary_body_nonfinite_values_rejected(captured, tmp_path, value):
    header, body = captured
    body["nested_future_field"] = [{"bad": value}]
    path = tmp_path / "bad.SerumPreset"
    with pytest.raises(ValueError):
        serum.write_preset(path, header, body)
    assert not path.exists()


def test_oversized_write_header_rejected_before_file(captured, tmp_path):
    header, body = captured
    body["presetDescription"] = "a" * 65536
    path = tmp_path / "bad.SerumPreset"
    with pytest.raises(ValueError, match="header length"):
        serum.write_preset(path, header, body)
    assert not path.exists()


def test_catalog_is_not_mutable_shared_state():
    catalog = serum.recipe_catalog()
    catalog["recipes"]["kick"]["settings"]["master_percent"] = 70
    assert serum.RECIPES["kick"]["settings"] == {}


def test_unknown_recipe_and_invalid_name(tmp_path):
    path = tmp_path / "bad.SerumPreset"
    with pytest.raises(ValueError):
        serum.build_preset("strings", path)
    for name in ("", "  ", 42, "a" * 101):
        with pytest.raises(ValueError):
            serum.build_preset("kick", path, name=name)
    assert not path.exists()
