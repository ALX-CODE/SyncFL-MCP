"""Bounded, read-only sampling of FL's native mixer meters.

Samples are bracketed by project identities and track names. They are meter
observations, not decoded audio, true peaks, loudness or proof of clipping.
"""

import math
import time

from fl_studio_mcp.utils.midi_connection import get_connection
from fl_studio_mcp.utils.recording_state import _supported

_SETUP_SECONDS = 2.0
_SAMPLE_SECONDS = 2.0
_READS = {
    "mixer.getTrackInfo": ["mode"],
    "mixer.getTrackName": ["index"],
    "mixer.getTrackPeaks": ["index", "mode"],
}
LIMITATIONS = [
    "Native peak meters are sampled; transients between observations may be missed.",
    "These readings are not LUFS, waveform RMS, true peaks, or proof of clipping.",
    "Playback and recording are only observed, never started, stopped or changed.",
    "Same-name track replacement between reads may not be detectable by FL's API.",
    "Observed duration is the span of discrete sample reads, not continuous audio coverage.",
]


def _validate(tracks, seconds, interval_ms, expected_names):
    if (
        not isinstance(tracks, list)
        or not 1 <= len(tracks) <= 32
        or any(type(track) is not int or not 0 <= track <= 1023 for track in tracks)
        or len(set(tracks)) != len(tracks)
    ):
        raise ValueError("tracks must contain 1..32 unique nonnegative integer indices")
    if type(seconds) not in (int, float) or not math.isfinite(seconds) or not 0.1 <= seconds <= 10:
        raise ValueError("seconds must be finite and within 0.1..10")
    if type(interval_ms) is not int or not 50 <= interval_ms <= 10000:
        raise ValueError("interval_ms must be an integer within 50..10000")
    if expected_names is not None and (
        not isinstance(expected_names, list)
        or len(expected_names) != len(tracks)
        or any(not isinstance(name, str) for name in expected_names)
    ):
        raise ValueError("expected_names must be a list of exact names aligned with tracks")


def _call(conn, action, params, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Meter watch time budget exhausted")
    result = conn.send_command(action, params, min(remaining, 2.0))
    if time.monotonic() > deadline:
        raise TimeoutError("Controller response exceeded the meter watch time budget")
    if not isinstance(result, dict) or result.get("success") is not True:
        raise RuntimeError(f"Meter watch {action} failed: {result}")
    return result


def _native(conn, calls, deadline):
    response = _call(
        conn,
        "bridge.batch",
        dict(
            dry_run=False,
            steps=[
                dict(action="native.call", params=dict(function=name, args=args))
                for name, args in calls
            ],
        ),
        deadline,
    )
    results = response.get("results")
    if (
        response.get("applied") != len(calls)
        or not isinstance(results, list)
        or len(results) != len(calls)
        or any(
            not isinstance(row, dict)
            or "value" not in row
            or row.get("success") is False
            or row.get("error")
            for row in results
        )
    ):
        raise RuntimeError("Incomplete native meter readback")
    return [row["value"] for row in results]


def _project(conn, deadline):
    state = _call(conn, "production.inspect", {"scope": "project"}, deadline)
    context = state.get("context")
    if (
        not isinstance(context, dict)
        or not isinstance(context.get("session"), str)
        or not context["session"]
        or type(context.get("project_generation")) is not int
        or context["project_generation"] < 0
        or type(state.get("is_playing")) is not bool
        or type(state.get("is_recording")) is not bool
    ):
        raise RuntimeError("Incomplete project identity or transport readback")
    identity = {key: context[key] for key in ("session", "project_generation")}
    if "channel_generation" in context:
        if type(context["channel_generation"]) is not int or context["channel_generation"] < 0:
            raise RuntimeError("Invalid channel generation readback")
        identity["channel_generation"] = context["channel_generation"]
    transport = {key: state[key] for key in ("is_playing", "is_recording")}
    return identity, transport


def _peak(value):
    if type(value) not in (float, int) or not math.isfinite(value) or value < 0:
        raise RuntimeError("Invalid native peak readback")
    return float(value)


def _db(value):
    return 20 * math.log10(value) if value > 0 else None


def watch_mixer_meters(
    tracks, seconds=2.0, interval_ms=100, expected_names=None, connection=None
):
    """Sample up to 32 exact tracks for a requested window of up to ten seconds.

    Setup has a separate two-second budget. No sample starts after the window,
    but a sample already in progress has up to two seconds to finish its identity
    checks. Each complete sample round is itself bounded to two seconds. Reported
    elapsed and observed durations include the actual work, not just the request.
    Connection failures return partial evidence with explicit incomplete status.
    Validation errors fail before any controller request. There is no background
    worker or retained meter cache to accidentally reuse for another project.
    """
    _validate(tracks, seconds, interval_ms, expected_names)
    conn = connection if connection is not None else get_connection()
    started = time.monotonic()
    deadline = started + _SETUP_SECONDS
    observation_started = None
    rows = [dict(track=track, name=None, samples=0) for track in tracks]
    result = dict(
        success=False,
        status="incomplete",
        complete=False,
        source="mixer.getTrackPeaks",
        modes={"left": 0, "right": 1, "maximum": 2},
        requested_seconds=seconds,
        max_elapsed_seconds=seconds + _SETUP_SECONDS + _SAMPLE_SECONDS,
        interval_ms=interval_ms,
        samples=0,
        project=None,
        tracks=rows,
        errors=[],
        limitations=list(LIMITATIONS),
    )
    try:
        capabilities = _call(conn, "bridge.capabilities", {}, deadline)
        native = capabilities.get("native")
        typed = capabilities.get("typed")
        if (
            not isinstance(native, dict)
            or not isinstance(typed, list)
            or "bridge.batch" not in typed
            or any(not _supported(native, name, args) for name, args in _READS.items())
        ):
            raise RuntimeError("Required native meter read capabilities are missing/incompatible")

        identity, transport = _project(conn, deadline)
        result["project"] = identity
        result["transport_start"] = transport
        result["transport_end"] = transport
        result["playback_observed"] = transport["is_playing"]
        maximum = _native(conn, [("mixer.getTrackInfo", [2])], deadline)[0]
        if type(maximum) is not int or not 0 <= maximum <= 1023:
            raise RuntimeError("Invalid mixer track extent")
        if any(track > maximum for track in tracks):
            raise RuntimeError("Requested mixer track does not exist")
        names = _native(
            conn, [("mixer.getTrackName", [track]) for track in tracks], deadline
        )
        if any(not isinstance(name, str) for name in names):
            raise RuntimeError("Invalid mixer track identity")
        if expected_names is not None and names != expected_names:
            raise RuntimeError("Mixer track names do not match expected_names")
        for row, name in zip(rows, names):
            row["name"] = name

        calls = []
        for track in tracks:
            calls.append(("mixer.getTrackName", [track]))
            calls.extend(("mixer.getTrackPeaks", [track, mode]) for mode in range(3))
            calls.append(("mixer.getTrackName", [track]))

        observation_started = time.monotonic()
        observation_deadline = observation_started + seconds
        while time.monotonic() < observation_deadline:
            sample_deadline = min(
                time.monotonic() + _SAMPLE_SECONDS, observation_deadline + _SAMPLE_SECONDS
            )
            observed, transport_before = _project(conn, sample_deadline)
            if observed != identity:
                raise RuntimeError("Project or controller session changed during meter watch")
            sample_time = time.monotonic() - started
            values = _native(conn, calls, sample_deadline)
            sample_end = time.monotonic() - started
            observed, transport_after = _project(conn, sample_deadline)
            if observed != identity:
                raise RuntimeError("Project or controller session changed during meter sample")
            pending = []
            for index, name in enumerate(names):
                before_name, left, right, maximum, after_name = values[index * 5 : index * 5 + 5]
                if before_name != name or after_name != name:
                    raise RuntimeError("Mixer track identity changed during meter watch")
                pending.append((_peak(left), _peak(right), _peak(maximum)))

            # Accept a whole round only after both identities and every value agree.
            for row, (left, right, maximum) in zip(rows, pending):
                row["samples"] += 1
                row["left_peak"] = max(left, row.get("left_peak", 0.0))
                row["right_peak"] = max(right, row.get("right_peak", 0.0))
                row["peak"] = max(maximum, row.get("peak", 0.0))
            result["samples"] += 1
            result.setdefault("first_sample_seconds", sample_time)
            result["last_sample_seconds"] = sample_end
            result["transport_end"] = transport_after
            result["playback_observed"] |= (
                transport_before["is_playing"] or transport_after["is_playing"]
            )
            remaining = observation_deadline - time.monotonic()
            if remaining > 0:
                time.sleep(min(interval_ms / 1000, remaining))

        if not result["samples"]:
            raise RuntimeError("No complete meter samples were observed")
        result.update(success=True, status="complete", complete=True)
    except (RuntimeError, TimeoutError, OSError, ValueError) as exc:
        result["errors"].append(f"{type(exc).__name__}: {exc}")
    finally:
        finished = time.monotonic()
        result["elapsed_seconds"] = round(finished - started, 6)
        setup_finished = observation_started if observation_started is not None else finished
        result["setup_seconds"] = round(setup_finished - started, 6)
        result["observation_elapsed_seconds"] = (
            round(finished - observation_started, 6) if observation_started is not None else 0.0
        )
        result["observed_duration_seconds"] = (
            round(result["last_sample_seconds"] - result["first_sample_seconds"], 6)
            if result["samples"] else 0.0
        )
        for row in rows:
            row["peak_dbfs"] = _db(row["peak"]) if "peak" in row else None
            row["left_peak_dbfs"] = _db(row["left_peak"]) if "left_peak" in row else None
            row["right_peak_dbfs"] = _db(row["right_peak"]) if "right_peak" in row else None
    return result
