"""Build the actual wheel and verify its native payload without executing it."""

import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from fl_studio_mcp.utils import background_loading, native_midi

ROOT = Path(__file__).resolve().parents[1]
RUNTIMES = {"midi_bridge": native_midi, "background_bridge": background_loading}


@pytest.fixture(scope="module")
def wheel_path(tmp_path_factory):
    pytest.importorskip("hatchling.build", reason="Install the declared wheel build backend")
    output = tmp_path_factory.mktemp("native-wheel")
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from hatchling.build import build_wheel; print(build_wheel(sys.argv[1]))",
            str(output),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    wheels = list(output.glob("*.whl"))
    assert len(wheels) == 1, result.stdout + result.stderr
    return wheels[0]


@pytest.mark.parametrize("name", RUNTIMES)
def test_wheel_contains_exact_native_runtime_and_notice_files(wheel_path, name):
    source = ROOT / "native" / name / "runtime"
    prefix = f"fl_studio_mcp/_native/{name}/"
    with zipfile.ZipFile(wheel_path) as wheel:
        expected = {
            path.relative_to(source).as_posix(): path
            for path in source.rglob("*")
            if path.is_file()
        }
        actual = {
            path[len(prefix) :]
            for path in wheel.namelist()
            if path.startswith(prefix) and not path.endswith("/")
        }
        assert actual == expected.keys()
        for relative, path in expected.items():
            assert wheel.read(prefix + relative) == path.read_bytes(), relative
        manifest_data = wheel.read(prefix + "manifest.json")
        manifest = json.loads(manifest_data)
        for relative, entry in manifest["files"].items():
            data = wheel.read(prefix + relative)
            assert len(data) == entry["size"], relative
            assert hashlib.sha256(data).hexdigest() == entry["sha256"], relative
        if name == "background_bridge":
            assert hashlib.sha256(manifest_data).hexdigest() == background_loading.MANIFEST_SHA256


def test_extracted_wheel_resolves_and_verifies_both_bundled_runtimes(wheel_path, tmp_path):
    with zipfile.ZipFile(wheel_path) as wheel:
        wheel.extractall(tmp_path)
    # Force imports from the extracted wheel while retaining installed Python dependencies.
    script = """
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from fl_studio_mcp.utils import native_midi, background_loading
modules = {'midi_bridge': native_midi, 'background_bridge': background_loading}
root = Path(sys.argv[1]).resolve() / 'fl_studio_mcp'
result = {}
for name, module in modules.items():
    assert Path(module.__file__).resolve().is_relative_to(root)
    expected = root / '_native' / name
    assert module.RUNTIME == expected
    result[name] = str(module.verify_runtime())
print(json.dumps(result))
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    actual = json.loads(result.stdout)
    for name, module in RUNTIMES.items():
        assert (
            Path(actual[name])
            == tmp_path / "fl_studio_mcp" / "_native" / name / module.RUNTIME_FILES[0]
        )


def _isolated_module(tmp_path, module):
    source = tmp_path / "src" / "fl_studio_mcp" / "utils" / Path(module.__file__).name
    source.parent.mkdir(parents=True)
    shutil.copyfile(module.__file__, source)
    spec = importlib.util.spec_from_file_location("_runtime_lookup_test", source)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


@pytest.mark.parametrize("name,module", RUNTIMES.items())
def test_source_checkout_fallback_verifies_runtime(tmp_path, name, module):
    source = tmp_path / "native" / name / "runtime"
    shutil.copytree(ROOT / "native" / name / "runtime", source)
    isolated = _isolated_module(tmp_path, module)
    assert isolated.RUNTIME == source
    assert isolated.verify_runtime() == source / module.RUNTIME_FILES[0]


@pytest.mark.parametrize("name,module", RUNTIMES.items())
def test_damaged_bundle_never_falls_back_to_valid_source(tmp_path, name, module):
    source = tmp_path / "native" / name / "runtime"
    shutil.copytree(ROOT / "native" / name / "runtime", source)
    bundled = tmp_path / "src" / "fl_studio_mcp" / "_native" / name
    shutil.copytree(source, bundled)
    (bundled / module.RUNTIME_FILES[0]).write_bytes(b"modified executable")
    isolated = _isolated_module(tmp_path, module)
    assert isolated.RUNTIME == bundled
    with pytest.raises(ValueError, match="hash"):
        isolated.verify_runtime()


@pytest.mark.parametrize("name,module", RUNTIMES.items())
def test_incomplete_bundle_never_falls_back_to_valid_source(tmp_path, name, module):
    source = tmp_path / "native" / name / "runtime"
    shutil.copytree(ROOT / "native" / name / "runtime", source)
    bundled = tmp_path / "src" / "fl_studio_mcp" / "_native" / name
    bundled.mkdir(parents=True)
    isolated = _isolated_module(tmp_path, module)
    assert isolated.RUNTIME == bundled
    with pytest.raises(FileNotFoundError):
        isolated.verify_runtime()
