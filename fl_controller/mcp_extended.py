"""Protocol-2 operations. Executed by the controller inside FL Studio, never in a stub runtime."""
import importlib
import hashlib
import math
import time

MODULES = {}
for _name in "mixer channels plugins transport patterns playlist ui general arrangement".split():
    try:
        MODULES[_name] = importlib.import_module(_name)
    except ImportError:
        pass
CATALOG = json.loads((SCRIPT_DIR / "native_catalog.json").read_text(encoding="utf-8"))
CATALOG = {k: v for k, v in CATALOG.items()
           if v["module"] in MODULES and hasattr(MODULES[v["module"]], v["function"])
           and MODULES["general"].getVersion() >= v.get("minimum_api_version", 1)}
RECEIPTS = {}
RECEIPT_COMMANDS = {}
LEGACY_DISPATCH = dispatch_command
IN_VALIDATED_BATCH = False

# Capture at script load, so a deployment on disk cannot impersonate a reload.
_controller_digest = hashlib.sha256()
for _filename in ("device_FLStudioMCP.py", "mcp_extended.py", "mcp_production.py",
                  "native_catalog.json"):
    _controller_digest.update(_filename.encode("utf-8"))
    _controller_digest.update((SCRIPT_DIR / _filename).read_bytes())
CONTROLLER_SOURCE_SHA256 = _controller_digest.hexdigest()


def integer(value, low, high, label):
    if type(value) is not int or not low <= value <= high:
        raise ValueError("%s must be an integer in %s..%s" % (label, low, high))
    return value


def number(value, low, high, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError("%s must be finite in %s..%s" % (label, low, high))
    return value


def track_id(value):
    return integer(value, 0, mixer.getTrackInfo(2), "track")


def plugin_target(p):
    t = track_id(p["track"])
    s = integer(p["slot"], 0, 9, "slot (zero-based)")
    if not plugins.isValid(t, s, True):
        raise ValueError("No plugin at track %s slot %s" % (t, s))
    name = plugins.getPluginName(t, s, False, True)
    if p.get("expected_plugin") and name != p["expected_plugin"]:
        raise ValueError("Plugin identity changed: expected %r, found %r" % (p["expected_plugin"], name))
    return t, s, name


def slot_info(t, s):
    if not plugins.isValid(t, s, True):
        return {"slot": s, "occupied": False}
    result = {"slot": s, "occupied": True, "name": plugins.getPluginName(t, s, False, True),
              "user_name": plugins.getPluginName(t, s, True, True),
              "parameter_count": plugins.getParamCount(t, s, True)}
    if hasattr(mixer, "getPluginMixLevel"):
        result["wet"] = mixer.getPluginMixLevel(t, s)
    return result


def track_info(t, routes=False):
    result = handle_mixer_get_track_info({"track": t})
    result["slots"] = [slot_info(t, s) for s in range(10)]
    if routes:
        result["routes"] = [{"destination": d, "level": mixer.getRouteToLevel(t, d)}
                            for d in range(mixer.getTrackInfo(2)+1) if d != t and mixer.getRouteSendActive(t, d)]
    return result


def validate_native(p):
    spec = CATALOG.get(p.get("function"))
    if spec is None:
        raise ValueError("Unsupported native function; query bridge.capabilities")
    args = p.get("args", [])
    if not isinstance(args, list) or not spec["required"] <= len(args) <= len(spec["arguments"]):
        raise ValueError("Arguments must match: " + spec["signature"])
    for value in args:
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("Non-finite native argument")
    if spec["kind"] != "read" and not p.get("allow_write", False):
        raise ValueError("Native mutations require allow_write=true; prefer typed operations")
    return spec, args


def preflight(action, p):
    if not isinstance(p, dict):
        raise ValueError("params must be an object")
    if action == "native.call":
        validate_native(p)
    elif action == "mixer.configure":
        track_id(p["track"])
        allowed = {"track", "name", "color", "volume", "pan", "stereo", "muted"}
        if set(p) - allowed:
            raise ValueError("Unknown mixer fields: " + str(set(p)-allowed))
        for key, bounds in {"volume": (0, 1), "pan": (-1, 1), "stereo": (-1, 1)}.items():
            if key in p:
                number(p[key], *bounds, key)
        if "name" in p and (not isinstance(p["name"], str) or len(p["name"]) > 128):
            raise ValueError("Invalid track name")
        if "color" in p:
            integer(p["color"], 0, 0xFFFFFF, "color")
        if "muted" in p and type(p["muted"]) is not bool:
            raise ValueError("muted must be boolean")
    elif action == "mixer.route":
        src, dest = track_id(p["source"]), track_id(p["destination"])
        if src == dest or src == 0:
            raise ValueError("Cannot route Master or create a self route")
        if type(p.get("enabled", True)) is not bool:
            raise ValueError("enabled must be boolean")
        number(p.get("level", 0.8), 0, 1, "route level")
    elif action == "channels.routeMany":
        for c in p["channels"]:
            integer(c, 0, channels.channelCount(True)-1, "global channel")
        track_id(p["track"])
    elif action in ("plugins.parameters", "plugins.setMany", "plugins.wet"):
        t, s, _ = plugin_target(p)
        if action == "plugins.setMany":
            if not p.get("expected_plugin"):
                raise ValueError("expected_plugin required for parameter writes")
            for change in p["changes"]:
                integer(change["parameter"], 0, plugins.getParamCount(t, s, True)-1, "parameter")
                number(change["value"], 0, 1, "parameter value")
                if change.get("expected_name") and change["expected_name"] != plugins.getParamName(change["parameter"], t, s, True):
                    raise ValueError("Parameter identity changed")
        if action == "plugins.wet":
            number(p["value"], 0, 1, "wet")
        if action == "plugins.parameters":
            integer(p.get("offset", 0), 0, 100000, "offset")
            integer(p.get("limit", 100), 1, 500, "limit")
    elif action == "mixer.inspect":
        for t in p.get("tracks", list(range(mixer.getTrackInfo(2)+1))):
            track_id(t)
    elif action not in {"bridge.ping", "bridge.capabilities", "bridge.receipt", "project.snapshot", "session.undo", "session.redo"}:
        raise ValueError("Unsupported batch action: " + action)


def routing_preflight(steps):
    routes = [s for s in steps if s["action"] == "mixer.route"]
    if not routes:
        return
    graph = {i: {j for j in range(mixer.getTrackInfo(2)+1) if i != j and mixer.getRouteSendActive(i, j)} for i in range(mixer.getTrackInfo(2)+1)}
    for step in routes:
        p = step["params"]
        if p.get("enabled", True):
            graph[p["source"]].add(p["destination"])
        else:
            graph[p["source"]].discard(p["destination"])
        active, done = set(), set()
        def visit(n):
            if n in active:
                raise ValueError("Routing would create feedback, including zero-level sidechains")
            if n in done:
                return
            active.add(n)
            for d in graph[n]:
                visit(d)
            active.remove(n)
            done.add(n)
        for n in graph:
            visit(n)


def extended_dispatch(action, p):
    global IN_VALIDATED_BATCH
    if action == "bridge.batch":
        steps = p.get("steps", [])
        if not isinstance(steps, list) or not 1 <= len(steps) <= 256:
            raise ValueError("Batch needs 1..256 steps")
        for step in steps:
            preflight(step["action"], step.get("params", {}))
        routing_preflight(steps)
        if p.get("dry_run"):
            return {"validated": len(steps), "applied": 0}
        results = []
        IN_VALIDATED_BATCH = True
        for i, step in enumerate(steps):
            try:
                result = extended_dispatch(step["action"], step.get("params", {}))
                if result.get("success") is False or result.get("error"):
                    raise RuntimeError(result.get("error", "Step failed"))
                results.append(result)
            except Exception as exc:
                IN_VALIDATED_BATCH = False
                return {"success": False, "error": str(exc), "failed_step": i,
                        "applied": i, "partial": True,
                        "failed_step_outcome": "may_have_applied; inspect state before retry",
                        "results": results}
        IN_VALIDATED_BATCH = False
        mixer.afterRoutingChanged()
        return {"applied": len(results), "results": results}
    if action == "bridge.ping":
        return {"protocol": 2, "api_version": MODULES["general"].getVersion(),
                "controller_source_sha256": CONTROLLER_SOURCE_SHA256,
                "tempo": mixer.getCurrentTempo(1), "safe_to_edit": MODULES["general"].safeToEdit()}
    if action == "bridge.capabilities":
        return {"protocol": 2, "native": CATALOG,
                "typed": ["bridge.batch", "mixer.configure", "mixer.route", "mixer.inspect", "channels.routeMany", "plugins.parameters", "plugins.setMany", "plugins.wet", "project.snapshot", "session.undo", "session.redo"],
                "plugin_lifecycle": "Windows UI workflow; native API has no plugin load/remove function"}
    if action == "bridge.receipt":
        return {"receipt": RECEIPTS.get(p.get("id")), "available": p.get("id") in RECEIPTS}
    if action == "native.call":
        spec, args = validate_native(p)
        return {"value": getattr(MODULES[spec["module"]], spec["function"])(*args)}
    if action == "mixer.inspect":
        return {"tracks": [track_info(t, p.get("routes", True)) for t in p.get("tracks", range(mixer.getTrackInfo(2)+1))]}
    if action == "project.snapshot":
        return {"transport": handle_transport_get_status(), "tempo": mixer.getCurrentTempo(1),
                "channels": [{"index": c, "name": channels.getChannelName(c, True), "type": channels.getChannelType(c, True), "mixer": channels.getTargetFxTrack(c, True), "muted": bool(channels.isChannelMuted(c, True))} for c in range(channels.channelCount(True))],
                "mixer": [track_info(t, True) for t in range(mixer.getTrackInfo(2)+1)]}
    if action == "mixer.configure":
        preflight(action, p)
        t = p["track"]
        for key, function in {"name": mixer.setTrackName, "color": mixer.setTrackColor, "volume": mixer.setTrackVolume, "pan": mixer.setTrackPan, "stereo": mixer.setTrackStereoSep, "muted": mixer.muteTrack}.items():
            if key in p:
                function(t, p[key])
        return track_info(t)
    if action == "mixer.route":
        preflight(action, p)
        if not IN_VALIDATED_BATCH:
            routing_preflight([{"action": action, "params": p}])
        src, dst = p["source"], p["destination"]
        mixer.setRouteTo(src, dst, p.get("enabled", True), False)
        if p.get("enabled", True):
            mixer.setRouteToLevel(src, dst, p.get("level", 0.8))
        mixer.afterRoutingChanged()
        enabled = bool(mixer.getRouteSendActive(src, dst))
        return {"source": src, "destination": dst, "enabled": enabled,
                "level": mixer.getRouteToLevel(src, dst) if enabled else None}
    if action == "channels.routeMany":
        preflight(action, p)
        for c in p["channels"]:
            channels.setTargetFxTrack(c, p["track"], True)
        return {"channels": [{"index": c, "mixer": channels.getTargetFxTrack(c, True)} for c in p["channels"]]}
    if action == "plugins.parameters":
        preflight(action, p)
        t, s, name = plugin_target(p)
        total = plugins.getParamCount(t, s, True)
        found = []
        end = min(total, p.get("offset", 0) + p.get("limit", 100))
        # Search scans all parameters, then paginates matches. Blank VST padding omitted.
        for n in range(total) if p.get("query") else range(p.get("offset", 0), end):
            param_name = plugins.getParamName(n, t, s, True)
            if not param_name or (p.get("query") and p["query"].casefold() not in param_name.casefold()):
                continue
            found.append({"parameter": n, "name": param_name, "value": plugins.getParamValue(n, t, s, True), "display": plugins.getParamValueString(n, t, s, True)})
        matched = len(found)
        if p.get("query"):
            found = found[p.get("offset", 0):p.get("offset", 0)+p.get("limit", 100)]
            end = p.get("offset", 0) + p.get("limit", 100)
        pagination_total = matched if p.get("query") else total
        return {"plugin": name, "total_parameters": total, "matched": matched, "next_offset": end if end < pagination_total else None, "parameters": found}
    if action == "plugins.setMany":
        preflight(action, p)
        t, s, name = plugin_target(p)
        before = [{"parameter": c["parameter"], "value": plugins.getParamValue(c["parameter"], t, s, True)} for c in p["changes"]]
        for c in p["changes"]:
            plugins.setParamValue(c["value"], c["parameter"], t, s, 0, True)
        return {"plugin": name, "before": before, "after": [{"parameter": c["parameter"], "value": plugins.getParamValue(c["parameter"], t, s, True), "display": plugins.getParamValueString(c["parameter"], t, s, True)} for c in p["changes"]]}
    if action == "plugins.wet":
        preflight(action, p)
        t, s, _ = plugin_target(p)
        mixer.setPluginMixLevel(t, s, p["value"])
        return slot_info(t, s)
    if action in ("session.undo", "session.redo"):
        MODULES["general"].undoUpDown(-1 if action.endswith("undo") else 1)
        return {"history_position": MODULES["general"].getUndoHistoryPos()}
    return LEGACY_DISPATCH(action, p)


def validate_command(command):
    """Reject malformed envelopes before calling any host function."""
    if not isinstance(command, dict):
        raise ValueError("Command must be an object")
    if type(command.get("protocol")) is not int or command["protocol"] != 2:
        raise ValueError("Command requires protocol 2")
    request_id = command.get("id")
    if not isinstance(request_id, str) or not request_id or len(request_id) > 128:
        raise ValueError("Command requires a nonempty id of at most 128 characters")
    action = command.get("action")
    if not isinstance(action, str) or not action or len(action) > 128:
        raise ValueError("Command requires a nonempty action of at most 128 characters")
    if not isinstance(command.get("params", {}), dict):
        raise ValueError("Command params must be an object")
    expiry = command.get("expires_at")
    if type(expiry) not in (int, float) or not math.isfinite(expiry):
        raise ValueError("Command requires a finite expiry")
    # NaN/Infinity inside nested parameters must not reach legacy setters.
    return json.dumps(command, sort_keys=True, separators=(",", ":"), allow_nan=False)


def execute_pending_command(event=None):
    global ACTIVE_MIDI_FLAGS
    # Some controller API versions use pmeFlags; the reference uses pmeflags.
    # Never invent permissions when execution did not originate in an event.
    flags = getattr(event, "pmeFlags", getattr(event, "pmeflags", None))
    ACTIVE_MIDI_FLAGS = flags if type(flags) is int and 0 <= flags <= 255 else None
    command = {}
    request_id = None
    fingerprint = None
    started = time.monotonic()
    try:
        command = json.loads(COMMAND_FILE.read_text(encoding="utf-8"))
        fingerprint = validate_command(command)
        request_id = command["id"]
        if request_id in RECEIPTS:
            if RECEIPT_COMMANDS.get(request_id) != fingerprint:
                raise ValueError("Request id reused with different content; no action applied")
            write_response(RECEIPTS[request_id])
            return
        if command["expires_at"] <= time.time():
            raise ValueError("Request expired before execution; no action applied")
        action, params = command["action"], command.get("params", {})
        if action not in {"bridge.ping", "bridge.capabilities", "bridge.receipt"} and not MODULES["general"].safeToEdit():
            raise RuntimeError("FL reports unsafe to edit (modal operation or busy)")
        result = extended_dispatch(action, params)
        response = {"success": not bool(result.get("error")), **result}
    except Exception as exc:
        response = {"success": False, "error": str(exc)}
    finally:
        ACTIVE_MIDI_FLAGS = None
    response.update({"id": request_id, "protocol": 2, "controller_ms": round((time.monotonic()-started)*1000, 2)})
    if request_id and request_id not in RECEIPTS:
        RECEIPTS[request_id] = response
        RECEIPT_COMMANDS[request_id] = fingerprint
        if len(RECEIPTS) > 128:
            oldest = next(iter(RECEIPTS))
            del RECEIPTS[oldest]
            del RECEIPT_COMMANDS[oldest]
    write_response(response)


def write_response(response):
    # FL's embedded Python disables os.replace. Client retries partial JSON and
    # accepts only a complete response carrying its exact request id.
    RESPONSE_FILE.write_text(json.dumps(response, ensure_ascii=True), encoding="utf-8")


print("FL MCP protocol 2: %s native functions available" % len(CATALOG))
exec(compile((SCRIPT_DIR / "mcp_production.py").read_text(encoding="utf-8"),
             "mcp_production.py", "exec"), globals())
