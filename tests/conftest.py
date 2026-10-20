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


# ---------------------------------------------------------------------------
# Fast by default: process-spawning integration coverage is opt-in
# ---------------------------------------------------------------------------
# These modules start the whole server, run installer/setup flows, or hash the
# entire release. Their wall time is dominated by interpreter startup, so they are
# the last thing you want while editing one function. The default `pytest` run
# excludes them; CI and release checks run everything with `-m ""`.
SPAWNING_MODULES = frozenset({
    "test_dev_setup.py",
    "test_windows_setup.py",
    "test_public_release.py",
    "test_audio_stdio.py",
    "test_packaged_native_runtimes.py",
    "test_packaged_plugin_adapters.py",
    "test_install_ps1.py",
    "test_verify_runtime.py",
    "test_mix_analysis.py",
    "test_install_fl_integration.py",
})

# Individual tests elsewhere that still shell out or build a full client.
SPAWNING_TESTS = frozenset({
    "test_tempo_analysis.py::test_mcp_tempo_tool_is_offline_and_returns_rust_result",
    "test_discovery.py::test_exact_contracts_discovery_bounds_and_full_catalog_remain_available",
    "test_screen_ocr.py::test_native_windows_ocr_reads_generated_fixture_without_screen_capture",
})


def pytest_collection_modifyitems(config, items):
    import pathlib

    for item in items:
        path = pathlib.Path(str(getattr(item, "path", item.fspath))).name
        test_id = f"{path}::{item.name}"
        if path in SPAWNING_MODULES or test_id in SPAWNING_TESTS:
            item.add_marker(pytest.mark.integration)
