import asyncio
from copy import deepcopy

from fastmcp import Client

from fl_studio_mcp.server import mcp
from fl_studio_mcp.utils import operator_contract as contract


class Connection:
    def __init__(self, directory):
        self._hardware_dir = directory
        self.calls = []
        self.results = {
            "bridge.ping": {"success": True, "safe_to_edit": 1},
            "production.inspect": {
                "success": True,
                "revision": "a" * 64,
                "is_playing": False,
                "is_recording": False,
            },
            "transport.getStatus": {"success": True, "is_playing": False, "is_recording": False},
        }

    def send_command(self, action, params, timeout):
        self.calls.append(action)
        return deepcopy(self.results[action])


def test_live_readiness_never_saves_opens_dialogs_or_mutates(tmp_path):
    conn = Connection(tmp_path)
    result = contract.inspect_session(conn)
    assert result["success"] is True
    assert result["status"] == "ready_for_preflight"
    assert conn.calls == ["bridge.ping", "production.inspect", "transport.getStatus"]
    assert result["saved_path_verified"] is False
    assert result["dependencies"]["windows_helper"]["runtime_verified"] is False


def test_failed_observation_stops_instead_of_inventing_readiness(tmp_path):
    conn = Connection(tmp_path)
    conn.results["production.inspect"] = {"success": False, "error": "controller unavailable"}
    result = contract.inspect_session(conn)
    assert result["success"] is False
    assert result["status"] == "unavailable"
    assert conn.calls == ["bridge.ping", "production.inspect"]


def test_changing_transport_or_missing_state_blocks_guarded_edit_readiness(tmp_path):
    conn = Connection(tmp_path)
    conn.results["transport.getStatus"]["is_recording"] = True
    assert contract.inspect_session(conn)["status"] == "observation_only"
    del conn.results["transport.getStatus"]["is_recording"]
    assert contract.inspect_session(conn)["status"] == "observation_only"


def test_catalog_conservative_read_policy_and_honest_gaps():
    catalog = contract.workflow_catalog()
    assert catalog["universal_production_complete"] is False
    assert not (
        {
            "fl_project_info",
            "fl_get_piano_roll_state",
            "fl_native_call",
            "fl_batch",
            "fl_ui_action",
            "fl_render_start",
            "fl_record",
        }
        & catalog["read_tools"].keys()
    )
    assert {"fl_ui_inspect", "fl_session_inspect"} <= catalog["read_tools"].keys()
    workflows = {item["id"]: item for item in catalog["workflows"]}
    assert workflows["recording_and_comping"]["coverage"] == "partial"
    assert "fl_recording_arm" in workflows["recording_and_comping"]["tools"]
    assert "fl_recording_inspect" in catalog["read_tools"]
    assert "fl_recording_arm" not in catalog["read_tools"]
    assert "fl_render_status" in catalog["read_tools"]
    assert "fl_render_status" not in contract.OFFLINE_TOOLS
    assert workflows["arrangement"]["coverage"] == "partial"
    assert workflows["key_analysis"]["tools"] == ["fl_audio_key"]
    assert "fl_audio_key" in catalog["read_tools"]
    assert "fl_audio_key" in contract.OFFLINE_TOOLS
    assert workflows["tempo_analysis"]["tools"] == ["fl_audio_bpm"]
    assert "fl_audio_bpm" in contract.READ_TOOLS & contract.OFFLINE_TOOLS


def test_dependency_status_supports_the_numpy_only_key_extra(monkeypatch):
    monkeypatch.setattr(contract.importlib.util, "find_spec",
                        lambda name: object() if name == "numpy" else None)
    status = contract.dependency_status()
    assert status["key_analysis"] == {"installed": True, "runtime_verified": False}
    assert status["audio_packages"]["installed"] is False


def test_catalog_reports_background_default_and_specific_input_dependencies(monkeypatch):
    monkeypatch.delenv("FL_MCP_INTERACTION_POLICY", raising=False)
    catalog = contract.workflow_catalog()
    policy = catalog["policy"]["interaction"]
    assert policy["mode"] == "background" and policy["default"] == "background"
    assert policy["desktop_input_allowed"] is False
    assert policy["automatic_interactive_fallback"] is False
    workflows = {item["id"]: item for item in catalog["workflows"]}
    assert workflows["save_and_delivery"]["input_requirements"]["desktop_input_tools"] == [
        "fl_project_save", "fl_project_save_as"]
    assert workflows["mix_balance"]["input_requirements"]["desktop_input_tools"] == []
    assert "fl_render_status" not in workflows["render_and_stems"][
        "input_requirements"]["desktop_input_tools"]
    assert {"fl_plugin_adapter_catalog", "fl_plugin_controls_inspect"} <= contract.READ_TOOLS
    assert "fl_plugin_adapter_catalog" in contract.OFFLINE_TOOLS
    assert "fl_plugin_controls_inspect" not in contract.OFFLINE_TOOLS
    monkeypatch.setenv("FL_MCP_INTERACTION_POLICY", "interactive")
    assert contract.workflow_catalog()["policy"]["interaction"]["desktop_input_allowed"] is True


def test_catalog_distinguishes_experimental_background_routes_and_named_control_writes():
    catalog = contract.workflow_catalog()
    workflows = {item["id"]: item for item in catalog["workflows"]}
    for identifier in ("background_effect_loading", "native_project_save"):
        workflow = workflows[identifier]
        assert workflow["coverage"] == "experimental"
        assert workflow["input_requirements"]["desktop_input_tools"] == []
        assert workflow["input_requirements"]["explicit_interactive_mode_required"] is False
    assert "fl_plugin_controls_set" in workflows["plugin_parameters"]["tools"]
    assert {"fl_background_effect_inspect", "fl_project_native_inspect"} <= contract.READ_TOOLS
    assert not {"fl_background_effect_load", "fl_project_save_native"} & contract.READ_TOOLS
    assert not (
        {"fl_background_effect_inspect", "fl_project_native_inspect"} & contract.OFFLINE_TOOLS
    )
    assert any("already selected" in item for item in workflows[
        "background_effect_loading"]["limitations"])
    assert any("host-state-only" in item for item in workflows[
        "native_project_save"]["limitations"])


def test_catalog_tool_names_match_actual_mcp_discovery():
    async def exercise():
        async with Client(mcp) as client:
            registered = {tool.name for tool in await client.list_tools()}
            catalog = (await client.call_tool("fl_workflow_catalog", {})).data
            referenced = set(catalog["read_tools"]) | set(contract.OFFLINE_TOOLS)
            referenced.update(
                name for workflow in catalog["workflows"] for name in workflow["tools"]
            )
            assert referenced <= registered, sorted(referenced - registered)

    asyncio.run(exercise())
