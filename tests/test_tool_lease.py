import asyncio
import subprocess
import sys
from types import SimpleNamespace

import pytest

from fl_studio_mcp.utils import tool_lease as leases
from fl_studio_mcp.utils.protocol import transaction_lock


def test_other_process_cannot_enter_active_tool_and_can_after_release(tmp_path):
    path = tmp_path / "tool.lock"
    code = (
        "import sys; from pathlib import Path; "
        "from fl_studio_mcp.utils.tool_lease import tool_lease; "
        "lease=tool_lease(Path(sys.argv[1])); lease.__enter__(); lease.__exit__(None,None,None)"
    )
    with leases.tool_lease(path):
        result = subprocess.run(
            [sys.executable, "-c", code, str(path)], capture_output=True, text=True, timeout=15
        )
        assert result.returncode != 0
        assert "ToolBusy" in result.stderr
    result = subprocess.run(
        [sys.executable, "-c", code, str(path)], capture_output=True, text=True, timeout=15
    )
    assert result.returncode == 0, result.stderr


def test_exception_releases_tool_lease(tmp_path):
    path = tmp_path / "tool.lock"
    with pytest.raises(ValueError), leases.tool_lease(path):
        raise ValueError("tool failed")
    with leases.tool_lease(path):
        pass


def test_live_tool_busy_never_dispatches_offline_analysis_can_continue(tmp_path, monkeypatch):
    monkeypatch.setattr(leases, "get_fl_settings_dir", lambda: tmp_path)
    middleware = leases.FLToolLeaseMiddleware()
    dispatched = []

    async def call_next(context):
        dispatched.append(context.message.name)
        return "finished"

    async def exercise():
        context = SimpleNamespace(message=SimpleNamespace(name="fl_record"))
        path = tmp_path / "Hardware" / "FLStudioMCP" / "mcp-tool.lock"
        with leases.tool_lease(path):
            result = await middleware.on_call_tool(context, call_next)
            assert result.is_error
            assert result.structured_content["outcome"] == "not_dispatched"
            assert not dispatched
            context.message.name = "fl_audio_compare"
            assert await middleware.on_call_tool(context, call_next) == "finished"
        context.message.name = "fl_record"
        assert await middleware.on_call_tool(context, call_next) == "finished"

    asyncio.run(exercise())
    assert dispatched == ["fl_audio_compare", "fl_record"]


def test_sync_worker_protocol_locks_do_not_deadlock_under_tool_lease(tmp_path, monkeypatch):
    monkeypatch.setattr(leases, "get_fl_settings_dir", lambda: tmp_path)

    def native_work():
        with transaction_lock(tmp_path / "native.lock", 0.5):
            return "native result"

    async def call_next(context):
        return await asyncio.to_thread(native_work)

    async def exercise():
        context = SimpleNamespace(message=SimpleNamespace(name="fl_project_snapshot"))
        return await leases.FLToolLeaseMiddleware().on_call_tool(context, call_next)

    assert asyncio.run(asyncio.wait_for(exercise(), 3)) == "native result"


def test_cancellation_releases_lease(tmp_path, monkeypatch):
    monkeypatch.setattr(leases, "get_fl_settings_dir", lambda: tmp_path)

    async def cancelled(context):
        raise asyncio.CancelledError

    async def exercise():
        context = SimpleNamespace(message=SimpleNamespace(name="fl_ui_inspect"))
        with pytest.raises(asyncio.CancelledError):
            await leases.FLToolLeaseMiddleware().on_call_tool(context, cancelled)

    asyncio.run(exercise())
    with leases.tool_lease(tmp_path / "Hardware" / "FLStudioMCP" / "mcp-tool.lock"):
        pass


def test_client_cancellation_holds_lease_until_real_work_finishes(tmp_path, monkeypatch):
    monkeypatch.setattr(leases, "get_fl_settings_dir", lambda: tmp_path)

    async def exercise():
        entered, finish = asyncio.Event(), asyncio.Event()

        async def work(context):
            entered.set()
            await finish.wait()
            return "write finished"

        context = SimpleNamespace(message=SimpleNamespace(name="fl_mixer_scene"))
        call = asyncio.create_task(leases.FLToolLeaseMiddleware().on_call_tool(context, work))
        await entered.wait()
        call.cancel()
        await asyncio.sleep(0)
        path = tmp_path / "Hardware" / "FLStudioMCP" / "mcp-tool.lock"
        with pytest.raises(leases.ToolBusy), leases.tool_lease(path):
            pytest.fail("A cancelled client cannot release an unfinished DAW write")
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await call
        with leases.tool_lease(path):
            pass

    asyncio.run(exercise())
