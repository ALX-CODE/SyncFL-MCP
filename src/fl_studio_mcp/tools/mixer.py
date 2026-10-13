"""SyncFL mixer tools: typed inputs, validated dispatch and native readback."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fl_studio_mcp.utils.tool_results import call

if TYPE_CHECKING:
    from fastmcp import FastMCP


def register_mixer_tools(mcp: FastMCP) -> None:

    @mcp.tool()
    def fl_get_mixer_track_count() -> int:
        """Read the host mixer capacity, including its master strip; -1 indicates failure."""
        return call(
            "mixer.getTrackCount",
            {},
            shape="int",
            key="count",
        )

    @mcp.tool()
    def fl_get_mixer_track_info(track: int) -> dict:
        """Inspect a zero-based mixer strip, including native gain and dB readback."""
        return call(
            "mixer.getTrackInfo",
            {"track": track},
        )

    @mcp.tool()
    def fl_get_all_mixer_tracks(include_empty: bool = False) -> list[dict]:
        """Inspect occupied mixer strips, or the whole mixer when include_empty is true."""
        return call(
            "mixer.getAllTracks",
            {"include_empty": include_empty},
            shape="list",
            key="tracks",
        )

    @mcp.tool()
    def fl_set_track_volume(track: int, volume: float) -> str:
        """Set normalized mixer gain in 0..1.25; unity is 0.8. Readback includes dB."""
        return call(
            "mixer.setTrackVolume",
            {"track": track, "volume": volume},
            shape="text",
            label="Set track volume",
        )

    @mcp.tool()
    def fl_set_track_pan(track: int, pan: float) -> str:
        """Set mixer balance in -1..1 (left to right), with 0 centered."""
        return call(
            "mixer.setTrackPan",
            {"track": track, "pan": pan},
            shape="text",
            label="Set track pan",
        )

    @mcp.tool()
    def fl_mute_track(track: int, muted: bool | None = None) -> str:
        """Set mixer mute explicitly, or toggle with muted=null. Returns the observed state."""
        return call(
            "mixer.muteTrack",
            {"track": track, "muted": muted},
            shape="text",
            label="Mute track",
        )

    @mcp.tool()
    def fl_solo_track(track: int, solo: bool | None = None, mode: int = 3) -> str:
        """Set/toggle solo. Mode 1 includes sources, 2 sends, 3 both, and 4 only this track."""
        return call(
            "mixer.soloTrack",
            {"track": track, "solo": solo, "mode": mode},
            shape="text",
            label="Solo track",
        )

    @mcp.tool()
    def fl_arm_track(track: int) -> str:
        """Toggle recording arm on one mixer strip; use readback to determine its new state."""
        return call(
            "mixer.armTrack",
            {"track": track},
            shape="text",
            label="Arm track",
        )

    @mcp.tool()
    def fl_set_track_name(track: int, name: str) -> str:
        """Assign a mixer strip label and return the host result."""
        return call(
            "mixer.setTrackName",
            {"track": track, "name": name},
            shape="text",
            label="Set track name",
        )

    @mcp.tool()
    def fl_set_track_color(track: int, red: int, green: int, blue: int) -> str:
        """Assign an RGB mixer label color using integer components in 0..255."""
        return call(
            "mixer.setTrackColor",
            {"track": track, "r": red, "g": green, "b": blue},
            shape="text",
            label="Set track color",
        )

    @mcp.tool()
    def fl_set_stereo_separation(track: int, separation: float) -> str:
        """Set mixer stereo separation in -1..1 using the native control."""
        return call(
            "mixer.setStereoSep",
            {"track": track, "separation": separation},
            shape="text",
            label="Set stereo separation",
        )
