"""Observed literal host spellings; no plugin or desktop access."""

import pytest

from fl_studio_mcp.utils.plugin_display import canonical_display_unit, parse_display_number


@pytest.mark.parametrize(
    "display,unit,expected",
    [
        ("2.500 sec ", "s", 2.5),
        ("2.500 sec ", "ms", 2500),
        ("-0.025 SEC", "ms", -25),
        ("+1e-3sec", "s", 0.001),
        ("2500 ms", "sec", 2.5),
        ("2k5 Hz", "Hz", 2500),
        ("2k5 Hz", "kHz", 2.5),
        ("2k500 Hz", "Hz", 2500),
        ("1k Hz", "Hz", 1000),
        ("16kHz", "Hz", 16000),
        ("+16KHz", "kHz", 16),
        ("-2k5 Hz", "Hz", -2500),
        ("-0k5 Hz", "Hz", -500),
        ("0k001 Hz", "Hz", 1),
        ("-0k000 Hz", "Hz", 0),
        (" 2k50 hZ ", "Hz", 2500),
        ("20.00k Hz", "Hz", 20000),
        ("1e2 kHz", "Hz", 100000),
        ("214.3 msec", "ms", 214.3),
        ("1500.0 msec", "s", 1.5),
        ("214.3 msec", "msec", 214.3),
        ("-12.0 semitones", "semitones", -12),
        ("140.0 bpm", "bpm", 140),
    ],
)
def test_observed_time_and_compact_frequency_spellings(display, unit, expected):
    assert parse_display_number(display, unit) == expected


def test_seconds_alias_has_one_canonical_unit():
    assert canonical_display_unit("sec") == "s"
    assert canonical_display_unit(" SEC ") == "s"
    assert canonical_display_unit("msec") == "ms"


@pytest.mark.parametrize(
    "display,unit",
    [
        ("2k5", "Hz"), ("2k5", ""), ("2k5 ms", "ms"), ("2k5 Hz", "ms"),
        ("2k5 Hz", "dB"), ("2k5 Hz", ""), ("2.500 sec", "Hz"), ("2.500 sec", ""),
        ("2.500 seconds", "s"), ("2.500 s ec", "s"), ("2,500 sec", "s"),
        ("2k,5 Hz", "Hz"), ("2k.5 Hz", "Hz"), ("2.5k5 Hz", "Hz"),
        ("2 k5 Hz", "Hz"), ("2k5kHz", "Hz"), ("2k5 Hz Hz", "Hz"),
        ("2k5 Hz dB", "Hz"), ("12 dB dB", "dB"), ("2.5 sec s", "s"),
        ("k5 Hz", "Hz"), ("--2k5 Hz", "Hz"), ("+-2k5 Hz", "Hz"),
        ("2k-5 Hz", "Hz"), ("2k5e2 Hz", "Hz"), ("NaNk5 Hz", "Hz"),
        ("inf sec", "s"), ("NaN sec", "s"), ("1e9999 sec", "s"),
        ("1e-999 sec", "s"), ("1e-999 sec", "ms"),
        ("9" * 400 + "k5 Hz", "Hz"), ("0k" + "0" * 400 + "1 Hz", "Hz"),
        ("3.0 semitones", ""), ("3.0 semitones", "%"), ("3.0", "semitones"),
        ("140.0 bpm", "Hz"), ("140.0 bpm", ""), ("2.0 semitone", "semitones"),
        ("214.3 msec", "Hz"), ("214.3 msec", ""), ("214.3 m sec", "ms"),
    ],
)
def test_malformed_wrong_unit_nonfinite_and_unrepresentable_formats_fail(display, unit):
    with pytest.raises(ValueError):
        parse_display_number(display, unit)
