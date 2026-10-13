import json
import math

import pytest

from fl_studio_mcp.utils.mixer_scene import apply_mixer_scene


class Connection:
    def __init__(self, path):
        self._hardware_dir = path
        self.tracks = {
            1: {"name": "Lead", "volume": 0.8, "pan": 0.0, "stereo": 0.0},
            2: {"name": "Pad", "volume": 0.7, "pan": 0.0, "stereo": 0.0},
        }
        self.calls = []
        self.writes = []
        self.playing = False
        self.fail = None
        self.rename_after_write = False
        self.ignore_write = False
        self.quantize_floor = 0.0
        self.volume_round_digits = None
        self.db_override = {}

    def send_command(self, action, params, timeout):
        self.calls.append((action, params))
        if action == "transport.getStatus":
            return {"success": True, "is_playing": self.playing, "is_recording": False}
        assert action == "bridge.batch"
        results = []
        for i, step in enumerate(params["steps"]):
            p = step["params"]
            if step["action"] == "native.call":
                f, args = p["function"], p["args"]
                if f == "mixer.getTrackInfo":
                    value = 2
                else:
                    track = self.tracks[args[0]]
                    key = {
                        "mixer.getTrackName": "name",
                        "mixer.getTrackVolume": "volume",
                        "mixer.getTrackPan": "pan",
                        "mixer.getTrackStereoSep": "stereo",
                    }[f]
                    value = track[key]
                    if f == "mixer.getTrackVolume" and len(args) == 2:
                        # Deliberately synthetic law: tests must not encode FL's law.
                        value = 40 * math.log10(value) + 5 if value else -math.inf
                        if args[0] in self.db_override:
                            value = self.db_override[args[0]]
                results.append({"value": value})
            else:
                assert step["action"] == "mixer.configure"
                self.writes.append(dict(p))
                if not self.ignore_write:
                    self.tracks[p["track"]].update({k: v for k, v in p.items() if k != "track"})
                    if "volume" in p and p["volume"] < self.quantize_floor:
                        self.tracks[p["track"]]["volume"] = 0.0
                    if "volume" in p and self.volume_round_digits is not None:
                        self.tracks[p["track"]]["volume"] = round(
                            self.tracks[p["track"]]["volume"], self.volume_round_digits
                        )
                if self.rename_after_write:
                    self.tracks[1]["name"] = "Different"
                if self.fail:
                    mode, self.fail = self.fail, None
                    if mode == "timeout":
                        raise TimeoutError("Mutation outcome unknown")
                    return {
                        "success": False,
                        "partial": True,
                        "failed_step": i,
                        "applied": i,
                        "error": "simulated partial failure",
                        "results": results,
                    }
                results.append({})
        return {"success": True, "results": results, "applied": len(results)}


@pytest.fixture
def conn(tmp_path):
    return Connection(tmp_path)


def item(**values):
    return {"track": 1, "expected_name": "Lead", "pan": -0.1, **values}


@pytest.mark.parametrize(
    "values",
    [
        {"track": True},
        {"track": -1},
        {"volume": True},
        {"pan": math.nan},
        {"volume_db": math.inf},
        {"volume": -0.1},
        {"stereo": 1.1},
        {"volume": 0.8, "volume_db": 0},
        {"expected_name": ""},
        {"wat": 1},
    ],
)
def test_invalid_input_before_connection(conn, values):
    with pytest.raises(ValueError):
        apply_mixer_scene([item(**values)], connection=conn)
    assert not conn.calls


def test_duplicate_tracks_rejected(conn):
    with pytest.raises(ValueError, match="unique"):
        apply_mixer_scene([item(), item()], connection=conn)
    assert not conn.calls


def test_identity_and_transport_guards(conn):
    with pytest.raises(ValueError, match="identity"):
        apply_mixer_scene([item(expected_name="Wrong")], connection=conn)
    conn.playing = True
    with pytest.raises(ValueError, match="stopped"):
        apply_mixer_scene([item()], connection=conn)
    assert not conn.writes


def test_nonexistent_track(conn):
    with pytest.raises(ValueError, match="does not exist"):
        apply_mixer_scene([item(track=3)], connection=conn)


def test_dry_run_never_probes_or_writes(conn):
    report = apply_mixer_scene([item(volume_db=-2)], connection=conn)
    assert report["success"] and report["dry_run"]
    assert report["restore_scene"] == [item(volume=0.8, pan=0.0)]
    assert not conn.writes


def test_success_batches_normalized_and_measured_db(conn, tmp_path):
    path = tmp_path / "receipt.json"
    report = apply_mixer_scene(
        [item(volume=0.6), {"track": 2, "expected_name": "Pad", "volume_db": -1.7, "stereo": 0.1}],
        True,
        path,
        conn,
    )
    assert report["success"] and report["status"] == "completed"
    assert conn.tracks[1]["volume"] == 0.6
    assert abs(40 * math.log10(conn.tracks[2]["volume"]) + 5 + 1.7) <= 0.02
    assert json.loads(path.read_text()) == report
    first_write = next(
        p
        for a, p in conn.calls
        if a == "bridge.batch" and p["steps"][0]["action"] == "mixer.configure"
    )
    assert len(first_write["steps"]) == 2


def test_unique_receipt_preserves_prior_restore(conn, tmp_path):
    path = tmp_path / "used.json"
    path.write_text("existing")
    with pytest.raises(FileExistsError):
        apply_mixer_scene([item()], True, path, conn)
    assert path.read_text() == "existing" and not conn.writes


def test_partial_acknowledged_failure_restores_and_reports(conn, tmp_path):
    conn.fail = "partial"
    path = tmp_path / "failure.json"
    report = apply_mixer_scene([item(volume=0.4)], True, path, conn)
    assert not report["success"] and report["partial"]
    assert report["restoration"]["success"]
    assert conn.tracks[1]["volume"] == 0.8 and conn.tracks[1]["pan"] == 0
    assert json.loads(path.read_text())["batches"][0]["status"] == "failed"


def test_timeout_not_retried_or_blindly_restored(conn, tmp_path):
    conn.fail = "timeout"
    report = apply_mixer_scene([item()], True, tmp_path / "uncertain.json", conn)
    assert report["status"] == "uncertain" and not report["restoration"]["success"]
    assert len(conn.writes) == 1


def test_out_of_range_db_restores_without_guessing(conn, tmp_path):
    report = apply_mixer_scene([item(volume_db=20)], True, tmp_path / "range.json", conn)
    assert not report["success"] and report["restoration"]["success"]
    assert "measured range" in report["error"]
    assert conn.tracks[1]["volume"] == 0.8
    measurements = report["calibration_measurements"]
    assert measurements[0]["tracks"][0]["volume"] == 0.0
    assert measurements[0]["tracks"][0]["volume_db"] is None
    assert measurements[1]["tracks"][0]["volume_db"] == 5.0


def test_changed_identity_prevents_followup_and_restoration(conn, tmp_path):
    conn.rename_after_write = True
    report = apply_mixer_scene([item(volume_db=-2)], True, tmp_path / "identity.json", conn)
    assert not report["success"] and not report["restoration"]["success"]
    assert len(conn.writes) == 1


@pytest.mark.parametrize("values", [{"volume": 0.5}, {"volume_db": -2}])
def test_ignored_write_fails_readback(conn, tmp_path, values):
    conn.ignore_write = True
    report = apply_mixer_scene([item(**values)], True, tmp_path / "ignored.json", conn)
    assert not report["success"] and report["restoration"]["success"]
    assert "readback mismatch" in report["error"]


def test_apply_requires_receipt_before_reads(conn):
    with pytest.raises(ValueError, match="receipt_file"):
        apply_mixer_scene([item()], apply=True, connection=conn)
    assert not conn.calls


def test_muted_volume_db_is_json_safe(conn, tmp_path):
    conn.tracks[1]["volume"] = 0
    report = apply_mixer_scene([item(volume=0.5)], True, tmp_path / "muted.json", conn)
    assert report["success"]
    assert report["restore_scene"][0]["volume"] == 0
    assert report["before"][0]["volume_db"] is None


def test_native_silence_floor_brackets_finite_db_without_a_fader_formula(conn, tmp_path):
    conn.quantize_floor = 0.0001
    report = apply_mixer_scene(
        [item(volume_db=-12), {"track": 2, "expected_name": "Pad", "volume_db": -1}],
        True,
        tmp_path / "silence-floor.json",
        conn,
    )
    assert report["success"]
    low = report["calibration_measurements"][0]
    assert low["phase"] == "db_endpoint_0"
    assert all(state["volume"] == 0.0 and state["volume_db"] is None for state in low["tracks"])
    for state, target in zip(report["after"], (-12, -1)):
        assert abs(state["volume_db"] - target) <= report["db_tolerance"]
    assert len(report["calibration_measurements"]) <= 26
    assert json.loads((tmp_path / "silence-floor.json").read_text()) == report


@pytest.mark.parametrize("db", [None, True, "-inf", math.nan, math.inf])
@pytest.mark.parametrize("volume", [0.0, 0.8])
def test_missing_or_malformed_db_never_becomes_silence(conn, db, volume):
    conn.tracks[1]["volume"] = volume
    conn.db_override[1] = db
    with pytest.raises(RuntimeError, match="Invalid dB readback"):
        apply_mixer_scene([item(volume_db=-12)], connection=conn)
    assert not conn.writes


def test_native_negative_infinity_requires_exact_zero_normalized_volume(conn):
    conn.tracks[1]["volume"] = 0.000001
    conn.db_override[1] = -math.inf
    with pytest.raises(RuntimeError, match="Invalid dB readback"):
        apply_mixer_scene([item(volume_db=-12)], connection=conn)
    assert not conn.writes


def test_db_endpoint_measurement_is_durable_before_next_mutation(conn, tmp_path):
    path = tmp_path / "measurements.json"
    original = conn.send_command

    def check_receipt(action, params, timeout):
        if action == "bridge.batch" and params["steps"][0]["action"] == "mixer.configure":
            if params["steps"][0]["params"].get("volume") == 1.0:
                receipt = json.loads(path.read_text())
                assert receipt["calibration_measurements"][0]["tracks"][0]["volume_db"] is None
        return original(action, params, timeout)

    conn.send_command = check_receipt
    assert apply_mixer_scene([item(volume_db=-12)], True, path, conn)["success"]


def test_rounded_normalized_calibration_still_requires_native_db_target(conn, tmp_path):
    conn.volume_round_digits = 3
    report = apply_mixer_scene(
        [item(volume_db=-12), {"track": 2, "expected_name": "Pad", "volume_db": -1}],
        True,
        tmp_path / "rounded.json",
        conn,
    )
    assert report["success"]
    assert report["db_tolerance"] == 0.02
    assert report["calibration_volume_tolerance"] == 0.000500001
    for state, target in zip(report["after"], (-12, -1)):
        assert abs(state["volume_db"] - target) <= 0.02
    # Verify this actually exercised rounding beyond the former 1e-5 guard.
    deltas = []
    for sample in report["calibration_measurements"]:
        batch = next(row for row in report["batches"] if row["phase"] == sample["phase"])
        requested = {row["track"]: row["volume"] for row in batch["changes"]}
        deltas.extend(
            abs(state["volume"] - requested[state["track"]])
            for state in sample["tracks"]
            if state["track"] in requested
        )
    assert 0.00001 < max(deltas) <= report["calibration_volume_tolerance"]


def test_calibration_rejects_normalized_deviation_beyond_explicit_bound(conn, tmp_path):
    original = conn.send_command

    def incorrect_write(action, params, timeout):
        result = original(action, params, timeout)
        if action == "bridge.batch":
            for step in params["steps"]:
                if step["action"] == "mixer.configure" and step["params"].get("volume") == 0.5:
                    conn.tracks[step["params"]["track"]]["volume"] = 0.501
        return result

    conn.send_command = incorrect_write
    report = apply_mixer_scene([item(volume_db=-12)], True, tmp_path / "deviation.json", conn)
    assert not report["success"]
    assert "Calibration normalized/dB readback mismatch" in report["error"]
    assert report["calibration_measurements"][-1]["phase"] == "db_bisection_0"
    assert report["restoration"]["success"]


def test_quantized_unreachable_db_target_fails_bounded_and_restores(conn, tmp_path):
    conn.volume_round_digits = 3
    report = apply_mixer_scene([item(volume_db=-60)], True, tmp_path / "unreachable.json", conn)
    assert not report["success"]
    assert report["error"] == "dB target failed bounded convergence"
    assert len(report["calibration_measurements"]) == 26
    assert report["restoration"]["success"]
    assert conn.tracks[1]["volume"] == 0.8
    for sample in report["calibration_measurements"]:
        db = sample["tracks"][0]["volume_db"]
        assert db is None or abs(db + 60) > report["db_tolerance"]


def test_calibration_rounding_allowance_does_not_weaken_explicit_normalized_target(conn, tmp_path):
    conn.volume_round_digits = 3
    report = apply_mixer_scene([item(volume=0.41796875)], True, tmp_path / "normalized.json", conn)
    assert not report["success"]
    assert "Final volume readback mismatch" in report["error"]
    assert report["restoration"]["success"]
