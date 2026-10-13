"""Serum 2.1.2 native presets: bounded codec and recipes captured from our own patches.

The payload is CBOR, not JSON. Unknown fields survive edits. This is deliberately
version-scoped; a successful file round trip alone does not prove audible quality.
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
import math
import struct
import time
from pathlib import Path

MAGIC = b"XferJson\0"
MAX_FILE = 32 * 1024 * 1024
MAX_RAW = 64 * 1024 * 1024
DATA = Path(__file__).resolve().parents[1] / "data" / "serum"


def _deps():
    try:
        import cbor2
        import zstandard
    except ImportError as exc:
        raise RuntimeError("Install fl-studio-mcp[serum] for native Serum presets") from exc
    return cbor2, zstandard


def read_preset(path):
    path = Path(path)
    if path.stat().st_size > MAX_FILE:
        raise ValueError("Preset exceeds the 32 MiB supported size")
    return _decode(path.read_bytes())


def _metadata(metadata):
    if not isinstance(metadata, dict):
        raise ValueError("Preset metadata must be a map")
    if metadata.get("product") != "Serum2" or metadata.get("fileType") != "SerumPreset":
        raise ValueError("Only Serum 2 instrument presets are supported")
    if metadata.get("version") != 10 or metadata.get("productVersion") != "2.1.2":
        raise ValueError("Only verified Serum 2.1.2 / preset version 10 is supported")
    if not isinstance(metadata.get("presetName"), str):
        raise ValueError("Preset name must be text")


def _finite_values(value):
    """Reject nonfinite numbers before writing an arbitrary decoded body."""
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Preset values must be finite")
    if isinstance(value, dict):
        for key, item in value.items():
            _finite_values(key)
            _finite_values(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _finite_values(item)


def _decode(b):
    if len(b) > MAX_FILE:
        raise ValueError("Preset exceeds the 32 MiB supported size")
    if len(b) < 25 or b[:9] != MAGIC:
        raise ValueError("Not a Serum 2 native preset")
    n = struct.unpack_from("<Q", b, 9)[0]
    if not 0 < n <= 65536 or 25 + n >= len(b):
        raise ValueError("Invalid preset header length")
    h = json.loads(b[17 : 17 + n])
    _metadata(h)
    size, mode = struct.unpack_from("<II", b, 17 + n)
    if mode != 2 or not 0 < size <= MAX_RAW:
        raise ValueError("Unsupported compression or payload size")
    payload = b[25 + n :]
    if hashlib.md5(payload).hexdigest() != h.get("hash"):
        raise ValueError("Preset payload checksum mismatch")
    cbor, zstd = _deps()
    # A frame's declared size must agree before decompression allocates memory.
    content_size = zstd.frame_content_size(payload)
    if content_size != size:
        raise ValueError("Compressed frame length does not match preset header")
    raw = zstd.ZstdDecompressor().decompress(
        payload, max_output_size=MAX_RAW, allow_extra_data=False
    )
    if len(raw) != size:
        raise ValueError("Truncated preset payload")
    stream = io.BytesIO(raw)
    body = cbor.load(stream)
    if stream.read(1):
        raise ValueError("Unexpected trailing CBOR data")
    if not isinstance(body, dict):
        raise ValueError("Preset body must be a map")
    _metadata(body)
    for key in ("product", "productVersion", "version", "fileType", "presetName"):
        if body.get(key) != h.get(key):
            raise ValueError(f"Header/body disagree: {key}")
    return h, body


def write_preset(path, header, body):
    """Create exclusively; never replace an existing preset or user edit."""
    path = Path(path).expanduser().absolute()
    if path.suffix.casefold() != ".serumpreset":
        raise ValueError("Destination must end in .SerumPreset")
    if path.exists():
        raise FileExistsError(path)
    _metadata(header)
    _metadata(body)
    _finite_values(body)
    for key in ("presetAuthor", "presetDescription"):
        if not isinstance(body.get(key), str):
            raise ValueError(f"{key} must be text")
    if not isinstance(body.get("tags"), list) or any(
        not isinstance(tag, str) for tag in body["tags"]
    ):
        raise ValueError("Preset tags must be a list of text values")
    cbor, zstd = _deps()
    raw = cbor.dumps(body)
    if len(raw) > MAX_RAW:
        raise ValueError("Preset body too large")
    payload = zstd.ZstdCompressor(level=3).compress(raw)
    h = copy.deepcopy(header)
    for key in (
        "product",
        "productVersion",
        "version",
        "fileType",
        "presetName",
        "presetAuthor",
        "presetDescription",
        "tags",
    ):
        h[key] = body[key]
    h["hash"] = hashlib.md5(payload).hexdigest()
    encoded = json.dumps(h, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
    if not 0 < len(encoded) <= 65536:
        raise ValueError("Invalid preset header length")
    result = MAGIC + struct.pack("<Q", len(encoded)) + encoded
    result += struct.pack("<II", len(raw), 2) + payload
    if len(result) > MAX_FILE:
        raise ValueError("Encoded preset too large")
    # Validate the complete serialized artifact before publishing any file.
    actual_header, actual_body = _decode(result)
    if actual_body != body:
        raise ValueError("Preset cannot round-trip exactly (including nonfinite values)")
    # Exclusive creation protects against concurrent writers after preflight.
    with path.open("xb") as stream:
        stream.write(result)
    actual_header, actual_body = read_preset(path)
    if actual_body != body:
        raise RuntimeError("Written preset did not round-trip exactly")
    return {
        "success": True,
        "path": str(path),
        "bytes": len(result),
        "sha256": hashlib.sha256(result).hexdigest(),
        "preset_name": actual_header["presetName"],
        "file_verified": True,
        "loaded_in_serum": False,
    }


# Each field is an observed Serum physical payload value. User-facing sustain
# and master values are knob percentages; master uses Serum's squared gain law.
FIELDS = {
    "master_percent": ("Global0", "kParamMasterVolume", 0, 70, "square_percent"),
    "mono": ("Global0", "kParamMonoToggle", 0, 1, "bool"),
    "fine_cents": ("Oscillator0", "kParamFine", -100, 100, "plain"),
    "level_percent": ("Oscillator0", "kParamVolume", 0, 100, "square_percent"),
    "unison_voices": ("Oscillator0", "kParamUnison", 1, 16, "integer"),
    "detune": ("Oscillator0", "kParamDetune", 0, 1, "plain"),
}
for env, module in (("amp", "Env0"), ("pitch", "Env1")):
    for part in ("attack", "hold", "decay", "release"):
        FIELDS[f"{env}_{part}_ms"] = (module, "kParam" + part.title(), 0, 32000, "ms")
    FIELDS[f"{env}_sustain_percent"] = (module, "kParamSustain", 0, 100, "percent")
    FIELDS[f"{env}_decay_curve"] = (module, "kParamCurve2", 0, 100, "plain")
FIELDS["pitch_amount_percent"] = ("ModSlot0", "kParamAmount", -100, 100, "plain")

RECIPES = {
    "kick_long": {
        "template": "kick_long",
        "description": "Sine kick with a 1.65-second bass tail and extended pitch fall.",
        "note": 32,
        "hold_seconds": 1.85,
        "settings": {},
    },
    "kick": {
        "template": "kick",
        "description": "Short sine kick with a unipolar pitch fall and hard clipping.",
        "note": 32,
        "hold_seconds": 0.6,
        "settings": {},
    },
    "808": {
        "template": "kick",
        "description": "Long mono sine bass with a short pitch transient and lighter clipping.",
        "note": 32,
        "hold_seconds": 1.8,
        "settings": {
            "amp_hold_ms": 0,
            "amp_decay_ms": 1800,
            "amp_release_ms": 90,
            "amp_decay_curve": 48,
            "pitch_decay_ms": 45,
            "pitch_amount_percent": 10,
            "fine_cents": 0,
            "distortion_drive_percent": 82,
            "master_percent": 32,
        },
    },
    "lead": {
        "template": "pad",
        "description": "Three-voice saw lead with a fast envelope and plate space.",
        "note": 60,
        "hold_seconds": 0.5,
        "settings": {
            "mono": True,
            "unison_voices": 3,
            "detune": 0.018,
            "amp_attack_ms": 2,
            "amp_hold_ms": 0,
            "amp_decay_ms": 350,
            "amp_sustain_percent": 65,
            "amp_release_ms": 100,
            "master_percent": 30,
        },
    },
    "pad": {
        "template": "pad",
        "description": "Seven-voice saw pad, slow attack, long release and plate space.",
        "note": 48,
        "hold_seconds": 2,
        "settings": {},
    },
}


def recipe_catalog():
    return {
        "serum_version": "2.1.2",
        "preset_version": 10,
        "recipes": copy.deepcopy(RECIPES),
        "settings": {
            key: {"minimum": v[2], "maximum": v[3], "unit": v[4]} for key, v in FIELDS.items()
        },
        "distortion_drive_percent": "0..100; kick/808 only",
        "note": "Recipes are starting points. Hearing the result remains the musical test.",
    }


def _apply(body, settings):
    for key, value in settings.items():
        if key == "distortion_drive_percent":
            spec = (None, None, 0, 100, "plain")
        elif key in FIELDS:
            spec = FIELDS[key]
        else:
            raise ValueError(f"Unknown Serum setting: {key}")
        module, parameter, low, high, unit = spec
        if unit == "bool":
            if type(value) is not bool:
                raise ValueError(f"{key} requires a boolean")
            numeric = float(value)
        else:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{key} requires a number")
            numeric = float(value)
        if not math.isfinite(numeric) or not low <= numeric <= high:
            raise ValueError(f"{key} must be within {low}..{high}")
        if unit == "integer" and not numeric.is_integer():
            raise ValueError(f"{key} requires an integer")
        if key.startswith("pitch_"):
            route = body.get("ModSlot0", {})
            if (
                route.get("source") != [3, 0]
                or route.get("destModuleID") != 0
                or route.get("destModuleTypeString") != "Oscillator"
                or route.get("destModuleParamName") != "kParamCoarsePit"
            ):
                raise ValueError("This template has no verified Env 2 to A pitch envelope routing")
        if unit == "ms":
            numeric /= 1000
        elif unit == "percent":
            numeric /= 100
        elif unit == "square_percent":
            numeric = (numeric / 100) ** 2
        if key == "distortion_drive_percent":
            effects = body.get("FXRack0", {}).get("FX", [])
            candidates = [x["FXDistortion"] for x in effects if "FXDistortion" in x]
            if len(candidates) != 1:
                raise ValueError("Template must have exactly one main-rack distortion")
            candidates[0].setdefault("plainParams", {})["kParamDrive"] = numeric
        else:
            body[module].setdefault("plainParams", {})[parameter] = numeric


def build_preset(recipe, output_path, settings=None, name=None):
    started = time.perf_counter()
    if not isinstance(recipe, str) or recipe not in RECIPES:
        raise ValueError("Recipe must be kick, kick_long, 808, lead or pad")
    if settings is not None and not isinstance(settings, dict):
        raise ValueError("settings must be an object of supported Serum fields")
    item = RECIPES[recipe]
    header, body = read_preset(DATA / (item["template"] + ".SerumPreset"))
    _apply(body, item["settings"])
    _apply(body, settings or {})
    name = name if name is not None else Path(output_path).stem
    if not isinstance(name, str) or not name.strip() or len(name) > 100:
        raise ValueError("Preset name must contain 1..100 characters")
    body["presetName"] = name.strip()
    body["presetAuthor"] = "SyncFL"
    body["presetDescription"] = item["description"]
    body["tags"] = [
        "Wavetable",
        "Mono" if body["Global0"]["plainParams"].get("kParamMonoToggle", 0) else "Poly",
    ]
    result = write_preset(output_path, header, body)
    result.update(
        recipe=recipe,
        audition_note=item["note"],
        hold_seconds=item["hold_seconds"],
        elapsed_ms=round((time.perf_counter() - started) * 1000, 2),
    )
    return result


def edit_preset(source_path, output_path, settings, name=None):
    """Change mapped values in an existing saved patch, preserving its other DSP data."""
    if not isinstance(settings, dict) or not settings:
        raise ValueError("settings must be a nonempty object")
    header, body = read_preset(source_path)
    _apply(body, settings)
    if name is not None:
        if not isinstance(name, str) or not name.strip() or len(name) > 100:
            raise ValueError("Preset name must contain 1..100 characters")
        body["presetName"] = name.strip()
    result = write_preset(output_path, header, body)
    result["source_path"] = str(Path(source_path).resolve())
    result["changed_settings"] = settings
    return result


def inspect_preset(path):
    h, b = read_preset(path)
    return {
        "name": h["presetName"],
        "version": h["productVersion"],
        "author": h.get("presetAuthor"),
        "description": h.get("presetDescription"),
        "envelopes": {k: b[k].get("plainParams", {}) for k in ("Env0", "Env1")},
        "oscillator_a": b.get("Oscillator0"),
        "global": b.get("Global0"),
        "modulation": {k: v for k, v in b.items() if k.startswith("ModSlot") and v.get("source")},
        "effects": {k: v for k, v in b.items() if k.startswith("FXRack") and v.get("FX")},
        "note": "Only nondefault values are serialized; absent fields are not zero.",
    }
