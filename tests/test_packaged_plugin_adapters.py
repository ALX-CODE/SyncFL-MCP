"""Offline contract checks against the shipped mappings, not synthetic catalogs.

These checks establish registry integrity, inverse mathematics and wheel contents.
They do not establish that a plugin build/DSP implements the recorded mapping.
"""

import hashlib
import math
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from fl_studio_mcp.utils import plugin_adapters as adapters
from fl_studio_mcp.utils import plugin_state
from fl_studio_mcp.utils.plugin_display import canonical_display_unit

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


@pytest.fixture(scope="module")
def packaged_registry():
    # One snapshot also makes these tests independent of concurrent calibration edits.
    registry, digest = adapters._load_registry()
    assert registry["adapters"], "The shipped catalog must contain a usable adapter"
    assert _SHA256.fullmatch(digest)
    return registry


def _analytic(control):
    curve = control.get("curve")
    return curve if curve and curve["kind"] != "table" else None


def _physical_bounds(control):
    curve = _analytic(control)
    values = (
        [curve["minimum"], curve["maximum"]]
        if curve
        else [point[1] for point in control["points"]]
    )
    return (
        max(min(values), control.get("minimum", -math.inf)),
        min(max(values), control.get("maximum", math.inf)),
    )


def _forward(control, normalized):
    """Evaluate the documented curve, independently of the production inverse."""
    curve = _analytic(control)
    if curve:
        low, high = curve["minimum"], curve["maximum"]
        if normalized == 0:
            return low
        if normalized == 1:
            return high
        kind = curve["kind"]
        if kind == "linear":
            fraction = normalized
        elif kind == "log":
            return math.exp(math.log(low) * (1 - normalized) + math.log(high) * normalized)
        elif kind == "power":
            fraction = normalized ** curve["exponent"]
        else:
            assert kind == "exp"
            k = curve["k"]
            # Stable even for a future strongly curved, but finite, calibration.
            fraction = (
                math.exp(k * (normalized - 1))
                * (-math.expm1(-k * normalized))
                / (-math.expm1(-k))
                if k > 50
                else math.expm1(k * normalized) / math.expm1(k)
            )
        return low + (high - low) * fraction
    for left, right in zip(control["points"], control["points"][1:]):
        if left[0] <= normalized <= right[0]:
            fraction = (normalized - left[0]) / (right[0] - left[0])
            return left[1] + (right[1] - left[1]) * fraction
    pytest.fail(f"Normalized result escaped the sampled domain: {control['key']}: {normalized}")


def _compile(adapter, control, field, value):
    result = adapters.compile_changes(adapter, [{"control": control["key"], field: value}])
    assert len(result) == 1
    change = result[0]
    assert change["parameter"] == control["parameter"]
    assert change["expected_name"] == control["name"]
    # The adapter output must remain accepted by the actual guarded consumer.
    plugin_state._validate_changes(result, adapter["plugin_names"][0], "a" * 64, False, None)
    return change


def test_shipped_identities_controls_and_evidence_are_unambiguous(packaged_registry):
    identifiers, bindings = set(), set()
    for adapter in packaged_registry["adapters"]:
        context = adapter["id"]
        assert context not in identifiers
        identifiers.add(context)
        assert _SHA256.fullmatch(adapter["layout_sha256"]), context
        evidence = adapter["evidence"]
        assert _SHA256.fullmatch(evidence.get("binary_sha256", "")), context
        assert any(
            isinstance(evidence.get(key), str) and evidence[key].strip()
            for key in ("version", "file_version", "registry_version")
        ), f"{context}: retain an observed binary/product version as provenance"
        for name in adapter["plugin_names"]:
            binding = (name, adapter["layout_sha256"], adapter["parameter_count"])
            assert binding not in bindings, context
            bindings.add(binding)
        controls = adapter["controls"]
        assert controls, context
        assert len({control["key"] for control in controls}) == len(controls), context
        assert len({control["parameter"] for control in controls}) == len(controls), context
        by_key = {control["key"]: control for control in controls}
        for control in controls:
            assert 0 <= control["parameter"] < adapter["parameter_count"], context
            for requirement in control.get("requires", []):
                selector = by_key[requirement["control"]]
                assert selector["kind"] == "enum", (context, control["key"])
                assert set(requirement["one_of"]) <= {
                    choice["label"] for choice in selector["choices"]
                }, (context, control["key"])


def test_all_shipped_enum_choices_compile_to_their_exact_literal_displays(packaged_registry):
    for adapter in packaged_registry["adapters"]:
        for control in adapter["controls"]:
            if control["kind"] != "enum":
                continue
            choices = control["choices"]
            context = (adapter["id"], control["key"])
            assert len({choice["label"] for choice in choices}) == len(choices), context
            assert len({choice["value"] for choice in choices}) == len(choices), context
            assert len({choice.get("display", choice["label"]) for choice in choices}) == len(
                choices
            ), context
            for choice in choices:
                assert choice["label"] == choice["label"].strip(), context
                change = _compile(adapter, control, "value", choice["label"])
                assert change["value"] == choice["value"], context
                assert change["expected_display"] == choice.get("display", choice["label"])
                assert "expected_numeric" not in change
            with pytest.raises(ValueError, match="exact declared choice"):
                _compile(adapter, control, "value", "\0not-a-declared-choice")


def test_all_shipped_numeric_mappings_round_trip_and_enforce_their_bounds(packaged_registry):
    for adapter in packaged_registry["adapters"]:
        for control in adapter["controls"]:
            if control["kind"] != "numeric":
                continue
            context = (adapter["id"], control["key"])
            if not _analytic(control) and not control.get("points"):
                with pytest.raises(ValueError, match="calibration_required"):
                    _compile(adapter, control, "value", 0)
                continue
            unit = control.get("display_unit", control["unit"])
            canonical_display_unit(unit)
            assert math.isfinite(control["tolerance"]) and control["tolerance"] >= 0, context
            low, high = _physical_bounds(control)
            assert low < high, f"{context}: metadata must leave a nonempty calibrated range"
            assert adapters._control_metadata(control)["calibrated_range"] == [low, high], context
            values = {low, high, (low + high) / 2, low * 0.75 + high * 0.25}
            if _analytic(control):
                values.update(
                    value for position in (0, 0.001, 0.125, 0.5, 0.875, 0.999, 1)
                    if low <= (value := _forward(control, position)) <= high
                )
            else:
                points = control["points"]
                values.update(point[1] for point in points if low <= point[1] <= high)
                values.update(
                    value for left, right in zip(points, points[1:])
                    if low <= (value := (left[1] + right[1]) / 2) <= high
                )
            for value in values:
                change = _compile(adapter, control, "value", value)
                normalized = change["value"]
                assert math.isfinite(normalized) and 0 <= normalized <= 1, context
                assert _forward(control, normalized) == pytest.approx(
                    value, abs=max(1, abs(low), abs(high)) * 2e-11, rel=2e-11
                ), (context, value, normalized)
                assert change["expected_numeric"] == {
                    "value": value, "unit": unit, "tolerance": control["tolerance"],
                }, context
                assert "expected_display" not in change
            for outside in (math.nextafter(low, -math.inf), math.nextafter(high, math.inf)):
                with pytest.raises(ValueError, match="outside calibrated range"):
                    _compile(adapter, control, "value", outside)
            for invalid in (True, False, math.nan, math.inf, -math.inf):
                with pytest.raises(ValueError):
                    _compile(adapter, control, "value", invalid)


def test_explicit_normalized_requests_do_not_claim_physical_verification(packaged_registry):
    for adapter in packaged_registry["adapters"]:
        for control in adapter["controls"]:
            for value in (0, 0.5, 1):
                change = _compile(adapter, control, "normalized", value)
                assert change == {
                    "parameter": control["parameter"], "expected_name": control["name"],
                    "value": value,
                }
            for invalid in (True, math.nan, math.inf, -0.00001, 1.00001):
                with pytest.raises(ValueError):
                    _compile(adapter, control, "normalized", invalid)


def test_wheel_contains_the_exact_registry_and_its_runtime_modules(tmp_path):
    pytest.importorskip("hatchling.build", reason="Install the declared wheel build backend")
    root = Path(__file__).resolve().parents[1]
    project = tmp_path / "package-source"
    package = project / "src" / "fl_studio_mcp"
    shutil.copytree(
        root / "src" / "fl_studio_mcp", package,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
    )
    runtime_directories = ("midi_bridge", "background_bridge", "midi_transport", "audio_tempo")
    for name in runtime_directories:
        relative = Path("native") / name / "runtime"
        shutil.copytree(root / relative, project / relative)
    for name in ("pyproject.toml", "README.md", "LICENSE"):
        if (root / name).is_file():
            shutil.copyfile(root / name, project / name)
    result = subprocess.run(
        [sys.executable, "-c",
         "from hatchling.build import build_wheel; print(build_wheel('dist'))"],
        cwd=project, capture_output=True, text=True, timeout=60, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    wheels = list((project / "dist").glob("*.whl"))
    assert len(wheels) == 1, result.stdout + result.stderr
    with zipfile.ZipFile(wheels[0]) as wheel:
        for relative in (
            "utils/plugin_adapters.json", "utils/plugin_adapters.py", "utils/plugin_state.py",
            "utils/plugin_display.py", "tools/plugin_adapters.py", "utils/native_midi_transport.py",
            "utils/key_analysis.py", "utils/tempo_analysis.py",
        ):
            expected = (package / relative).read_bytes()
            packaged = wheel.read("fl_studio_mcp/" + relative)
            assert packaged == expected, relative
        registry = wheel.read("fl_studio_mcp/utils/plugin_adapters.json")
        assert hashlib.sha256(registry).digest() == hashlib.sha256(
            (package / "utils" / "plugin_adapters.json").read_bytes()
        ).digest()
        for name in runtime_directories:
            runtime = project / "native" / name / "runtime"
            for source in runtime.rglob("*"):
                if source.is_file():
                    relative = source.relative_to(runtime).as_posix()
                    packaged = wheel.read(f"fl_studio_mcp/_native/{name}/{relative}")
                    assert packaged == source.read_bytes()
