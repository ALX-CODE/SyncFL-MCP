"""Read-only, bounded MIDI reference discovery and transparent musical summaries.

Fingerprints compare pitch classes and notated rhythm, not sound or musical
quality. No MIDI, index, cache or database is written by this module.
"""

from __future__ import annotations

import hashlib
import io
import math
import os
import re
import stat
import time
import unicodedata
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

import mido

MAX_ROOTS = 16
MAX_RESULTS = 100
MAX_FILES = 2_000
MAX_ENTRIES = 100_000
MAX_DIRECTORIES = 5_000
MAX_DEPTH = 16
MAX_SECONDS = 10.0
MAX_FILE_BYTES = 1_048_576
MAX_TOTAL_BYTES = 33_554_432
MAX_EVENTS = 100_000
MAX_TRACKS = 256
MAX_QUERY_NOTES = 1_024
MAX_ERRORS = 20
DURATION_BINS = (0.0625, 0.125, 0.25, 0.5, 1.0, 2.0, 4.0, 8.0)


def _is_link(path: Path) -> bool:
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def _default_roots() -> list[Path]:
    # A wheel may not contain repository references. Never substitute a broad
    # user/drive scan when the explicitly maintained corpus is absent.
    candidate = Path(__file__).resolve().parents[3] / "references" / "lead-midi"
    return [candidate] if candidate.is_dir() else []


def _roots(roots: list[str | Path] | None) -> list[Path]:
    raw = _default_roots() if roots is None else roots
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_ROOTS:
        raise ValueError(
            f"Provide 1..{MAX_ROOTS} absolute MIDI directories in roots; "
            "the bundled reference directory may be unavailable"
        )
    selected = set()
    for item in raw:
        if not isinstance(item, (str, Path)) or not str(item).strip() or "\0" in str(item):
            raise ValueError("Each MIDI root must be a nonempty absolute directory path")
        path = Path(item)
        if not path.is_absolute() or path == Path(path.anchor):
            raise ValueError("MIDI roots must be absolute directories, not drive/filesystem roots")
        try:
            if any(_is_link(part) for part in (path, *path.parents)):
                raise ValueError(f"MIDI roots cannot pass through symlinks or junctions: {path}")
            resolved = path.resolve(strict=True)
            if not resolved.is_dir():
                raise ValueError(f"MIDI root is not a directory: {path}")
        except OSError as exc:
            raise ValueError(f"MIDI root is unavailable: {path}") from exc
        selected.add(resolved)
    # Remove nested roots so neither scanning nor fingerprints are duplicated.
    ordered = sorted(selected, key=lambda path: (len(path.parts), str(path).casefold()))
    return [path for index, path in enumerate(ordered)
            if not any(path.is_relative_to(parent) for parent in ordered[:index])]


def _tokens(value: str) -> list[str]:
    return re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", value).casefold())


def _number(value: Any, name: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    if not 0 <= value <= 1_000_000_000 or not math.isfinite(value):
        raise ValueError(f"{name} must be finite and within 0..1000000000")
    if positive and value == 0:
        raise ValueError(f"{name} must be positive")
    return float(value)


def _query_notes(notes: list[dict] | None) -> list[tuple[int, float, float | None]] | None:
    if notes is None:
        return None
    if not isinstance(notes, list) or not 1 <= len(notes) <= MAX_QUERY_NOTES:
        raise ValueError(f"query_notes must contain 1..{MAX_QUERY_NOTES} notes")
    result = []
    for index, note in enumerate(notes):
        if not isinstance(note, dict) or set(note) != {"pitch", "start_beats", "duration_beats"}:
            raise ValueError(
                "Each query note must contain exactly pitch, start_beats and duration_beats"
            )
        pitch = note["pitch"]
        if isinstance(pitch, bool) or not isinstance(pitch, int) or not 0 <= pitch <= 127:
            raise ValueError(f"query_notes[{index}].pitch must be an integer within 0..127")
        start = _number(note["start_beats"], "start_beats")
        duration = _number(note["duration_beats"], "duration_beats", positive=True)
        result.append((pitch, start, duration))
    return result


def _fingerprint(notes: list[tuple[int, float, float | None]]) -> dict:
    pitches = [0] * 12
    phases = [0] * 16
    durations = [0] * (len(DURATION_BINS) + 1)
    for pitch, start, duration in notes:
        pitches[pitch % 12] += 1
        # Sixteen equal bins within one quarter-note beat. The result is tempo
        # independent but deliberately preserves the notated beat phase.
        phases[min(15, int((start % 1.0) * 16))] += 1
        if duration is not None:
            index = next((i for i, edge in enumerate(DURATION_BINS) if duration <= edge), 8)
            durations[index] += 1

    def normalized(values):
        total = sum(values)
        return [value / total if total else 0.0 for value in values]

    return {
        "pitch_class": normalized(pitches),
        "onset_phase": normalized(phases),
        "duration": normalized(durations),
        "duration_observations": sum(durations),
    }


def _validate_chunks(data: bytes, track_count: int) -> None:
    """Bound event construction and VLQs before mido allocates message objects."""
    offset = 14
    events = 0

    def vlq(position, end):
        value = 0
        for _ in range(4):
            if position >= end:
                raise ValueError("Truncated MIDI variable-length integer")
            byte = data[position]
            position += 1
            value = (value << 7) | (byte & 0x7f)
            if byte < 0x80:
                return value, position
        raise ValueError("MIDI variable-length integer exceeds four bytes")

    for _ in range(track_count):
        if data[offset:offset + 4] != b"MTrk" or offset + 8 > len(data):
            raise ValueError("Missing or truncated MIDI track chunk")
        end = offset + 8 + int.from_bytes(data[offset + 4:offset + 8], "big")
        if end > len(data):
            raise ValueError("MIDI track chunk exceeds file length")
        position = offset + 8
        running = None
        ended = False
        while position < end:
            events += 1
            if events > MAX_EVENTS:
                raise ValueError(f"MIDI exceeds the {MAX_EVENTS} event limit")
            _, position = vlq(position, end)
            if position >= end:
                raise ValueError("MIDI event is missing its status")
            status = data[position]
            if status < 0x80:
                if running is None:
                    raise ValueError("MIDI running status has no channel status")
                status = running
            else:
                position += 1
            if 0x80 <= status < 0xf0:
                running = status
                length = 1 if status & 0xe0 == 0xc0 else 2
                if position + length > end or any(
                    byte > 0x7f for byte in data[position:position + length]
                ):
                    raise ValueError("Invalid or truncated MIDI channel data")
            elif status in (0xf0, 0xf7, 0xff):
                meta = None
                if status == 0xff:
                    if position >= end or data[position] > 0x7f:
                        raise ValueError("Invalid MIDI meta-event type")
                    meta = data[position]
                    position += 1
                else:
                    running = None
                length, position = vlq(position, end)
                if position + length > end:
                    raise ValueError("MIDI event payload exceeds its track")
                if meta == 0x2f:
                    if length != 0 or position != end:
                        raise ValueError("MIDI end-of-track must be an empty final event")
                    ended = True
            else:
                raise ValueError("Unsupported system status in a MIDI track")
            position += length
        if not ended:
            raise ValueError("MIDI track is missing end-of-track")
        offset = end
    if offset != len(data):
        raise ValueError("Unexpected trailing data after MIDI track chunks")


def _summary(data: bytes) -> dict:
    if len(data) < 14 or data[:4] != b"MThd" or int.from_bytes(data[4:8], "big") != 6:
        raise ValueError("Expected a standard MIDI header of length 6")
    kind = int.from_bytes(data[8:10], "big")
    tracks = int.from_bytes(data[10:12], "big")
    division = int.from_bytes(data[12:14], "big")
    if kind not in (0, 1):
        raise ValueError(
            "Only synchronous MIDI types 0 and 1 are supported; type 2 has no shared timeline"
        )
    if not 1 <= tracks <= MAX_TRACKS or (kind == 0 and tracks != 1):
        raise ValueError(f"MIDI must contain 1..{MAX_TRACKS} tracks (type 0 requires exactly one)")
    if division == 0 or division & 0x8000:
        raise ValueError(
            "MIDI must use positive ticks per quarter-note beat; SMPTE timing is unsupported"
        )
    _validate_chunks(data, tracks)
    midi = mido.MidiFile(file=io.BytesIO(data), clip=False)
    all_notes = []
    summaries = []
    tempos = set()
    for index, track in enumerate(midi.tracks):
        tick = 0
        notes = []
        active = defaultdict(deque)
        unmatched = overlaps = 0
        for message in track:
            # Non-note events carry delta time too, including program/tempo changes.
            tick += message.time
            if message.type == "set_tempo":
                tempos.add(message.tempo)
            if message.type == "note_on" and message.velocity > 0:
                key = (message.channel, message.note)
                overlaps += bool(active[key])
                active[key].append(len(notes))
                notes.append([message.note, tick, None])
            elif message.type == "note_off" or (
                message.type == "note_on" and message.velocity == 0
            ):
                key = (message.channel, message.note)
                if active[key]:
                    note = notes[active[key].popleft()]
                    note[2] = (tick - note[1]) / division
                else:
                    unmatched += 1
        unclosed = sum(len(pending) for pending in active.values())
        summaries.append({
            "track": index,
            "name": track.name[:256],
            "name_truncated": len(track.name) > 256,
            "duration_beats": tick / division,
            "note_count": len(notes),
            "pitch_range": [min(note[0] for note in notes), max(note[0] for note in notes)]
            if notes else None,
            "unmatched_note_offs": unmatched,
            "unclosed_notes": unclosed,
            "overlapping_same_pitch": overlaps,
        })
        all_notes.extend((pitch, start / division, duration) for pitch, start, duration in notes)
    duration = max(track["duration_beats"] for track in summaries)
    return {
        "midi_type": kind,
        "ticks_per_beat": division,
        "duration_beats": duration,
        "note_count": len(all_notes),
        "notes_per_beat": len(all_notes) / duration if duration else None,
        "pitch_range": [min(note[0] for note in all_notes), max(note[0] for note in all_notes)]
        if all_notes else None,
        "embedded_tempos_us_per_beat": sorted(tempos)[:128],
        "embedded_tempos_truncated": len(tempos) > 128,
        "tracks": summaries,
        "fingerprint": _fingerprint(all_notes),
    }


def _score(path: Path, root: Path, query: str, reference: dict | None, summary: dict) -> dict:
    tokens = list(dict.fromkeys(_tokens(query)))
    name_tokens = _tokens(path.stem)
    path_tokens = _tokens(str(path.relative_to(root).with_suffix("")))
    matched = [token for token in tokens if token in path_tokens]
    exact = bool(tokens) and tokens == name_tokens
    text_score = (0.85 * sum(token in name_tokens for token in tokens)
                  + 0.15 * len(matched)) / len(tokens) if tokens else 0.0
    if exact:
        text_score = 1.0
    components = None
    note_score = None
    if reference is not None:
        components = {
            key: sum(min(a, b) for a, b in zip(reference[key], summary["fingerprint"][key]))
            for key in ("pitch_class", "onset_phase", "duration")
        }
        note_score = sum(components[key] * weight for key, weight in (
            ("pitch_class", 0.5), ("onset_phase", 0.3), ("duration", 0.2),
        ))
    score = text_score
    if note_score is not None:
        score = 0.4 * text_score + 0.6 * note_score if tokens else note_score
    return {"score": score, "text_score": text_score, "note_similarity": note_score,
            "similarity_components": components, "matched_query_tokens": matched}


def search_midi_references(
    query: str = "",
    roots: list[str | Path] | None = None,
    limit: int = 20,
    query_notes: list[dict] | None = None,
    max_files: int = 250,
) -> dict[str, Any]:
    """Find MIDI references without loading FL or changing any files.

    Text search uses case-insensitive filename/directory tokens, not embeddings.
    Optional note similarity uses histogram intersection (0..1). Duration is in
    quarter-note beats, including all event deltas and trailing rests. FIFO note
    pairing is explicit; unmatched note durations are never invented.
    """
    if not isinstance(query, str) or len(query) > 256 or "\0" in query:
        raise ValueError("query must be a string of at most 256 characters without NUL")
    if query.strip() and not _tokens(query):
        raise ValueError("query must contain at least one letter or number")
    for value, name, maximum in (
        (limit, "limit", MAX_RESULTS), (max_files, "max_files", MAX_FILES),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
            raise ValueError(f"{name} must be an integer within 1..{maximum}")
    parsed_query = _query_notes(query_notes)
    reference = _fingerprint(parsed_query) if parsed_query is not None else None
    selected = _roots(roots)
    started = time.monotonic()
    stack = [(root, root, 0) for root in reversed(selected)]
    results, errors, warnings = [], [], []
    seen = set()
    entries_seen = scanned_files = bytes_read = directories = skipped_links = error_count = 0

    def expired():
        return time.monotonic() - started >= MAX_SECONDS

    def problem(message, path=None):
        nonlocal error_count
        error_count += 1
        if len(errors) < MAX_ERRORS:
            errors.append({"path": str(path) if path is not None else None, "error": message})

    exhausted = False
    while stack and not exhausted:
        if expired() or directories >= MAX_DIRECTORIES or entries_seen >= MAX_ENTRIES:
            warnings.append("Scan reached its directory, entry or time budget; narrow roots")
            break
        directory, root, depth = stack.pop()
        directories += 1
        try:
            if _is_link(directory):
                skipped_links += 1
                continue
            if not directory.resolve(strict=True).is_relative_to(root):
                problem("Directory resolved outside its selected root", directory)
                continue
            with os.scandir(directory) as entries:
                children = []
                for entry in entries:
                    if expired() or entries_seen >= MAX_ENTRIES:
                        warnings.append("Scan reached its entry or time budget; narrow roots")
                        exhausted = True
                        break
                    entries_seen += 1
                    path = Path(entry.path)
                    try:
                        if _is_link(path):
                            skipped_links += 1
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            if depth >= MAX_DEPTH:
                                problem("Scan depth limit reached", path)
                            else:
                                children.append((path, root, depth + 1))
                            continue
                        if path.suffix.casefold() not in {".mid", ".midi"} or not entry.is_file(
                            follow_symlinks=False,
                        ):
                            continue
                        path = path.resolve(strict=True)
                        if not path.is_relative_to(root):
                            problem("MIDI resolved outside its selected root", path)
                            continue
                        if path in seen:
                            continue
                        seen.add(path)
                        if scanned_files >= max_files:
                            warnings.append(
                                "Scan reached max_files; narrow roots or raise max_files"
                            )
                            exhausted = True
                            break
                        scanned_files += 1
                        before = path.stat()
                        if before.st_size > MAX_FILE_BYTES:
                            problem(f"MIDI exceeds the {MAX_FILE_BYTES} byte file limit", path)
                            continue
                        if bytes_read + before.st_size > MAX_TOTAL_BYTES:
                            warnings.append("Scan reached its total byte budget; narrow roots")
                            exhausted = True
                            break
                        with path.open("rb") as source:
                            data = source.read(MAX_FILE_BYTES + 1)
                        bytes_read += len(data)
                        after = path.stat()
                        if len(data) > MAX_FILE_BYTES or (
                            before.st_size, before.st_mtime_ns
                        ) != (after.st_size, after.st_mtime_ns):
                            problem("MIDI changed during the read or exceeded its size limit", path)
                            continue
                        summary = _summary(data)
                        ranking = _score(path, root, query, reference, summary)
                        if query.strip() and not ranking["matched_query_tokens"]:
                            continue
                        results.append({"name": path.stem, "path": str(path),
                                        "sha256": hashlib.sha256(data).hexdigest(),
                                        "bytes": len(data), **summary, "ranking": ranking})
                    except (OSError, ValueError, EOFError, KeyError, IndexError) as exc:
                        problem(str(exc), path)
                stack.extend(sorted(
                    children, key=lambda child: str(child[0]).casefold(), reverse=True,
                ))
        except OSError as exc:
            problem(str(exc), directory)
    if expired() and not warnings:
        warnings.append("Scan reached its time budget; narrow roots")
    results.sort(key=lambda result: (
        -result["ranking"]["score"], result["name"].casefold(), result["path"].casefold(),
    ))
    complete = not warnings and not error_count
    return {
        "references": results[:limit], "roots": [str(root) for root in selected], "query": query,
        "matching_files": len(results), "scanned_files": scanned_files,
        "scan_complete": complete, "truncated": not complete or len(results) > limit,
        "skipped_links": skipped_links, "errors": errors, "error_count": error_count,
        "warnings": list(dict.fromkeys(warnings)), "bytes_read": bytes_read,
        "limits": {"max_files": max_files, "file_bytes": MAX_FILE_BYTES,
                   "total_bytes": MAX_TOTAL_BYTES, "events_per_file": MAX_EVENTS,
                   "tracks_per_file": MAX_TRACKS, "entries": MAX_ENTRIES,
                   "directories": MAX_DIRECTORIES, "depth": MAX_DEPTH, "seconds": MAX_SECONDS},
        "fingerprint_definition": {
            "pitch_class": "12 normalized note-on counts (C through B); pitch sensitive",
            "onset_phase": "16 normalized onset-count bins within each quarter-note beat",
            "duration": "Normalized matched-note counts in ascending duration bins",
            "duration_bin_upper_bounds_beats": [*DURATION_BINS, None],
            "note_pairing": "FIFO per track/channel/pitch; unclosed durations excluded",
        },
        "ranking_definition": {
            "text": "0.85 filename-token coverage + 0.15 relative-path-token coverage",
            "note_similarity": "Histogram intersection: 0.5 pitch + 0.3 onset phase + 0.2 duration",
            "combined": "0.4 text + 0.6 notes when both supplied; otherwise the supplied score",
            "limits": (
                "No audio, melodic order, transposition invariance or musical quality comparison"
            ),
        },
    }
