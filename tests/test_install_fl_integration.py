"""Exercise the installer only against temporary Settings directories."""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def installer(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "install_fl_integration_test", ROOT / "scripts" / "install_fl_integration.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def settings(tmp_path):
    path = tmp_path / "Image-Line" / "FL Studio" / "Settings"
    path.mkdir(parents=True)
    return path


def test_check_is_read_only_and_reports_missing_companions(installer, settings):
    result = installer.install(settings)
    assert not result["success"] and not result["applied"]
    assert len(result["files"]) == len(installer.DEPLOYED_FILES)
    assert list(settings.iterdir()) == []


def test_complete_install_preserves_unrelated_files_and_prior_versions(installer, settings):
    previous = settings / "Hardware" / "FLStudioMCP" / "device_FLStudioMCP.py"
    previous.parent.mkdir(parents=True)
    previous.write_bytes(b"prior controller")
    unrelated = previous.parent / "user_notes.txt"
    unrelated.write_bytes(b"preserve")
    result = installer.install(settings, apply=True)
    assert result["success"] and result["applied"]
    assert result["changed"] == len(installer.DEPLOYED_FILES)
    assert result["installed_controller_sha256"] == installer.controller_source_hash()
    backup = Path(result["backup"])
    assert (backup / previous.relative_to(settings)).read_bytes() == b"prior controller"
    assert unrelated.read_bytes() == b"preserve"
    for source, relative in installer.DEPLOYED_FILES:
        assert (settings / relative).read_bytes() == (ROOT / source).read_bytes()
    assert json.loads((backup / "installation.json").read_text())["success"]


def test_identical_install_is_idempotent(installer, settings):
    installer.install(settings, apply=True)
    before = {path: path.stat().st_mtime_ns for path in settings.rglob("*") if path.is_file()}
    result = installer.install(settings, apply=True)
    assert result["success"] and result["changed"] == 0 and result["backup"] is None
    assert {path: path.stat().st_mtime_ns for path in before} == before


@pytest.mark.parametrize("guard", ["mcp-verification.json", "mcp_request.json"])
def test_pending_piano_work_refuses_controller_changes(installer, settings, guard):
    piano = settings / "Piano roll scripts"
    piano.mkdir()
    (piano / guard).write_text('[{"pending":true}]')
    with pytest.raises(RuntimeError, match="verification|Pending piano"):
        installer.install(settings, apply=True)
    assert not (settings / "Hardware/FLStudioMCP/device_FLStudioMCP.py").exists()


def test_busy_gui_lock_refuses_changes_before_other_locks(installer, settings, monkeypatch):
    locks = []

    @contextmanager
    def busy(path, timeout):
        locks.append(path.name)
        raise TimeoutError("gui transaction active")
        yield

    monkeypatch.setattr(installer, "transaction_lock", busy)
    with pytest.raises(TimeoutError, match="gui transaction active"):
        installer.install(settings, apply=True)
    assert locks == ["mcp-gui.lock"]
    assert not (settings / "Hardware/FLStudioMCP/device_FLStudioMCP.py").exists()


def test_failed_write_restores_prior_files(installer, settings, monkeypatch):
    path = settings / "Hardware/FLStudioMCP/device_FLStudioMCP.py"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"prior controller")
    original = installer._write_atomic
    failed = False

    def write(target, data):
        nonlocal failed
        if target == settings / "Hardware/FLStudioMCP/mcp_extended.py" and not failed:
            failed = True
            raise OSError("simulated disk write failure")
        original(target, data)

    monkeypatch.setattr(installer, "_write_atomic", write)
    with pytest.raises(OSError, match="simulated disk"):
        installer.install(settings, apply=True)
    assert path.read_bytes() == b"prior controller"
    assert not (settings / "Hardware/FLStudioMCP/mcp_extended.py").exists()
    assert not (settings / "Hardware/FLStudioMCP/mcp_production.py").exists()


def test_missing_source_fails_before_writing_targets(installer, settings, monkeypatch):
    monkeypatch.setattr(
        installer,
        "DEPLOYED_FILES",
        [
            ("fl_controller/device_FLStudioMCP.py", "Hardware/FLStudioMCP/device_FLStudioMCP.py"),
            ("not-present.py", "Hardware/FLStudioMCP/not-present.py"),
        ],
    )
    with pytest.raises(FileNotFoundError):
        installer.install(settings, apply=True)
    assert list(settings.iterdir()) == []


def test_manifest_target_cannot_escape_settings(installer, settings, monkeypatch):
    monkeypatch.setattr(
        installer,
        "DEPLOYED_FILES",
        [
            ("fl_controller/device_FLStudioMCP.py", "../outside.py"),
        ],
    )
    with pytest.raises(ValueError, match="escapes Settings"):
        installer.install(settings, apply=True)
    assert not (settings.parent / "outside.py").exists()
    assert list(settings.iterdir()) == []


@pytest.mark.parametrize("value", ["relative/Settings", "missing"])
def test_invalid_settings_never_created(installer, tmp_path, value):
    path = Path(value) if value.startswith("relative") else tmp_path / value
    with pytest.raises(ValueError, match="existing absolute"):
        installer.install(path, apply=True)
    assert not path.exists()


def test_cli_resolves_explicit_user_data_and_check_returns_digest(settings):
    args = [
        sys.executable,
        str(ROOT / "scripts/install_fl_integration.py"),
        "--fl-user-data",
        str(settings.parent.parent),
    ]
    applied = subprocess.run(args + ["--apply"], capture_output=True, text=True, timeout=20)
    assert applied.returncode == 0, applied.stdout + applied.stderr
    checked = subprocess.run(args + ["--check"], capture_output=True, text=True, timeout=20)
    assert checked.returncode == 0, checked.stdout + checked.stderr
    report = json.loads(checked.stdout)
    assert not report["applied"] and report["success"]
    assert report["controller_source_sha256"] == report["installed_controller_sha256"]


def test_root_installers_delegate_file_copy_and_keep_native_startup_read_only():
    windows = (ROOT / "install.ps1").read_text(encoding="utf-8-sig")
    unix = (ROOT / "install.sh").read_text(encoding="utf-8-sig")
    for content in (windows, unix):
        assert "scripts" in content and "install_fl_integration.py" in content
        assert "--apply" in content
        assert "setup.sh" not in content
        assert "initialize_native_midi(" not in content and "native_midi_status(" not in content
    assert "Copy-Item" not in windows
    assert "verify_runtime" in windows
    assert "optional fallback" in windows and "FL_MCP_MIDI_PORT" in windows
    assert "IAC Driver" in unix and "Linux/Wine compatibility" in unix


def test_powershell_installer_parses_without_execution():
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if not shell:
        pytest.skip("PowerShell parser unavailable")
    script = (
        "$e=$null; $t=$null; "
        "[void][System.Management.Automation.Language.Parser]::ParseFile("
        "$env:FL_INSTALL_TEST_FILE,[ref]$t,[ref]$e); "
        "if($e){$e | Out-String; exit 1}"
    )
    result = subprocess.run(
        [shell, "-NoProfile", "-Command", script],
        env=dict(os.environ, FL_INSTALL_TEST_FILE=str(ROOT / "install.ps1")),
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_bash_installer_parses_without_execution():
    bash = Path(r"C:\Program Files\Git\bin\bash.exe") if os.name == "nt" else shutil.which("bash")
    if not bash or not Path(bash).is_file():
        pytest.skip("Bash parser unavailable")
    result = subprocess.run(
        [str(bash), "-n", str(ROOT / "install.sh")], capture_output=True, text=True, timeout=20
    )
    assert result.returncode == 0, result.stdout + result.stderr
