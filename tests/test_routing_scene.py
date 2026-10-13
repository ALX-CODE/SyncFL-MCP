import copy
import json
import math

import pytest

from fl_studio_mcp.utils import routing_scene
from fl_studio_mcp.utils.routing_scene import apply_routing_scene, inspect_routing


class Connection:
    def __init__(self, path):
        self._hardware_dir = path
        self.names = ["Master", "Kick", "Bass", "Bus"]
        self.routes = {(1, 0): 0.8, (2, 0): 0.8, (3, 0): 0.8}
        self.channels = [dict(index=0, name="Kick sample", mixer=1)]
        self.context = dict(
            session="session-a",
            project_generation=1,
            channels=[dict(index=0, name="Kick sample", type=0)],
        )
        self.playing = False
        self.recording = False
        self.calls = []
        self.writes = []
        self.ignore_write = False
        self.fail_write = None
        self.after_preflight = None
        self.after_write = None
        self.receipt = None

    def transport(self):
        return dict(is_playing=self.playing, is_recording=self.recording)

    def send_command(self, action, params, timeout):
        self.calls.append((action, copy.deepcopy(params)))
        if action == "production.inspect":
            return dict(
                success=True,
                context=copy.deepcopy(self.context),
                revision=routing_scene._digest(self.context),
                **self.transport(),
            )
        if action == "project.snapshot":
            mixer = [
                dict(
                    index=i,
                    name=name,
                    routes=[
                        dict(destination=d, level=v) for (s, d), v in self.routes.items() if s == i
                    ],
                )
                for i, name in enumerate(self.names)
            ]
            return dict(
                success=True,
                mixer=mixer,
                channels=copy.deepcopy(self.channels),
                transport=self.transport(),
            )
        assert action == "bridge.batch"
        if params["dry_run"]:
            if self.after_preflight:
                self.after_preflight(self)
            return dict(success=True, validated=len(params["steps"]), applied=0)
        if self.receipt:
            assert json.loads(self.receipt.read_text())["status"] == "dispatching"
        self.writes.append(copy.deepcopy(params))
        for step in params["steps"]:
            p = step["params"]
            assert step["action"] == "mixer.route"
            if not self.ignore_write:
                pair = p["source"], p["destination"]
                if p["enabled"]:
                    self.routes[pair] = p["level"]
                else:
                    self.routes.pop(pair, None)
            if self.fail_write == "partial":
                return dict(
                    success=False,
                    partial=True,
                    failed_step=0,
                    applied=0,
                    error="simulated partial mutation",
                )
        if self.after_write:
            self.after_write(self)
        if self.fail_write == "timeout":
            raise TimeoutError("Timeout after dispatch")
        if self.fail_write == "error":
            return dict(success=False, error="acknowledgment failed")
        return dict(success=True, applied=len(params["steps"]), results=[{}] * len(params["steps"]))


@pytest.fixture
def conn(tmp_path):
    return Connection(tmp_path)


def item(**kwargs):
    return dict(
        source=1,
        destination=2,
        expected_source="Kick",
        expected_destination="Bass",
        enabled=True,
        level=0,
        **kwargs,
    )


def replace_item(**kwargs):
    result = item()
    result.update(kwargs)
    return result


def revision(conn):
    return inspect_routing(conn)["revision"]


def apply(conn, tmp_path, items=None):
    conn.receipt = tmp_path / "receipt.json"
    return apply_routing_scene(items or [item()], revision(conn), True, conn.receipt, conn)


def test_inspect_keeps_silent_sidechains_and_channel_routes(conn):
    conn.routes[1, 2] = 0
    result = inspect_routing(conn)
    assert len(result["revision"]) == 64
    assert {"source": 1, "destination": 2, "level": 0} in result["routes"]
    assert result["channels"] == [{"channel": 0, "name": "Kick sample", "mixer": 1}]
    assert result["limitations"] and not conn.writes
    assert revision(conn) == result["revision"]


def test_dry_run_preflights_without_writing(conn):
    result = apply_routing_scene([item()], revision(conn), connection=conn)
    assert result["status"] == "preflighted" and result["dry_run"]
    assert result["restore_scene"] == [
        {k: v for k, v in replace_item(enabled=False).items() if k != "level"}
    ]
    assert conn.calls[-1] == ("bridge.batch", {"steps": result["steps"], "dry_run": True})
    assert not conn.writes


@pytest.mark.parametrize(
    "changes",
    [
        {"source": True},
        {"source": 0},
        {"source": -1},
        {"destination": -1},
        {"destination": 1},
        {"enabled": 1},
        {"level": True},
        {"level": math.nan},
        {"level": math.inf},
        {"level": -0.1},
        {"level": 1.1},
        {"expected_source": ""},
        {"unknown": 1},
        {"enabled": False},
    ],
)
def test_invalid_values_fail_before_any_connection(conn, changes):
    with pytest.raises(ValueError):
        apply_routing_scene([replace_item(**changes)], "a" * 64, connection=conn)
    assert not conn.calls


def test_level_required_for_enabling(conn):
    missing = item()
    missing.pop("level")
    with pytest.raises(ValueError, match="explicit level"):
        apply_routing_scene([missing], "a" * 64, connection=conn)
    assert not conn.calls


def test_duplicate_routes_rejected(conn):
    with pytest.raises(ValueError, match="unique"):
        apply_routing_scene([item(), item()], "a" * 64, connection=conn)
    assert not conn.calls


@pytest.mark.parametrize("field", ["expected_source", "expected_destination"])
def test_exact_names_required(conn, field):
    with pytest.raises(ValueError, match="identity"):
        apply_routing_scene([replace_item(**{field: "Other"})], revision(conn), connection=conn)
    assert not conn.writes


@pytest.mark.parametrize("field", ["playing", "recording"])
def test_stopped_transport_required(conn, field):
    setattr(conn, field, True)
    with pytest.raises(ValueError, match="stopped"):
        apply_routing_scene([item()], revision(conn), connection=conn)
    assert not conn.writes


def test_stale_graph_revision_rejected(conn):
    old = revision(conn)
    conn.routes[1, 0] = 0.4
    with pytest.raises(ValueError, match="revision changed"):
        apply_routing_scene([item()], old, connection=conn)
    assert not conn.writes


def test_project_reopen_invalidates_revision_even_with_same_names(conn):
    old = revision(conn)
    conn.context["project_generation"] += 1
    with pytest.raises(ValueError, match="revision changed"):
        apply_routing_scene([item()], old, connection=conn)


def test_feedback_rejected_including_zero_level_sidechain(conn):
    conn.routes[2, 1] = 0
    with pytest.raises(ValueError, match="feedback"):
        apply_routing_scene([item()], revision(conn), connection=conn)
    assert not conn.writes


def test_disables_are_ordered_before_additions_for_bus_rewire(conn, tmp_path):
    conn.routes[2, 1] = 0
    disable = dict(
        source=2, destination=1, expected_source="Bass", expected_destination="Kick", enabled=False
    )
    result = apply(conn, tmp_path, [item(), disable])
    assert result["success"] and result["status"] == "verified"
    assert conn.writes[0]["steps"][0]["params"]["enabled"] is False
    assert (2, 1) not in conn.routes and conn.routes[1, 2] == 0


def test_success_has_durable_receipt_and_two_separate_readbacks(conn, tmp_path):
    result = apply(conn, tmp_path)
    assert result["success"] and result["status"] == "verified"
    assert conn.routes[1, 2] == 0 and conn.routes[1, 0] == 0.8
    assert len(conn.writes) == 1 and len(result["readbacks"]) == 2
    assert json.loads(conn.receipt.read_text()) == result


def test_existing_send_restores_its_original_level(conn, tmp_path):
    conn.routes[1, 2] = 0.37
    result = apply(conn, tmp_path)
    assert result["restore_scene"] == [replace_item(level=0.37)]


def test_missing_track_rejected(conn):
    with pytest.raises(ValueError, match="does not exist"):
        apply_routing_scene([replace_item(destination=9)], revision(conn), connection=conn)


@pytest.mark.parametrize("receipt", [None, "relative.json", "C:/relative.txt"])
def test_apply_requires_new_absolute_json_receipt_before_reads(conn, receipt):
    with pytest.raises(ValueError, match="receipt"):
        apply_routing_scene([item()], "a" * 64, True, receipt, conn)
    assert not conn.calls


def test_occupied_receipt_never_overwritten_or_dispatched(conn, tmp_path):
    path = tmp_path / "existing.json"
    path.write_text("existing")
    with pytest.raises(FileExistsError):
        apply_routing_scene([item()], revision(conn), True, path, conn)
    assert path.read_text() == "existing" and not conn.writes


def test_change_after_preflight_rejects_before_dispatch(conn, tmp_path):
    conn.after_preflight = lambda c: c.routes.update({(1, 0): 0.3})
    result = apply(conn, tmp_path)
    assert result["status"] == "not_applied" and not conn.writes
    assert not result["success"]
    assert json.loads(conn.receipt.read_text()) == result


def test_timeout_is_durable_and_never_retried_or_restored(conn, tmp_path):
    conn.fail_write = "timeout"
    result = apply(conn, tmp_path)
    assert result["status"] == "unknown" and not result["success"]
    assert conn.routes[1, 2] == 0 and len(conn.writes) == 1
    assert json.loads(conn.receipt.read_text()) == result


def test_acknowledged_failure_is_not_claimed_success_even_if_observed(conn, tmp_path):
    conn.fail_write = "error"
    result = apply(conn, tmp_path)
    assert result["status"] == "observed_after_error" and not result["success"]
    assert len(conn.writes) == 1


def test_partial_mutation_retains_restore_scene_without_automatic_restore(conn, tmp_path):
    conn.fail_write = "partial"
    bus = dict(
        source=2,
        destination=3,
        expected_source="Bass",
        expected_destination="Bus",
        enabled=True,
        level=0.8,
    )
    result = apply(conn, tmp_path, [item(), bus])
    assert result["status"] == "unknown" and result["response"]["partial"]
    assert len(conn.writes) == 1 and len(result["restore_scene"]) == 2


def test_ignored_write_fails_independent_readback(conn, tmp_path):
    conn.ignore_write = True
    result = apply(conn, tmp_path)
    assert result["status"] == "verification_failed" and not result["success"]
    assert len(conn.writes) == 1


def test_unrelated_route_change_also_fails_verification(conn, tmp_path):
    conn.after_write = lambda c: c.routes.update({(3, 0): 0.2})
    result = apply(conn, tmp_path)
    assert result["status"] == "verification_failed" and not result["success"]
    assert len(conn.writes) == 1


@pytest.mark.parametrize(
    "change",
    [
        lambda c: c.names.__setitem__(1, "Different"),
        lambda c: c.channels[0].update(mixer=2),
        lambda c: c.context.update(project_generation=2),
        lambda c: setattr(c, "playing", True),
    ],
)
def test_identity_or_transport_change_stops_verification(conn, tmp_path, change):
    conn.after_write = change
    result = apply(conn, tmp_path)
    assert result["status"] == "unknown" and not result["success"]
    assert len(conn.writes) == 1


def test_receipt_failure_after_dispatch_is_unknown_without_retry(conn, tmp_path, monkeypatch):
    original = routing_scene.atomic_json

    def fail_after_dispatch(path, report):
        if report["status"] != "dispatching":
            raise OSError("Disk failure")
        original(path, report)

    monkeypatch.setattr(routing_scene, "atomic_json", fail_after_dispatch)
    result = apply(conn, tmp_path)
    assert result["status"] == "unknown" and not result["success"]
    assert result["receipt_persistence_error"] == "Disk failure"
    assert json.loads(conn.receipt.read_text())["status"] == "dispatching"
    assert len(conn.writes) == 1


def test_malformed_incomplete_snapshot_is_rejected(conn):
    conn.names = []
    with pytest.raises(RuntimeError, match="mixer tracks"):
        inspect_routing(conn)


def test_tool_registration_forwards_to_shared_backend(monkeypatch):
    from fl_studio_mcp.tools.routing import register_routing_tools

    class MCP:
        def __init__(self):
            self.registered = {}

        def tool(self):
            def register(function):
                self.registered[function.__name__] = function
                return function

            return register

    mcp = MCP()
    register_routing_tools(mcp)
    monkeypatch.setattr(routing_scene, "inspect_routing", lambda: {"status": "inspected"})
    assert mcp.registered["fl_routing_inspect"]() == {"status": "inspected"}
    monkeypatch.setattr(routing_scene, "apply_routing_scene", lambda *args: args)
    assert mcp.registered["fl_routing_scene"]([item()], "a" * 64) == (
        [item()],
        "a" * 64,
        False,
        None,
    )
