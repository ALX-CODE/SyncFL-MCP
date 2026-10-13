"""SyncFL channels tools: typed inputs, validated dispatch and native readback."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fl_studio_mcp.utils.tool_results import call

if TYPE_CHECKING:
    from fastmcp import FastMCP


def register_channel_tools(mcp: FastMCP) -> None:

    @mcp.tool()
    def fl_get_channel_count(global_count: bool = True) -> int:
        """Count Channel Rack entries globally, or within the current group."""
        return call(
            "channels.getCount",
            {"global_count": global_count},
            shape="int",
            key="count",
        )

    @mcp.tool()
    def fl_get_channel_info(index: int, use_global_index: bool = True) -> dict:
        """Inspect a Channel Rack entry; global indexing is the default."""
        return call(
            "channels.getInfo",
            {"index": index, "use_global": use_global_index},
        )

    @mcp.tool()
    def fl_get_all_channels() -> list[dict]:
        """List every Channel Rack entry with selection, mute and mixer routing."""
        return call(
            "channels.getAll",
            {},
            shape="list",
            key="channels",
        )

    @mcp.tool()
    def fl_get_selected_channel() -> dict | None:
        """Inspect the selected Channel Rack entry; null means no selection."""
        return call(
            "channels.getSelected",
            {},
            shape="optional",
            key="channel",
        )

    @mcp.tool()
    def fl_select_channel(index: int, select: bool = True) -> str:
        """Select or deselect one global Channel Rack index."""
        return call(
            "channels.select",
            {"index": index, "select": select},
            shape="text",
            label="Select channel",
        )

    @mcp.tool()
    def fl_select_one_channel(index: int) -> str:
        """Make one global Channel Rack entry the exclusive selection."""
        return call(
            "channels.selectOne",
            {"index": index},
            shape="text",
            label="Select one channel",
        )

    @mcp.tool()
    def fl_trigger_note(
        channel: int, note: int, velocity: int = 100, midi_channel: int = -1
    ) -> str:
        """Send a live MIDI note (0..127); velocity 0 releases it. This does not write pattern
        notes.

        """
        return call(
            "channels.triggerNote",
            {"channel": channel, "note": note, "velocity": velocity, "midi_channel": midi_channel},
            shape="text",
            label="Trigger note",
        )

    @mcp.tool()
    def fl_set_channel_volume(index: int, volume: float) -> str:
        """Set normalized Channel Rack gain in 0..1."""
        return call(
            "channels.setVolume",
            {"index": index, "volume": volume},
            shape="text",
            label="Set channel volume",
        )

    @mcp.tool()
    def fl_set_channel_pan(index: int, pan: float) -> str:
        """Set Channel Rack balance in -1..1, with 0 centered."""
        return call(
            "channels.setPan",
            {"index": index, "pan": pan},
            shape="text",
            label="Set channel pan",
        )

    @mcp.tool()
    def fl_mute_channel(index: int, muted: bool | None = None) -> str:
        """Set Channel Rack mute, or toggle when muted is null."""
        return call(
            "channels.mute",
            {"index": index, "muted": muted},
            shape="text",
            label="Mute channel",
        )

    @mcp.tool()
    def fl_solo_channel(index: int, solo: bool | None = None) -> str:
        """Set Channel Rack solo, or toggle when solo is null."""
        return call(
            "channels.solo",
            {"index": index, "solo": solo},
            shape="text",
            label="Solo channel",
        )

    @mcp.tool()
    def fl_set_channel_name(index: int, name: str) -> str:
        """Change the label of a global Channel Rack entry."""
        return call(
            "channels.setName",
            {"index": index, "name": name},
            shape="text",
            label="Set channel name",
        )

    @mcp.tool()
    def fl_set_channel_color(index: int, red: int, green: int, blue: int) -> str:
        """Set a Channel Rack label color from integer red/green/blue values in 0..255."""
        return call(
            "channels.setColor",
            {"index": index, "r": red, "g": green, "b": blue},
            shape="text",
            label="Set channel color",
        )

    @mcp.tool()
    def fl_route_channel_to_mixer(channel_index: int, mixer_track: int) -> str:
        """Assign a global Channel Rack entry to a zero-based mixer strip."""
        return call(
            "channels.routeToMixer",
            {"channel_index": channel_index, "mixer_track": mixer_track},
            shape="text",
            label="Route channel to mixer",
        )

    @mcp.tool()
    def fl_get_grid_bit(channel: int, position: int) -> bool:
        """Read one zero-based step of the current pattern."""
        return call(
            "channels.getGridBit",
            {"channel": channel, "position": position},
            shape="bool",
            key="value",
        )

    @mcp.tool()
    def fl_set_grid_bit(channel: int, position: int, value: bool) -> str:
        """Enable or disable one zero-based step of the current pattern."""
        return call(
            "channels.setGridBit",
            {"channel": channel, "position": position, "value": value},
            shape="text",
            label="Set grid bit",
        )

    @mcp.tool()
    def fl_get_step_sequence(channel: int, steps: int = 16) -> list[bool]:
        """Read the requested number of current-pattern steps as boolean states."""
        return call(
            "channels.getStepSequence",
            {"channel": channel, "steps": steps},
            shape="list",
            key="sequence",
        )

    @mcp.tool()
    def fl_set_step_sequence(channel: int, pattern: list[bool]) -> str:
        """Write boolean steps to the current pattern and report the affected span."""
        return call(
            "channels.setStepSequence",
            {"channel": channel, "pattern": pattern},
            shape="text",
            label="Set step sequence",
        )
