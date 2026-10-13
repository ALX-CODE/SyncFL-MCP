"""SyncFL transport tools: typed inputs, validated dispatch and native readback."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fl_studio_mcp.utils.tool_results import call

if TYPE_CHECKING:
    from fastmcp import FastMCP


def register_transport_tools(mcp: FastMCP) -> None:

    @mcp.tool()
    def fl_play() -> str:
        """Toggle playback and report the acknowledged play state."""
        return call(
            "transport.start",
            {},
            shape="text",
            label="Play",
        )

    @mcp.tool()
    def fl_stop() -> str:
        """Stop playback and return the controller acknowledgement."""
        return call(
            "transport.stop",
            {},
            shape="text",
            label="Stop",
        )

    @mcp.tool()
    def fl_record() -> str:
        """Toggle recording; inspect the returned state before sending another toggle."""
        return call(
            "transport.record",
            {},
            shape="text",
            label="Record",
        )

    @mcp.tool()
    def fl_get_transport_status() -> dict:
        """Read playback, recording, timeline position and song/pattern mode."""
        return call(
            "transport.getStatus",
            {},
        )

    @mcp.tool()
    def fl_set_song_position(position: float, mode: int = 1) -> str:
        """Seek using mode -1 (normalized), 0 (milliseconds), 1 (seconds) or 2 (ticks)."""
        return call(
            "transport.setPosition",
            {"position": position, "mode": mode},
            shape="text",
            label="Set song position",
        )

    @mcp.tool()
    def fl_get_song_length() -> dict:
        """Read the host timeline duration in ticks, seconds and milliseconds."""
        return call(
            "transport.getLength",
            {},
        )

    @mcp.tool()
    def fl_set_loop_mode(mode: str) -> str:
        """Choose song or pattern playback without keyboard input."""
        return call(
            "transport.setLoopMode",
            {"mode": mode},
            shape="text",
            label="Set loop mode",
        )

    @mcp.tool()
    def fl_set_playback_speed(speed: float) -> str:
        """Set a finite playback multiplier between 0.25 and 4."""
        return call(
            "transport.setPlaybackSpeed",
            {"speed": speed},
            shape="text",
            label="Set playback speed",
        )
