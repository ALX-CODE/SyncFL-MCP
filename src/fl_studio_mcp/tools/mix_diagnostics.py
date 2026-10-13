"""Read-only diagnosis of real rendered audio and reference constraints."""


def register_mix_diagnostics_tools(mcp):
    @mcp.tool(annotations={"readOnlyHint": True, "idempotentHint": True})
    def fl_mix_diagnose(
        path: str,
        reference_path: str | None = None,
        target_lufs: float | None = None,
        true_peak_ceiling_dbtp: float = -1.0,
        activity_dbfs: float = -60.0,
        timeout_seconds: float = 120.0,
        dual_mono: bool = False,
    ) -> dict:
        """Diagnose measured rendered audio; propose changes without applying them.

        Absolute local mono/stereo files only, 8..192 kHz, <=2 hours and <=4 GiB.
        Requires the audio extra and existing FFmpeg; timeout is 1..300 seconds
        per file. Reports real LUFS/true peak, full-scale sample counts and source
        hashes. Choose reference_path OR target_lufs (-70..0), never both.
        The explicit peak ceiling is -24..0 dBTP. Optional gain proposals respect
        that ceiling; predictions require audition and remeasurement. References
        are measured independently without time alignment. No FL calls, writes,
        automatic processing or diagnosis from track names. No audible-quality,
        spectral, EQ or compression claim. dual_mono models stereo playback of mono.
        """
        from fl_studio_mcp.utils.mix_diagnostics import diagnose_audio

        return diagnose_audio(
            path, reference_path, target_lufs, true_peak_ceiling_dbtp,
            activity_dbfs, timeout_seconds, dual_mono,
        )
