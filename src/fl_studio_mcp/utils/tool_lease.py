"""One live tool at a time across MCP processes, without holding worker thread locks."""

from __future__ import annotations

import asyncio
import os
import time
from contextlib import ExitStack, contextmanager

# Re-exported on purpose: this is the single monkeypatch point for the lease
# location, and tool_lease_middleware resolves it through this module.
from fl_studio_mcp.utils.paths import get_fl_settings_dir  # noqa: F401

WAIT_VARIABLE = "FL_MCP_LEASE_WAIT_SECONDS"
DEFAULT_WAIT_SECONDS = 10.0

# A status ping is one bridge round trip, already serialized by the protocol
# lock. Reporting "busy" here hid the connection state while any job ran.
LEASE_FREE_TOOLS = frozenset({"fl_connection_status"})


class ToolBusy(RuntimeError):
    pass


def lease_wait_seconds():
    """Bounded wait for another process's tool; invalid values fail fast (0)."""
    try:
        value = float(os.environ.get(WAIT_VARIABLE, DEFAULT_WAIT_SECONDS))
    except ValueError:
        return 0.0
    return min(value, 60.0) if value > 0 else 0.0


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


async def acquire_tool_lease(path, wait):
    """Queue behind the active tool for up to `wait` seconds without blocking the loop.

    Nothing is dispatched while waiting, so a timeout is still `not_dispatched`.
    """
    started = time.monotonic()
    while True:
        stack = ExitStack()
        try:
            stack.enter_context(tool_lease(path))
            return stack
        except ToolBusy:
            waited = time.monotonic() - started
            if waited >= wait:
                raise
            await asyncio.sleep(min(wait - waited, 0.002 if waited < 0.25 else 0.01))


def __getattr__(name):
    """Keep FLToolLeaseMiddleware importable from here without paying for FastMCP."""
    if name == "FLToolLeaseMiddleware":
        from fl_studio_mcp.utils.tool_lease_middleware import FLToolLeaseMiddleware

        return FLToolLeaseMiddleware
    raise AttributeError(f"{__name__} has no attribute {name!r}")
