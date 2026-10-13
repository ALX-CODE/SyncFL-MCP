"""Exercise Windows setup against isolated command stubs, never the user's FL."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SHELL = shutil.which("powershell") or shutil.which("pwsh")
pytestmark = pytest.mark.skipif(os.name != "nt" or not SHELL, reason="Windows setup")


def run(script, *arguments, env=None, cwd=None):
    return subprocess.run(
        [SHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), *arguments],
        capture_output=True, text=True, timeout=40, env=env, cwd=cwd,
    )


def test_plan_has_no_side_effects_and_excludes_development_tools(tmp_path):
    client = tmp_path / "missing config.toml"
    result = run(ROOT / "install.ps1", "-Plan", "-Client", "codex", "-ClientConfig", str(client))
    assert result.returncode == 0, result.stdout + result.stderr
    plan = json.loads(result.stdout)
    assert plan["python"] == "3.13"
    assert plan["dependencies"] == ["audio", "serum"]
    assert plan["development_tools"] is False
    assert plan["writes_client_config"] is True
    assert plan["creates_midi_endpoints"] is False
    assert not client.exists()


def test_legacy_entry_delegates_options_without_replacing_an_unspecified_port():
    result = run(ROOT / "scripts/setup_laptop.ps1", "-Plan", "-SkipCodex", "-SkipDependencies")
    assert result.returncode == 0, result.stdout + result.stderr
    plan = json.loads(result.stdout)
    assert plan["client"] == "none"
    assert plan["midi_port"] in (None, "")
    assert plan["downloads"] is False
    assert plan["fl_installer"] == "scripts/install_fl_integration.py"


@pytest.fixture
def isolated_setup(tmp_path):
    root = tmp_path / "checkout with spaces"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(ROOT / "install.ps1", root / "install.ps1")
    python = root / ".venv/Scripts/python.exe"
    python.parent.mkdir(parents=True)
    shutil.copy2(sys.executable, python)
    (root / ".venv/pyvenv.cfg").write_text(
        f"home = {sys.base_prefix}\ninclude-system-site-packages = false\n", encoding="utf-8",
    )
    common = (
        "import json, pathlib, sys\n"
        "root = pathlib.Path(__file__).resolve().parents[1]\n"
        "with (root/'calls.jsonl').open('a') as f:\n"
        "    f.write(json.dumps([pathlib.Path(__file__).name, sys.argv[1:]])+'\\n')\n"
    )
    for name in ("register_codex.py", "register_claude.py", "doctor.py"):
        (scripts / name).write_text(common + "print('{}')\n")
    (scripts / "install_fl_integration.py").write_text(
        common + "print(json.dumps({'changed': 6, 'backup': 'fixture-backup'}))\n",
    )
    package = root / "fl_studio_mcp/utils"
    package.mkdir(parents=True)
    (package.parent / "__init__.py").touch()
    (package / "__init__.py").touch()
    (package / "native_midi.py").write_text("def verify_runtime(): return 'fixture verified'\n")
    return root


@pytest.mark.parametrize("client,helper,extra", [
    ("codex", "register_codex.py", []),
    ("claude-desktop", "register_claude.py", ["--client", "desktop"]),
    ("claude-code", "register_claude.py", ["--client", "code"]),
])
def test_setup_calls_guarded_installer_and_passes_only_explicit_client_options(
    isolated_setup, client, helper, extra,
):
    data = isolated_setup / "custom FL data"
    data.mkdir()
    config = isolated_setup / "client config"
    result = run(
        isolated_setup / "install.ps1", "-SkipDependencies", "-Client", client,
        "-FlUserData", str(data), "-ClientConfig", str(config), "-MidiPort", "My MIDI port",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    calls = [json.loads(line) for line in (isolated_setup / "calls.jsonl").read_text().splitlines()]
    assert calls == [
        ["install_fl_integration.py", ["--apply", "--fl-user-data", str(data)]],
        [helper, extra + ["--apply", "--fl-user-data", str(data), "--midi-port", "My MIDI port",
                          "--config", str(config)]],
        ["doctor.py", []],
    ]
    assert not config.exists()  # The fixture stub never writes real client configuration.


def test_setup_failure_stops_before_client_configuration(isolated_setup):
    (isolated_setup / "scripts/install_fl_integration.py").write_text(
        "import sys\nprint('fixture installation failure')\nsys.exit(7)\n",
    )
    result = run(isolated_setup / "install.ps1", "-SkipDependencies", "-Client", "codex")
    assert result.returncode != 0
    assert not (isolated_setup / "calls.jsonl").exists()


@pytest.mark.parametrize("development,cli,skip_fl", [
    (False, False, False), (False, True, False), (True, False, True),
])
def test_dependency_selection_and_cli_are_explicit(isolated_setup, development, cli, skip_fl):
    tools = isolated_setup / "tools"
    tools.mkdir()
    python = isolated_setup / ".venv/Scripts/python.exe"
    (isolated_setup / "uv_stub.py").write_text(
        "import json,os,pathlib,sys\n"
        "with (pathlib.Path(__file__).parent/'uv-calls.jsonl').open('a') as f:\n"
        "    f.write(json.dumps(sys.argv[1:])+'\\n')\n",
    )
    (tools / "uv.cmd").write_text(
        f'@"{python}" "{isolated_setup / "uv_stub.py"}" %*\r\n',
    )
    with (isolated_setup / "uv_stub.py").open("a") as stream:
        stream.write(
            "assert os.environ['UV_PROJECT_ENVIRONMENT'] == "
            "str(pathlib.Path(__file__).parent/'.venv')\n",
        )
    environment = dict(
        os.environ, PATH=str(tools) + os.pathsep + os.environ["PATH"],
        UV_PROJECT_ENVIRONMENT=str(isolated_setup / "unrelated environment"),
    )
    if development or cli:
        (isolated_setup / "agent-harness").mkdir()
        (isolated_setup / "agent-harness/setup.py").touch()
        package = isolated_setup / "cli_anything/flstudio"
        package.mkdir(parents=True)
        (package / "__init__.py").touch()
        (package / "__main__.py").write_text("print('CLI help fixture')\n")
    options = ["-NoBootstrap"]
    if development:
        options.append("-Development")
    if cli:
        options.append("-Cli")
    if skip_fl:
        options.append("-SkipFlIntegration")
    result = run(isolated_setup / "install.ps1", *options, env=environment)
    assert result.returncode == 0, result.stdout + result.stderr
    commands = [
        json.loads(line) for line in (isolated_setup / "uv-calls.jsonl").read_text().splitlines()
    ]
    sync = ["sync", "--project", str(isolated_setup), "--locked", "--python", "3.13",
            "--extra", "audio", "--extra", "serum"]
    sync += ["--extra", "dev"] if development else ["--no-dev"]
    expected = [
        ["python", "install", "3.13"],
        sync,
    ]
    if development or cli:
        expected.append(["pip", "install", "--python", str(python), "--no-deps", "--editable",
                         str(isolated_setup / "agent-harness")])
    assert commands == expected
    if skip_fl:
        assert not (isolated_setup / "calls.jsonl").exists()


def test_development_plan_includes_cli_and_can_skip_fl(tmp_path):
    result = run(ROOT / "install.ps1", "-Plan", "-Development", "-SkipFlIntegration")
    assert result.returncode == 0, result.stdout + result.stderr
    plan = json.loads(result.stdout)
    assert plan["dependencies"] == ["audio", "serum", "dev"]
    assert plan["development_tools"] is True
    assert plan["cli"] is True
    assert plan["installs_fl_integration"] is False
    assert plan["writes_client_config"] is False


def test_missing_cli_source_stops_before_changing_fl(isolated_setup):
    result = run(isolated_setup / "install.ps1", "-Cli", "-SkipDependencies")
    assert result.returncode != 0
    assert "CLI source is missing" in result.stderr
    assert not (isolated_setup / "calls.jsonl").exists()


def test_relative_user_paths_resolve_from_the_invocation_directory(isolated_setup, tmp_path):
    caller = tmp_path / "caller"
    caller.mkdir()
    (caller / "FL data").mkdir()
    (caller / "windows-helper.exe").touch()
    result = run(
        isolated_setup / "install.ps1", "-SkipDependencies", "-Client", "codex",
        "-FlUserData", "FL data", "-ClientConfig", "client.toml",
        "-WindowsServer", "windows-helper.exe", cwd=caller,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    calls = [json.loads(line) for line in (isolated_setup / "calls.jsonl").read_text().splitlines()]
    assert calls[0] == [
        "install_fl_integration.py", ["--apply", "--fl-user-data", str(caller / "FL data")],
    ]
    assert calls[1] == ["register_codex.py", [
        "--apply", "--fl-user-data", str(caller / "FL data"),
        "--windows-server", str(caller / "windows-helper.exe"),
        "--config", str(caller / "client.toml"),
    ]]
