"""General audio assets and render verification."""


def register_audio_tools(mcp):
    @mcp.tool()
    def fl_audio_bpm(
        path: str, min_bpm: float = 40.0, max_bpm: float = 240.0, top_n: int = 5,
    ) -> dict:
        """Estimate a WAV's tempo with the bundled Rust core; no FL or desktop input.

        Returns BPM candidates, periodicity, window consistency and half/double
        relationships. Confidence is relative among returned candidates, not a
        probability of correctness. Silence/insufficient pulse produces no BPM.
        Uses at most the first 180 seconds and reports the analyzed duration.
        Fixed-tempo estimate only: variable tempo and sparse/syncopated music can
        be ambiguous. min/max bound search (20..400 BPM), top_n is 1..10.
        Supports PCM/IEEE float RIFF WAV; the bundled runtime is Windows x64.
        Never changes project tempo, stretches audio or writes the input file.
        """
        from fl_studio_mcp.utils.tempo_analysis import analyze_tempo

        return analyze_tempo(path, min_bpm=min_bpm, max_bpm=max_bpm, top_n=top_n)

    @mcp.tool()
    def fl_audio_key(path: str, top_n: int = 5, temperature: float = 0.15) -> dict:
        """Rank global major/minor key candidates from a WAV, without FL or desktop input.

        NumPy only (install the key extra). Reads PCM 8/16/24/32-bit and IEEE
        float 32/64-bit RIFF WAVs, 1..8 channels, up to two hours. Uses a 4096/1024
        Hann STFT, 55..5000 Hz, A440 and Krumhansl-Kessler profiles. top_n: 1..24;
        temperature must be positive. Confidence is a relative softmax share of
        ALL 24 candidates, not a probability of correctness. Silence/flat chroma
        returns no candidates. A global estimate cannot track modulations and
        can confuse relative keys, percussion, detuning or short excerpts.
        Never changes audio, the project or plugin tuning settings.
        """
        try:
            from fl_studio_mcp.utils.key_analysis import detect_key
        except ModuleNotFoundError as exc:
            if exc.name == "numpy":
                raise RuntimeError("Key analysis requires NumPy; install the key extra") from exc
            raise

        candidates = detect_key(path, top_n=top_n, temperature=temperature)
        return dict(
            candidates=candidates,
            analysis="global_major_minor",
            tuning_reference_hz=440,
            confidence_basis="relative_softmax_over_all_24_keys_not_calibrated_probability",
            no_tonal_evidence=not candidates,
        )

    @mcp.tool()
    async def fl_render_start(
        directory: str,
        expected_revision: str,
        options: dict | None = None,
        apply: bool = False,
        expected_sample_rate: int | None = None,
        expected_min_seconds: float | None = None,
    ) -> dict:
        """Unavailable in backend-only SyncFL. Starting a render has no backend implementation.
        fl_render_status can observe an existing render.
        """
        from fl_studio_mcp.utils.windows_workflows import run_workflow

        return await run_workflow(
            "render_start",
            directory=directory,
            expected_revision=expected_revision,
            options=options,
            apply=apply,
            expected_sample_rate=expected_sample_rate,
            expected_min_seconds=expected_min_seconds,
        )

    @mcp.tool()
    async def fl_render_status(directory: str) -> dict:
        """Observe a render job; verify WAVs, duration, levels and stem alignment after completion.

        Returns rendering until the owned dialog closes; verified requires decoded,
        stable WAVs matching options, unchanged saved project and some non-silent audio.
        Running/verifying observations return success=true, verified=false. Only
        status=verified has verified=true. Failed/cancelled jobs return success=false.
        Job receipts persist across MCP reconnects. Never retries Start.
        """
        from fl_studio_mcp.utils.windows_workflows import run_workflow

        return await run_workflow("render_status", directory=directory)

    @mcp.tool()
    async def fl_render_cancel(directory: str) -> dict:
        """Unavailable in backend-only SyncFL. Cancelling a render has no backend
        implementation. fl_render_status can observe an existing render.
        """
        from fl_studio_mcp.utils.windows_workflows import run_workflow

        return await run_workflow("render_cancel", directory=directory)

    @mcp.tool()
    def fl_audio_inspect(path: str, activity_dbfs: float = -60.0) -> dict:
        """Measure a WAV/FLAC/etc: hash, duration, levels, DC, silence and stereo correlation.

        Streams songs up to two hours/eight channels. Includes one-second level
        timeline; no LUFS/true-peak claim. All channels contribute to levels, so
        anti-phase stereo is not incorrectly classified as silence.
        """
        from fl_studio_mcp.utils.audio_files import inspect_audio

        return inspect_audio(path, activity_dbfs)

    @mcp.tool()
    def fl_audio_loudness(
        path: str,
        activity_dbfs: float = -60.0,
        timeout_seconds: float = 120.0,
        dual_mono: bool = False,
    ) -> dict:
        """Read a complete audio file's LUFS, loudness range and oversampled true peak.

        Requires the audio extra and installed FFmpeg with loudnorm (PATH,
        ~/.local/bin, or FL_STUDIO_MCP_FFMPEG). Mono/stereo, 8..192 kHz, <=2 hours
        and <=4 GiB. FFmpeg has a per-file 1..300-second timeout. No audio is
        written; source hash is checked. Compact sample/RMS/DC/stereo metrics
        accompany EBU R128 input measurements. Silence, below-gate material and
        <400 ms files have unavailable LUFS; LRA is unavailable below 3 seconds.
        dual_mono=true models mono playback through two speakers. Does not use FL.
        """
        from fl_studio_mcp.utils.mix_analysis import measure_loudness

        return measure_loudness(path, activity_dbfs, timeout_seconds, dual_mono)

    @mcp.tool()
    def fl_audio_compare(
        path: str,
        reference_path: str,
        level_match: bool = True,
        true_peak_ceiling_dbtp: float = -1.0,
        activity_dbfs: float = -60.0,
        timeout_seconds: float = 120.0,
        dual_mono: bool = False,
    ) -> dict:
        """Compare full mix/reference files by measured loudness, peaks, RMS and stereo metrics.

        Read-only fl_audio_loudness bounds and dependencies apply to each file.
        Deltas are candidate minus reference. Optional constant-gain estimate
        reports matching gain, predicted true peak, ceiling headroom and gain
        limited by the explicit -24..0 dBTP ceiling. It never applies gain or
        claims perceptual similarity. Files are not time-aligned; duration and
        channel-count mismatches are reported. Remeasure audio after processing.
        """
        from fl_studio_mcp.utils.mix_analysis import compare_audio

        return compare_audio(
            path, reference_path, level_match, true_peak_ceiling_dbtp,
            activity_dbfs, timeout_seconds, dual_mono,
        )

    @mcp.tool()
    def fl_audio_edit(
        source: str,
        output: str,
        expected_sha256: str,
        start_frame: int = 0,
        end_frame: int | None = None,
        gain_db: float = 0.0,
        fade_in_frames: int = 0,
        fade_out_frames: int = 0,
        reverse: bool = False,
        normalize_peak_dbfs: float | None = None,
        subtype: str = "PCM_24",
        dry_run: bool = True,
    ) -> dict:
        """Trim/reverse/fade/gain a sample to a NEW WAV with exact frame and sample readback.

        Source hash from fl_audio_inspect. End frame is exclusive. Linear fades
        use zero or at least two frames and cannot overlap. Normalization applies
        after fades and requires gain_db=0. Formats: PCM_16/PCM_24/FLOAT. PCM clipping
        is rejected. Preview by default; select <=48 million interleaved samples.
        Source stays unchanged; loop/cue metadata is not copied. Use fl_sample_add
        to load the result into FL. Requires the audio dependency extra.
        """
        from fl_studio_mcp.utils.audio_files import edit_audio

        return edit_audio(
            source,
            output,
            expected_sha256,
            start_frame,
            end_frame,
            gain_db,
            fade_in_frames,
            fade_out_frames,
            reverse,
            normalize_peak_dbfs,
            subtype,
            dry_run,
        )
