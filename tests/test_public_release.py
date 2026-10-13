"""Public source packaging must exclude private history/content and preserve inputs."""

import hashlib
import importlib.util
import json
import subprocess
import zipfile
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "public_release", Path(__file__).resolve().parents[1] / "scripts/export_public_release.py"
)
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


def command(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


@pytest.fixture
def repository(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "src").mkdir()
    (root / "src/tool.py").write_text("print('example')\n")
    (root / "README.md").write_text("# Example\n")
    (root / "projects").mkdir()
    (root / "projects/private.wav").write_bytes(b"private fixture")
    policy = {
        "schema_version": 1,
        "include_directories": ["src"],
        "include_files": ["README.md", "public-release.json"],
    }
    (root / "public-release.json").write_text(json.dumps(policy))
    command(root, "init")
    command(root, "add", ".")
    command(
        root,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-m",
        "Fixture",
    )
    return root


def test_clean_export_excludes_history_and_media_and_records_exact_hashes(repository, tmp_path):
    output, archive = tmp_path / "export", tmp_path / "source.zip"
    original = (repository / "src/tool.py").read_bytes()
    result = release.export(repository, output, archive)
    assert result["success"] and result["source_dirty"] is False
    assert not (output / ".git").exists() and not (output / "projects").exists()
    manifest = json.loads((output / "PUBLIC-MANIFEST.json").read_text())
    assert manifest["git_history_included"] is False
    for item in manifest["files"]:
        data = (output / item["path"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == item["sha256"]
    with zipfile.ZipFile(archive) as zipped:
        assert zipped.testzip() is None
        assert zipped.read("SyncFL-MCP/src/tool.py") == original
    assert (repository / "src/tool.py").read_bytes() == original


def test_dirty_source_requires_explicit_review_override(repository, tmp_path):
    (repository / "src/tool.py").write_text("print('changed')\n")
    with pytest.raises(ValueError, match="Commit tracked changes"):
        release.export(repository, tmp_path / "refused")
    result = release.export(repository, tmp_path / "review", allow_dirty=True)
    assert result["source_dirty"] is True


def test_export_never_overwrites_existing_or_writes_inside_source(repository, tmp_path):
    existing = tmp_path / "existing"
    existing.mkdir()
    for output in [existing, repository / "export"]:
        with pytest.raises(ValueError, match="new and outside"):
            release.export(repository, output)


def test_publish_retries_a_transient_windows_lock(monkeypatch, tmp_path):
    staging, output = tmp_path / "staging", tmp_path / "output"
    staging.mkdir()
    original = release.os.rename
    calls = []

    def locked_once(source, target):
        calls.append((source, target))
        if len(calls) == 1:
            error = PermissionError("Temporary scanner lock")
            error.winerror = 32
            raise error
        original(source, target)

    monkeypatch.setattr(release.os, "rename", locked_once)
    monkeypatch.setattr(release.time, "sleep", lambda _: None)
    release.publish_directory(staging, output)
    assert len(calls) == 2 and output.is_dir() and not staging.exists()


def test_publish_lock_retries_are_bounded(monkeypatch, tmp_path):
    staging, output = tmp_path / "staging", tmp_path / "output"
    staging.mkdir()
    calls = []

    def always_locked(source, target):
        calls.append((source, target))
        error = PermissionError("Persistent lock")
        error.winerror = 5
        raise error

    monkeypatch.setattr(release.os, "rename", always_locked)
    monkeypatch.setattr(release.time, "sleep", lambda _: None)
    with pytest.raises(PermissionError):
        release.publish_directory(staging, output)
    assert len(calls) == 8 and staging.is_dir() and not output.exists()


@pytest.mark.parametrize(
    "name",
    [
        "../escape",
        "/absolute",
        ".git/config",
        "projects/a.wav",
        "references/a.mid",
        "C:/outside",
        "src\\file.py",
    ],
)
def test_policy_rejects_private_or_escaping_paths(name):
    with pytest.raises(ValueError):
        release.safe_relative(name)


def test_media_is_refused_even_below_an_allowlisted_source_directory(repository, tmp_path):
    (repository / "src/leak.wav").write_bytes(b"do not export")
    command(repository, "add", ".")
    with pytest.raises(ValueError, match="Private media"):
        release.export(repository, tmp_path / "refused", allow_dirty=True)


def test_known_secret_and_home_path_scan_reports_only_filename():
    secret = "gh" + "p_" + "x" * 36
    with pytest.raises(ValueError, match="Credential-like") as error:
        release.scan_content("src/config.py", secret.encode())
    assert secret not in str(error.value)
    with pytest.raises(ValueError, match="home path"):
        release.scan_content("readme.md", b"/private/person/file", home=Path("/private/person"))
    with pytest.raises(ValueError, match="Unexpected binary"):
        release.scan_content("src/not_text.py", b"hello\0world")


@pytest.mark.parametrize("prefix", ["C:" + "/Users/", "/" + "Users/", "/" + "home/"])
def test_other_developers_home_paths_are_rejected(prefix):
    private_path = prefix + "private-person/build/source.cpp"
    with pytest.raises(ValueError, match="home path") as error:
        release.scan_content("src/config.py", private_path.encode(), home=Path("/unrelated"))
    assert "private-person" not in str(error.value)


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16-le", "utf-16-be"])
@pytest.mark.parametrize("reviewed", [False, True])
def test_approved_binaries_still_reject_embedded_private_paths(encoding, reviewed):
    private_path = "C:" + "/Users/" + "private-person/build/header.h"
    data = b"MZ\0" + private_path.encode(encoding) + b"\0"
    with pytest.raises(ValueError, match="home path") as error:
        release.scan_content("native/bridge/runtime/helper.exe", data, reviewed_binary=reviewed)
    assert "private-person" not in str(error.value)


def test_approved_binary_still_rejects_embedded_credentials():
    secret = "gh" + "p_" + "x" * 36
    with pytest.raises(ValueError, match="Credential-like") as error:
        release.scan_content("logo.png", b"PNG\0" + secret.encode(), reviewed_binary=True)
    assert secret not in str(error.value)


def test_tokenized_download_urls_require_review():
    value = "https://example.invalid/file?" + "token=" + "private-value"
    with pytest.raises(ValueError, match="Credential-like") as error:
        release.scan_content("README.md", value.encode())
    assert "private-value" not in str(error.value)


def test_system_profiles_and_explicit_placeholders_remain_valid_examples():
    examples = "C:" + "/Users/Public/file /home/<user>/project /Users/${USER}/project"
    release.scan_content("README.md", examples.encode(), home=Path("/unrelated"))


def test_all_tracked_release_files_pass_content_review():
    root = Path(__file__).resolve().parents[1]
    if not (root / ".git").exists():
        pytest.skip("Git-index audit requires a checkout; source archives have no Git history")
    policy = json.loads((root / "public-release.json").read_text())
    reviewed = policy.get("reviewed_binary_files", {})
    for name in release.selected_paths(root):
        release.scan_content(name, (root / name).read_bytes(), reviewed_binary=name in reviewed)


@pytest.mark.parametrize(
    "bridge", ["midi_bridge", "background_bridge", "midi_transport", "audio_tempo"]
)
def test_runtime_hashes_checked_before_export(tmp_path, bridge):
    prefix = f"native/{bridge}/runtime/"
    manifest = {"files": {"helper.exe": {"size": 4, "sha256": "0" * 64}}}
    with pytest.raises(ValueError, match="hash mismatch"):
        release.check_runtime_files(
            tmp_path,
            {
                prefix + "manifest.json": json.dumps(manifest).encode(),
                prefix + "helper.exe": b"data",
            },
        )


def test_reviewed_binary_requires_the_exact_reviewed_contents(repository, tmp_path):
    path = repository / "src/own.SerumPreset"
    path.write_bytes(b"own\0settings")
    policy_path = repository / "public-release.json"
    policy = json.loads(policy_path.read_text())
    policy["reviewed_binary_files"] = {
        "src/own.SerumPreset": {
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "reason": "Synthetic test fixture",
        }
    }
    policy_path.write_text(json.dumps(policy))
    command(repository, "add", ".")
    release.export(repository, tmp_path / "approved", allow_dirty=True)
    path.write_bytes(b"changed\0settings")
    with pytest.raises(ValueError, match="Reviewed binary changed"):
        release.export(repository, tmp_path / "refused", allow_dirty=True)


def test_bundled_serum_templates_have_only_relative_references_and_no_binary_payloads():
    pytest.importorskip("cbor2")
    pytest.importorskip("zstandard")
    from fl_studio_mcp.utils.serum_presets import DATA, read_preset

    root = Path(__file__).resolve().parents[1]
    policy = json.loads((root / "public-release.json").read_text())

    def inspect(value):
        assert not isinstance(value, (bytes, bytearray)), "Embedded binary payload"
        if isinstance(value, dict):
            for key, item in value.items():
                inspect(key)
                inspect(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                inspect(item)
        elif isinstance(value, str):
            assert not value.startswith(("/", "\\\\"))
            assert not (len(value) > 1 and value[1] == ":"), "Absolute filesystem path"
            assert "users/" not in value.replace("\\", "/").lower()

    for path in DATA.glob("*.SerumPreset"):
        header, body = read_preset(path)
        assert header["presetAuthor"] == body["presetAuthor"] == "SyncFL"
        assert header["presetName"].startswith("SyncFL - ")
        name = path.relative_to(root).as_posix()
        assert (
            hashlib.sha256(path.read_bytes()).hexdigest()
            == policy["reviewed_binary_files"][name]["sha256"]
        )
        for section in (header, body):
            inspect(section)


@pytest.mark.parametrize("autocrlf", ["true", "false"])
def test_checkout_preserves_hash_pinned_runtime_files(tmp_path, autocrlf):
    root = Path(__file__).resolve().parents[1]
    source = tmp_path / "source"
    source.mkdir()
    (source / ".gitattributes").write_bytes((root / ".gitattributes").read_bytes())
    runtime_files = {}
    for bridge in ("midi_bridge", "background_bridge", "midi_transport", "audio_tempo"):
        name = f"native/{bridge}/runtime/manifest.json"
        data = (root / name).read_bytes()
        target = source / name
        target.parent.mkdir(parents=True)
        target.write_bytes(data)
        runtime_files[name] = data
        for relative in json.loads(data)["files"]:
            filename = f"native/{bridge}/runtime/{relative}"
            contents = (root / filename).read_bytes()
            output = source / filename
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(contents)
            runtime_files[filename] = contents
    command(source, "init")
    command(source, "add", ".")
    command(
        source,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-m",
        "Runtime manifests",
    )
    checkout = tmp_path / "checkout"
    command(tmp_path, "clone", "--quiet", "--no-checkout", str(source), str(checkout))
    command(checkout, "config", "core.autocrlf", autocrlf)
    command(checkout, "checkout", "--force", "HEAD")
    for name, data in runtime_files.items():
        assert (checkout / name).read_bytes() == data, name
