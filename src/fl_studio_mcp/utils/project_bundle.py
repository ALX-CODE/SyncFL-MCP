"""Offline FLP inspection, portable sample collection, and honest MIDI export.

The source file is never written. Unknown events (including plugin state) are
copied byte-for-byte. Only documented sample path and project data path events
are rewritten in the portable copy. This is not a plugin dependency resolver.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import struct
import tempfile
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Mapping, Sequence

import mido

_SAMPLE_EXTENSIONS = {
    ".wav", ".wave", ".mp3", ".mp2", ".mp1", ".ogg", ".oga", ".flac", ".aif",
    ".aiff", ".aifc", ".wv", ".wma", ".opus", ".caf", ".snd", ".au", ".sf2",
    ".dwp", ".rx2", ".rex", ".rfl", ".ds", ".xi", ".voc",
}


class BundleError(ValueError):
    """Invalid project or a dependency that cannot safely be packaged."""


class MissingSamplesError(BundleError):
    def __init__(self, missing: list[dict]):
        self.missing = missing
        super().__init__("Unresolved sample references: " + json.dumps(missing, ensure_ascii=False))


@dataclass(frozen=True)
class Event:
    id: int
    payload: bytes
    encoded: bytes


def _vlq(value: int) -> bytes:
    result = bytearray()
    while value >= 128:
        result.append((value & 127) | 128)
        value >>= 7
    result.append(value)
    return bytes(result)


def _events(data: bytes) -> list[Event]:
    result = []
    pos = 0
    major = None
    while pos < len(data):
        start = pos
        event_id = data[pos]
        pos += 1
        if event_id < 192:
            size = 1 if event_id < 64 else 2 if event_id < 128 else 4
            # FL25+ breaks the historic DWORD range for 0xAC. A fourth byte
            # would consume the next opcode and silently hide tempo/channels.
            # Format evidence: dawhubapp/flpdiff docs/fl-format/fl25-event-format.md.
            if event_id == 172 and major is not None and major >= 25:
                size = 3
        else:
            size = 0
            for shift in range(0, 35, 7):
                if pos >= len(data):
                    raise BundleError("Truncated variable event length")
                byte = data[pos]
                pos += 1
                size |= (byte & 127) << shift
                if byte < 128:
                    break
            else:
                raise BundleError("Oversized variable event length")
        end = pos + size
        if end > len(data):
            raise BundleError(f"Truncated FLP event {event_id}")
        result.append(Event(event_id, data[pos:end], data[start:end]))
        if event_id == 199:
            try:
                major = int(data[pos:end].split(b".", 1)[0])
            except ValueError as exc:
                raise BundleError("Invalid FL version event") from exc
        pos = end
    return result


def _parse(data: bytes) -> tuple[list[tuple[bytes, bytes]], list[Event], int]:
    chunks = []
    pos = 0
    while pos < len(data):
        if pos + 8 > len(data):
            raise BundleError("Truncated FLP chunk header")
        name = data[pos:pos + 4]
        size = struct.unpack_from("<I", data, pos + 4)[0]
        end = pos + 8 + size
        if end > len(data):
            raise BundleError("FLP chunk exceeds file size")
        chunks.append((name, data[pos + 8:end]))
        pos = end
    if (not chunks or chunks[0][0] != b"FLhd"
            or sum(name == b"FLhd" for name, _ in chunks) != 1
            or sum(name == b"FLdt" for name, _ in chunks) != 1):
        raise BundleError("Expected exactly one FLhd and one FLdt chunk")
    header = chunks[0][1]
    if len(header) < 6:
        raise BundleError("Truncated FLP header")
    format_id, _, ppq = struct.unpack_from("<HHH", header)
    if format_id != 0 or not 1 <= ppq <= 32767:
        raise BundleError("Expected a project FLP with a valid MIDI-compatible PPQ")
    return chunks, _events(next(raw for name, raw in chunks if name == b"FLdt")), ppq


def _text(payload: bytes) -> str:
    try:
        return payload.decode("utf-16le").rstrip("\0")
    except UnicodeError as exc:
        raise BundleError("Invalid Unicode FLP text event") from exc


def _note(raw: bytes) -> dict:
    position, flags, channel, length, key, group = struct.unpack_from("<IHHIHH", raw)
    fine, unknown, release, color, pan, velocity, mod_x, mod_y = raw[16:24]
    return dict(position=position, flags=flags, channel=channel, length=length,
                key=key, group=group, fine_pitch=fine, unknown_byte=unknown,
                release=release, midi_channel=color, pan=pan, velocity=velocity,
                mod_x=mod_x, mod_y=mod_y, slide=bool(flags & 8))


def _inspect(data: bytes) -> dict:
    _, events, ppq = _parse(data)
    versions = [e.payload.rstrip(b"\0").decode("ascii") for e in events if e.id == 199]
    if len(versions) != 1 or not re.fullmatch(r"\d+(?:\.\d+){1,3}", versions[0]):
        raise BundleError("Missing or invalid FL Studio version")
    version = versions[0]
    major = int(version.split(".")[0])
    if major < 11:
        raise BundleError("Only Unicode FL Studio projects (11+) are supported")
    patterns: dict[int, dict] = {}
    channels: dict[int, dict] = {}
    references, clips, warnings = [], [], []
    pattern = channel = None
    tempo = None
    numerator = denominator = None
    controller_events = 0
    playlist_stride = 88 if major == 26 else None
    for index, event in enumerate(events):
        raw = event.payload
        if event.id == 65:
            channel = None
            pattern = int.from_bytes(raw, "little")
            patterns.setdefault(pattern, dict(id=pattern, name=f"Pattern {pattern}", notes=[]))
        elif event.id == 193 and pattern is not None:
            patterns[pattern]["name"] = _text(raw)
        elif event.id == 224:
            if pattern is None or len(raw) % 24:
                raise BundleError("Unbound or malformed 24-byte pattern note event")
            patterns[pattern]["notes"].extend(_note(raw[i:i + 24])
                                              for i in range(0, len(raw), 24))
        elif event.id == 223:
            controller_events += 1
        elif event.id == 64:
            pattern = None
            channel = int.from_bytes(raw, "little")
            channels.setdefault(channel, dict(id=channel, name=f"Channel {channel}"))
        elif event.id in (192, 203) and channel is not None:
            channels[channel]["name"] = _text(raw)
        elif event.id in (99, 236, 147):
            channel = pattern = None
        elif event.id == 196:
            path = _text(raw)
            if path:
                references.append(dict(event_index=index, channel=channel, path=path))
        elif event.id == 156:
            tempo = int.from_bytes(raw, "little") / 1000
        elif event.id == 66 and tempo is None:
            tempo = float(int.from_bytes(raw, "little"))
        elif event.id == 17 and numerator is None:
            numerator = raw[0]
        elif event.id == 18 and denominator is None:
            denominator = raw[0]
        elif event.id == 233:
            if playlist_stride is None:
                warnings.append(f"Playlist layout for FL {version} was preserved, not decoded")
                continue
            if len(raw) % playlist_stride:
                raise BundleError("Malformed FL 26 playlist event (expected 88-byte records)")
            for offset in range(0, len(raw), playlist_stride):
                start, base, item, length, track = struct.unpack_from("<IHHIH", raw, offset)
                offset_bytes = raw[offset + 24:offset + 32]
                start_offset, end_offset = struct.unpack("<ff", offset_bytes)
                # Pattern clips can store FFFFFFFF sentinels in these fields.
                # Decoding those as NaN breaks equality and emits invalid JSON.
                # Keep their exact bytes as well as finite audio-clip offsets.
                clips.append(dict(position=start, length=length, track=500 - track,
                                  index=item, pattern=item - base if item > base else None,
                                  offset_bytes=offset_bytes.hex(),
                                  start_offset=(start_offset if math.isfinite(start_offset)
                                                else None),
                                  end_offset=(end_offset if math.isfinite(end_offset) else None)))
    if tempo is None:
        tempo = 120.0
        warnings.append("No base tempo found; standalone MIDI uses 120 BPM")
    if not 1 <= tempo <= 1000:
        raise BundleError(f"Invalid base tempo: {tempo}")
    numerator = 4 if numerator is None else numerator
    denominator = 4 if denominator is None else denominator
    if not numerator or denominator not in (1, 2, 4, 8, 16, 32, 64):
        raise BundleError("Invalid MIDI time signature")
    return dict(version=version, ppq=ppq, tempo=tempo,
                time_signature=[numerator, denominator], patterns=list(patterns.values()),
                channels=list(channels.values()), sample_references=references, clips=clips,
                pattern_controller_events=controller_events, warnings=warnings)


def inspect_project(source: str | Path) -> dict:
    """Read saved FLP notes, channels, paths and FL26 playlist clips, without FL UI."""
    path = Path(source).resolve(strict=True)
    result = _inspect(path.read_bytes())
    result["source"] = str(path)
    return result


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_name(value: str, fallback: str = "untitled") -> str:
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")[:90] or fallback
    if name.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(
            f"{kind}{i}" for kind in ("COM", "LPT") for i in range(1, 10))}:
        name = "_" + name
    return name


def _publish(staging: Path, destination: Path) -> None:
    # Windows search/antivirus can briefly hold a just-written directory without
    # delete sharing. Retry only that transient failure, never an existing target.
    for delay in (0.0, 0.1, 0.25, 0.5, 1.0, 2.0):
        if delay:
            time.sleep(delay)
        if destination.exists() or destination.is_symlink():
            raise BundleError("Destination appeared while packaging; refusing to overwrite")
        try:
            staging.rename(destination)
            return
        except PermissionError:
            if delay == 2.0:
                raise


def _resolve_reference(raw: str, source_dir: Path, roots: Sequence[Path],
                       variables: Mapping[str, str | Path]) -> Path:
    if "\0" in raw or ".." in PureWindowsPath(raw).parts:
        raise BundleError("Unsafe sample path containing traversal or NUL")
    for key, value in variables.items():
        raw = re.sub(re.escape(f"%{key.strip('%')}%"), lambda _: str(value), raw,
                     flags=re.IGNORECASE)
    if re.search(r"%[^%]+%", raw):
        raise BundleError("Unresolved FL path variable; pass an explicit path_variables mapping")
    windows = PureWindowsPath(raw)
    path = Path(raw.replace("\\", "/"))
    if path.suffix.lower() not in _SAMPLE_EXTENSIONS:
        raise BundleError("Sample reference has an unsupported audio/instrument extension")
    if windows.drive and not windows.is_absolute():
        raise BundleError("Drive-relative sample paths are ambiguous")
    # No directory scanning: only the exact reference and explicit root/name candidates.
    candidates = [path] if path.is_absolute() or windows.is_absolute() else [source_dir / path]
    candidates.extend(root / path.name for root in roots)
    matches = {candidate.resolve() for candidate in candidates if candidate.is_file()}
    if len(matches) != 1:
        raise BundleError("Missing sample" if not matches else "Ambiguous sample search roots")
    return matches.pop()


def _export_midi(pattern: dict, channel: int, notes: list[dict], info: dict,
                 target: Path) -> dict:
    midi = mido.MidiFile(type=1, ticks_per_beat=info["ppq"])
    meta = mido.MidiTrack()
    midi.tracks.append(meta)
    # latin1 is the MIDI library default; UTF-8 is selected explicitly for our files.
    midi.charset = "utf8"
    meta.append(mido.MetaMessage("track_name", name=pattern["name"]))
    meta.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(info["tempo"])))
    meta.append(mido.MetaMessage("time_signature", numerator=info["time_signature"][0],
                                 denominator=info["time_signature"][1]))
    track = mido.MidiTrack()
    midi.tracks.append(track)
    issues: Counter = Counter()
    events = []
    active: dict[int, list[tuple[int, int, int]]] = defaultdict(list)
    exported = 0
    for note in sorted(notes, key=lambda n: (n["position"], n["key"])):
        start, key = note["position"], note["key"]
        if key > 127:
            issues["notes_outside_midi_range_omitted"] += 1
            continue
        midi_channel = note["midi_channel"] & 15
        length = note["length"]
        if length == 0:
            length = max(1, info["ppq"] // 4)
            issues["zero_length_steps_exported_as_sixteenth_notes"] += 1
        if note["slide"]:
            issues["slide_notes_exported_as_ordinary_notes"] += 1
        if note["fine_pitch"] != 120:
            issues["per_note_fine_pitch_not_encoded"] += 1
        if note["mod_x"] != 128 or note["mod_y"] != 128:
            issues["per_note_modulation_not_encoded"] += 1
        if note["velocity"] > 127 or note["release"] > 127:
            issues["velocity_or_release_clamped_to_127"] += 1
        if note["velocity"] == 0:
            issues["zero_velocity_is_note_off_in_standard_midi"] += 1
        pan = min(127, max(0, round(note["pan"] * 127 / 128)))
        active[midi_channel] = [item for item in active[midi_channel] if item[0] > start]
        if any(other_pan != pan for _, other_pan, _ in active[midi_channel]):
            issues["overlapping_different_pan_is_channel_wide_in_midi"] += 1
        if any(other_key == key for _, _, other_key in active[midi_channel]):
            issues["overlapping_same_pitch_retrigger_is_receiver_dependent"] += 1
        active[midi_channel].append((start + length, pan, key))
        events.append((start, 1, mido.Message("control_change", channel=midi_channel,
                                              control=10, value=pan)))
        events.append((start, 2, mido.Message("note_on", channel=midi_channel, note=key,
                                              velocity=min(note["velocity"], 127))))
        events.append((start + length, 0, mido.Message("note_off", channel=midi_channel,
                                                       note=key,
                                                       velocity=min(note["release"], 127))))
        exported += 1
    last = 0
    for tick, _, message in sorted(events, key=lambda e: (e[0], e[1])):
        track.append(message.copy(time=tick - last))
        last = tick
    midi.save(target)
    return dict(path="midi/" + target.name, pattern=pattern["id"], channel=channel,
                source_notes=len(notes), exported_notes=exported, limitations=dict(issues))


def pack_project(source: str | Path, destination: str | Path, *,
                 search_roots: Sequence[str | Path] = (),
                 path_variables: Mapping[str, str | Path] | None = None) -> dict:
    """Create a new directory with portable FLP, referenced files, MIDI and hashes.

    Refuses existing destinations and unresolved/ambiguous references. No external
    programs, FL state changes, broad directory scans, or plugin rewrites occur.
    Explicit search roots match only basenames; FL variable roots must be provided.
    """
    source = Path(source).resolve(strict=True)
    destination = Path(destination).absolute()
    if destination.exists() or destination.is_symlink():
        raise BundleError("Destination must not already exist")
    # Reject user-supplied traversal instead of silently normalizing it.
    if ".." in destination.parts:
        raise BundleError("Destination path must not contain traversal")
    data = source.read_bytes()
    info = _inspect(data)
    chunks, events, _ = _parse(data)
    roots = [Path(root).resolve(strict=True) for root in search_roots]
    variables = {"FLProjectPath": source.parent, **(path_variables or {})}
    resolved = []
    missing = []
    for reference in info["sample_references"]:
        try:
            sample = _resolve_reference(reference["path"], source.parent, roots, variables)
            digest = _hash(sample)
            relative = ("sample_" + digest + "__" + _safe_name(sample.stem)[:40]
                        + sample.suffix.lower())
            resolved.append({**reference, "source": str(sample), "path": relative,
                             "sha256": digest, "bytes": sample.stat().st_size})
        except (BundleError, OSError) as exc:
            missing.append({**reference, "reason": str(exc)})
    if missing:
        raise MissingSamplesError(missing)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".fl-bundle-", dir=destination.parent))
    try:
        (staging / "midi").mkdir()
        (staging / "original").mkdir()
        original = staging / "original" / source.name
        original.write_bytes(data)
        copied = set()
        replacements = {}
        for reference in resolved:
            relative = reference["path"]
            if relative not in copied:
                shutil.copyfile(reference["source"], staging / relative)
                if _hash(staging / relative) != reference["sha256"]:
                    raise BundleError("Sample changed during copying")
                copied.add(relative)
            replacements[reference["event_index"]] = relative
        rewritten_events = []
        rewritten_data_paths = 0
        for index, event in enumerate(events):
            if index in replacements or event.id == 202:
                # Clear the old machine-specific Project Data Folder in the copy.
                replacement = replacements.get(index, "")
                payload = (replacement + "\0").encode("utf-16le")
                rewritten_events.append(bytes([event.id]) + _vlq(len(payload)) + payload)
                rewritten_data_paths += event.id == 202
            else:
                rewritten_events.append(event.encoded)
        event_data = b"".join(rewritten_events)
        portable = b"".join(name + struct.pack("<I", len(event_data if name == b"FLdt" else raw))
                            + (event_data if name == b"FLdt" else raw) for name, raw in chunks)
        portable_name = _safe_name(source.stem) + ".flp"
        (staging / portable_name).write_bytes(portable)
        _, portable_events, _ = _parse(portable)
        if len(portable_events) != len(events):
            raise BundleError("Portable copy changed the number of FLP events")
        for index, (before, after) in enumerate(zip(events, portable_events)):
            if before.id != after.id or (index not in replacements and before.id != 202
                                         and before.encoded != after.encoded):
                raise BundleError(f"Portable copy changed protected event at index {index}")
        verified = _inspect(portable)
        for key in ("patterns", "channels", "clips", "tempo", "ppq", "time_signature"):
            if verified[key] != info[key]:
                raise BundleError(f"Portable copy changed protected project field: {key}")
        midi_files = []
        channel_names = {c["id"]: c["name"] for c in info["channels"]}
        for pattern in info["patterns"]:
            groups = defaultdict(list)
            for note in pattern["notes"]:
                groups[note["channel"]].append(note)
            for channel, notes in groups.items():
                name = (f"P{pattern['id']:03d}_{_safe_name(pattern['name'])[:60]}"
                        f"__C{channel:03d}_"
                        f"{_safe_name(channel_names.get(channel, 'channel'))[:40]}.mid")
                midi_files.append(_export_midi(pattern, channel, notes, info,
                                               staging / "midi" / name))
        (staging / "project-notes.json").write_text(
            json.dumps(info, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        limitations = [
            "Opaque plugin state is preserved. Plugin binaries, licenses and external files "
            "loaded inside plugins are not discoverable from FL sample-path events.",
            "MIDIs contain pattern-local notes with leading rests, base tempo and meter. "
            "Playlist repetitions, clip offsets, mixer automation and tempo changes remain in FLP.",
            "FL note colors use their low four bits as MIDI channels; MIDI pan is channel-wide. "
            "Exact FL note properties are retained in project-notes.json and the FLP.",
            "This validates structure and file hashes; opening on another machine is a separate "
            "live acceptance check.",
        ]
        manifest = dict(schema_version=1, source_flp_sha256=hashlib.sha256(data).hexdigest(),
                        portable_flp=portable_name, original_flp="original/" + source.name,
                        version=info["version"], ppq=info["ppq"], tempo=info["tempo"],
                        sample_references=resolved, unique_samples=len(copied),
                        missing_samples=[], midi_files=midi_files,
                        rewritten_data_paths=rewritten_data_paths,
                        limitations=limitations, warnings=info["warnings"])
        (staging / "README.md").write_text(
            "# Portable FL Studio project\n\n"
            f"Open **{portable_name}** in FL Studio {info['version']} or newer. Keep all "
            "`sample_*` files beside it. The relative basenames use FL Studio's project-folder "
            "sample search. Keep the same plugin versions installed.\n\n"
            "`original/` is an exact source backup with the old computer's paths; use the "
            "root FLP for playback. `midi/` contains one file per nonempty pattern/channel. "
            "Import each into its matching instrument; sample trigger MIDI does not embed audio. "
            "The FLP preserves the arrangement, sounds and mix.\n\n"
            + "\n\n".join("- " + item for item in limitations)
            + "\n\nSee manifest.json for hashes, reference mappings and per-MIDI limitations. "
            "Do not publish sample-pack content publicly.\n\n"
            "FL project-file behavior: https://www.image-line.com/fl-studio-learning/"
            "fl-studio-online-manual/html/fformats_project.htm\n", encoding="utf-8")
        manifest["files"] = [dict(path=path.relative_to(staging).as_posix(),
                                  bytes=path.stat().st_size, sha256=_hash(path))
                             for path in sorted(staging.rglob("*")) if path.is_file()]
        (staging / "manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        # No replacement of existing destinations, even if another caller won the race.
        _publish(staging, destination)
        return manifest
    except BaseException:
        shutil.rmtree(staging)
        raise
