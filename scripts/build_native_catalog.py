"""Generate an auditable signature allowlist from installed Image-Line API stubs."""
import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STUBS = ROOT / ".venv/Lib/site-packages"
MODULES = "mixer channels plugins transport patterns playlist ui general arrangement".split()
DENY = {"processRECEvent", "dumpScoreLog", "clearLog", "restoreUndoLevel",
        "setUndoHistoryPos", "setUndoHistoryCount", "setUndoHistoryLast",
        "deleteChannel", "deletePattern", "deleteTrack", "automateEvent",
        "midiNoteOn", "noteOn", "globalTransport", "directFeedback", "linkToLastTweaked"}
catalog = {}
for module in MODULES:
    for path in sorted((STUBS / module).glob("*.py")):
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if not isinstance(node, ast.FunctionDef) or node.name.startswith("_") or node.name in DENY:
                continue
            args = node.args.posonlyargs + node.args.args
            if node.args.vararg or node.args.kwarg:
                continue
            key = module + "." + node.name
            entry = {"module": module, "function": node.name,
                     "arguments": [a.arg for a in args],
                     "required": len(args) - len(node.args.defaults),
                     "signature": ast.unparse(node.args),
                     "kind": "read" if node.name.startswith(("get", "is", "trackCount", "channelCount", "patternCount", "trackNumber", "channelNumber", "safeToEdit", "selected")) else "write"}
            # Last overload is normally the complete signature.
            catalog[key] = entry
catalog["mixer.getPluginMixLevel"] = {"module":"mixer","function":"getPluginMixLevel","arguments":["index","plugIndex"],"required":2,"kind":"read","signature":"index, plugIndex"}
catalog["mixer.setPluginMixLevel"] = {"module":"mixer","function":"setPluginMixLevel","arguments":["index","plugIndex","value","pickupMode"],"required":3,"kind":"write","signature":"index, plugIndex, value, pickupMode=0"}
# Corrections verified against Image-Line's current MIDI scripting reference.
# The installed third-party stubs incorrectly add pickupMode to this getter and
# omit paramIndex from getColor. Preserve the actual runtime calling convention.
catalog["plugins.getParamValueString"].update(
    arguments=["paramIndex", "index", "slotIndex", "useGlobalIndex"],
    signature="paramIndex, index, slotIndex=-1, useGlobalIndex=False",
)
catalog["plugins.getColor"].update(
    arguments=["index", "slotIndex", "flag", "paramIndex", "useGlobalIndex"],
    signature="index, slotIndex=-1, flag=0, paramIndex=0, useGlobalIndex=False",
)
# Reviewed overrides for the API 45 laptop. Metadata distinguishes documentation
# review from live acceptance; new functions are filtered again in the controller.
REFERENCE = "https://www.image-line.com/fl-studio-learning/fl-studio-online-manual/html/midi_scripting.htm"
def reviewed(key, arguments, required, signature, kind, minimum):
    module, function = key.split(".")
    catalog[key] = dict(module=module, function=function, arguments=arguments,
                        required=required, signature=signature, kind=kind,
                        minimum_api_version=minimum, source=REFERENCE,
                        validation="signature_reviewed; runtime availability is not a live test")

reviewed("channels.soloChannel", ["index", "value", "useGlobalIndex"], 1,
         "index, value=-1, useGlobalIndex=False", "write", 33)
reviewed("mixer.setCurrentTempo", ["tempo", "asInt"], 1, "tempo, asInt=0", "write", 38)
reviewed("channels.getShuffle", [], 0, "", "read", 42)
reviewed("channels.setShuffle", ["value"], 1, "value", "write", 42)
for function in ("getProjectTitle", "getProjectAuthor", "getProjectGenre"):
    reviewed("general." + function, [], 0, "", "read", 38)
for function, argument in (("setRecPPQ", "ppq"), ("setNumerator", "numerator"),
                           ("setDenominator", "denominator")):
    reviewed("general." + function, [argument], 1, argument, "write", 38)
reviewed("patterns.setPatternLength", ["index", "length"], 2, "index, length", "write", 39)
reviewed("patterns.incrementPatternLength", ["index", "offset"], 2, "index, offset", "write", 39)
reviewed("patterns.duplicatePatternData", ["index"], 1, "index", "write", 45)
reviewed("mixer.getTrackCount", [], 0, "", "read", 38)
for key in ("patterns.patternNumber", "patterns.patternMax", "arrangement.selectionStart",
            "arrangement.selectionEnd", "arrangement.currentTime", "arrangement.currentTimeHint",
            "playlist.liveBlockNumToTime", "playlist.liveTimeToBlockNum"):
    catalog[key]["kind"] = "read"
for entry in catalog.values():
    entry.setdefault("validation", "stub_signature; not individually verified")
(ROOT / "fl_controller/native_catalog.json").write_text(json.dumps(catalog, indent=2), encoding="utf-8")
print(f"Generated {len(catalog)} documented native signatures; runtime availability checked separately")
