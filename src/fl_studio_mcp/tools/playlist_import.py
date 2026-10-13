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
        """Unavailable in backend-only SyncFL. Creating a new Playlist audio channel has no
        backend implementation. Saved-FLP edits can reuse existing audio channels.
        """
        from fl_studio_mcp.utils.playlist_import import run_audio_clip_add

        return await run_audio_clip_add(
            path=path, expected_audio_sha256=expected_audio_sha256,
            expected_revision=expected_revision, directory=directory,
            position_ticks=position_ticks, track=track, apply=apply,
        )
