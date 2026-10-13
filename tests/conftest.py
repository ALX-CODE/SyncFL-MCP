"""Keep unit-test MCP clients independent of the real desktop tool lease."""

import pytest

from fl_studio_mcp.utils import tool_lease


@pytest.fixture(autouse=True)
def isolated_mcp_tool_lease(tmp_path, monkeypatch):
    # Mocked tool routes still traverse the real middleware. Their locks belong
    # in the test workspace, so an active FL job cannot make unit tests fail.
    monkeypatch.setattr(tool_lease, "get_fl_settings_dir", lambda: tmp_path)


@pytest.fixture(autouse=True)
def default_native_access(monkeypatch):
    # A developer shell or MCP registration may export FL_MCP_ACCESS=full.
    monkeypatch.delenv("FL_MCP_ACCESS", raising=False)
