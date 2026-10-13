"""SyncFL plugins tools: typed inputs, validated dispatch and native readback."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fl_studio_mcp.utils.tool_results import call

if TYPE_CHECKING:
    from fastmcp import FastMCP


def register_plugin_tools(mcp: FastMCP) -> None:

    @mcp.tool()
    def fl_is_plugin_valid(index: int, slot_index: int = -1, use_global_index: bool = True) -> bool:
        """Test an existing plugin location. Slot -1 addresses a generator; 0..9 addresses an
        effect.

        Plugin locations use zero-based indices; use_global_index selects global Channel Rack
        indexing.
        """
        return call(
            "plugins.isValid",
            {"index": index, "slot_index": slot_index, "use_global": use_global_index},
            shape="bool",
            key="valid",
        )

    @mcp.tool()
    def fl_get_plugin_name(index: int, slot_index: int = -1, use_global_index: bool = True) -> str:
        """Read the existing generator/effect name at the supplied location.

        Plugin locations use zero-based indices; use_global_index selects global Channel Rack
        indexing.
        """
        return call(
            "plugins.getName",
            {"index": index, "slot_index": slot_index, "use_global": use_global_index},
            shape="string",
            key="name",
        )

    @mcp.tool()
    def fl_get_plugin_param_count(
        index: int, slot_index: int = -1, use_global_index: bool = True
    ) -> int:
        """Count exposed controls of an existing plugin; -1 indicates failure.

        Plugin locations use zero-based indices; use_global_index selects global Channel Rack
        indexing.
        """
        return call(
            "plugins.getParamCount",
            {"index": index, "slot_index": slot_index, "use_global": use_global_index},
            shape="int",
            key="count",
        )

    @mcp.tool()
    def fl_get_plugin_params(
        index: int, slot_index: int = -1, use_global_index: bool = True, max_params: int = 50
    ) -> list[dict]:
        """Read a bounded set of exposed controls, normalized values and displayed text.

        Plugin locations use zero-based indices; use_global_index selects global Channel Rack
        indexing.
        """
        return call(
            "plugins.getParams",
            {
                "index": index,
                "slot_index": slot_index,
                "use_global": use_global_index,
                "max_params": max_params,
            },
            shape="list",
            key="params",
        )

    @mcp.tool()
    def fl_get_plugin_param_value(
        param_index: int, plugin_index: int, slot_index: int = -1, use_global_index: bool = True
    ) -> dict:
        """Read one exposed plugin control, including its displayed value.

        Plugin locations use zero-based indices; use_global_index selects global Channel Rack
        indexing.
        """
        return call(
            "plugins.getParamValue",
            {
                "param_index": param_index,
                "plugin_index": plugin_index,
                "slot_index": slot_index,
                "use_global": use_global_index,
            },
        )

    @mcp.tool()
    def fl_set_plugin_param_value(
        param_index: int,
        value: float,
        plugin_index: int,
        slot_index: int = -1,
        use_global_index: bool = True,
    ) -> str:
        """Set one exposed control in 0..1 and read it back. Prefer calibrated named controls for
        physical units.

        Plugin locations use zero-based indices; use_global_index selects global Channel Rack
        indexing.
        """
        return call(
            "plugins.setParamValue",
            {
                "param_index": param_index,
                "value": value,
                "plugin_index": plugin_index,
                "slot_index": slot_index,
                "use_global": use_global_index,
            },
            shape="text",
            label="Set plugin param value",
        )

    @mcp.tool()
    def fl_get_preset_count(index: int, slot_index: int = -1, use_global_index: bool = True) -> int:
        """Read the preset count exposed by an existing generator or effect.

        Plugin locations use zero-based indices; use_global_index selects global Channel Rack
        indexing.
        """
        return call(
            "plugins.getPresetCount",
            {"index": index, "slot_index": slot_index, "use_global": use_global_index},
            shape="int",
            key="count",
        )

    @mcp.tool()
    def fl_next_preset(index: int, slot_index: int = -1, use_global_index: bool = True) -> str:
        """Advance the existing plugin preset once and acknowledge its identity.

        Plugin locations use zero-based indices; use_global_index selects global Channel Rack
        indexing.
        """
        return call(
            "plugins.nextPreset",
            {"index": index, "slot_index": slot_index, "use_global": use_global_index},
            shape="text",
            label="Next preset",
        )

    @mcp.tool()
    def fl_prev_preset(index: int, slot_index: int = -1, use_global_index: bool = True) -> str:
        """Move the existing plugin preset back once and acknowledge its identity.

        Plugin locations use zero-based indices; use_global_index selects global Channel Rack
        indexing.
        """
        return call(
            "plugins.prevPreset",
            {"index": index, "slot_index": slot_index, "use_global": use_global_index},
            shape="text",
            label="Prev preset",
        )

    @mcp.tool()
    def fl_get_plugin_color(index: int, slot_index: int = -1, use_global_index: bool = True) -> str:
        """Read the host color assigned to an existing generator or effect.

        Plugin locations use zero-based indices; use_global_index selects global Channel Rack
        indexing.
        """
        return call(
            "plugins.getColor",
            {"index": index, "slot_index": slot_index, "use_global": use_global_index},
            shape="string",
            key="color",
        )
