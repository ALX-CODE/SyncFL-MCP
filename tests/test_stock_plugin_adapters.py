"""Stock coverage and evidence contracts; no live FL session is required."""

import json
import re
from collections import Counter
from pathlib import Path

import pytest

from fl_studio_mcp.utils import plugin_adapters

STOCK_PLUGINS = {
    "fruity_blood_overdrive": "Fruity Blood Overdrive",
    "fruity_chorus": "Fruity Chorus",
    "fruity_compressor": "Fruity Compressor",
    "fruity_convolver": "Fruity Convolver",
    "fruity_delay": "Fruity Delay",
    "fruity_delay_2": "Fruity Delay 2",
    "fruity_delay_3": "Fruity Delay 3",
    "fruity_delay_bank": "Fruity Delay Bank",
    "fruity_fast_dist": "Fruity Fast Dist",
    "fruity_flanger": "Fruity Flanger",
    "fruity_flangus": "Fruity Flangus",
    "fruity_limiter": "Fruity Limiter",
    "fruity_love_philter": "Fruity Love Philter",
    "fruity_multiband_compressor": "Fruity Multiband Compressor",
    "fruity_parametric_eq_2": "Fruity Parametric EQ 2",
    "fruity_phaser": "Fruity Phaser",
    "fruity_reeverb": "Fruity Reeverb",
    "fruity_reeverb_2": "Fruity Reeverb 2",
    "fruity_soft_clipper": "Fruity Soft Clipper",
    "fruity_stereo_enhancer": "Fruity Stereo Enhancer",
    "fruity_stereo_shaper": "Fruity Stereo Shaper",
    "maximus": "Maximus",
    "soundgoodizer": "Soundgoodizer",
    "transient_processor": "Transient Processor",
    "gross_beat": "Gross Beat",
    "tuner": "Tuner",
    "3x_osc": "3x Osc",
    "fl_keys": "FL Keys",
    "fl_slayer": "FL Slayer",
    "morphine": "Morphine",
    "ogun": "Ogun",
    "poizone": "PoiZone",
    "sakura": "Sakura",
    "sawer": "Sawer",
    "sytrus": "Sytrus",
    "toxic_biohazard": "Toxic Biohazard",
}
STOCK_IDS = set(STOCK_PLUGINS)
CONTROL_KINDS = ("numeric", "enum", "normalized")
WITHHELD_AFTER_HOST_EXIT = {"sakura", "sawer"}
WRITE_REACHABLE_BASELINES = {"morphine", "toxic_biohazard"}


@pytest.fixture(scope="module")
def stock_profiles():
    registry, _ = plugin_adapters._load_registry()
    stock_rows = [profile for profile in registry["adapters"] if profile["id"] in STOCK_IDS]
    assert len(stock_rows) == len({profile["id"] for profile in stock_rows})
    profiles = {profile["id"]: profile for profile in registry["adapters"]}
    missing = STOCK_IDS - profiles.keys()
    assert not missing, f"Missing stock profiles: {missing}"
    return {identifier: profiles[identifier] for identifier in STOCK_IDS}


@pytest.fixture(scope="module")
def stock_evidence():
    root = Path(__file__).resolve().parents[1]
    evidence = json.loads((root / "docs/plugin-calibration-evidence.json").read_bytes())
    rows = [profile for profile in evidence["profiles"] if profile["id"] in STOCK_IDS]
    assert len(rows) == len({profile["id"] for profile in rows})
    profiles = {profile["id"]: profile for profile in rows}
    assert profiles.keys() == STOCK_IDS
    return profiles


def test_all_requested_stock_plugins_are_shipped(stock_profiles):
    assert stock_profiles.keys() == STOCK_IDS
    assert {
        identifier: profile["title"] for identifier, profile in stock_profiles.items()
    } == STOCK_PLUGINS


@pytest.mark.parametrize("identifier", sorted(STOCK_IDS))
def test_stock_profiles_have_completed_restored_matching_evidence(
    stock_profiles, stock_evidence, identifier,
):
    profile = stock_profiles[identifier]
    evidence = stock_evidence[identifier]
    assert evidence["title"] == profile["title"]
    assert evidence["layout_sha256"] == profile["layout_sha256"]
    expected_stable = identifier not in WITHHELD_AFTER_HOST_EXIT | {"sytrus"}
    assert evidence["stable_calibration"] is expected_stable
    # Completion and restoration describe the inventory/holdout traversal;
    # they must not imply physical calibration after an interrupted sweep.
    assert evidence["completed"] is (identifier != "sytrus")
    assert evidence["whole_exposed_state_restored"] is (identifier != "sytrus")
    assert evidence["verification"].strip()
    for field in ("baseline_sha256", "holdout_sha256"):
        assert evidence[field] == profile["evidence"][field]


@pytest.mark.parametrize("identifier", sorted(STOCK_IDS))
def test_stock_evidence_counts_match_shipped_control_kinds(
    stock_profiles, stock_evidence, identifier,
):
    controls = stock_profiles[identifier]["controls"]
    evidence = stock_evidence[identifier]
    counts = Counter(control["kind"] for control in controls)
    assert evidence["counts"] == {kind: counts[kind] for kind in CONTROL_KINDS}
    assert sum(evidence["counts"].values()) == len(controls)
    assert evidence["qualified_controls"] == counts["numeric"] + counts["enum"]
    # Candidate checks may fail and be downgraded; the report must retain those
    # failures instead of claiming that every exploratory mapping was qualified.
    assert 0 <= evidence["passed_checks"] <= evidence["holdout_checks"]
    assert evidence["withheld_controls"] == len(evidence["downgraded"])
    downgraded = set(evidence["downgraded"])
    assert len(downgraded) == len(evidence["downgraded"])
    assert downgraded <= {control["key"] for control in controls if control["kind"] == "normalized"}


def test_stock_evidence_totals_match_registry_without_fixed_qualification_counts(
    stock_profiles, stock_evidence,
):
    registry_counts = Counter(
        control["kind"] for profile in stock_profiles.values() for control in profile["controls"]
    )
    evidence_counts = Counter()
    for evidence in stock_evidence.values():
        evidence_counts.update(evidence["counts"])
    assert evidence_counts == registry_counts
    assert sum(entry["qualified_controls"] for entry in stock_evidence.values()) == (
        registry_counts["numeric"] + registry_counts["enum"]
    )


@pytest.mark.parametrize("identifier", sorted(STOCK_IDS))
def test_stock_raw_evidence_hashes_are_valid_sha256(stock_profiles, stock_evidence, identifier):
    profile = stock_profiles[identifier]
    for field in ("binary_sha256", "baseline_sha256", "calibration_sha256", "holdout_sha256"):
        assert re.fullmatch(r"[0-9a-f]{64}", profile["evidence"].get(field, "")), field
    for field in ("layout_sha256", "baseline_sha256", "holdout_sha256"):
        assert re.fullmatch(r"[0-9a-f]{64}", stock_evidence[identifier].get(field, "")), field


def test_soundgoodizer_is_honestly_detection_only(stock_profiles):
    profile = stock_profiles["soundgoodizer"]
    assert profile["parameter_count"] == 0
    assert profile["controls"] == []
    notes = " ".join(profile.get("notes", []) + profile.get("cautions", []))
    assert re.search(r"\bdetection[ -]only\b", notes, re.IGNORECASE), notes


@pytest.mark.parametrize("identifier", ["fruity_delay", "fruity_reeverb", "fl_slayer"])
def test_legacy_plugins_do_not_claim_physical_calibration(stock_profiles, identifier):
    controls = stock_profiles[identifier]["controls"]
    assert controls
    assert all(control["kind"] == "normalized" for control in controls)
    assert all(
        not ({"curve", "points", "choices", "normalized_tolerance"} & control.keys())
        for control in controls
    )


def test_delay_3_layout_changing_selector_remains_normalized_only(stock_profiles):
    controls = stock_profiles["fruity_delay_3"]["controls"]
    control = next(control for control in controls if control["parameter"] == 1)
    assert control["name"] == "Delay type"
    assert control["kind"] == "normalized"
    assert not ({"curve", "points", "choices", "normalized_tolerance"} & control.keys())
    notes = " ".join(control.get("notes", [])).casefold()
    assert "delay type" in notes
    assert any(word in notes for word in ("changed", "renamed"))
    assert "exposed parameter" in notes
    assert "layout" in notes


@pytest.mark.parametrize("identifier", sorted(WITHHELD_AFTER_HOST_EXIT))
def test_host_exit_withholds_physical_mappings_and_reports_incomplete_calibration(
    stock_profiles, stock_evidence, identifier,
):
    profile = stock_profiles[identifier]
    evidence = stock_evidence[identifier]
    assert profile["controls"]
    assert all(control["kind"] == "normalized" for control in profile["controls"])
    assert all(
        not ({"curve", "points", "choices", "normalized_tolerance"} & control.keys())
        for control in profile["controls"]
    )
    assert profile["evidence"]["calibration_complete"] is False
    assert profile["evidence"]["calibration_status"] == "withheld_after_host_exit"
    assert evidence["calibration_status"] == "withheld_after_host_exit"
    assert evidence["stable_calibration"] is False
    assert evidence["qualified_controls"] == 0
    assert evidence["holdout_checks"] == 0
    assert evidence["passed_checks"] == 0


def test_sytrus_invalidated_baseline_withholds_all_physical_mappings(
    stock_profiles, stock_evidence,
):
    profile = stock_profiles["sytrus"]
    evidence = stock_evidence["sytrus"]
    assert len(profile["controls"]) == 597
    assert all(control["kind"] == "normalized" for control in profile["controls"])
    assert all(
        not ({"curve", "points", "choices", "requires", "normalized_tolerance"} & control.keys())
        for control in profile["controls"]
    )
    assert profile["evidence"]["calibration_complete"] is False
    assert profile["evidence"]["calibration_status"] == "withheld_after_baseline_change"
    assert evidence["calibration_status"] == "withheld_after_baseline_change"
    assert evidence["stable_calibration"] is False
    assert evidence["completed"] is False
    assert evidence["whole_exposed_state_restored"] is False
    assert evidence["qualified_controls"] == 0
    assert evidence["holdout_checks"] == 0
    assert evidence["passed_checks"] == 0
    assert evidence["excluded_holdout_checks"] > 0


@pytest.mark.parametrize("identifier", sorted(WRITE_REACHABLE_BASELINES))
def test_quantized_presets_identify_the_test_baseline_without_claiming_original_restoration(
    stock_profiles, stock_evidence, identifier,
):
    profile_evidence = stock_profiles[identifier]["evidence"]
    assert profile_evidence["baseline_kind"] == "write-reachable lab baseline"
    assert stock_evidence[identifier]["baseline_kind"] == profile_evidence["baseline_kind"]
    # Setters cannot reproduce every original preset value; restoring the
    # separate test baseline must not be presented as restoring that preset.
    assert profile_evidence["original_preset_restored"] is False
    assert re.fullmatch(r"[0-9a-f]{64}", profile_evidence.get("original_preset_sha256", ""))


@pytest.mark.parametrize("identifier", sorted(STOCK_IDS))
def test_stock_profiles_preserve_binary_evidence_and_unambiguous_controls(
    stock_profiles, identifier,
):
    profile = stock_profiles[identifier]
    assert re.fullmatch(r"[0-9a-f]{64}", profile["evidence"].get("binary_sha256", ""))
    controls = profile["controls"]
    # Repeated display names are valid across synth operators; keys and native
    # parameter indices must each identify one control unambiguously.
    assert len({control["key"] for control in controls}) == len(controls)
    assert len({control["parameter"] for control in controls}) == len(controls)
    for control in controls:
        assert control["key"].strip()
        assert control["name"].strip()
        assert 0 <= control["parameter"] < profile["parameter_count"]
