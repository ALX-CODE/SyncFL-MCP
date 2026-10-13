"""Typed first native audio-clip placement without external desktop orchestration."""


def register_playlist_import_tools(mcp):
    @mcp.tool()
    async def fl_audio_clip_add(
        path: str,
        expected_audio_sha256: str,
        expected_revision: str,
        directory: str,
        position_ticks: int,
        track: int,
        apply: bool = False,
    ) -> dict:
        """Import one WAV as a new Playlist Audio Clip; preview by default.

        Requires the WAV hash from fl_audio_inspect, current project revision from
        fl_production_inspect, stopped transport, and an absolute NEW work directory.
        position_ticks is nonnegative; Playlist track is one-based 1..500. The
        visible FL26.1/96 DPI Playlist must contain a default-named, unmuted row
        empty across saved arrangements. Native import creates a new audio source;
        the server saves, places it exactly in a new FLP, opens and saves that copy
        to prove placement/source identity. Active project becomes verified.flp.
        Keeps before.flp and durable receipts. A timeout/needs_inspection result
        must be inspected before retrying; one file drop is dispatched at most.
        Does not trim/stretch audio or measure audible playback. success/verified
        both true with status=verified establish saved placement after native reload.
        success_scope is audio_clip_placement. Exact permitted native bookkeeping
        normalizations are reported; existing plugin payload changes fail closed.
        """
        from fl_studio_mcp.utils.playlist_import import run_audio_clip_add

        return await run_audio_clip_add(
            path=path, expected_audio_sha256=expected_audio_sha256,
            expected_revision=expected_revision, directory=directory,
            position_ticks=position_ticks, track=track, apply=apply,
        )
