"""Discover exact asset files and expose complete FL file workflows."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field


def register_studio_file_tools(mcp):
    @mcp.tool()
    def fl_find_assets(
        query: str,
        kind: Literal["all", "sample", "preset", "plugin", "generator", "effect"] = "all",
        roots: list[str] | None = None,
        limit: int = 30,
    ) -> dict:
        """Find exact paths for samples, .fst/.serumpreset presets and installed plugins.

        Read-only; FL need not be running. Query matches filenames without case
        sensitivity; an empty query lists assets. Search active FL user-data and
        installed stock folders, or provide absolute asset directories in roots.
        Generator/effect identity comes from Plugin database directory structure.
        Check scan_complete/truncated and narrow roots when incomplete. Duplicate
        names remain separate paths; never choose a fuzzy plugin match to load.
        limit is 1..100. Drive roots, symlinks and junction traversal are excluded.
        """
        from fl_studio_mcp.utils.asset_catalog import find_assets

        return find_assets(query, kind=kind, roots=roots, limit=limit)

    @mcp.tool()
    async def fl_sample_add(
        path: str,
        name: str | None = None,
        mixer_track: int | None = None,
    ) -> dict:
        """Add a WAV as a new Channel Rack sampler through the native backend.

        path must be an exact absolute sample path from fl_find_assets or the user.
        mixer_track is a zero-based insert. OLE delivery and native readback verify
        the new sampler channel, its name/type and routing; independent sample
        source-path readback is unavailable. Experimental OLE bridge, no desktop input.
        Requires running FL on Windows; do not retry
        an uncertain result because the channel may already have been created.
        """
        from fl_studio_mcp.utils.studio_loading import run_file_workflow

        return await run_file_workflow("add_sample", path=path, name=name, mixer_track=mixer_track)

    @mcp.tool()
    async def fl_instrument_add(
        name: str | None = None,
        path: str | None = None,
        channel_name: str | None = None,
        mixer_track: int | None = None,
    ) -> dict:
        """Add a generator as a new Channel Rack instrument and verify its identity.

        Supply an exact Plugin database name or an absolute generator .fst path.
        Name resolution prefers the active user's Generator favorites, rejects
        ambiguity, then searches installed entries. Optional channel_name and
        zero-based mixer_track name/route the new channel. Windows FL required.
        Experimental native OLE bridge; no keyboard, mouse or mode switch.
        """
        from fl_studio_mcp.utils.studio_loading import run_file_workflow

        return await run_file_workflow(
            "add_instrument", name=name, path=path,
            channel_name=channel_name, mixer_track=mixer_track,
        )

    @mcp.tool()
    async def fl_effect_add(
        track: int,
        slot: Annotated[int, Field(ge=0, le=9, strict=True)],
        name: str | None = None,
        path: str | None = None,
    ) -> dict:
        """Add an effect to an empty mixer slot, then verify the actual plugin.

        track is zero-based; slot 0..9 corresponds to FL slots 1..10. Supply an
        exact Plugin database effect name or an absolute effect .fst path. Names
        prefer active-user favorites and reject ambiguity. Occupied slots are
        guarded; this does not grant permission to replace another effect.
        Experimental native OLE bridge with native track selection and readback.
        """
        from fl_studio_mcp.utils.studio_loading import run_file_workflow

        return await run_file_workflow("add_effect", track=track, slot=slot, name=name, path=path)

    @mcp.tool()
    async def fl_preset_load(
        path: str,
        target: Literal["new_channel", "channel", "mixer"] = "new_channel",
        channel: int | None = None,
        track: int | None = None,
        slot: Annotated[int | None, Field(ge=0, le=9, strict=True)] = None,
        expected_name: str | None = None,
    ) -> dict:
        """Load an exact FL .fst preset into a new channel or a guarded existing target.

        target selects new_channel, channel (with global channel index), or mixer
        (with track and slot). Existing targets require a fresh expected_name.
        All indices are zero-based; mixer slots are 0..9. This replaces the
        target's sound/state, retaining an existing channel's name and mixer route.
        Supports plugin .fst format 0x30; full mixer-track format 0x40 is rejected.
        Use Serum tools for native .SerumPreset formats.
        Inspect current identities before loading; uncertain writes are not retried.
        Experimental native OLE bridge; desktop input is never used.
        """
        from fl_studio_mcp.utils.studio_loading import run_file_workflow

        return await run_file_workflow(
            "load_preset", path=path, target=target, channel=channel,
            track=track, slot=slot, expected_name=expected_name,
        )

    @mcp.tool()
    def fl_piano_roll_prepare(
        channel: int | None = None,
        expected_name: str | None = None,
    ) -> dict:
        """Unavailable in backend-only SyncFL. Legacy piano-script activation is unavailable.
        Use fl_background_piano_inspect and fl_background_piano_select.
        """
        from fl_studio_mcp.utils.piano_ready import prepare_piano

        return prepare_piano(channel=channel, expected_name=expected_name)
