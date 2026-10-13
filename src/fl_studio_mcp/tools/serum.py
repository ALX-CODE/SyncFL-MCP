"""Compact Serum-specific tools. Optional dependencies load only when needed."""


def register_serum_tools(mcp):
    @mcp.tool()
    async def fl_serum_load_preset(channel: int, path: str) -> dict:
        """Unavailable in backend-only SyncFL. Direct live loading of .SerumPreset files has no
        backend implementation. Use offline Serum preset tools, or fl_preset_load for
        an existing FL .fst preset.
        """
        from fl_studio_mcp.utils.serum_windows import load_serum_preset

        return await load_serum_preset(channel, path)

    @mcp.tool()
    def fl_serum_recipes() -> dict:
        """Discover kick/808/lead/pad recipes, editable physical settings and audition notes."""
        from fl_studio_mcp.utils.serum_presets import recipe_catalog

        return recipe_catalog()

    @mcp.tool()
    def fl_serum_inspect(channel: int) -> dict:
        """Read 43 named Serum controls in compact groups; validate live parameter identities.

        Uses the global channel index. Returns supported keys, units, ranges and displays.
        No edits. Unknown FX slots and modulation destinations are not guessed.
        """
        from fl_studio_mcp.utils.serum_live import inspect_serum

        return inspect_serum(channel)

    @mcp.tool()
    def fl_serum_apply(channel: int, changes: dict, dry_run: bool = True) -> dict:
        """Apply named Serum controls in one transaction with settled readback and rollback.

        Example changes: {"env1.decay_ms":300,"osc_a.fine_cents":-32,"voice.mono":true}.
        Requires stopped playback. Validate every key before writing; dry_run defaults true.
        Routing/polarity/FX topology come from a preset, not this live parameter adapter.
        """
        from fl_studio_mcp.utils.serum_live import apply_serum

        return apply_serum(channel, changes, dry_run)

    @mcp.tool()
    def fl_serum_build_preset(
        recipe: str, output_path: str, settings: dict | None = None, name: str | None = None
    ) -> dict:
        """Build a native Serum 2.1.2 preset directly; no GUI/save dialog or FL mutation.

        Recipes: kick, kick_long, 808, lead, pad. Discover settings with fl_serum_recipes.
        Preserve modulation/FX topology from our verified templates. Never overwrites.
        File validation does not mean the preset has been loaded or auditioned.
        """
        from fl_studio_mcp.utils.serum_presets import build_preset

        return build_preset(recipe, output_path, settings, name)

    @mcp.tool()
    def fl_serum_edit_preset(
        source_path: str, output_path: str, settings: dict, name: str | None = None
    ) -> dict:
        """Edit mapped physical controls in a saved Serum preset, preserving other DSP fields.

        Creates a new file, never overwrites. Useful when FL's live cache is stale.
        Same settings as recipe builder; pitch keys require verified Env2-to-A pitch route.
        """
        from fl_studio_mcp.utils.serum_presets import edit_preset

        return edit_preset(source_path, output_path, settings, name)

    @mcp.tool()
    def fl_serum_inspect_preset(path: str) -> dict:
        """Read native Serum 2.1.2 preset envelopes, modulation, oscillator and FX settings.

        Verifies the container/checksum/version. Absent fields mean defaults, not zero.
        """
        from fl_studio_mcp.utils.serum_presets import inspect_preset

        return inspect_preset(path)

    @mcp.tool()
    def fl_serum_analyze_audio(path: str, reference_path: str | None = None) -> dict:
        """Measure WAV/audio levels, activity, reliable pitch and harmonics, optionally compare.

        No FL edits. Pitch may be unavailable for chords, noise and fast sweeps.
        Measurements are not a musical quality or perceptual similarity score.
        """
        from fl_studio_mcp.utils.serum_audio import analyze_audio

        return analyze_audio(path, reference_path)

    @mcp.tool()
    def fl_serum_probe(
        channel: int,
        output_path: str,
        note: int = 32,
        velocity: int = 100,
        hold_seconds: float = 0.6,
        reference_path: str | None = None,
    ) -> dict:
        """Record one temporary Serum note via WASAPI, send note-off, and measure the WAV.

        Requires stopped transport and confirmed Serum channel. Never writes piano notes
        or overwrites audio. Includes other system audio: keep other apps quiet.
        """
        from fl_studio_mcp.utils.serum_audio import capture_serum_probe

        return capture_serum_probe(
            channel, output_path, note, velocity, hold_seconds, reference_path
        )
