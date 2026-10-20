"""FastMCP middleware for the cross-process FL tool lease.

Split out of ``tool_lease`` so the lock primitive itself does not import FastMCP.
FastMCP plus pydantic is about two seconds of import cost, and the subprocess
lease test — and anything else that only needs the lock — should not pay it.
"""

from __future__ import annotations

import asyncio
import json

from fastmcp.server.middleware import Middleware
from fastmcp.tools.tool import ToolResult

from fl_studio_mcp.utils import tool_lease as _lease
from fl_studio_mcp.utils.operator_contract import OFFLINE_TOOLS
from fl_studio_mcp.utils.tool_lease import (
    LEASE_FREE_TOOLS,
    ToolBusy,
    acquire_tool_lease,
    lease_wait_seconds,
)


class FLToolLeaseMiddleware(Middleware):
    def __init__(self, wait_seconds=None):
        self.wait_seconds = wait_seconds

    async def on_call_tool(self, context, call_next):
        if context.message.name in OFFLINE_TOOLS or context.message.name in LEASE_FREE_TOOLS:
            return await call_next(context)
        # Resolved through the module so one monkeypatch of
        # tool_lease.get_fl_settings_dir still redirects the lease location.
        path = _lease.get_fl_settings_dir() / "Hardware" / "FLStudioMCP" / "mcp-tool.lock"
        wait = lease_wait_seconds() if self.wait_seconds is None else self.wait_seconds
        try:
            lease = await acquire_tool_lease(path, wait)
        except ToolBusy as exc:
            report = dict(
                success=False,
                status="busy",
                outcome="not_dispatched",
                retry_safe=True,
                waited_seconds=round(wait, 3),
                error=str(exc),
                recovery="Wait for the active tool/job, then inspect again",
            )
            return ToolResult(content=json.dumps(report), structured_content=report, is_error=True)
        with lease:
            # A caller timeout must not release the DAW while an async
            # workflow or synchronous worker is still applying its write.
            task = asyncio.create_task(call_next(context))
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                while not task.done():
                    try:
                        await asyncio.shield(task)
                    except asyncio.CancelledError:
                        continue
                    except Exception:
                        break
                if task.done() and not task.cancelled():
                    task.exception()
                raise
