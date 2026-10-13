"""Cooperatively serialized routing scenes with fresh graph checks and receipts.

The controller's typed routing preflight also checks feedback. No host-only
workflow can prevent the user or a legacy client changing FL between callbacks.
This adapter therefore verifies the full graph after one write, never retries it,
and never automatically restores an uncertain mutation.
"""

import hashlib
import json
import math
import os
import time
import uuid
from pathlib import Path

from fl_studio_mcp.utils.midi_connection import get_connection
from fl_studio_mcp.utils.protocol import atomic_json, transaction_lock

LEVEL_TOLERANCE = 0.00001
LIMITATIONS = [
    "The shared GUI lock coordinates participating clients; it cannot lock FL's UI "
    "or legacy clients across callbacks.",
    "A zero-level enabled route supplies a sidechain; the receiving plugin's "
    "sidechain input still needs separate configuration.",
    "Track identity uses current indices and exact names; same-name track replacement "
    "within one project cannot always be detected by the native API.",
]


def _call(conn, action, params):
    result = conn.send_command(action, params, 30)
    if not isinstance(result, dict) or result.get("success") is not True:
        raise RuntimeError(f"Routing {action} failed: {result}")
    return result


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, allow_nan=False).encode("utf-8")
    ).hexdigest()


def _revision(value):
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(c not in "0123456789abcdef" for c in value)
    ):
        raise ValueError("Use the revision returned by fl_routing_inspect")


def _transport(value):
    result = {key: value.get(key) for key in ("is_playing", "is_recording")}
    if any(type(v) is not bool for v in result.values()):
        raise RuntimeError("Incomplete transport readback")
    return result


def _stopped(state):
    if any(state["transport"].values()):
        raise ValueError("Transport must be stopped and recording off")


def _read_state(conn):
    # Bracket the graph callback with project-generation/revision observations.
    before = _call(conn, "production.inspect", {"scope": "project"})
    snapshot = _call(conn, "project.snapshot", {})
    after = _call(conn, "production.inspect", {"scope": "project"})
    _revision(before.get("revision"))
    if (
        before["revision"] != after.get("revision")
        or before.get("context") != after.get("context")
        or not isinstance(before.get("context"), dict)
    ):
        raise RuntimeError("Project changed while reading routing; inspect again")
    transport = _transport(after)
    if _transport(before) != transport or _transport(snapshot.get("transport", {})) != transport:
        raise RuntimeError("Transport changed while reading routing; inspect again")
    tracks = []
    routes = []
    for track in snapshot.get("mixer", []):
        index, name = track.get("index"), track.get("name")
        if type(index) is not int or index < 0 or not isinstance(name, str):
            raise RuntimeError("Invalid mixer track identity")
        tracks.append({"track": index, "name": name})
        if not isinstance(track.get("routes"), list):
            raise RuntimeError("Mixer snapshot omits routing")
        for route in track["routes"]:
            destination, level = route.get("destination"), route.get("level")
            if (
                type(destination) is not int
                or destination < 0
                or type(level) not in (int, float)
                or not math.isfinite(level)
                or not 0 <= level <= 1
                or destination == index
            ):
                raise RuntimeError("Invalid route readback")
            routes.append({"source": index, "destination": destination, "level": float(level)})
    tracks.sort(key=lambda t: t["track"])
    ids = [t["track"] for t in tracks]
    if not ids or ids != list(range(len(ids))):
        raise RuntimeError("Incomplete or duplicate mixer tracks")
    pairs = [(r["source"], r["destination"]) for r in routes]
    if len(set(pairs)) != len(pairs) or any(r["destination"] not in ids for r in routes):
        raise RuntimeError("Incomplete or duplicate mixer routes")
    routes.sort(key=lambda r: (r["source"], r["destination"]))
    channels = []
    for channel in snapshot.get("channels", []):
        index, target = channel.get("index"), channel.get("mixer")
        if (
            type(index) is not int
            or index < 0
            or type(target) is not int
            or target not in ids
            or not isinstance(channel.get("name"), str)
        ):
            raise RuntimeError("Invalid channel routing readback")
        channels.append({"channel": index, "name": channel["name"], "mixer": target})
    channels.sort(key=lambda c: c["channel"])
    if [c["channel"] for c in channels] != list(range(len(channels))):
        raise RuntimeError("Incomplete or duplicate channels")
    context_channels = before["context"].get("channels")
    if not isinstance(context_channels, list) or len(context_channels) != len(channels):
        raise RuntimeError("Channel count changed while reading routing")
    if any(
        (c["channel"], c["name"]) != (p.get("index"), p.get("name"))
        for c, p in zip(channels, context_channels)
    ):
        raise RuntimeError("Channel identity changed while reading routing")
    state = dict(
        project_revision=before["revision"],
        project=before["context"],
        tracks=tracks,
        routes=routes,
        channels=channels,
    )
    return dict(
        success=True, status="inspected", revision=_digest(state), transport=transport, **state
    )


def inspect_routing(connection=None):
    """Return active routes, including silent sidechains, and a revision for editing."""
    conn = connection if connection is not None else get_connection()
    with transaction_lock(conn._hardware_dir / "mcp-gui.lock", 5):
        return dict(_read_state(conn), limitations=list(LIMITATIONS))


def _validate(items, expected_revision, apply, receipt_file):
    _revision(expected_revision)
    if type(apply) is not bool:
        raise ValueError("apply must be boolean")
    if not isinstance(items, list) or not 1 <= len(items) <= 256:
        raise ValueError("Routing scene requires 1..256 routes")
    fields = {"source", "destination", "expected_source", "expected_destination", "enabled"}
    seen = set()
    for item in items:
        if not isinstance(item, dict) or fields - item.keys() or set(item) - fields - {"level"}:
            raise ValueError("Each route needs exact source/destination names and enabled")
        source, destination = item["source"], item["destination"]
        if (
            type(source) is not int
            or source <= 0
            or type(destination) is not int
            or destination < 0
            or source == destination
        ):
            raise ValueError("Route indices must be nonnegative; no Master source or self route")
        if (source, destination) in seen:
            raise ValueError("Each source/destination route must be unique")
        seen.add((source, destination))
        if any(
            not isinstance(item[key], str) or not item[key]
            for key in ("expected_source", "expected_destination")
        ):
            raise ValueError("Exact nonempty source and destination names required")
        if type(item["enabled"]) is not bool:
            raise ValueError("enabled must be boolean")
        if item["enabled"]:
            level = item.get("level")
            if type(level) not in (int, float) or not math.isfinite(level) or not 0 <= level <= 1:
                raise ValueError("Enabled routes require explicit level in 0..1; sidechain is 0")
        elif "level" in item:
            raise ValueError("Disabled routes must omit level")
    if apply and not receipt_file:
        raise ValueError("Apply requires a new absolute receipt_file")
    path = Path(receipt_file) if receipt_file else None
    if path is not None and (not path.is_absolute() or path.suffix.lower() != ".json"):
        raise ValueError("receipt_file must be an absolute JSON path")
    return path


def _check_cycles(tracks, routes):
    graph = {t["track"]: set() for t in tracks}
    indegree = dict.fromkeys(graph, 0)
    for route in routes:
        source, destination = route["source"], route["destination"]
        graph[source].add(destination)
        indegree[destination] += 1
    ready = [t for t, count in indegree.items() if not count]
    visited = 0
    while ready:
        source = ready.pop()
        visited += 1
        for destination in graph[source]:
            indegree[destination] -= 1
            if not indegree[destination]:
                ready.append(destination)
    if visited != len(graph):
        raise ValueError("Routing would create feedback, including zero-level sidechains")


def _plan(before, items):
    names = {t["track"]: t["name"] for t in before["tracks"]}
    graph = {(r["source"], r["destination"]): r["level"] for r in before["routes"]}
    restore = []
    for item in items:
        source, destination = item["source"], item["destination"]
        if source not in names or destination not in names:
            raise ValueError("Mixer track does not exist")
        if (
            names[source] != item["expected_source"]
            or names[destination] != item["expected_destination"]
        ):
            raise ValueError("Mixer track identity changed; inspect routing again")
        pair = source, destination
        old = {k: v for k, v in item.items() if k not in ("enabled", "level")}
        old["enabled"] = pair in graph
        if old["enabled"]:
            old["level"] = graph[pair]
        restore.append(old)
        if item["enabled"]:
            graph[pair] = float(item["level"])
        else:
            graph.pop(pair, None)
    routes = [dict(source=s, destination=d, level=v) for (s, d), v in sorted(graph.items())]
    _check_cycles(before["tracks"], routes)
    # Remove old edges before adding new ones, so acyclic bus rewires do not
    # transiently create a cycle merely because items were listed in that order.
    ordered = sorted(items, key=lambda item: item["enabled"])
    steps = [
        dict(
            action="mixer.route",
            params={
                k: v for k, v in item.items() if k in ("source", "destination", "enabled", "level")
            },
        )
        for item in ordered
    ]
    return steps, routes, sorted(restore, key=lambda item: item["enabled"])


def _same_identity(before, after):
    return all(
        before[key] == after[key] for key in ("project_revision", "project", "tracks", "channels")
    )


def _routes_match(actual, expected):
    if len(actual) != len(expected):
        return False
    return all(
        a["source"] == e["source"]
        and a["destination"] == e["destination"]
        and abs(a["level"] - e["level"]) <= LEVEL_TOLERANCE
        for a, e in zip(actual, expected)
    )


def apply_routing_scene(items, expected_revision, apply=False, receipt_file=None, connection=None):
    """Preview/apply exact routing edits, observing all routes after one mutation batch."""
    path = _validate(items, expected_revision, apply, receipt_file)
    items = [dict(item) for item in items]
    conn = connection if connection is not None else get_connection()
    with transaction_lock(conn._hardware_dir / "mcp-gui.lock", 5):
        before = _read_state(conn)
        _stopped(before)
        if before["revision"] != expected_revision:
            raise ValueError("Routing revision changed; inspect again before applying")
        steps, expected_routes, restore = _plan(before, items)
        preview = _call(conn, "bridge.batch", {"steps": steps, "dry_run": True})
        if preview.get("validated") != len(steps) or preview.get("applied") != 0:
            raise RuntimeError("Routing controller did not confirm complete preflight")
        report = dict(
            success=not apply,
            status="preflighted",
            dry_run=not apply,
            operation_id=uuid.uuid4().hex,
            items=items,
            before=before,
            expected_routes=expected_routes,
            steps=steps,
            restore_scene=restore,
            level_tolerance=LEVEL_TOLERANCE,
            limitations=list(LIMITATIONS),
            recovery="Inspect routing again and use its fresh revision for any restore_scene. "
            "Never replay an uncertain scene; no automatic restoration is performed.",
        )
        if not apply:
            return report
        report["receipt_file"] = str(path)
        with path.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        dispatched = False
        try:
            current = _read_state(conn)
            _stopped(current)
            if current["revision"] != before["revision"]:
                raise RuntimeError("Routing changed after preflight; no edit dispatched")
            report["status"] = "dispatching"
            atomic_json(path, report)
            dispatched = True
            response = conn.send_command("bridge.batch", {"steps": steps, "dry_run": False}, 30)
            report["response"] = response
            report["status"] = "awaiting_readback"
            atomic_json(path, report)
            acknowledged = (
                isinstance(response, dict)
                and response.get("success") is True
                and response.get("applied") == len(steps)
            )
            consecutive = 0
            report["readbacks"] = []
            for attempt in range(5):
                after = _read_state(conn)
                report["readbacks"].append(after)
                if not _same_identity(before, after):
                    report.update(status="unknown", error="Project or track identity changed")
                    break
                _stopped(after)
                consecutive = (
                    consecutive + 1 if _routes_match(after["routes"], expected_routes) else 0
                )
                if consecutive >= 2:
                    report.update(
                        success=acknowledged,
                        status="verified" if acknowledged else "observed_after_error",
                    )
                    break
                if attempt < 4:
                    time.sleep(0.05)
            else:
                report.update(
                    status="verification_failed" if acknowledged else "unknown",
                    error="Independent full-graph readback did not match requested routes",
                )
        except Exception as exc:
            report.update(
                success=False, status="unknown" if dispatched else "not_applied", error=str(exc)
            )
        try:
            atomic_json(path, report)
        except Exception as exc:
            report.update(
                success=False,
                status="unknown" if dispatched else "not_applied",
                receipt_persistence_error=str(exc),
            )
        return report
