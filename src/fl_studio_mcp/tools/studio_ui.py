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
        """Unavailable in backend-only SyncFL. Desktop input is unavailable. Use native backend
        operations; fl_ui_inspect provides observation only.
        """
        return await action_ui(window_handle, expected_revision, action, verification_plan,
                               apply, receipt_file)
