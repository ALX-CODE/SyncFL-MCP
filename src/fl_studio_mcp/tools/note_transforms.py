"""Pure musical note transformations kept separate from guarded live writes."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field


def register_note_transform_tools(mcp):
    @mcp.tool(annotations={"readOnlyHint": True, "idempotentHint": True})
    def fl_notes_transform(
        notes: list[dict],
        ppq: Annotated[int, Field(ge=1, le=96000, strict=True)],
        transpose_semitones: Annotated[int, Field(ge=-127, le=127, strict=True)] = 0,
        grid_ticks: Annotated[int | None, Field(ge=1, strict=True)] = None,
        strength: Annotated[float, Field(ge=0, le=1, strict=True, allow_inf_nan=False)] = 1.0,
        quantize_lengths: Annotated[bool, Field(strict=True)] = False,
    ) -> dict:
        """Propose transposition/quantization offline, preserving all FL note expression.

        Required ppq is explicit. notes uses fl_send_notes fields: midi, time/duration
        in quarter-note beats or exact time_ticks/length_ticks; velocity, pan,
        release, color, slide, porta, pitchofs, fcut, fres, muted, selected, group,
        repeats are retained. Conflicting beat/tick aliases and unknown fields fail.
        Velocity, pan, release, fcut and fres use normalized 0..1 values.
        Up to 65536 notes; order and duplicates are preserved.

        grid_ticks optionally quantizes onsets to multiples of that integer tick
        spacing from zero, without assuming a time signature. strength 0..1 blends
        from original to the nearest grid; quantize_lengths also quantizes lengths
        using that grid/strength. Grid and blend ties round to the larger tick;
        beat input conversion uses the shared nearest-even note protocol. Pitch or
        timing overflow and zero quantized lengths fail, never clamp. No randomness.

        Returns proposed normalized notes, changed_count, content hashes and
        applied:false. Hashes describe ordered content plus PPQ, not live revisions.
        Inspect the intended channel and fresh piano-roll state before explicitly
        using the existing guarded note workflow. This never queues or applies notes.
        """
        from fl_studio_mcp.utils.note_transforms import transform_notes

        return transform_notes(
            notes, ppq, transpose_semitones, grid_ticks, strength, quantize_lengths,
        )
