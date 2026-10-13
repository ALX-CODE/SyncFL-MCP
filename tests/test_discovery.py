"""Retrieval quality and proxy boundaries, using no real DAW mutations."""

import asyncio

import pytest
from fastmcp import Client, FastMCP
from fastmcp.tools import ToolResult
from mcp.types import TextContent

from fl_studio_mcp.discovery import (
    CATALOG,
    PINNED,
    ROUTES,
    create_discovery_server,
    discover,
)
from fl_studio_mcp.server import mcp
from fl_studio_mcp.utils import tool_lease as leases
from fl_studio_mcp.utils.operator_contract import WORKFLOWS

ENTRY_TOOLS = PINNED | {"fl_discover", "fl_describe", "fl_call", "fl_workflows"}


def test_catalog_covers_every_registered_tool_and_workflow():
    async def run():
        tools = await mcp.list_tools()
        assert {tool.name for tool in tools} == set(CATALOG["tools"])
        assert set(ROUTES) == {workflow["id"] for workflow in WORKFLOWS}
        for _, names in ROUTES.values():
            assert set(names.split()) <= set(CATALOG["tools"])
        for name in CATALOG["tools"]:
            result = discover(tools, name, None, 5)
            assert result["matches"][0]["name"] == name
        assert all(item["domain"] in CATALOG["domains"] for item in CATALOG["tools"].values())

    asyncio.run(run())


# First twelve retain the audit's baseline. Others cover different phrasing and
# domain conflicts; these are deterministic routing checks, not an LLM benchmark.
@pytest.mark.parametrize(("query", "expected"), [
    ("lower the backing vocals by 2 dB", {"fl_mixer_scene"}),
    ("mixer fader volume dB", {"fl_mixer_scene"}),
    ("set vocal compressor attack in milliseconds",
     {"fl_plugin_controls_inspect", "fl_plugin_controls_set"}),
    ("plugin named calibrated physical controls",
     {"fl_plugin_controls_inspect", "fl_plugin_controls_set"}),
    ("write melody notes piano roll", {"fl_background_piano_write", "fl_send_notes"}),
    ("sidechain kick bass routing", {"fl_routing_inspect", "fl_routing_scene"}),
    ("export song wav stems", {"fl_render_start", "fl_render_status"}),
    ("find the key and BPM of this sample", {"fl_audio_key", "fl_audio_bpm"}),
    ("save project safely", {"fl_project_save", "fl_project_save_native"}),
    ("record microphone vocal take", {"fl_recording_start", "fl_recording_inspect"}),
    ("undo uncertain timed out plugin change", {"fl_receipt"}),
    ("fl_background_piano_write", {"fl_background_piano_write"}),
    ("make the lead vocal quieter", {"fl_mixer_scene"}),
    ("raise compressor threshold", {"fl_plugin_controls_set"}),
    ("route kick into bass for ducking", {"fl_routing_inspect", "fl_routing_scene"}),
    ("bounce the mixdown", {"fl_render_start", "fl_render_status"}),
    ("capture input from mic", {"fl_recording_start", "fl_recording_status"}),
    ("synth wavetable unison", {"fl_serum_inspect", "fl_serum_apply"}),
    ("transpose a MIDI reference melody", {"fl_notes_transform"}),
    ("trim and reverse a sample", {"fl_audio_inspect", "fl_audio_edit"}),
    ("change built in EQ polarity", {"fl_mixer_controls_inspect", "fl_mixer_controls_set"}),
    ("measure LUFS and true peak", {"fl_audio_loudness"}),
    ("automation curve target link", {"fl_automation_target_inspect"}),
    ("native save with clean state", {"fl_project_native_inspect", "fl_project_save_native"}),
])
def test_task_retrieval_retains_inspection_and_completion_tools(query, expected):
    async def run():
        result = discover(await mcp.list_tools(), query, None, 5)
        assert expected <= {item["name"] for item in result["matches"]}
        assert len(result["matches"]) <= 5

    asyncio.run(run())


def test_exact_contracts_discovery_bounds_and_full_catalog_remain_available():
    async def run():
        async with Client(mcp) as raw, Client(create_discovery_server(mcp)) as client:
            contracts = {tool.name: tool for tool in await raw.list_tools()}
            entries = await client.list_tools()
            assert {tool.name for tool in entries} == ENTRY_TOOLS
            call = next(tool for tool in entries if tool.name == "fl_call")
            assert call.annotations.readOnlyHint is False
            for name, original in contracts.items():
                described = (await client.call_tool("fl_describe", {"names": [name]})).data
                assert described["tools"][0]["contract"] == original.model_dump(
                    mode="json", exclude_none=True)
            result = await client.call_tool("fl_discover", {
                "query": "volume", "domain": "mixer", "limit": 2,
            })
            assert len(result.data["matches"]) == 2
            assert all(item["domain"] == "mixer" for item in result.data["matches"])
            workflows = (await client.call_tool("fl_workflows", {})).data
            assert len(workflows["workflows"]) == len(WORKFLOWS)
            route = (await client.call_tool("fl_workflows", {
                "workflow": "render_and_stems",
            })).data
            assert route["workflow"] == next(w for w in WORKFLOWS
                                              if w["id"] == "render_and_stems")
            for name, arguments in (
                ("fl_discover", {"query": "x", "limit": 11}),
                ("fl_discover", {"query": "x", "domain": "made-up"}),
                ("fl_describe", {"names": []}),
                ("fl_describe", {"names": ["fl_no_such_tool"]}),
                ("fl_describe", {"names": ["fl_play"] * 6}),
                ("fl_call", {"name": "fl_call"}),
                ("fl_call", {"name": "fl_discover"}),
                ("fl_call", {"name": "fl_no_such_tool"}),
            ):
                result = await client.call_tool(name, arguments, raise_on_error=False)
                assert result.is_error
            # Known names still work directly for existing clients/scripts.
            assert (await client.call_tool("fl_build_info")).data == (
                await client.call_tool("fl_call", {"name": "fl_build_info"})).data
            assert {r.uri for r in await client.list_resources()} == {
                r.uri for r in await raw.list_resources()}

    asyncio.run(run())


def test_backend_lease_held_once_and_errors_preserved(tmp_path):
    backend = FastMCP("guarded canary")
    dispatched = []
    evidence = {"status": "dispatched_unverified", "receipt": "receipt-123", "success": False}

    @backend.tool
    def canary(value: int) -> ToolResult:
        dispatched.append(value)
        return ToolResult(content=[TextContent(type="text", text="exact original error")],
                          structured_content=evidence, meta={"trace": "opaque"}, is_error=True)

    backend.add_middleware(leases.FLToolLeaseMiddleware())

    async def run():
        async with Client(create_discovery_server(backend)) as client:
            path = tmp_path / "Hardware/FLStudioMCP/mcp-tool.lock"
            with leases.tool_lease(path):
                assert not (await client.call_tool("fl_discover", {"query": "canary"})).is_error
                assert not (await client.call_tool("fl_describe", {"names": ["canary"]})).is_error
                result = await client.call_tool("fl_call", {
                    "name": "canary", "arguments": {"value": 1},
                }, raise_on_error=False)
                assert result.is_error
                assert result.structured_content["outcome"] == "not_dispatched"
                assert dispatched == []
            invalid = await client.call_tool("fl_call", {
                "name": "canary", "arguments": {"value": "not an integer"},
            }, raise_on_error=False)
            assert invalid.is_error and dispatched == []
            result = await client.call_tool("fl_call", {
                "name": "canary", "arguments": {"value": 7},
            }, raise_on_error=False)
            assert result.is_error
            assert result.structured_content == evidence
            assert result.content == [TextContent(type="text", text="exact original error")]
            assert result.meta == {"trace": "opaque"}
            assert dispatched == [7]  # No hidden retries, including backend error outcomes.
            with leases.tool_lease(path):
                pass

    asyncio.run(run())


def test_discovery_does_not_bypass_backend_visibility():
    backend = FastMCP("filtered backend")
    dispatched = []

    @backend.tool
    def unavailable_canary() -> str:
        dispatched.append(True)
        return "must not execute"

    backend.disable(names={"unavailable_canary"})

    async def run():
        async with Client(create_discovery_server(backend)) as client:
            result = await client.call_tool("fl_discover", {"query": "unavailable_canary"})
            assert result.data["matches"] == []
            for tool, arguments in (
                ("fl_describe", {"names": ["unavailable_canary"]}),
                ("fl_call", {"name": "unavailable_canary"}),
                ("unavailable_canary", {}),
            ):
                assert (await client.call_tool(tool, arguments, raise_on_error=False)).is_error
            assert dispatched == []

    asyncio.run(run())


def test_cancelled_proxy_call_keeps_backend_lease_until_work_finishes(tmp_path):
    async def run():
        entered, finish, completed = asyncio.Event(), asyncio.Event(), asyncio.Event()
        backend = FastMCP("cancellation canary")

        @backend.tool
        async def slow_canary() -> str:
            entered.set()
            await finish.wait()
            completed.set()
            return "finished once"

        backend.add_middleware(leases.FLToolLeaseMiddleware())
        async with Client(create_discovery_server(backend)) as client:
            call = asyncio.create_task(client.call_tool("fl_call", {"name": "slow_canary"}))
            await asyncio.wait_for(entered.wait(), 5)
            call.cancel()
            with pytest.raises(asyncio.CancelledError):
                await call
            await asyncio.sleep(0.05)
            with pytest.raises(leases.ToolBusy), leases.tool_lease(
                tmp_path / "Hardware/FLStudioMCP/mcp-tool.lock"
            ):
                pytest.fail("An unfinished backend operation released its lease")
            finish.set()
            await asyncio.wait_for(completed.wait(), 5)

    asyncio.run(asyncio.wait_for(run(), 15))
