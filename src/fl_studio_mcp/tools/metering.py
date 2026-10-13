"""Short, read-only observations of native FL mixer meters."""


def register_metering_tools(mcp):
    @mcp.tool(annotations={"readOnlyHint": True, "idempotentHint": False})
    def fl_mixer_meter_watch(
        tracks: list[int],
        seconds: float = 2.0,
        interval_ms: int = 100,
        expected_names: list[str] | None = None,
    ) -> dict:
        """Sample native peaks on 1..32 exact mixer tracks for 0.1..10 seconds.

        Zero-based tracks; optional exact expected_names must align with tracks.
        Minimum interval is 50 ms, plus controller/readback overhead. Uses
        a sampling window after setup; setup and the last sample can add at
        most four seconds. Actual observed duration is returned explicitly.
        Reports observed left/right/maximum peaks and complete sample counts; errors or
        project/session/track identity changes return incomplete partial evidence.
        Never starts playback, recording, or changes routing. Samples may miss
        transients and are not LUFS, true peaks, waveform RMS or clipping proof.
        """
        from fl_studio_mcp.utils.metering import watch_mixer_meters

        return watch_mixer_meters(tracks, seconds, interval_ms, expected_names)
