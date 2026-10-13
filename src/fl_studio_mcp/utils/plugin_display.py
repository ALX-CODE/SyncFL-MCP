"""Parse literal host values without inferring a parameter's scale or meaning.

Observed host spellings include ``sec`` for seconds and compact frequencies such
as ``2k5 Hz`` (2500 Hz). Compact notation requires an explicit Hz suffix.
"""

import math
import re
from decimal import Decimal, DecimalException

_NUMBER = r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?"
_DISPLAY = re.compile(
    rf"(?P<number>{_NUMBER})\s*(?P<unit>dBTP|dB|%|ms|sec|s|k\s*Hz|Hz)?", re.IGNORECASE
)
_COMPACT_HZ = re.compile(
    r"(?P<sign>[+-]?)(?P<whole>[0-9]+)k(?P<fraction>[0-9]*)\s*Hz", re.IGNORECASE
)
_RATIO = re.compile(rf"(?P<number>{_NUMBER})\s*:\s*1(?:\.0*)?")
_UNITS = {
    unit.casefold(): unit for unit in ("", "dB", "dBTP", "%", "ms", "s", "Hz", "kHz", "ratio")
}
_UNITS["sec"] = "s"
_SCALES = {
    "ms": ("time", Decimal("0.001")),
    "s": ("time", Decimal(1)),
    "Hz": ("frequency", Decimal(1)),
    "kHz": ("frequency", Decimal(1000)),
}


def canonical_display_unit(unit):
    """Validate a supported unit; an exact empty string requests a unitless value."""
    if not isinstance(unit, str) or (unit and not unit.strip()):
        raise ValueError("Display unit must be a supported unit string, or '' for unitless")
    canonical = _UNITS.get(unit.strip().casefold())
    if canonical is None:
        raise ValueError(
            "Unsupported display unit; use dB, dBTP, %, ms, s/sec, Hz, kHz, ratio or ''"
        )
    return canonical


def parse_display_number(display, unit):
    """Return a finite display number in the requested compatible unit.

    Only a single complete numeric display is accepted. Ratios use N:1; their
    denominator is never guessed. Unitless, percent, dB and dBTP stay distinct.
    Time and frequency may be explicitly converted within their own dimensions;
    sec is an alias for s, and 2k5 Hz is the explicit frequency spelling 2.5 kHz.
    """
    target = canonical_display_unit(unit)
    if not isinstance(display, str) or not display.strip():
        raise ValueError("Host display is missing or is not text")
    text = display.strip()
    if target == "ratio":
        match = _RATIO.fullmatch(text)
        if match is None:
            raise ValueError("Host display must contain one finite N:1 ratio")
        source = "ratio"
        number = match["number"]
    else:
        compact = _COMPACT_HZ.fullmatch(text)
        if compact is not None:
            number = compact["sign"] + compact["whole"] + "." + (compact["fraction"] or "0")
            source = "kHz"
        else:
            match = _DISPLAY.fullmatch(text)
            if match is None:
                raise ValueError("Host display is not one unambiguous supported numeric value")
            source = canonical_display_unit(re.sub(r"\s+", "", match["unit"] or ""))
            number = match["number"]
    try:
        value = Decimal(number)
        nonzero = value != 0
        if source != target:
            source_scale, target_scale = _SCALES.get(source), _SCALES.get(target)
            if source_scale is None or target_scale is None or source_scale[0] != target_scale[0]:
                raise ValueError(
                    f"Host display unit {source!r} does not match requested unit {target!r}"
                )
            value = value * source_scale[1] / target_scale[1]
        result = float(value)
    except (DecimalException, OverflowError) as exc:
        raise ValueError("Host display number is outside the finite number range") from exc
    if not math.isfinite(result) or (nonzero and result == 0):
        raise ValueError("Host display number is outside the finite representable number range")
    return result
