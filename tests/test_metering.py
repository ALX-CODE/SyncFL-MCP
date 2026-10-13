import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from fl_studio_mcp.utils import metering


class Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class Connection:
    def __init__(self):
        self.native = json.loads(
            (Path(__file__).parents[1] / "fl_controller" / "native_catalog.json").read_text(
                encoding="utf-8"
            )
        )
        self.context = dict(session="one", project_generation=0, channel_generation=0)
        self.names = ["Master", "Vocal", "Beat"]
        self.peaks = [0.25, 0.5, 0.75]
        self.playing = False
        self.calls = []
        self.project_reads = 0
        self.peak_rounds = 0
        self.on_project = None
        self.on_peaks = None
        self.incomplete = False
        self.fail_round = None

    def send_command(self, action, params, timeout):
        self.calls.append((action, copy.deepcopy(params), timeout))
        assert 0 < timeout <= 2
        if action == "bridge.capabilities":
            return dict(success=True, native=self.native, typed=["bridge.batch"])
        if action == "production.inspect":
            assert params == {"scope": "project"}
            self.project_reads += 1
            if self.on_project:
                self.on_project(self)
            return dict(
                success=True,
                context=copy.deepcopy(self.context),
                is_playing=self.playing,
                is_recording=False,
            )
        assert action == "bridge.batch" and params["dry_run"] is False
        steps = params["steps"]
        contains_peaks = any(
            step["params"]["function"] == "mixer.getTrackPeaks" for step in steps
        )
        if contains_peaks:
            self.peak_rounds += 1
            if self.on_peaks:
                self.on_peaks(self)
            if self.fail_round == self.peak_rounds:
                raise TimeoutError("Controller response timed out")
        results = []
        for step in steps:
            assert step["action"] == "native.call"
            data = step["params"]
            assert set(data) == {"function", "args"}
            function, args = data["function"], data["args"]
            assert self.native[function]["kind"] == "read"
            if function == "mixer.getTrackInfo":
                assert args == [2]
                value = len(self.names) - 1
            elif function == "mixer.getTrackName":
                value = self.names[args[0]]
            else:
                assert function == "mixer.getTrackPeaks" and args[1] in (0, 1, 2)
                value = self.peaks[args[1]]
            results.append({"value": value})
        return dict(
            success=True,
            applied=len(steps),
            results=results[:-1] if self.incomplete else results,
        )


@pytest.fixture
def clock(monkeypatch):
    fake = Clock()
    monkeypatch.setattr(
        metering, "time", SimpleNamespace(monotonic=fake.monotonic, sleep=fake.sleep)
    )
    return fake


@pytest.fixture
def conn():
    return Connection()


def test_meter_sampling_is_read_only_bounded_and_tracks_observed_values(conn, clock):
    result = metering.watch_mixer_meters([2, 0], 0.2, 50, ["Beat", "Master"], conn)
    assert result["success"] is True and result["complete"] is True
    assert result["samples"] == 4
    assert result["elapsed_seconds"] == 0.2
    assert result["setup_seconds"] == 0
    assert result["observation_elapsed_seconds"] == 0.2
    assert result["observed_duration_seconds"] == 0.15
    assert [row["track"] for row in result["tracks"]] == [2, 0]
    for row in result["tracks"]:
        assert row["samples"] == 4 and row["peak"] == 0.75
        assert row["left_peak"] == 0.25 and row["right_peak"] == 0.5
        assert row["peak_dbfs"] == pytest.approx(-2.49877473)
    assert result["playback_observed"] is False
    assert "not LUFS" in " ".join(result["limitations"])


@pytest.mark.parametrize(
    "kwargs",
    [
        {"tracks": []}, {"tracks": [True]}, {"tracks": [1, 1]}, {"tracks": [-1]},
        {"tracks": list(range(33))}, {"seconds": 0}, {"seconds": 10.1},
        {"seconds": float("nan")}, {"seconds": True}, {"interval_ms": 49},
        {"interval_ms": 100.0}, {"expected_names": ["other", "count"]},
        {"expected_names": {1: "Vocal"}},
    ],
)
def test_bad_arguments_fail_before_any_controller_request(conn, clock, kwargs):
    params = dict(tracks=[1], seconds=0.1, interval_ms=50, connection=conn)
    params.update(kwargs)
    with pytest.raises(ValueError):
        metering.watch_mixer_meters(**params)
    assert not conn.calls


@pytest.mark.parametrize("field,value", [("kind", "write"), ("arguments", ["index"])])
def test_unsupported_meter_signature_is_incomplete_without_sampling(conn, clock, field, value):
    conn.native["mixer.getTrackPeaks"][field] = value
    result = metering.watch_mixer_meters([1], 0.1, 50, connection=conn)
    assert not result["complete"] and result["samples"] == 0
    assert len(conn.calls) == 1 and "capabilities" in result["errors"][0]


def test_wrong_expected_name_does_not_read_peaks(conn, clock):
    result = metering.watch_mixer_meters([1], 0.1, 50, ["Other"], conn)
    assert result["samples"] == 0 and "expected_names" in result["errors"][0]
    assert conn.peak_rounds == 0


def test_nonexistent_track_is_reported_before_querying_its_name(conn, clock):
    result = metering.watch_mixer_meters([3], 0.1, 50, connection=conn)
    assert result["samples"] == 0 and "does not exist" in result["errors"][0]
    assert conn.peak_rounds == 0


@pytest.mark.parametrize("field,new", [("session", "two"), ("project_generation", 1)])
def test_project_change_discards_inflight_sample_and_preserves_prior(conn, clock, field, new):
    def change(connection):
        if connection.peak_rounds == 2:
            connection.context[field] = new

    conn.on_peaks = change
    result = metering.watch_mixer_meters([1], 0.2, 50, connection=conn)
    assert result["complete"] is False and result["samples"] == 1
    assert result["tracks"][0]["samples"] == 1
    assert "changed" in result["errors"][0]


def test_track_rename_stops_watch_without_accepting_changed_sample(conn, clock):
    def rename(connection):
        if connection.peak_rounds == 2:
            connection.names[1] = "New instrument"

    conn.on_peaks = rename
    result = metering.watch_mixer_meters([1], 0.2, 50, connection=conn)
    assert result["samples"] == 1 and result["complete"] is False
    assert result["tracks"][0]["name"] == "Vocal"
    assert "track identity changed" in result["errors"][0]


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), -0.1, True, "0.5", None])
def test_invalid_native_peaks_cannot_be_reported_as_measurements(conn, clock, invalid):
    conn.peaks[1] = invalid
    result = metering.watch_mixer_meters([1], 0.1, 50, connection=conn)
    assert result["samples"] == 0 and result["complete"] is False
    assert result["tracks"][0]["peak_dbfs"] is None
    assert "Invalid native peak" in result["errors"][0]


def test_silence_is_valid_and_has_no_finite_db_value(conn, clock):
    conn.peaks = [0, 0, 0]
    result = metering.watch_mixer_meters([1], 0.1, 50, connection=conn)
    assert result["success"] is True and result["tracks"][0]["peak"] == 0
    assert result["tracks"][0]["peak_dbfs"] is None


def test_timeout_retains_only_completed_observations(conn, clock):
    conn.fail_round = 2
    result = metering.watch_mixer_meters([1], 0.2, 50, connection=conn)
    assert result["success"] is False and result["samples"] == 1
    assert result["tracks"][0]["samples"] == 1
    assert "timed out" in result["errors"][0]


def test_partial_batch_is_never_a_successful_watch(conn, clock):
    conn.incomplete = True
    result = metering.watch_mixer_meters([1], 0.1, 50, connection=conn)
    assert result["samples"] == 0 and result["complete"] is False
    assert "Incomplete native" in result["errors"][0]


def test_playback_changes_are_observed_without_commands_to_change_them(conn, clock):
    conn.on_peaks = lambda connection: setattr(connection, "playing", True)
    result = metering.watch_mixer_meters([1], 0.1, 50, connection=conn)
    assert result["success"] and result["playback_observed"]
    assert result["transport_start"]["is_playing"] is False
    assert result["transport_end"]["is_playing"] is True


def test_controller_time_is_part_of_the_sampling_window(conn, clock):
    def slow(connection):
        clock.now += 0.06

    conn.on_peaks = slow
    result = metering.watch_mixer_meters([1], 0.1, 50, connection=conn)
    assert result["samples"] == 1 and result["elapsed_seconds"] == 0.1
    assert result["complete"] is True


def test_late_controller_response_is_incomplete_and_not_counted(conn, clock):
    conn.on_peaks = lambda connection: setattr(clock, "now", 2.1)
    result = metering.watch_mixer_meters([1], 0.1, 50, connection=conn)
    assert result["samples"] == 0 and result["complete"] is False
    assert "exceeded" in result["errors"][0]


@pytest.mark.parametrize("latency", [0.015, 0.025, 0.04, 0.075])
def test_responsive_controller_completes_with_realistic_call_latency(conn, clock, latency):
    original = conn.send_command

    def delayed(action, params, timeout):
        clock.now += latency
        return original(action, params, timeout)

    conn.send_command = delayed

    result = metering.watch_mixer_meters([0], seconds=2, connection=conn)

    assert result["success"] is True and result["complete"] is True
    assert not result["errors"]
    assert result["samples"] >= 2
    assert result["setup_seconds"] == pytest.approx(4 * latency)
    assert result["observation_elapsed_seconds"] >= 2
    assert result["elapsed_seconds"] >= result["setup_seconds"] + 2
    assert result["elapsed_seconds"] <= result["max_elapsed_seconds"]
    assert 0 < result["observed_duration_seconds"] <= result["observation_elapsed_seconds"]
    assert result["observed_duration_seconds"] == pytest.approx(
        result["last_sample_seconds"] - result["first_sample_seconds"]
    )


def test_last_sample_can_finish_identity_check_after_sampling_window(conn, clock):
    original = conn.send_command
    peak_starts = []

    def delayed(action, params, timeout):
        clock.now += 0.04
        if action == "bridge.batch" and any(
            step["params"]["function"] == "mixer.getTrackPeaks" for step in params["steps"]
        ):
            peak_starts.append(clock.now)
        return original(action, params, timeout)

    conn.send_command = delayed

    result = metering.watch_mixer_meters([0], seconds=2, connection=conn)

    assert result["complete"] is True
    observation_deadline = result["setup_seconds"] + 2
    assert result["elapsed_seconds"] > observation_deadline
    assert result["last_sample_seconds"] < result["elapsed_seconds"]
    assert all(start - 0.08 < observation_deadline for start in peak_starts)
    assert result["transport_end"] == {"is_playing": False, "is_recording": False}


def test_preflight_has_its_own_bounded_budget(conn, clock):
    original = conn.send_command

    def delayed(action, params, timeout):
        clock.now += 0.7
        return original(action, params, timeout)

    conn.send_command = delayed

    result = metering.watch_mixer_meters([0], seconds=10, connection=conn)

    assert result["complete"] is False and result["samples"] == 0
    assert result["observation_elapsed_seconds"] == 0
    assert result["observed_duration_seconds"] == 0
    assert result["setup_seconds"] == 2.1
    assert conn.peak_rounds == 0
    assert "exceeded" in result["errors"][0]


def test_incomplete_final_identity_read_does_not_accept_peak_values(conn, clock):
    def slow_final_identity(connection):
        if connection.project_reads == 3:
            clock.now += 2.1

    conn.on_project = slow_final_identity

    result = metering.watch_mixer_meters([0], seconds=0.1, connection=conn)

    assert result["complete"] is False and result["samples"] == 0
    assert result["observed_duration_seconds"] == 0
    assert result["tracks"][0]["peak_dbfs"] is None
