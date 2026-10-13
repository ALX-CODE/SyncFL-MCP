"""Bounded FL disk-recording jobs with an independent worker and decoded take evidence.

The caller configures and arms one exact mixer destination first. FL's catalog
has no filename/input setter, so this module never guesses them or a GUI layout.
Only the worker touches transport; status/stop exchange persistent files and can
run while the worker owns the cross-process FL tool lease.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import stat as stat_module
import subprocess
import sys
import time
import uuid
from pathlib import Path

from .audio_files import inspect_audio
from .midi_connection import get_connection
from .protocol import atomic_json, transaction_lock
from .recording_state import (
    _boolean,
    _capabilities,
    _check_revision,
    _read_native,
    _read_state,
    _supported,
    _transport,
)
from .tool_lease import ToolBusy, tool_lease

SCHEMA = "fl-recording-job-v1"
RECEIPT = "recording-job.json"
STOP_REQUEST = "stop-request.json"
TERMINAL = frozenset({"verified", "stopped_early", "failed", "needs_inspection", "not_started"})
LIMITATIONS = [
    "The exact WAV filename, audio input and recording pickup must already be configured in FL.",
    "Source descriptions are caller-declared; input device/channel, monitoring and latency "
    "are not exposed by these native APIs and are not verified.",
    "The deadline schedules one native Stop. An unresponsive host or changed project "
    "can prevent confirmation; needs_inspection never claims recording stopped.",
    "Decoded audio proves a take file, not its musical quality or Playlist timing/alignment.",
    "Recording can add an Audio Clip/channel and can auto-unarm the mixer target in FL.",
    "During capture the filename API may report the NEXT proposed take; output evidence "
    "always comes from the original path validated before Start.",
]


def _number(value, low, high, label):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{label} must be finite in {low}..{high}")
    return float(value)


def _path(value, suffix=None):
    path = Path(value)
    if not path.is_absolute() or (suffix and path.suffix.lower() != suffix):
        raise ValueError(f"Provide an absolute {suffix or 'directory'} path")
    return path.resolve()


def _same_path(left, right):
    return (os.path.normcase(str(Path(left).resolve()))
            == os.path.normcase(str(Path(right).resolve())))


def _identity(state):
    context = state["project"]
    if not context.get("session") or type(context.get("project_generation")) is not int:
        raise RuntimeError("Controller lacks stable project session/generation")
    return {key: context[key] for key in ("session", "project_generation")}


def _caps(conn):
    capabilities = _capabilities(conn)
    result = conn.send_command("bridge.capabilities", {}, 5)
    catalog = result.get("native", {}) if result.get("success") is True else {}
    required = {"transport.record": "write", "transport.start": "write",
                "transport.stop": "write", "ui.isPrecountEnabled": "read"}
    missing = [name for name, kind in required.items() if not _supported(catalog, name, [], kind)]
    if (missing or not capabilities["recording_filename_supported"]
            or not capabilities["start_on_input_supported"]):
        raise RuntimeError("Recording requires filename, start-on-input, precount and "
                           "transport APIs; missing/incompatible: " + ", ".join(missing))
    return capabilities


def _snapshot(conn, capabilities, track):
    state = _read_state(conn, None, capabilities)
    target = next((row for row in state["tracks"] if row["track"] == track), None)
    if target is None:
        raise RuntimeError("Recording mixer target no longer exists")
    precount = _boolean(_read_native(conn, [("ui.isPrecountEnabled", [])])[0], "precount")
    return dict(project=state["project"], project_revision=state["project_revision"],
                transport=state["transport"], target=target,
                track_count=state["track_count"],
                armed_tracks=[row["track"] for row in state["tracks"] if row["armed"]],
                start_on_input=state["start_on_input"], precount=precount)


def _preflight(state, request):
    _identity(state)
    if state["track_count"] > 256:
        raise ValueError("Bounded recording supports at most 256 mixer tracks")
    if any(state["transport"].values()):
        raise ValueError("Stop playback and disable the record latch before starting a take")
    if (state["target"]["name"] != request["expected_name"]
            or state["target"]["revision"] != request["expected_revision"]):
        raise ValueError("Recording target/revision changed; inspect the target again")
    if state["armed_tracks"] != [request["track"]]:
        raise ValueError("Exactly the requested mixer track must be armed")
    if state["start_on_input"] is not False or state["precount"] is not False:
        raise ValueError("Disable start-on-input and precount before bounded recording")
    filename = state["target"]["recording_filename"]
    if not filename or not Path(filename).is_absolute() or not _same_path(
        filename, request["recording_file"]
    ):
        raise ValueError("Configure the target's exact recording_file in FL first; "
                         "a generated or different filename is not accepted")
    output = Path(request["recording_file"])
    if output.exists() or not output.parent.is_dir():
        raise ValueError("The recording WAV must be NEW, in an existing directory")


def _write(directory, report, **changes):
    report.update(changes, updated_at=time.time())
    atomic_json(directory / RECEIPT, report)


def _job(directory):
    directory = _path(directory).resolve(strict=True)
    report = json.loads((directory / RECEIPT).read_text(encoding="utf-8"))
    if (report.get("schema") != SCHEMA or not _same_path(report.get("directory", ""), directory)
            or not isinstance(report.get("job_id"), str)):
        raise ValueError("Not a matching FL recording job directory")
    return directory, report


def _launch(directory, conn):
    env = os.environ.copy()
    if getattr(conn, "_port_name", None):
        env["FL_MCP_MIDI_PORT"] = conn._port_name
    options = dict(stdin=subprocess.DEVNULL, stderr=subprocess.STDOUT, close_fds=True,
                   env=env, cwd=str(Path(__file__).resolve().parents[3]))
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    else:
        options["start_new_session"] = True
    with (directory / "worker.log").open("xb") as stream:
        process = subprocess.Popen(
            [sys.executable, "-m", "fl_studio_mcp.utils.recording_jobs",
             "--worker", str(directory)],
            stdout=stream, **options,
        )
    return process.pid


def start_recording(track, expected_name, expected_revision, recording_file, directory,
                    duration_seconds, source_kind, source_description, apply=False,
                    expected_sample_rate=None, expected_channels=None,
                    minimum_audio_seconds=0.1, require_non_silent=True, connection=None):
    _check_revision(expected_revision)
    if (type(track) is not int or track < 0
            or not isinstance(expected_name, str) or not expected_name):
        raise ValueError("Provide a nonnegative track and its exact nonempty expected_name")
    if type(apply) is not bool or type(require_non_silent) is not bool:
        raise ValueError("apply and require_non_silent must be booleans")
    if source_kind not in ("internal", "microphone") or not isinstance(source_description, str):
        raise ValueError("Describe an internal or microphone source explicitly")
    if not 1 <= len(source_description.strip()) <= 2048:
        raise ValueError("source_description must contain 1..2048 characters")
    duration = _number(duration_seconds, 1, 600, "duration_seconds")
    minimum = _number(minimum_audio_seconds, 0.01, duration, "minimum_audio_seconds")
    if expected_sample_rate is not None and (
        type(expected_sample_rate) is not int or not 8000 <= expected_sample_rate <= 384000
    ):
        raise ValueError("expected_sample_rate must be an integer in 8000..384000")
    if expected_channels is not None and (
        type(expected_channels) is not int or not 1 <= expected_channels <= 8
    ):
        raise ValueError("expected_channels must be an integer in 1..8")
    directory, output = _path(directory), _path(recording_file, ".wav")
    if directory.exists() or not directory.parent.is_dir():
        raise ValueError("Job directory must be NEW, in an existing parent directory")
    request = dict(track=track, expected_name=expected_name, expected_revision=expected_revision,
                   recording_file=str(output), duration_seconds=duration,
                   expected_sample_rate=expected_sample_rate, expected_channels=expected_channels,
                   minimum_audio_seconds=minimum, require_non_silent=require_non_silent)
    conn = connection if connection is not None else get_connection()
    if any(importlib.util.find_spec(name) is None for name in ("numpy", "soundfile")):
        raise RuntimeError("Recording take verification requires the installed audio extra")
    with transaction_lock(conn._hardware_dir / "mcp-gui.lock", 5):
        capabilities = _caps(conn)
        before = _snapshot(conn, capabilities, track)
        _preflight(before, request)
        report = dict(schema=SCHEMA, job_id=uuid.uuid4().hex, directory=str(directory),
                      success=True, success_scope="preflight", status="preflighted",
                      request=request, before=before, project_identity=_identity(before),
                      source=dict(kind=source_kind, description=source_description,
                                  verification="caller_declared", input_device=None,
                                  input_channel=None, pickup=None, monitoring=None, latency=None),
                      recorded_take_verified=False, playlist_alignment_verified=False,
                      dispatches=[], limitations=list(LIMITATIONS), created_at=time.time(),
                      recovery="Read job status and inspect FL after uncertain outcomes. "
                               "Never replay a record toggle or start request.")
        if not apply:
            return report
        directory.mkdir()
        _write(directory, report, status="queued", success_scope="job_queued")
        try:
            pid = _launch(directory, conn)
        except Exception as exc:
            _write(directory, report, status="not_started", success=False,
                   success_scope=None, error=str(exc))
            return report
        # The worker owns future receipt writes. It waits for this MCP tool's
        # mcp-tool.lock to be released, then rechecks every precondition itself.
        return {**report, "worker_pid": pid}


def recording_status(directory):
    """Disk-only observation; never accesses the DAW or alters worker state."""
    directory, report = _job(directory)
    age = max(0, time.time() - report.get("updated_at", report["created_at"]))
    report["heartbeat_age_seconds"] = age
    report["stop_requested"] = (directory / STOP_REQUEST).exists()
    if report["status"] not in TERMINAL and age > 20:
        report.update(success=False, success_scope=None, worker_health="unresponsive_or_exited",
                      warning="Worker heartbeat is stale. Recording may still be active; "
                              "inspect FL and stop it explicitly if needed.")
    else:
        report["worker_health"] = "finished" if report["status"] in TERMINAL else "recent_heartbeat"
        if report["status"] not in TERMINAL:
            # Polling a healthy in-progress job is a successful observation, not
            # completion. Executors must wait for the requested terminal evidence.
            report.update(success=True, success_scope="job_observation")
    return report


def stop_recording(directory):
    """Persist one stop request. Only the owning worker may operate FL transport."""
    directory, report = _job(directory)
    if report["status"] in TERMINAL:
        return {**recording_status(directory), "stop_request_status": "already_terminal"}
    request = dict(schema="fl-recording-stop-v1", job_id=report["job_id"], requested_at=time.time())
    with transaction_lock(directory / "stop-request.lock", 5):
        if (directory / STOP_REQUEST).exists():
            return {**recording_status(directory), "stop_request_status": "already_requested"}
        # The worker must never observe a half-written JSON request. Separate
        # request locking preserves the first request while publishing atomically.
        atomic_json(directory / STOP_REQUEST, request)
    return {**recording_status(directory), "success": True, "success_scope": "stop_request_saved",
            "stop_request_status": "requested", "transport_stopped_verified": False}


def _stop_requested(directory, report):
    path = directory / STOP_REQUEST
    if not path.exists():
        return False
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema") != "fl-recording-stop-v1" or value.get("job_id") != report["job_id"]:
        raise RuntimeError("Foreign or malformed recording stop request")
    return True


def _dispatch(conn, directory, report, function, safety=False):
    event = dict(function=function, status="dispatching", at=time.time())
    report["dispatches"].append(event)
    try:
        _write(directory, report)
    except OSError as exc:
        if not safety:
            raise
        # Stop/owned latch cleanup must not be prevented by a full/unwritable
        # receipt volume while capture is already active. Never retry the input.
        event["receipt_error"] = str(exc)
    try:
        if function == "transport.start":
            report["start_dispatched"] = True
        response = conn.send_command("native.call", dict(function=function, args=[],
                                                         allow_write=True), 1)
    except Exception as exc:
        event.update(status="unknown", error=str(exc))
        _write(directory, report)
        raise
    event.update(status="acknowledged" if response.get("success") is True else "unknown",
                 response=response)
    try:
        _write(directory, report)
    except OSError as exc:
        if not safety:
            raise
        event["receipt_error"] = str(exc)
    if response.get("success") is not True:
        raise RuntimeError(f"{function} outcome uncertain; no retry: {response}")


def _owned(state, report):
    if (_identity(state) != report["project_identity"]
            or state["target"]["name"] != report["request"]["expected_name"]):
        raise RuntimeError("Recording project or track changed; no foreign transport input sent")


def _current(conn, capabilities, report):
    # Runtime monitoring uses short, bounded read callbacks. The broad startup
    # inspection can wait longer, but it runs before any recording is enabled.
    class BoundedConnection:
        def send_command(self, action, params, timeout):
            return conn.send_command(action, params, min(timeout, 1))

    def project():
        result = conn.send_command("production.inspect", {"scope": "project"}, 1)
        if not isinstance(result, dict) or result.get("success") is not True:
            raise RuntimeError("Recording project/transport observation unavailable")
        _transport(result)
        return result

    before = project()
    track, count = report["request"]["track"], report["before"]["track_count"]
    values = _read_native(BoundedConnection(), [
        ("mixer.getTrackName", [track]), ("mixer.getTrackRecordingFileName", [track]),
        ("mixer.getTrackInfo", [2]), ("ui.isStartOnInputEnabled", []),
        ("ui.isPrecountEnabled", []),
        *[("mixer.isTrackArmed", [index]) for index in range(count)],
    ])
    after = project()
    name, filename, maximum, start_on_input, precount = values[:5]
    if not isinstance(name, str) or not isinstance(filename, str) or maximum + 1 != count:
        raise RuntimeError("Recording mixer identity/extent changed")
    armed = [_boolean(value, "track arm state") for value in values[5:]]
    state = dict(project=after["context"], project_revision=after["revision"],
                 transport=_transport(after), track_count=count,
                 target=dict(track=track, name=name, recording_filename=filename,
                             recording_filename_status="reported_proposed",
                             armed=armed[track]),
                 armed_tracks=[index for index, value in enumerate(armed) if value],
                 start_on_input=_boolean(start_on_input, "start-on-input"),
                 precount=_boolean(precount, "precount"))
    if (_identity(dict(project=before["context"])) != _identity(state)
            or _transport(before) != state["transport"]):
        raise RuntimeError("Project/transport changed during recording observation")
    _owned(state, report)
    return state


def _finish_transport(conn, directory, report, capabilities):
    state = _current(conn, capabilities, report)
    transport = state["transport"]
    if transport["is_playing"]:
        if not report.get("start_dispatched"):
            raise RuntimeError("Playback is not owned by this job; no stop dispatched")
        if report.get("stop_dispatched"):
            raise RuntimeError("Previous Stop did not verify; no retry dispatched")
        report["stop_dispatched"] = True
        _dispatch(conn, directory, report, "transport.stop", safety=True)
        state = _current(conn, capabilities, report)
        if state["transport"]["is_playing"]:
            raise RuntimeError("Stop delivered but playback is still active")
    if state["transport"]["is_recording"]:
        if not report.get("record_latch_observed") or report.get("record_disable_dispatched"):
            raise RuntimeError("Record latch ownership/cleanup is uncertain; no blind toggle sent")
        report["record_disable_dispatched"] = True
        _dispatch(conn, directory, report, "transport.record", safety=True)
    confirmations = [_current(conn, capabilities, report) for _ in range(2)]
    if any(any(state["transport"].values()) for state in confirmations):
        raise RuntimeError("Stopped transport and cleared record latch did not settle")
    _write(directory, report, transport_stopped_verified=True, after=confirmations[-1],
           stopped_at=time.time())


def _observe_output(report):
    """Watch the original destination, which may remain buffered until Stop."""
    path = Path(report["request"]["recording_file"])
    previous = report.get("output_file_observation", {})
    try:
        stat = path.lstat()
    except FileNotFoundError:
        if previous.get("exists"):
            raise RuntimeError("Original recording output disappeared during the take")
        observation = dict(path=str(path), exists=False)
    else:
        if not stat_module.S_ISREG(stat.st_mode) or path.is_symlink():
            raise RuntimeError("Original recording output is not a regular non-symlink file")
        identity = dict(device=stat.st_dev, inode=stat.st_ino)
        if previous.get("exists") and previous["identity"] != identity:
            raise RuntimeError("Original recording output was replaced during the take")
        observation = dict(path=str(path), exists=True, identity=identity,
                           bytes=stat.st_size, modified_ns=stat.st_mtime_ns)
    report["output_file_observation"] = observation
    return observation


def _verify_take(directory, report):
    path = Path(report["request"]["recording_file"])
    deadline = time.monotonic() + 8
    previous = None
    while time.monotonic() < deadline:
        _observe_output(report)
        if path.is_file():
            stat = path.stat()
            signature = stat.st_size, stat.st_mtime_ns
            if stat.st_size > 44 and signature == previous:
                break
            previous = signature
        time.sleep(0.25)
    else:
        raise RuntimeError("No stable nonempty WAV appeared at the exact configured recording path")
    audio = inspect_audio(path)
    _observe_output(report)
    _write(directory, report, audio=audio, take_file_decoded=True)
    request = report["request"]
    if audio["format"] not in ("WAV", "WAVEX", "RF64"):
        raise RuntimeError("Recorded file does not decode as WAV audio")
    if audio["duration_seconds"] < request["minimum_audio_seconds"]:
        raise RuntimeError("Decoded take is shorter than minimum_audio_seconds")
    if audio["duration_seconds"] > request["duration_seconds"] + 7:
        raise RuntimeError("Decoded take exceeded the scheduled duration plus Stop response budget")
    if request["expected_sample_rate"] and audio["sample_rate"] != request["expected_sample_rate"]:
        raise RuntimeError("Take sample rate differs from the requested rate")
    if request["expected_channels"] and audio["channels"] != request["expected_channels"]:
        raise RuntimeError("Take channel count differs from the requested count")
    if request["require_non_silent"] and audio["silent"]:
        raise RuntimeError("The recorded take is silent at the -60 dBFS activity threshold")
    return audio


def _run_owned(conn, directory, report):
    capabilities = _caps(conn)
    request = report["request"]
    state = _snapshot(conn, capabilities, request["track"])
    _owned(state, report)
    _preflight(state, request)
    if _stop_requested(directory, report):
        _write(directory, report, status="not_started", success=True,
               success_scope="stop_before_start", transport_stopped_verified=True)
        return
    _write(directory, report, status="enabling_record", success=False, success_scope=None)
    try:
        _dispatch(conn, directory, report, "transport.record")
        state = _current(conn, capabilities, report)
        if state["transport"] != dict(is_playing=False, is_recording=True):
            raise RuntimeError("Record latch did not enable with playback stopped")
        report["record_latch_observed"] = True
        if (state["armed_tracks"] != [request["track"]] or state["start_on_input"]
                or state["precount"] or not _same_path(
                    state["target"]["recording_filename"], request["recording_file"])):
            raise RuntimeError(
                "Recording prerequisites changed after latch enable; playback not sent")
        if Path(request["recording_file"]).exists():
            raise RuntimeError(
                "Recording output appeared before playback; existing audio preserved")
        started = time.monotonic()
        _write(directory, report, status="starting", started_at=time.time(),
               stop_deadline_at=time.time() + request["duration_seconds"])
        _dispatch(conn, directory, report, "transport.start")
        state = _current(conn, capabilities, report)
        if state["transport"] != dict(is_playing=True, is_recording=True):
            raise RuntimeError("Recording transport did not start")
        _observe_output(report)
        _write(directory, report, status="recording", success=True,
               success_scope="recording_transport",
               proposed_filename=state["target"]["recording_filename"],
               latest=state)
        early = False
        while time.monotonic() - started < request["duration_seconds"]:
            if _stop_requested(directory, report):
                early = True
                break
            state = _current(conn, capabilities, report)
            if state["transport"] != dict(is_playing=True, is_recording=True):
                raise RuntimeError("Recording transport changed before this job stopped it")
            if state["armed_tracks"] != [request["track"]]:
                raise RuntimeError("Armed recording targets changed during the take")
            _observe_output(report)
            _write(directory, report, latest=state,
                   proposed_filename=state["target"]["recording_filename"],
                   elapsed_seconds=max(0, time.monotonic() - started))
            remaining = max(0, request["duration_seconds"] - (time.monotonic() - started))
            time.sleep(min(0.25, remaining))
        _write(directory, report, status="stopping", success=False, success_scope=None,
               stop_reason="requested" if early else "duration_limit")
        _finish_transport(conn, directory, report, capabilities)
        _write(directory, report, status="verifying_take")
        audio = _verify_take(directory, report)
        _write(directory, report, status="stopped_early" if early else "verified", success=True,
               success_scope="decoded_take", recorded_take_verified=True, audio=audio)
    except Exception as exc:
        error = str(exc)
        cleanup_error = None
        try:
            _finish_transport(conn, directory, report, capabilities)
        except Exception as cleanup:
            cleanup_error = str(cleanup)
        _write(directory, report, success=False, success_scope=None,
               status="needs_inspection" if cleanup_error else "failed", error=error,
               cleanup_error=cleanup_error)


def run_worker(directory, connection=None):
    directory, report = _job(directory)
    if report["status"] != "queued":
        raise ValueError("Only a new queued job may launch; recording jobs are never replayed")
    conn = connection if connection is not None else get_connection()
    try:
        # Parent still owns its MCP tool lease during subprocess startup. Wait
        # without DAW commands, then revalidate instead of replaying anything.
        deadline = time.monotonic() + 15
        while True:
            try:
                with tool_lease(conn._hardware_dir / "mcp-tool.lock"):
                    with transaction_lock(directory / "worker.lock", 0):
                        _, report = _job(directory)
                        if report["status"] != "queued":
                            return  # A second worker must not overwrite the first worker's outcome.
                        _write(directory, report, worker_pid=os.getpid(), status="preparing",
                               success=False, success_scope=None)
                        with transaction_lock(conn._hardware_dir / "mcp-gui.lock", 5):
                            _run_owned(conn, directory, report)
                    return
            except ToolBusy:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Parent/other FL tool lease did not release; no input sent")
                time.sleep(0.1)
    except Exception as exc:
        _write(directory, report, success=False, success_scope=None,
               status="not_started" if not report["dispatches"] else "needs_inspection",
               error=str(exc))
    finally:
        if connection is None:
            conn.disconnect()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Owned FL recording job worker")
    parser.add_argument("--worker", required=True)
    run_worker(parser.parse_args().worker)
