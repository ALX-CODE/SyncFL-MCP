"""Built-in EQ and strip switches through background-only native operations."""

from fl_studio_mcp.utils.mixer_controls import inspect_controls, set_controls


def register_mixer_control_tools(mcp):
    @mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False})
    def fl_mixer_controls_inspect(track: int) -> dict:
        """Read a strip's native EQ, dB/Hz, effects enable, polarity and stereo swap.

        Does not select the track or change focus. Returns a revision for guarded writes.
        """
        return inspect_controls(track)

    @mcp.tool()
    def fl_mixer_controls_set(
        track: int,
        expected_name: str,
        expected_revision: str,
        changes: dict,
        apply: bool = False,
        receipt_file: str | None = None,
    ) -> dict:
        """Preflight/apply built-in EQ and mixer switches without desktop input.

        changes accepts bands:[{band, gain_normalized?, frequency_normalized?,
        bandwidth_normalized?}] in 0..1, plus effects_enabled, reverse_polarity,
        swap_channels booleans. Band indices start at zero. Hz/dB readbacks are
        observations, not input units. Requires stopped transport and exact name/revision.
        Apply requires a new receipt_file. Two later reads verify requested controls.
        Uncertain writes never retry or automatically restore; inspect the receipt.
        """
        return set_controls(
            track, expected_name, expected_revision, changes, apply=apply, receipt_file=receipt_file
        )
