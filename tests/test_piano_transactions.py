"""Execute the real embedded script against a fake score; never touch live FL."""

import copy
import json
import shutil
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from fl_studio_mcp.utils import note_protocol, piano_bridge
from fl_studio_mcp.utils.piano_bridge import PianoBridge

ROOT = Path(__file__).resolve().parents[1]


class Note:
    def __init__(self, pitch=60):
        self.number, self.time, self.length = pitch, 0, 96
        self.velocity, self.pan, self.release = 0.8, 0.5, 0.5
        self.fcut = self.fres = 0.5
        self.color = self.group = self.pitchofs = self.repeats = 0
        self.slide = self.porta = self.muted = self.selected = False

    def clone(self):
        return copy.deepcopy(self)


class Score:
    PPQ = 960

    def __init__(self):
        self.notes = [Note()]
        self.notes[0].pan = 0.2
        self.notes[0].repeats = 3
        self.markers = ["preserved"]
        self.adds = 0
        self.fail_at = set()

    @property
    def noteCount(self):
        return len(self.notes)

    def getNote(self, index):
        return self.notes[index]

    def deleteNote(self, index):
        self.notes.pop(index)

    def addNote(self, note):
        self.adds += 1
        if self.adds in self.fail_at:
            raise RuntimeError("Injected FL write failure")
        self.notes.append(note.clone())


@pytest.fixture
def host(tmp_path, monkeypatch):
    score = Score()
    monkeypatch.setitem(sys.modules, "flpianoroll", SimpleNamespace(score=score, Note=Note))
    script = tmp_path / "ComposeWithLLM.pyscript"
    shutil.copyfile(ROOT / "scripts/ComposeWithLLM.pyscript", script)
    shutil.copyfile(note_protocol.__file__, tmp_path / "mcp_note_protocol.py")
    scope = {"__file__": str(script)}
    exec(compile(script.read_text(), str(script), "exec"), scope)

    def trigger(**kwargs):
        scope["apply"]()
        return True

    # Successful exchanges write several files; loaded CI runners can exceed 30 ms.
    # Use the production default here and shorten only tests of timeout behavior.
    bridge = PianoBridge(tmp_path, SimpleNamespace(trigger=trigger))
    return bridge, score, scope


def replace(notes):
    return [{"action": "clear"}, {"action": "add_notes", "notes": notes}]


def test_exact_ticks_expression_and_marker_preservation(host):
    bridge, score, _ = host
    state = bridge.exchange()["state"]
    result = bridge.exchange(
        replace(
            [
                {
                    "midi": 67,
                    "time_ticks": 123,
                    "length_ticks": 37,
                    "pan": 0.3,
                    "slide": True,
                    "repeats": 2,
                }
            ]
        ),
        state,
    )
    assert result["success"] and score.notes[0].time == 123
    assert score.notes[0].length == 37 and score.notes[0].slide
    assert score.markers == ["preserved"] and bridge.read_queue() == []
    assert (bridge.directory / "mcp-receipts" / (result["id"] + ".json")).exists()
    assert not bridge.verification.exists()


@pytest.mark.parametrize("bad", [None, -1, float("nan"), True, 0])
def test_host_validation_never_clears_original(host, bad):
    bridge, score, _ = host
    state = bridge.exchange()["state"]
    with pytest.raises(ValueError):
        bridge.exchange(
            replace([{"midi": 64, "duration": 1}, {"midi": 67, "duration": bad}]), state
        )
    assert [n.number for n in score.notes] == [60] and score.adds == 0


def test_embedded_validation_never_clears_original(host):
    bridge, score, scope = host
    state = bridge.exchange()["state"]
    envelope = {
        "protocol": 3,
        "id": "a" * 32,
        "expires_at": time.time() + 10,
        "expected_revision": state["revision"],
        "requests": replace([{"midi": 64, "duration": 1}, {"midi": 67, "duration": None}]),
    }
    bridge.queue.write_text(json.dumps(envelope))
    scope["apply"]()
    result = bridge.result(envelope["id"])
    assert not result["success"] and result["outcome"] == "not_applied"
    assert [n.number for n in score.notes] == [60] and score.adds == 0


def test_partial_write_restores_all_original_attributes(host):
    bridge, score, _ = host
    state = bridge.exchange()["state"]
    score.fail_at = {2}
    with pytest.raises(RuntimeError, match="restored"):
        bridge.exchange(replace([{"midi": 64, "duration": 1}, {"midi": 67, "duration": 1}]), state)
    assert [n.number for n in score.notes] == [60]
    assert score.notes[0].pan == 0.2 and score.notes[0].repeats == 3


def test_failed_restore_retains_queue_and_recovery(host):
    bridge, score, _ = host
    state = bridge.exchange()["state"]
    score.fail_at = {1, 2}
    with pytest.raises(RuntimeError, match="unknown"):
        bridge.exchange(replace([{"midi": 64, "duration": 1}]), state)
    assert bridge.read_queue() != []
    result = json.loads(bridge.response.read_text())
    assert result["before"]["notes"][0]["midi"] == 60


def test_revision_guard_rejects_human_edit(host):
    bridge, score, _ = host
    state = bridge.exchange()["state"]
    score.notes[0].number = 72
    with pytest.raises(RuntimeError, match="Score changed"):
        bridge.exchange(replace([{"midi": 64, "duration": 1}]), state)
    assert score.notes[0].number == 72 and score.adds == 0


def test_duplicate_request_never_replays(host):
    bridge, score, scope = host
    state = bridge.exchange()["state"]
    queued = bridge.exchange(
        [{"action": "add_notes", "notes": [{"midi": 64, "duration": 1}]}], state, auto_trigger=False
    )
    scope["apply"]()
    writes = score.adds
    scope["apply"]()
    assert score.adds == writes
    assert bridge.result(queued["id"])["success"]


def test_stale_response_times_out_without_losing_request(host):
    bridge, _, _ = host
    bridge.exchange()
    bridge.trigger = SimpleNamespace(trigger=lambda **_: True)
    bridge.timeout = 0.03
    with pytest.raises(TimeoutError, match="unresolved"):
        bridge.exchange()
    assert bridge.read_queue() != []


def test_corrupt_queue_is_never_overwritten(host):
    bridge, score, _ = host
    bridge.queue.write_text('{"incomplete":')
    with pytest.raises(RuntimeError, match="Unreadable piano queue"):
        bridge.exchange()
    assert bridge.queue.read_text() == '{"incomplete":' and score.adds == 0


def test_legacy_host_is_rejected_without_mutation(host):
    bridge, score, scope = host
    bridge.queue.write_text(json.dumps([{"action": "clear"}]))
    scope["apply"]()
    assert not json.loads(bridge.response.read_text())["success"]
    assert score.noteCount == 1 and score.adds == 0


def test_post_callback_quantization_is_verified(host):
    bridge, score, scope = host

    def trigger(**kwargs):
        scope["apply"]()
        for note in score.notes:
            for field in note_protocol.FLOAT_FIELDS:
                scale = 255 if field in ("fcut", "fres") else 128
                setattr(note, field, round(getattr(note, field) * scale) / scale)
        return True

    bridge.trigger = SimpleNamespace(trigger=trigger)
    state = bridge.exchange()["state"]
    result = bridge.exchange(replace([{"midi": 64, "duration": 1, "velocity": 0.7}]), state)
    assert result["committed_readback"]
    assert result["state"]["notes"][0]["velocity"] == 0.703125


def test_late_commit_mismatch_does_not_report_success(host):
    bridge, score, scope = host

    def trigger(**kwargs):
        scope["apply"]()
        if bridge.read_queue()["requests"]:
            score.notes[0].number = 99
        return True

    bridge.trigger = SimpleNamespace(trigger=trigger)
    state = bridge.exchange()["state"]
    with pytest.raises(RuntimeError, match="Committed score differs"):
        bridge.exchange(replace([{"midi": 64, "duration": 1}]), state)
    assert any(
        json.loads(p.read_text()).get("outcome") == "unknown"
        for p in (bridge.directory / "mcp-receipts").iterdir()
    )
    assert json.loads(bridge.verification.read_text())["status"] == "unknown"
    fresh = bridge.exchange()["state"]
    with pytest.raises(RuntimeError, match="Unresolved committed-state"):
        bridge.exchange([{"action": "clear"}], fresh)


def test_pending_commit_verification_survives_host_restart(host):
    bridge, score, scope = host
    state = bridge.exchange()["state"]
    bridge.exchange([{"action": "clear"}], state, auto_trigger=False)
    envelope = bridge.read_queue()
    scope["apply"]()
    bridge.wait(envelope)  # Simulate exit before the post-callback observation.
    assert bridge.read_queue() == [] and bridge.verification.exists()
    restarted = PianoBridge(bridge.directory, bridge.trigger)
    fresh = restarted.exchange()["state"]
    with pytest.raises(RuntimeError, match="Unresolved committed-state"):
        restarted.exchange(replace([{"midi": 64, "duration": 1}]), fresh)
    assert score.noteCount == 0


def test_expired_manual_request_never_executes(host):
    bridge, score, _ = host
    state = bridge.exchange()["state"]
    bridge.exchange([{"action": "clear"}], state, auto_trigger=False)
    envelope = bridge.read_queue()
    envelope["expires_at"] = time.time() - 1
    bridge.queue.write_text(json.dumps(envelope))
    with pytest.raises(RuntimeError, match="expired"):
        bridge.trigger_pending()
    assert score.noteCount == 1 and score.adds == 0


def test_project_ppq_change_never_executes(host):
    bridge, score, _ = host
    state = bridge.exchange()["state"]
    score.PPQ = 96
    with pytest.raises(RuntimeError, match="PPQ changed"):
        bridge.exchange([{"action": "clear"}], state)
    assert score.noteCount == 1 and score.adds == 0


@pytest.mark.parametrize("ppq", [24, 48, 96, 192, 384, 960])
def test_ticks_do_not_roundtrip_through_float_beats(ppq):
    notes = [{"midi": 60, "time_ticks": t, "length_ticks": 1} for t in range(ppq * 4)]
    result = note_protocol.normalize_requests([{"action": "add_notes", "notes": notes}], ppq)
    assert [n["time_ticks"] for n in result[0]["notes"]] == list(range(ppq * 4))


@pytest.fixture
def automatic_host(host, monkeypatch):
    existing, score, scope = host
    calls = []

    def shortcut(**kwargs):
        raise AssertionError("Unknown last-script shortcut must never run on Windows")

    monkeypatch.setattr(piano_bridge, "get_trigger", lambda: SimpleNamespace(
        platform="Windows", trigger=shortcut))

    def dispatch():
        calls.append((existing.read_queue(), threading.get_ident()))
        scope["apply"]()
        return True

    selector = SimpleNamespace(
        dispatch_compose=dispatch, selected_identity=lambda: (3, "Fixture Synth", 1)
    )
    monkeypatch.setitem(sys.modules, "fl_studio_mcp.utils.piano_ready", selector)
    bridge = PianoBridge(existing.directory)
    return bridge, score, calls, selector


def test_default_windows_selects_exact_script_for_every_exchange(automatic_host, monkeypatch):
    bridge, score, calls, _ = automatic_host
    issued_at = []

    def wall_time():
        value = time.time()
        issued_at.append(value)
        return value

    # Check the expiry budget at issuance, independent of later disk/CI delays.
    # Only replace this module's clock reference; host expiry checks stay real.
    monkeypatch.setattr(piano_bridge, "time", SimpleNamespace(
        time=wall_time, monotonic=time.monotonic, sleep=time.sleep))
    state = bridge.exchange()["state"]
    result = bridge.exchange(replace([{"midi": 64, "duration": 1}]), state)
    assert result["success"] and result["committed_readback"]
    assert score.notes[0].number == 64
    assert len(calls) == 3
    assert calls[0][0]["requests"] == calls[2][0]["requests"] == []
    assert calls[1][0]["requests"][0]["action"] == "clear"
    assert {thread for _, thread in calls} == {threading.get_ident()}
    assert len(issued_at) == 3
    assert [envelope["expires_at"] - issued
            for (envelope, _), issued in zip(calls, issued_at, strict=True)] == [45] * 3
    assert bridge.read_queue() == [] and not bridge.verification.exists()


def test_injected_windows_trigger_remains_supported_without_selector(host):
    existing, score, scope = host
    calls = []

    def trigger(**kwargs):
        calls.append(kwargs)
        scope["apply"]()
        return True

    injected = SimpleNamespace(platform="Windows", trigger=trigger)
    bridge = PianoBridge(existing.directory, injected)
    assert not bridge.select_compose
    assert bridge.exchange()["success"]
    assert calls == [{"delay": 0}] and score.noteCount == 1


def test_cold_selection_failure_retains_read_without_mutating(automatic_host):
    bridge, score, _, selector = automatic_host
    selector.dispatch_compose = lambda: False
    with pytest.raises(RuntimeError, match="dispatch unverified"):
        bridge.exchange()
    assert bridge.read_queue()["requests"] == []
    assert score.noteCount == 1 and score.adds == 0


def test_prepared_request_selects_script_once_without_preceding_probe(automatic_host):
    bridge, score, calls, _ = automatic_host
    state = bridge.exchange()["state"]
    queued = bridge.exchange([{"action": "clear"}], state, auto_trigger=False)
    calls.clear()
    result = bridge.trigger_pending()
    assert result["success"] and result["committed_readback"]
    assert len(calls) == 2
    assert calls[0][0]["id"] == queued["id"]
    assert calls[0][0]["requests"] == [{"action": "clear"}]
    assert calls[1][0]["requests"] == [] and score.noteCount == 0


def test_expired_prepared_request_never_selects_or_clears_queue(automatic_host):
    bridge, score, calls, _ = automatic_host
    state = bridge.exchange()["state"]
    bridge.exchange([{"action": "clear"}], state, auto_trigger=False)
    envelope = bridge.read_queue()
    envelope["expires_at"] = time.time() - 1
    bridge.queue.write_text(json.dumps(envelope))
    calls.clear()
    with pytest.raises(RuntimeError, match="expired.*without dispatch"):
        bridge.trigger_pending()
    assert calls == [] and bridge.read_queue() == envelope
    assert score.noteCount == 1 and score.adds == 0


@pytest.mark.parametrize("status,outcome", [("applying", "unknown"), ("error", "unknown")])
def test_unresolved_receipt_never_selects_or_replays(automatic_host, status, outcome):
    bridge, score, calls, _ = automatic_host
    state = bridge.exchange()["state"]
    queued = bridge.exchange([{"action": "clear"}], state, auto_trigger=False)
    envelope = bridge.read_queue()
    receipt = {"protocol": 3, "id": queued["id"], "status": status, "outcome": outcome}
    bridge.response.write_text(json.dumps(receipt))
    calls.clear()
    with pytest.raises(RuntimeError, match="outcome unknown"):
        bridge.trigger_pending()
    assert calls == [] and bridge.read_queue() == envelope
    assert json.loads(bridge.response.read_text()) == receipt
    assert score.noteCount == 1 and score.adds == 0


@pytest.mark.parametrize("patch", [
    {"id": "bad"}, {"expires_at": None}, {"expires_at": float("nan")}, {"requests": None},
])
def test_malformed_prepared_request_never_selects(automatic_host, patch):
    bridge, score, calls, _ = automatic_host
    state = bridge.exchange()["state"]
    bridge.exchange([{"action": "clear"}], state, auto_trigger=False)
    envelope = dict(bridge.read_queue(), **patch)
    bridge.queue.write_text(json.dumps(envelope))
    before = bridge.queue.read_text()
    calls.clear()
    with pytest.raises((ValueError, RuntimeError)):
        bridge.trigger_pending()
    assert calls == [] and bridge.queue.read_text() == before and score.adds == 0


def test_pending_write_respects_committed_verification_barrier(automatic_host):
    bridge, score, calls, _ = automatic_host
    state = bridge.exchange()["state"]
    bridge.exchange([{"action": "clear"}], state, auto_trigger=False)
    bridge.verification.write_text('{"status":"unknown"}')
    envelope = bridge.read_queue()
    calls.clear()
    with pytest.raises(RuntimeError, match="committed-state verification"):
        bridge.trigger_pending()
    assert calls == [] and bridge.read_queue() == envelope
    assert bridge.verification.exists() and score.adds == 0


def test_windows_session_locks_gui_before_piano_and_releases_reverse(tmp_path, monkeypatch):
    from fl_studio_mcp.utils import midi_connection

    bridge = SimpleNamespace(directory=tmp_path / "piano", select_compose=True)
    monkeypatch.setattr(piano_bridge, "PianoBridge", lambda: bridge)
    monkeypatch.setattr(midi_connection, "get_connection", lambda: SimpleNamespace(
        _hardware_dir=tmp_path / "hardware"))
    events = []

    @contextmanager
    def lock(path, timeout):
        events.append(("enter", path.name))
        try:
            yield
        finally:
            events.append(("exit", path.name))

    monkeypatch.setattr(piano_bridge, "transaction_lock", lock)
    with piano_bridge.piano_session() as selected:
        assert selected is bridge
        assert events == [("enter", "mcp-gui.lock"), ("enter", "pattern-bank.lock")]
    assert events[-2:] == [("exit", "pattern-bank.lock"), ("exit", "mcp-gui.lock")]


def expired_read(bridge, **changes):
    envelope = dict(protocol=3, id="e" * 32, expires_at=time.time() - 1, requests=[])
    envelope.update(changes)
    bridge.queue.write_text(json.dumps(envelope))
    return envelope


def test_expired_read_is_archived_and_retired_without_touching_score(host):
    bridge, score, _ = host
    envelope = expired_read(bridge)
    result = bridge.recover_expired_read()
    assert result["recovered"] and result["id"] == envelope["id"]
    cancellation = json.loads(Path(result["receipt_file"]).read_text())
    assert cancellation["outcome"] == "expired_read_cancelled"
    assert cancellation["request_hash"] == note_protocol.request_digest(envelope)
    assert cancellation["requests"] == [] and bridge.read_queue() == []
    assert score.noteCount == 1 and score.adds == 0 and not bridge.response.exists()
    assert bridge.recover_expired_read() == {"recovered": False, "reason": "empty"}


def test_exchange_automatically_recovers_only_the_expired_read(host):
    bridge, score, _ = host
    envelope = expired_read(bridge)
    result = bridge.exchange()
    assert result["success"] and result["id"] != envelope["id"]
    assert (bridge.directory / "mcp-receipts" / (envelope["id"] + ".expired-read.json")).exists()
    assert bridge.read_queue() == [] and score.adds == 0


@pytest.mark.parametrize("changes", [
    {"requests": [{"action": "clear"}]}, {"requests": None}, {"protocol": 2},
    {"id": "bad"}, {"expires_at": time.time() + 3600}, {"expires_at": True},
    {"expires_at": None}, {"expires_at": float("nan")},
])
def test_recovery_never_retires_a_write_live_read_or_malformed_envelope(host, changes):
    bridge, score, _ = host
    expired_read(bridge, **changes)
    before = bridge.queue.read_text()
    assert not bridge.recover_expired_read()["recovered"]
    assert bridge.queue.read_text() == before and score.adds == 0
    assert not (bridge.directory / "mcp-receipts").exists()


@pytest.mark.parametrize("receipt", [
    '{"truncated":', "[]", "{}",
    json.dumps({"protocol": 3, "id": "e" * 32, "status": "applying", "outcome": "unknown"}),
    json.dumps({"protocol": 3, "id": "e" * 32, "status": "error", "outcome": "unknown"}),
    json.dumps({"protocol": 3, "id": "e" * 32, "status": "success", "outcome": "verified"}),
])
def test_recovery_preserves_unresolved_or_malformed_response(host, receipt):
    bridge, score, _ = host
    expired_read(bridge)
    bridge.response.write_text(receipt)
    queue_before = bridge.queue.read_text()
    assert not bridge.recover_expired_read()["recovered"]
    assert bridge.queue.read_text() == queue_before and bridge.response.read_text() == receipt
    assert score.adds == 0


def test_recovery_does_not_override_unknown_archived_receipt(host):
    bridge, _, _ = host
    envelope = expired_read(bridge)
    receipts = bridge.directory / "mcp-receipts"
    receipts.mkdir()
    previous = receipts / (envelope["id"] + ".json")
    previous.write_text(json.dumps({
        "protocol": 3, "id": envelope["id"], "status": "error", "outcome": "unknown",
    }))
    before = previous.read_text()
    assert bridge.recover_expired_read()["reason"] == "unresolved_receipt"
    assert bridge.read_queue() == envelope and previous.read_text() == before


def test_recovery_preserves_existing_verified_receipt_in_separate_archive(host):
    bridge, _, scope = host
    result = bridge.exchange()
    original_receipt = bridge.directory / "mcp-receipts" / (result["id"] + ".json")
    original_bytes = original_receipt.read_bytes()
    envelope = expired_read(bridge)
    recovered = bridge.recover_expired_read()
    assert recovered["recovered"]
    assert original_receipt.read_bytes() == original_bytes
    assert json.loads(bridge.response.read_text())["id"] == result["id"]
    assert json.loads(Path(recovered["receipt_file"]).read_text())["id"] == envelope["id"]


def test_verification_barrier_blocks_expired_read_recovery(host):
    bridge, _, _ = host
    envelope = expired_read(bridge)
    bridge.verification.write_text('{"status":"unknown"}')
    assert bridge.recover_expired_read()["reason"] == "verification_pending"
    assert bridge.read_queue() == envelope and bridge.verification.exists()


def test_cancellation_archive_failure_never_clears_queue(host, monkeypatch):
    bridge, _, _ = host
    envelope = expired_read(bridge)

    def failing_archive(path, value):
        raise OSError("disk full")

    monkeypatch.setattr(piano_bridge, "atomic_json", failing_archive)
    with pytest.raises(OSError, match="disk full"):
        bridge.recover_expired_read()
    assert bridge.read_queue() == envelope


def test_windows_read_state_and_envelope_bind_full_target_identity(automatic_host):
    bridge, _, calls, selector = automatic_host
    target = (3, "Fixture Synth", 1, "12345", "Project.flp - FL Studio")
    selector.selected_identity = lambda: target
    result = bridge.exchange()
    assert calls[0][0]["target_identity"] == list(target)
    assert result["state"]["target_identity"] == list(target)
    receipt = json.loads((bridge.directory / "mcp-receipts" / (result["id"] + ".json")).read_text())
    assert receipt["state"]["target_identity"] == list(target)


@pytest.mark.parametrize("target", [(4, "Fixture Synth", 1), (3, "Fixture Synth", 2)])
def test_matching_note_revision_on_different_target_never_mutates(automatic_host, target):
    bridge, score, calls, selector = automatic_host
    state = bridge.exchange()["state"]
    selector.selected_identity = lambda: target
    calls.clear()
    with pytest.raises(RuntimeError, match="target changed since inspection"):
        bridge.exchange([{"action": "clear"}], state)
    assert calls == [] and score.noteCount == 1 and score.adds == 0
    assert bridge.read_queue() == []


def test_windows_mutation_requires_target_bound_fresh_state(automatic_host):
    bridge, score, calls, _ = automatic_host
    state = bridge.exchange()["state"]
    del state["target_identity"]
    calls.clear()
    with pytest.raises(RuntimeError, match="target changed since inspection"):
        bridge.exchange([{"action": "clear"}], state)
    assert calls == [] and score.adds == 0


def test_prepared_write_cannot_follow_a_changed_target(automatic_host):
    bridge, score, calls, selector = automatic_host
    state = bridge.exchange()["state"]
    bridge.exchange([{"action": "clear"}], state, auto_trigger=False)
    envelope = bridge.read_queue()
    selector.selected_identity = lambda: (4, "Fixture Synth", 1)
    calls.clear()
    with pytest.raises(RuntimeError, match="target changed since inspection"):
        bridge.trigger_pending()
    assert calls == [] and bridge.read_queue() == envelope and score.adds == 0


def test_target_change_after_read_never_returns_falsely_bound_state(automatic_host):
    bridge, score, _, selector = automatic_host
    original = selector.dispatch_compose

    def change_target():
        result = original()
        selector.selected_identity = lambda: (4, "Fixture Synth", 1)
        return result

    selector.dispatch_compose = change_target
    with pytest.raises(RuntimeError, match="target changed since inspection"):
        bridge.exchange()
    assert score.adds == 0 and bridge.read_queue() == []


@pytest.mark.parametrize("change_at", [1, 2])
def test_target_change_after_write_preserves_unknown_barrier(automatic_host, change_at):
    bridge, score, calls, selector = automatic_host
    state = bridge.exchange()["state"]
    calls.clear()
    original = selector.dispatch_compose

    def change_target():
        result = original()
        if len(calls) == change_at:
            selector.selected_identity = lambda: (4, "Fixture Synth", 1)
        return result

    selector.dispatch_compose = change_target
    with pytest.raises(RuntimeError, match="target changed"):
        bridge.exchange([{"action": "clear"}], state)
    assert score.noteCount == 0
    barrier = json.loads(bridge.verification.read_text())
    assert barrier["status"] == "unknown" and barrier["result"]["outcome"] == "unknown"
    assert barrier["result"]["before"]["noteCount"] == 1
    assert len(calls) == change_at


def test_unbound_legacy_windows_pending_mutation_never_dispatches(automatic_host):
    bridge, score, calls, _ = automatic_host
    state = bridge.exchange()["state"]
    bridge.exchange([{"action": "clear"}], state, auto_trigger=False)
    envelope = bridge.read_queue()
    del envelope["target_identity"]
    bridge.queue.write_text(json.dumps(envelope))
    calls.clear()
    with pytest.raises(RuntimeError, match="target changed since inspection"):
        bridge.trigger_pending()
    assert calls == [] and bridge.read_queue() == envelope and score.adds == 0


def test_two_empty_rolls_with_same_revision_cannot_exchange_a_write(automatic_host):
    bridge, score, calls, selector = automatic_host
    score.notes = []
    first = bridge.exchange()["state"]
    selector.selected_identity = lambda: (4, "Fixture Synth", 1)
    second = bridge.exchange()["state"]
    assert first["revision"] == second["revision"]
    assert first["noteCount"] == second["noteCount"] == 0
    calls.clear()
    with pytest.raises(RuntimeError, match="target changed since inspection"):
        bridge.exchange([{"action": "add_notes", "notes": [{"midi": 60, "duration": 1}]}], first)
    assert calls == [] and score.noteCount == 0 and score.adds == 0


def test_new_project_with_same_channel_pattern_and_score_cannot_receive_write(automatic_host):
    bridge, score, calls, selector = automatic_host
    selector.selected_identity = lambda: (3, "Fixture Synth", 1, "100", "First.flp - FL Studio")
    state = bridge.exchange()["state"]
    selector.selected_identity = lambda: (3, "Fixture Synth", 1, "100", "Second.flp - FL Studio")
    calls.clear()
    with pytest.raises(RuntimeError, match="target changed since inspection"):
        bridge.exchange([{"action": "clear"}], state)
    assert calls == [] and score.adds == 0
