"""Developer bootstrap uses isolated Git/install stubs, never real credentials or FL."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SHELL = shutil.which("powershell") or shutil.which("pwsh")
CANONICAL = "https://github.com/ALX-CODE/SyncFL-MCP.git"
pytestmark = pytest.mark.skipif(os.name != "nt" or not SHELL, reason="Windows bootstrap")

INSTALL_STUB = r"""
param([switch]$Development, [switch]$SkipFlIntegration, [string]$Client,
      [string]$ClientConfig, [string]$PythonVersion, [switch]$NoBootstrap)
$Recorded = @{}
foreach ($Key in $PSBoundParameters.Keys) {
    $Value = $PSBoundParameters[$Key]
    if ($Value -is [System.Management.Automation.SwitchParameter]) { $Value = [bool]$Value }
    $Recorded[$Key] = $Value
}
$Recorded | ConvertTo-Json |
    Set-Content -LiteralPath (Join-Path $PSScriptRoot 'install-call.json')
if ($env:SYNCFL_TEST_INSTALL_FAIL) { exit 7 }
"""

GIT_STUB = r"""
import json, os, pathlib, sys
args = sys.argv[1:]
state_path = pathlib.Path(os.environ['SYNCFL_TEST_GIT_STATE'])
state = json.loads(state_path.read_text())
with pathlib.Path(os.environ['SYNCFL_TEST_GIT_LOG']).open('a') as stream:
    stream.write(json.dumps(args) + '\n')
if args[:2] == ['clone', '--']:
    destination = pathlib.Path(args[3])
    destination.mkdir(parents=True, exist_ok=True)
    if state.get('clone_fail'):
        (destination / 'partial-clone').write_text('preserve me')
        sys.stderr.write('fixture clone failed\n')
        sys.exit(7)
    sys.stderr.write('fixture clone progress\n')
    (destination / 'install.ps1').write_text(os.environ['SYNCFL_TEST_INSTALL_STUB'])
    state['repos'][str(destination.resolve())] = state.get('clone_origin', args[2])
    state_path.write_text(json.dumps(state))
    sys.exit(0)
if args[:1] != ['-C']:
    sys.exit(99)
directory = pathlib.Path(args[1]).resolve()
for candidate in [directory, *directory.parents]:
    origin = state['repos'].get(str(candidate))
    if origin is not None:
        if args[2:] == ['rev-parse', '--show-toplevel']:
            print(candidate)
            sys.exit(0)
        if args[2:] == ['remote', 'get-url', 'origin'] and origin:
            print(origin)
            sys.exit(0)
        break
sys.exit(1)
"""


@pytest.fixture
def sandbox(tmp_path):
    bundle = tmp_path / "source bundle"
    (bundle / "scripts").mkdir(parents=True)
    shutil.copy2(ROOT / "scripts/setup_dev.ps1", bundle / "scripts/setup_dev.ps1")
    (bundle / "install.ps1").write_text(INSTALL_STUB)
    tools = tmp_path / "tools"
    tools.mkdir()
    stub = tools / "git_stub.py"
    stub.write_text(GIT_STUB)
    (tools / "git.cmd").write_text(f'@"{sys.executable}" "{stub}" %*\r\n')
    state_path = tmp_path / "git-state.json"
    state_path.write_text(json.dumps({"repos": {}}))
    log = tmp_path / "git-calls.jsonl"
    env = dict(os.environ)
    # Never inherit test-control variables from another bootstrap fixture.
    for name in list(env):
        if name.startswith("SYNCFL_TEST_") or name in {
            "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR",
        }:
            del env[name]
    env.update(
        PATH=str(tools) + os.pathsep + env["PATH"],
        USERPROFILE=str(tmp_path / "user profile"),
        SYNCFL_TEST_GIT_STATE=str(state_path), SYNCFL_TEST_GIT_LOG=str(log),
        SYNCFL_TEST_INSTALL_STUB=INSTALL_STUB,
    )

    class Sandbox:
        def __init__(self):
            self.bundle = bundle
            self.env = env

        def state(self, **values):
            state = json.loads(state_path.read_text())
            state.update(values)
            state_path.write_text(json.dumps(state))

        def checkout(self, path=None, origin=CANONICAL):
            path = path or bundle
            path.mkdir(parents=True, exist_ok=True)
            (path / "install.ps1").write_text(INSTALL_STUB)
            state = json.loads(state_path.read_text())
            state["repos"][str(path.resolve())] = origin
            state_path.write_text(json.dumps(state))
            return path

        def run(self, *arguments, cwd=None):
            return subprocess.run(
                [SHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                 str(bundle / "scripts/setup_dev.ps1"), *map(str, arguments)],
                env=env, cwd=cwd, text=True, capture_output=True, timeout=30,
            )

        def calls(self):
            if not log.exists():
                return []
            return [json.loads(line) for line in log.read_text().splitlines()]

    return Sandbox()


def installed_options(path):
    return json.loads((path / "install-call.json").read_text(encoding="utf-8-sig"))


def assert_success(result):
    assert result.returncode == 0, result.stdout + result.stderr


def test_default_reuses_own_canonical_root_for_offline_development(sandbox):
    root = sandbox.checkout()
    marker = root / "user-changes.txt"
    marker.write_text("keep this")
    assert_success(sandbox.run())
    assert installed_options(root) == {
        "Development": True, "SkipFlIntegration": True, "Client": "none", "PythonVersion": "3.13",
    }
    assert marker.read_text() == "keep this"
    assert all(call[0] == "-C" for call in sandbox.calls())


@pytest.mark.parametrize("origin", [
    CANONICAL, "git@github.com:ALX-CODE/SyncFL-MCP.git",
    "ssh://git@github.com/ALX-CODE/SyncFL-MCP", "https://github.com/alx-code/syncfl-mcp",
])
def test_explicit_canonical_checkout_is_reused_without_git_mutations(sandbox, tmp_path, origin):
    target = sandbox.checkout(tmp_path / "existing checkout", origin)
    assert_success(sandbox.run("-Destination", target))
    assert installed_options(target)["Development"] is True
    assert [call[2:] for call in sandbox.calls()] == [
        ["rev-parse", "--show-toplevel"], ["remote", "get-url", "origin"],
    ]


@pytest.mark.parametrize("existing_empty", [False, True])
def test_clone_uses_canonical_origin_and_preserves_normal_git_auth(
    sandbox, tmp_path, existing_empty,
):
    target = tmp_path / "new parent" / "new checkout"
    if existing_empty:
        target.mkdir(parents=True)
    assert_success(sandbox.run("-Destination", target))
    assert sandbox.calls()[0] == ["clone", "--", CANONICAL, str(target)]
    assert installed_options(target)["SkipFlIntegration"] is True


@pytest.mark.parametrize("case", ["wrong-origin", "missing-origin", "nongit", "nested", "file"])
def test_refuses_unsafe_existing_destinations(sandbox, tmp_path, case):
    target = tmp_path / "refused"
    if case == "file":
        target.write_text("keep this file")
    elif case == "nested":
        root = sandbox.checkout(tmp_path / "parent checkout")
        target = root / "nested"
        target.mkdir()
        (target / "user-work.txt").write_text("keep this")
    elif case == "nongit":
        target.mkdir()
        (target / "user-work.txt").write_text("keep this")
    else:
        origin = "https://github.com/someone/other.git" if case == "wrong-origin" else ""
        sandbox.checkout(target, origin)
    result = sandbox.run("-Destination", target)
    assert result.returncode != 0
    assert not (target / "install-call.json").exists()
    assert all(call[0] != "clone" for call in sandbox.calls())
    if case == "file":
        assert target.read_text() == "keep this file"
    elif case in {"nested", "nongit"}:
        assert (target / "user-work.txt").read_text() == "keep this"


def test_plan_reports_clone_without_writes_or_installer_calls(sandbox, tmp_path):
    target = tmp_path / "missing" / "checkout"
    result = sandbox.run("-Destination", target, "-Plan", "-RunTests")
    assert_success(result)
    plan = json.loads(result.stdout)
    assert plan["checkout_action"] == "clone"
    assert plan["repository"] == CANONICAL
    assert plan["changes_git_history"] is False
    assert plan["installer_options"]["SkipFlIntegration"] is True
    assert plan["run_tests"] is True
    assert not target.parent.exists()
    assert sandbox.calls() == []


def test_plan_reuse_never_invokes_installer(sandbox):
    root = sandbox.checkout()
    result = sandbox.run("-Plan")
    assert_success(result)
    assert json.loads(result.stdout)["checkout_action"] == "reuse"
    assert not (root / "install-call.json").exists()


def test_archive_default_plan_chooses_user_source_directory(sandbox):
    result = sandbox.run("-Plan")
    assert_success(result)
    plan = json.loads(result.stdout)
    assert Path(plan["destination"]).parts[-2:] == ("source", "SyncFL-MCP")
    assert Path(plan["destination"]).parent.parent == Path(sandbox.env["USERPROFILE"])
    assert not (sandbox.bundle / "install-call.json").exists()


def test_options_forward_and_relative_paths_remain_caller_relative(sandbox, tmp_path):
    caller = tmp_path / "caller"
    caller.mkdir()
    target = sandbox.checkout(caller / "checkout")
    assert_success(sandbox.run(
        "-Destination", "checkout", "-Client", "claude-code", "-ClientConfig", "client.json",
        "-PythonVersion", "3.14", "-NoBootstrap", "-InstallFlIntegration", cwd=caller,
    ))
    assert installed_options(target) == {
        "Development": True, "Client": "claude-code", "ClientConfig": str(caller / "client.json"),
        "PythonVersion": "3.14", "NoBootstrap": True,
    }
    assert not (caller / "client.json").exists()


def test_client_config_requires_an_explicit_client(sandbox, tmp_path):
    result = sandbox.run("-Destination", tmp_path / "new", "-ClientConfig", "client.json")
    assert result.returncode != 0
    assert sandbox.calls() == []


def test_clone_failure_preserves_partial_checkout_and_does_not_install(sandbox, tmp_path):
    sandbox.state(clone_fail=True)
    target = tmp_path / "failed clone"
    result = sandbox.run("-Destination", target)
    assert result.returncode != 0
    assert "fixture clone failed" in result.stderr
    assert (target / "partial-clone").read_text() == "preserve me"
    assert not (target / "install-call.json").exists()


@pytest.mark.parametrize("name", ["GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR"])
def test_inherited_git_location_overrides_cannot_redirect_checkout_checks(sandbox, tmp_path, name):
    sandbox.env[name] = str(tmp_path / "different repository")
    result = sandbox.run("-Destination", tmp_path / "new checkout", "-Plan")
    assert result.returncode != 0
    assert f"Unset {name}" in result.stderr
    assert sandbox.calls() == []


def test_revalidates_clone_origin_before_running_installer(sandbox, tmp_path):
    sandbox.state(clone_origin="https://github.com/other/repo.git")
    target = tmp_path / "wrong cloned origin"
    result = sandbox.run("-Destination", target)
    assert result.returncode != 0
    assert not (target / "install-call.json").exists()


def test_install_failure_propagates_and_does_not_run_tests(sandbox):
    root = sandbox.checkout()
    sandbox.env["SYNCFL_TEST_INSTALL_FAIL"] = "1"
    result = sandbox.run("-RunTests")
    assert result.returncode != 0
    assert installed_options(root)["Development"] is True
    assert not (root / "tests-run.json").exists()


@pytest.mark.parametrize("failure", [False, True])
def test_optional_test_run_uses_checkout_python_and_propagates_failure(sandbox, failure):
    root = sandbox.checkout()
    python = root / ".venv/Scripts/python.exe"
    python.parent.mkdir(parents=True)
    shutil.copy2(sys.executable, python)
    (root / ".venv/pyvenv.cfg").write_text(
        f"home = {sys.base_prefix}\ninclude-system-site-packages = false\n",
    )
    (root / "pytest.py").write_text(
        "import json,pathlib,sys\n"
        "pathlib.Path('tests-run.json').write_text(json.dumps({'cwd':str(pathlib.Path.cwd()),"
        "'arguments':sys.argv[1:]}))\n"
        f"sys.exit({3 if failure else 0})\n",
    )
    result = sandbox.run("-RunTests")
    assert (result.returncode != 0) is failure
    assert json.loads((root / "tests-run.json").read_text()) == {
        "cwd": str(root), "arguments": ["tests", "agent-harness/cli_anything/flstudio/tests", "-q"],
    }


def test_develop_wrapper_forwards_arguments_and_preserves_exit_code():
    script = (ROOT / "Develop.cmd").read_text()
    assert '"%~dp0scripts\\setup_dev.ps1" %*' in script
    assert 'set "SYNCFL_DEV_EXIT=%ERRORLEVEL%"' in script
    assert "pause" in script
    assert "exit /b %SYNCFL_DEV_EXIT%" in script
