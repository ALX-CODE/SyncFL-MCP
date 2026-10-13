import asyncio
import sys
from types import ModuleType

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from fl_studio_mcp.server import mcp


def test_asset_tool_is_discoverable_and_calls_catalog(tmp_path):
    sample = tmp_path / "Kick.wav"
    sample.write_bytes(b"fixture")

    async def exercise():
        async with Client(mcp) as client:
            tools = {tool.name: tool for tool in await client.list_tools()}
            schema = tools["fl_find_assets"].inputSchema
            assert schema["required"] == ["query"]
            assert "sample" in schema["properties"]["kind"]["enum"]
            response = await client.call_tool("fl_find_assets", {
                "query": "kICK", "kind": "sample", "roots": [str(tmp_path)], "limit": 1,
            })
            result = response.structured_content
            assert result["scan_complete"]
            assert result["assets"] == [{
                "name": "Kick", "path": str(sample.resolve()), "kind": "sample",
            }]

    asyncio.run(exercise())


def install_workflow_stub(monkeypatch):
    module = ModuleType("fl_studio_mcp.utils.studio_loading")
    calls = []

    async def run_file_workflow(operation, **kwargs):
        calls.append((operation, kwargs))
        return {"success": True, "operation": operation}

    module.run_file_workflow = run_file_workflow
    monkeypatch.setitem(sys.modules, module.__name__, module)
    return calls


def test_file_workflows_are_discoverable_and_forward_complete_arguments(monkeypatch):
    calls = install_workflow_stub(monkeypatch)
    scenarios = [
        ("fl_sample_add", "add_sample", {
            "path": "C:/Samples/Kick.wav", "name": "Kick", "mixer_track": 4,
        }),
        ("fl_instrument_add", "add_instrument", {
            "name": "Serum 2", "path": None, "channel_name": "Lead", "mixer_track": 7,
        }),
        ("fl_effect_add", "add_effect", {
            "track": 7, "slot": 9, "name": "Pro-Q 4", "path": None,
        }),
        ("fl_preset_load", "load_preset", {
            "path": "C:/Presets/Lead.fst", "target": "channel", "channel": 4,
            "track": None, "slot": None, "expected_name": "Lead",
        }),
    ]

    async def exercise():
        async with Client(mcp) as client:
            tools = {tool.name: tool for tool in await client.list_tools()}
            slot = tools["fl_effect_add"].inputSchema["properties"]["slot"]
            assert slot["minimum"] == 0 and slot["maximum"] == 9
            target = tools["fl_preset_load"].inputSchema["properties"]["target"]
            assert target["default"] == "new_channel"
            for tool, operation, arguments in scenarios:
                result = await client.call_tool(tool, arguments)
                assert result.structured_content == {"success": True, "operation": operation}
                assert calls[-1] == (operation, arguments)

    asyncio.run(exercise())


@pytest.mark.parametrize("slot", [-1, 10, True])
def test_effect_slot_validation_rejects_out_of_range_before_backend(monkeypatch, slot):
    calls = install_workflow_stub(monkeypatch)

    async def exercise():
        async with Client(mcp) as client:
            with pytest.raises(ToolError):
                await client.call_tool("fl_effect_add", {"track": 1, "slot": slot, "name": "EQ"})

    asyncio.run(exercise())
    assert not calls


def test_prepare_piano_is_discoverable_and_calls_sync_backend(monkeypatch):
    module = ModuleType("fl_studio_mcp.utils.piano_ready")
    calls = []

    def prepare_piano(**kwargs):
        calls.append(kwargs)
        return {"success": True, "ready": True}

    module.prepare_piano = prepare_piano
    monkeypatch.setitem(sys.modules, module.__name__, module)

    async def exercise():
        async with Client(mcp) as client:
            result = await client.call_tool("fl_piano_roll_prepare", {
                "channel": 3, "expected_name": "Lead",
            })
            assert result.structured_content == {"success": True, "ready": True}

    asyncio.run(exercise())
    assert calls == [{"channel": 3, "expected_name": "Lead"}]
