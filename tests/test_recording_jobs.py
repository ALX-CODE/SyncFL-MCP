"""Recording lifecycle tests with real decoded fixture WAVs and simulated FL callbacks."""

import json
from contextlib import contextmanager
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from test_recording_state import Connection

from fl_studio_mcp.utils import recording_jobs as jobs
from fl_studio_mcp.utils.recording_state import inspect_recording
from fl_studio_mcp.utils.tool_lease import ToolBusy


class RecordingConnection(Connection):
    def __init__(self, path):
        super().__init__(path)
        self.armed[1] = True
        self.filenames[1] = str(path / "take.wav")
        self.precount = 0
        self.transport_writes = []
        self.silent = False
        self.no_file = False
        self.fail_function = None
        self.fail_after = False
        self.on_start = None
        self.on_stop = None
        self.auto_unarm = False
        self.active_output = None

    def value(self, name, args):
        if name == "ui.isPrecountEnabled":
            return self.precount
        return super().value(name, args)

    def send_command(self, action, params, timeout):
        if action != "native.call" or not params["function"].startswith("transport."):
            return super().send_command(action, params, timeout)
        function = params["function"]
        assert params == dict(function=function, args=[], allow_write=True)
        self.transport_writes.append(function)
        if self.fail_function == function and not self.fail_after:
            raise TimeoutError("request outcome unknown")
        if function == "transport.record":
            self.recording = not self.recording
        elif function == "transport.start":
            self.playing = True
            self.active_output = self.filenames[1]
            if self.on_start:
                self.on_start(self)
        elif function == "transport.stop":
            self.playing = False
            if self.auto_unarm:
                self.armed[1] = False
            if not self.no_file:
                samples = np.zeros(8000) if self.silent else np.full(8000, 0.1)
                sf.write(self.active_output, samples, 8000, subtype="FLOAT")
            if self.on_stop:
                self.on_stop(self)
        else:
            raise AssertionError(function)
        if self.fail_function == function and self.fail_after:
            raise TimeoutError("response lost AFTER mutation")
        return dict(success=True, value=None)


@pytest.fixture
def rig(tmp_path, monkeypatch):
    conn = RecordingConnection(tmp_path)
    conn.directory = tmp_path / "job"
    clock = [1000.0]
    monkeypatch.setattr(jobs.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(jobs.time, "time", lambda: clock[0])
    monkeypatch.setattr(jobs.time, "sleep",
                        lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    monkeypatch.setattr(jobs, "_launch", lambda *args: 12345)
    conn.clock = clock
    return conn


def start(conn, apply=True, **kwargs):
    revision = inspect_recording([1], conn)["tracks"][0]["revision"]
    params = dict(track=1, expected_name="Vocal", expected_revision=revision,
                  recording_file=conn.filenames[1], directory=str(conn.directory),
                  duration_seconds=1.0, source_kind="internal",
                  source_description="Test oscillator routed to the armed mixer insert",
                  apply=apply, expected_sample_rate=8000, expected_channels=1, connection=conn)
    return jobs.start_recording(**{**params, **kwargs})


def run(conn):
    jobs.run_worker(str(conn.directory), connection=conn)
    return jobs.recording_status(str(conn.directory))


def test_preflight_never_creates_job_or_dispatches(rig):
    report = start(rig, apply=False)
    assert report["success_scope"] == "preflight" and report["status"] == "preflighted"
    assert not rig.transport_writes and not rig.directory.exists()


def test_job_queue_is_not_take_evidence(rig):
    result = start(rig)
    assert result["status"] == "queued" and result["success_scope"] == "job_queued"
    assert not result["recorded_take_verified"] and not rig.transport_writes
    assert jobs.recording_status(rig.directory)["status"] == "queued"


def test_worker_records_stops_clears_owned_latch_and_decodes_audio(rig):
    start(rig)
    result = run(rig)
    assert result["success"] and result["status"] == "verified"
    assert result["success_scope"] == "decoded_take" and result["recorded_take_verified"]
    assert result["transport_stopped_verified"] and not result["playlist_alignment_verified"]
    assert result["audio"]["frames"] == 8000 and result["audio"]["sample_rate"] == 8000
    assert len(result["audio"]["sha256"]) == 64 and result["source"]["input_device"] is None
    assert rig.transport_writes == ["transport.record", "transport.start", "transport.stop",
                                    "transport.record"]
    assert not rig.playing and not rig.recording


def test_auto_unarm_and_audio_channel_generation_do_not_change_project_ownership(rig):
    rig.auto_unarm = True
    rig.on_stop = lambda conn: conn.context.update(channel_generation=1)
    start(rig)
    result = run(rig)
    assert result["status"] == "verified" and result["after"]["armed_tracks"] == []


def test_filename_getter_may_advance_to_next_take_during_capture(rig):
    original = rig.filenames[1]
    start(rig)
    rig.on_start = lambda conn: conn.filenames.__setitem__(
        1, str(conn._hardware_dir / "take_2.wav"))
    result = run(rig)
    assert result["status"] == "verified" and result["recorded_take_verified"]
    assert result["proposed_filename"].endswith("take_2.wav")
    assert result["output_file_observation"]["path"] == original
    assert result["output_file_observation"]["exists"]
    assert Path(original).is_file() and not Path(rig.filenames[1]).exists()
    assert "active_filename" not in result


def test_observed_original_output_cannot_disappear_or_be_replaced(tmp_path):
    path = tmp_path / "take.wav"
    report = dict(request=dict(recording_file=str(path)))
    assert not jobs._observe_output(report)["exists"]
    path.write_bytes(b"buffered recording")
    assert jobs._observe_output(report)["exists"]
    path.rename(tmp_path / "preserved.wav")
    with pytest.raises(RuntimeError, match="disappeared"):
        jobs._observe_output(report)
    path.write_bytes(b"different file")
    with pytest.raises(RuntimeError, match="replaced"):
        jobs._observe_output(report)


@pytest.mark.parametrize("change", ["second_arm", "none_armed", "precount", "start_on_input",
                                    "playing", "recording", "wrong_filename", "occupied"])
def test_preflight_requires_one_stopped_exact_configured_target(rig, change):
    kwargs = {}
    if change == "second_arm":
        rig.armed[2] = True
    elif change == "none_armed":
        rig.armed[1] = False
    elif change == "precount":
        rig.precount = 1
    elif change == "start_on_input":
        rig.start_on_input = 1
    elif change == "playing":
        rig.playing = True
    elif change == "recording":
        rig.recording = True
    elif change == "wrong_filename":
        kwargs["recording_file"] = str(rig._hardware_dir / "other.wav")
    else:
        Path(rig.filenames[1]).write_bytes(b"preserved")
    with pytest.raises(ValueError):
        start(rig, **kwargs)
    assert not rig.transport_writes and not rig.directory.exists()


def test_missing_native_api_fails_before_job_launch(rig):
    del rig.native["ui.isPrecountEnabled"]
    with pytest.raises(RuntimeError, match="requires"):
        start(rig)
    assert not rig.transport_writes


@pytest.mark.parametrize("field,value", [("duration_seconds", 0), ("duration_seconds", 601),
                                          ("duration_seconds", True),
                                          ("duration_seconds", float("nan")),
                                          ("source_kind", "unknown"), ("apply", 1),
                                          ("expected_sample_rate", 1)])
def test_invalid_requests_do_not_dispatch(rig, field, value):
    with pytest.raises(ValueError):
        start(rig, **{field: value})
    assert not rig.transport_writes and not rig.directory.exists()


def test_worker_revalidates_project_after_parent_releases_lease(rig):
    start(rig)
    rig.context["project_generation"] += 1
    result = run(rig)
    assert result["status"] == "not_started" and not rig.transport_writes


def test_worker_revalidates_output_path_after_handoff(rig):
    start(rig)
    Path(rig.filenames[1]).write_bytes(b"external file appeared")
    result = run(rig)
    assert result["status"] == "not_started" and not rig.transport_writes
    assert Path(rig.filenames[1]).read_bytes() == b"external file appeared"


def test_stop_request_before_start_sends_no_transport_commands(rig):
    start(rig)
    first = jobs.stop_recording(rig.directory)
    original = (rig.directory / jobs.STOP_REQUEST).read_bytes()
    repeated = jobs.stop_recording(rig.directory)
    assert first["success_scope"] == "stop_request_saved"
    assert repeated["stop_request_status"] == "already_requested"
    assert (rig.directory / jobs.STOP_REQUEST).read_bytes() == original
    result = run(rig)
    assert result["status"] == "not_started" and result["transport_stopped_verified"]
    assert not rig.transport_writes


def test_stop_during_recording_is_observed_and_take_is_retained(rig):
    start(rig)
    rig.on_start = lambda conn: jobs.stop_recording(conn.directory)
    result = run(rig)
    assert result["status"] == "stopped_early" and result["recorded_take_verified"]
    assert result["stop_reason"] == "requested" and result["transport_stopped_verified"]


def test_stale_worker_status_does_not_claim_stopped(rig):
    start(rig)
    rig.clock[0] += 30
    result = jobs.recording_status(rig.directory)
    assert result["worker_health"] == "unresponsive_or_exited" and not result["success"]
    assert "may still be active" in result["warning"] and not rig.transport_writes


@pytest.mark.parametrize("status", ["queued", "preparing", "enabling_record", "starting",
                                    "recording", "stopping", "verifying_take"])
def test_healthy_inflight_status_is_successful_observation_not_verified_take(rig, status):
    start(rig)
    directory, report = jobs._job(rig.directory)
    jobs._write(directory, report, status=status, success=False, success_scope=None)
    result = jobs.recording_status(directory)
    assert result["success"] and result["success_scope"] == "job_observation"
    assert not result["recorded_take_verified"] and not rig.transport_writes
    assert not json.loads((directory / jobs.RECEIPT).read_text())["success"]


def test_worker_waits_for_parent_lease_before_any_native_dispatch(rig, monkeypatch):
    start(rig)
    attempts = []

    @contextmanager
    def lease(path):
        attempts.append(path)
        if len(attempts) < 3:
            assert not rig.transport_writes
            raise ToolBusy("parent still returning")
        yield

    monkeypatch.setattr(jobs, "tool_lease", lease)
    result = run(rig)
    assert result["status"] == "verified" and len(attempts) == 3


def test_timeout_after_start_does_not_retry_and_stops_owned_playback(rig):
    start(rig)
    rig.fail_function, rig.fail_after = "transport.start", True
    result = run(rig)
    assert result["status"] == "failed" and not result["recorded_take_verified"]
    assert result["transport_stopped_verified"]
    assert rig.transport_writes.count("transport.start") == 1
    assert rig.transport_writes.count("transport.stop") == 1
    assert not rig.playing and not rig.recording


def test_uncertain_record_toggle_is_not_repeated_or_blindly_undone(rig):
    start(rig)
    rig.fail_function, rig.fail_after = "transport.record", True
    result = run(rig)
    assert result["status"] == "needs_inspection"
    assert rig.transport_writes == ["transport.record"]
    assert rig.recording and not rig.playing


def test_changed_project_never_receives_stop_or_record_toggle(rig):
    start(rig)
    rig.on_start = lambda conn: conn.context.update(project_generation=1)
    result = run(rig)
    assert result["status"] == "needs_inspection"
    assert rig.transport_writes == ["transport.record", "transport.start"]


def test_silent_take_is_not_verified(rig):
    start(rig)
    rig.silent = True
    result = run(rig)
    assert result["status"] == "failed" and not result["recorded_take_verified"]
    assert result["transport_stopped_verified"] and Path(rig.filenames[1]).is_file()
    assert result["take_file_decoded"] and result["audio"]["silent"]


def test_absent_take_is_not_verified(rig):
    start(rig)
    rig.no_file = True
    result = run(rig)
    assert result["status"] == "failed" and not result["recorded_take_verified"]
    assert "No stable" in result["error"] and result["transport_stopped_verified"]


def test_finished_job_cannot_be_replayed_or_overwritten(rig):
    start(rig)
    result = run(rig)
    original = (rig.directory / jobs.RECEIPT).read_bytes()
    with pytest.raises(ValueError, match="never replayed"):
        run(rig)
    assert (rig.directory / jobs.RECEIPT).read_bytes() == original
    assert jobs.stop_recording(rig.directory)["stop_request_status"] == "already_terminal"
    assert result["recorded_take_verified"]


def test_receipt_records_dispatch_before_native_input(rig):
    start(rig)

    def observed(conn):
        receipt = json.loads((conn.directory / jobs.RECEIPT).read_text())
        assert receipt["dispatches"][-1]["function"] == "transport.start"
        assert receipt["dispatches"][-1]["status"] == "dispatching"

    rig.on_start = observed
    assert run(rig)["status"] == "verified"
