"""Local MIDI reference discovery without a live FL connection."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field


def register_midi_reference_tools(mcp):
    @mcp.tool(annotations={"readOnlyHint": True, "idempotentHint": True})
    def fl_midi_reference_search(
        query: str = "",
        roots: list[str] | None = None,
        limit: Annotated[int, Field(ge=1, le=100, strict=True)] = 20,
        query_notes: list[dict] | None = None,
        max_files: Annotated[int, Field(ge=1, le=2000, strict=True)] = 250,
    ) -> dict:
        """Find local .mid/.midi references by filename/path tokens or note fingerprints.

        Read-only; no FL, network, database or cache required. roots must be explicit
        absolute directories; omission uses repository references/lead-midi only
        when present. Drive roots, symlinks/junctions and SMPTE/type-2 MIDI are
        excluded. Check scan_complete, truncated and errors before treating the
        results as a complete corpus search. Limits include 1 MiB/file, 32 MiB total,
        100000 events/file, 256 tracks, depth 16 and a 10-second scan budget.

        query_notes is an optional list of exactly {pitch: 0..127, start_beats:
        nonnegative number, duration_beats: positive number}, maximum 1024 notes.
        All times use quarter-note beats, independent of embedded tempo. Rankings
        expose filename token coverage and histogram intersection for pitch class,
        onset phase and duration. Similarity is not a sound or musical-quality
        score and does not compare melodic order. Unclosed note durations are
        excluded; overlapping same-pitch notes pair FIFO per track/channel.

        Returns exact paths, SHA-256 hashes, per-track durations/note counts and
        fingerprints. A path identifies a reference to inspect/audition; it does
        not import MIDI or modify the live project. Extracted reference tempos may
        be placeholders and generic MIDI does not preserve all FL articulation.
        """
        from fl_studio_mcp.utils.midi_reference import search_midi_references

        return search_midi_references(query, roots, limit, query_notes, max_files)
