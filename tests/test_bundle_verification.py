import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "verify_project_bundle",
    Path(__file__).resolve().parents[1] / "scripts/verify_project_bundle.py",
)
verification = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verification)


def fixture_bundle(root, path="notes.json"):
    data = b'{\r\n  "notes": []\r\n}\r\n'
    (root / "notes.json").write_bytes(data)
    entry = {"path": path, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    (root / "manifest.json").write_text(json.dumps({"files": [entry]}), encoding="utf-8")


def test_exact_bytes_and_line_ending_drift(tmp_path):
    fixture_bundle(tmp_path)
    assert verification.verify_bundle(tmp_path)["ok"]
    path = tmp_path / "notes.json"
    path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n"))
    assert verification.verify_bundle(tmp_path)["errors"] == ["Content mismatch: notes.json"]


def test_missing_file(tmp_path):
    fixture_bundle(tmp_path)
    (tmp_path / "notes.json").unlink()
    assert verification.verify_bundle(tmp_path)["errors"] == ["Missing file: notes.json"]


@pytest.mark.parametrize("path", ["../outside", "/outside", "C:/outside", "..\\outside"])
def test_rejects_unsafe_paths(tmp_path, path):
    fixture_bundle(tmp_path, path)
    with pytest.raises(ValueError, match="Unsafe"):
        verification.verify_bundle(tmp_path)


def test_rejects_duplicate_manifest_entries(tmp_path):
    fixture_bundle(tmp_path)
    path = tmp_path / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["files"] *= 2
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="Duplicate"):
        verification.verify_bundle(tmp_path)
