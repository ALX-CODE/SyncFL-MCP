"""Caller-visible FL GUI access through the integrated Windows MCP helper."""

import json
from typing import Annotated

from fastmcp.tools.tool import ToolResult
from mcp.types import TextContent
from pydantic import Field, StrictBool

from fl_studio_mcp.utils.studio_ui import UIAction, action_ui, inspect_ui


def register_studio_ui_tools(mcp):
    @mcp.tool()
    async def fl_ui_inspect(
        window_handle: str | None = None,
        include_screenshot: StrictBool = False,
        max_depth: Annotated[int, Field(strict=True, ge=1, le=12)] = 6,
        include_ocr: StrictBool = False,
    ) -> ToolResult:
        """Observe FL-owned windows and UIA controls; optional image for your native vision.

        Read-only, including during playback. Defaults to the unique FL main HWND;
        choose a child/plugin/dialog HWND from the returned windows. The 60-second
        revision is specific to that HWND and is consumed by one apply. Screenshot
        is mandatory before coordinate fallback actions. Those actions recapture and
        require an identical image; animated windows may refuse. include_ocr uses
        local Windows OCR on the same capture and returns bounded text/word boxes
        in window-relative pixels. OCR alone returns no image and does not enable
        coordinate actions or prove a semantic target. No model is called.
        """
        report, images = await inspect_ui(window_handle, include_screenshot, max_depth,
                                          include_ocr=include_ocr)
        return ToolResult(content=[TextContent(type="text", text=json.dumps(report)), *images],
                          structured_content=report)

    @mcp.tool()
    async def fl_ui_action(
        window_handle: str,
        expected_revision: str,
        action: UIAction,
        verification_plan: str,
        apply: StrictBool = False,
        receipt_file: str | None = None,
    ) -> dict:
        """Preflight or dispatch ONE guarded FL GUI action for a gap in semantic tools.

        Prefer native FL tools. Select exactly one enabled actionable UI element; for
        owner-drawn controls inspect with screenshot and use physical pixels relative
        to the target window's top-left. Actions: key, type, click, drag, scroll.
        Key/type without selector require the target to own keyboard focus. All
        applies need stopped transport, fresh observation and a NEW absolute .json
        receipt_file. No global shortcuts or arbitrary helper calls are exposed.

        A dispatch always remains unverified, even when the helper succeeds. Supply
        an independent parameter/project/audio verification_plan, then carry it out.
        success=true with success_scope=input_delivery confirms delivered input only;
        verified and musical_state_verified stay false until independent checks.
        Inspect after errors; never replay. UIA and screenshots cannot prove music.
        """
        return await action_ui(window_handle, expected_revision, action, verification_plan,
                               apply, receipt_file)
