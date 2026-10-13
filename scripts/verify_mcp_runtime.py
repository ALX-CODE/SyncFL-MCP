"""Verify a fresh stdio server, optionally against the stopped live FL controller."""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import StdioTransport


async def verify(live=False, mode="discovery"):
    async with Client(
        StdioTransport(
            command=sys.executable, args=["-m", "fl_studio_mcp.server"], keep_alive=False,
            env={**os.environ, "FL_MCP_TOOL_MODE": mode},
        )
    ) as client:
        tools = await client.list_tools()
        names = sorted(t.name for t in tools)

        async def call(name, args):
            if mode == "discovery" and name not in names:
                args = {"name": name, "arguments": args}
                name = "fl_call"
            response = await client.call_tool(name, args)
            if response.structured_content is not None:
                return response.structured_content
            return json.loads(next(c.text for c in response.content if c.type == "text"))

        build = await call("fl_build_info", {})
        required = {
            "fl_build_info",
            "fl_project_package",
            "fl_project_file_inspect",
            "fl_automation_file_inspect",
            "fl_automation_file_edit",
            "fl_mixer_scene",
            "fl_workflow_catalog",
            "fl_session_inspect",
            "fl_routing_inspect",
            "fl_routing_scene",
            "fl_audio_loudness",
            "fl_audio_key",
            "fl_audio_bpm",
            "fl_audio_compare",
            "fl_plugin_inspect",
            "fl_plugin_apply",
            "fl_ui_inspect",
            "fl_ui_action",
            "fl_recording_inspect",
            "fl_recording_arm",
            "fl_recording_configure_file",
            "fl_recording_start",
            "fl_recording_status",
            "fl_recording_stop",
            "fl_automation_target_inspect",
            "fl_automation_links_inspect",
            "fl_automation_file_retarget",
            "fl_audio_clip_add",
            "fl_mix_diagnose",
            "fl_mixer_meter_watch",
            "fl_midi_reference_search",
            "fl_notes_transform",
            "fl_plugin_adapter_catalog",
            "fl_plugin_controls_inspect",
            "fl_plugin_controls_set",
            "fl_project_native_inspect",
            "fl_project_save_native",
            "fl_background_effect_inspect",
            "fl_background_effect_load",
        }
        underlying = set(names)
        if mode == "discovery":
            from fl_studio_mcp.discovery import CATALOG, PINNED

            assert set(names) == PINNED | {"fl_discover", "fl_describe", "fl_call", "fl_workflows"}
            underlying = set(CATALOG["tools"])
            ordered = sorted(underlying)
            described = set()
            for offset in range(0, len(ordered), 5):
                result = await call("fl_describe", {"names": ordered[offset:offset + 5]})
                described.update(item["contract"]["name"] for item in result["tools"])
            assert described == underlying
            route = await call("fl_discover", {"query": "lower backing vocals by 2 dB"})
            assert "fl_mixer_scene" in {item["name"] for item in route["matches"]}
        assert required <= underlying, required - underlying
        catalog = await call("fl_workflow_catalog", {})
        referenced = set(catalog["read_tools"])
        referenced.update(name for workflow in catalog["workflows"] for name in workflow["tools"])
        assert referenced <= underlying, referenced - underlying
        report = dict(success=True, build=build, mode=mode, tool_count=len(names),
                      underlying_tool_count=len(underlying), tools=names)
        if live:
            health = await call("fl_connection_status", {})
            report["health"] = health
            report["success"] = bool(health.get("connected"))
            if report["success"]:
                snapshot = await call("fl_project_snapshot", {})
                report["live"] = {k: snapshot[k] for k in ["tempo", "transport", "channels"]}
        return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--mode", choices=("discovery", "full"), default="discovery")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = asyncio.run(verify(args.live, args.mode))
    if args.output:
        args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["success"] else 1)
