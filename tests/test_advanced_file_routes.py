"""Compatibility tools must dispatch the verified loaders exposed by the MCP."""

import asyncio
from types import SimpleNamespace

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from fl_studio_mcp.server import mcp
from fl_studio_mcp.tools import piano_roll
from fl_studio_mcp.utils import studio_loading


def test_legacy_plugin_add_and_chain_use_file_workflow(monkeypatch):
    calls = []

    async def run_file_workflow(operation, **kwargs):
        calls.append((operation, kwargs))
        return {"success": True, "operation": operation}

    monkeypatch.setattr(studio_loading, "run_file_workflow", run_file_workflow)
    items = [{"track": 7, "slot": 0, "name": "Fruity Reeverb 2", "wet": 0.25}]

    async def exercise():
        async with Client(mcp) as client:
            await client.call_tool("fl_plugin_add", {
                "track": 7, "slot": 0, "name": "Fruity Reeverb 2",
            })
            await client.call_tool("fl_plugin_chain", {
                "items": items, "receipt_file": "C:/Receipts/chain.json",
            })

    asyncio.run(exercise())
    assert calls == [
        ("add_effect", {"track": 7, "slot": 0, "name": "Fruity Reeverb 2"}),
        ("load_chain", {"items": items, "receipt_file": "C:/Receipts/chain.json"}),
    ]


def test_legacy_picker_row_rejected_without_loading(monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail("An obsolete picker row must not dispatch loading")

    monkeypatch.setattr(studio_loading, "run_file_workflow", forbidden)

    async def exercise():
        async with Client(mcp) as client:
            with pytest.raises(ToolError, match="exact .fst path"):
                await client.call_tool("fl_plugin_add", {
                    "track": 7, "slot": 0, "name": "EQ", "result_row": 1,
                })

    asyncio.run(exercise())


def test_windows_piano_info_advertises_actual_script_activation(tmp_path, monkeypatch):
    trigger = SimpleNamespace(platform="Windows", is_supported=True, keystroke="Ctrl+Alt+Y")
    monkeypatch.setattr(piano_roll, "get_trigger", lambda: trigger)
    monkeypatch.setattr(piano_roll, "get_fl_settings_dir", lambda: tmp_path)

    async def exercise():
        async with Client(mcp) as client:
            result = await client.call_tool("fl_get_piano_roll_info", {})
            assert result.structured_content["activation"] == "explicit_compose_menu"
            assert result.structured_content["trigger_keystroke"] is None

    asyncio.run(exercise())
