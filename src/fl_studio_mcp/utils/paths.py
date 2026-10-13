"""Shared FL Studio user-data path resolution.

FL Studio's "User data folder" (Options > File settings) is not always
Documents/Image-Line — some users relocate it (e.g. to
Documents/MUSIC SOFTWARE FOLDERS/Image-Line). Both the MCP server and the
FL-side scripts must agree on where the JSON handoff files live, so all
path lookups go through this module.

Resolution order:
1. $FL_USER_DATA_FOLDER (explicit override, e.g. set in the MCP server config)
2. Windows heuristic: Documents/MUSIC SOFTWARE FOLDERS/Image-Line if present
3. Platform default: ~/Documents/Image-Line (macOS/Windows) or ~/.fl-studio (Linux)
"""

from __future__ import annotations

import os
import platform
from pathlib import Path


def get_fl_settings_dir() -> Path:
    """Return the FL Studio Settings directory (user data folder)."""
    override = os.environ.get("FL_USER_DATA_FOLDER")
    if override:
        return Path(override) / "FL Studio" / "Settings"

    if platform.system() in ("Darwin", "Windows"):
        default = Path.home() / "Documents" / "Image-Line" / "FL Studio" / "Settings"
    else:
        # Linux fallback (FL Studio doesn't officially support Linux)
        default = Path.home() / ".fl-studio" / "Settings"

    # Windows heuristic: FL Studio's user data folder may be relocated.
    if platform.system() == "Windows":
        relocated = (
            Path.home()
            / "Documents"
            / "MUSIC SOFTWARE FOLDERS"
            / "Image-Line"
            / "FL Studio"
            / "Settings"
        )
        if relocated.exists():
            return relocated

    return default
