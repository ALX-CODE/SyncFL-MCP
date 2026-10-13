"""One live tool at a time across MCP processes, without holding worker thread locks."""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import contextmanager

from fastmcp.server.middleware import Middleware
from fastmcp.tools.tool import ToolResult

from fl_studio_mcp.utils.operator_contract import OFFLINE_TOOLS
from fl_studio_mcp.utils.paths import get_fl_settings_dir


class ToolBusy(RuntimeError):
    pass


@contextmanager
def tool_lease(path):
    # OS-owned byte lock is released on process exit. No Python RLock is held:
    # FastMCP invokes synchronous tools in worker threads, which acquire their
    # own protocol locks. Sharing that RLock here would deadlock those workers.
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ToolBusy("Another FL MCP tool is active") from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


class FLToolLeaseMiddleware(Middleware):
    async def on_call_tool(self, context, call_next):
        if context.message.name in OFFLINE_TOOLS:
            return await call_next(context)
        path = get_fl_settings_dir() / "Hardware" / "FLStudioMCP" / "mcp-tool.lock"
        try:
            with tool_lease(path):
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
        except ToolBusy as exc:
            report = dict(
                success=False,
                status="busy",
                outcome="not_dispatched",
                retry_safe=True,
                error=str(exc),
                recovery="Wait for the active tool/job, then inspect again",
            )
            return ToolResult(content=json.dumps(report), structured_content=report, is_error=True)
