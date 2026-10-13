"""Typed production controls with explicit musical targets and revisions."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool

from fl_studio_mcp.utils.production import edit_production, inspect_production

Unit = Annotated[float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)]
Pan = Annotated[float, Field(strict=True, ge=-1, le=1, allow_inf_nan=False)]


class ProjectSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tempo_bpm: Annotated[
        float, Field(strict=True, ge=10, le=522, allow_inf_nan=False)
    ] | None = None
    shuffle: Unit | None = None


class ChannelSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    volume_normalized: Unit | None = None
    pan: Pan | None = None
    pitch_semitones: Annotated[
        float, Field(strict=True, ge=-48, le=48, allow_inf_nan=False)
    ] | None = None
    muted: StrictBool | None = None
    mixer_track: Annotated[int, Field(strict=True, ge=0)] | None = None


def register_production_tools(mcp):
    @mcp.tool()
    def fl_production_inspect(
        scope: Literal["project", "channel", "steps"],
        channel: int | None = None,
        pattern: int | None = None,
        total_steps: int | None = None,
    ) -> dict:
        """Read exact targets and a revision for guarded edits.

        Channel indices are GLOBAL, zero-based. Steps need the currently selected
        one-based pattern and an explicit total_steps extent (1..1024). Project
        reports fractional BPM, shuffle (0..1), and recording PPQ.
        """
        target = {k: v for k, v in dict(scope=scope, channel=channel, pattern=pattern,
                                        total_steps=total_steps).items() if v is not None}
        return inspect_production(target)

    @mcp.tool()
    def fl_project_settings(
        changes: ProjectSettings, expected_revision: str, apply: StrictBool = False,
        receipt_file: str | None = None,
    ) -> dict:
        """Preflight or set exact BPM, and shuffle where FL exposes its native API.

        Inspect project first. Apply needs stopped transport, the unchanged
        revision and a NEW absolute receipt_file. A null inspected shuffle means
        unavailable on this build; its writes are rejected. PPQ/meter edits are not enabled.
        """
        return edit_production({"scope": "project"}, changes.model_dump(exclude_none=True),
                               expected_revision, apply, receipt_file)

    @mcp.tool()
    def fl_channel_configure(
        channel: int, changes: ChannelSettings, expected_revision: str,
        apply: StrictBool = False, receipt_file: str | None = None,
    ) -> dict:
        """Configure an inspected GLOBAL channel: gain, pan, pitch, mute or routing.

        Volume is normalized 0..1; pitch is semitones. Apply requires the unchanged
        channel revision and a new absolute receipt. Readback runs in new callbacks.
        """
        return edit_production({"scope": "channel", "channel": channel},
                               changes.model_dump(exclude_none=True), expected_revision,
                               apply, receipt_file)

    @mcp.tool()
    def fl_step_sequence_edit(
        channel: int, pattern: int, total_steps: int, sequence: list[StrictBool],
        expected_revision: str, apply: StrictBool = False, receipt_file: str | None = None,
    ) -> dict:
        """Replace exactly total_steps (1..1024) on a GLOBAL channel/selected pattern.

        Short sequences are padded with false, clearing unused steps WITHIN the
        explicit extent; steps beyond that extent are preserved. Inspect the same
        target/extent first. Apply needs a fresh revision and a new absolute receipt.
        """
        return edit_production(
            dict(scope="steps", channel=channel, pattern=pattern, total_steps=total_steps),
            {"sequence": sequence}, expected_revision, apply, receipt_file,
        )
