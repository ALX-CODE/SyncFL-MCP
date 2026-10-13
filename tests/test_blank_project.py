"""Blank startup creates its own named FLP; existing work is never replaced."""

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from fl_studio_mcp.utils import backend_project as project


@pytest.fixture
def blank(tmp_path, monkeypatch):
    install = tmp_path / "FL Studio 2026"
    template = install / "Data/Templates/Empty/Empty.flp"
    template.parent.mkdir(parents=True)
    template.write_bytes(b"t" * 22)
    main = dict(handle="10", pid=42, title="FL Studio 2026")
    state = dict(revision="current", dirty=0, is_playing=False, is_recording=False,
                 context=dict(session="s", project_generation=1, program_title=main["title"],
                              project_title="", channels=[dict(index=0, name="Sampler", type=0)]),
                 values=dict(ppq=96, tempo_bpm=140))
    info = dict(version="24.2.99", ppq=96, tempo=140, channels=[dict(id=0, name="Sampler")],
                patterns=[], clips=[], sample_references=[])
    rig = SimpleNamespace(state=state, main=main, info=info, calls=[], pattern_count=0,
                          loads=[], fail_load=False, saved=False, events=[])
    monkeypatch.setattr(project, "_inspect", lambda _: copy.deepcopy(info))
    monkeypatch.setattr(project, "_parse", lambda _: ([], rig.events, 96))
    monkeypatch.setattr(project, "_window_context", lambda: dict(main))
    monkeypatch.setattr(project.bridge, "_project", lambda *a: copy.deepcopy(state))
    monkeypatch.setattr(project.bridge, "_process_identity", lambda _: dict(
        image=str(install / "FL64.exe")))
    monkeypatch.setattr(project.bridge, "verify_runtime", lambda: tmp_path / "bridge.exe")

    def call(conn, action, params):
        rig.calls.append((action, params))
        if action == "bridge.ping":
            return dict(project_native=dict(controller_pid=42), safe_to_edit=True)
        if params["function"] == "patterns.patternCount":
            return dict(value=rig.pattern_count)
        if params["function"] == "patterns.setPatternName":
            state["dirty"] = 1
            return dict(value=None)
        raise AssertionError(params)

    def load(conn, main, before, source, data, info, executable, receipt, report):
        rig.loads.append(source)
        assert source.read_bytes() == template.read_bytes()
        if rig.fail_load:
            raise RuntimeError("Load was declined")
        state["context"]["project_generation"] += 1
        rig.main["title"] = source.name + " - FL Studio 2026"
        report.update(success=True, after=copy.deepcopy(state))

    def save(conn, before, data):
        rig.saved = True
        state["dirty"] = 0
        final = dict(info, version="26.1.0", patterns=[dict(id=1, name="Pattern 1", notes=[])])
        Path(before["path"]).write_bytes(b"native-saved-fl26")
        return b"native-saved-fl26", final

    monkeypatch.setattr(project.bridge, "_call", call)
    monkeypatch.setattr(project, "_load", load)
    monkeypatch.setattr(project, "_save_and_read", save)
    rig.args = dict(path=str(tmp_path / "outputs/Beat.flp"), expected_revision="current",
                    receipt_file=str(tmp_path / "receipts/create.json"),
                    connection=SimpleNamespace(_hardware_dir=tmp_path), apply=True)
    return rig


def test_preflight_does_not_create_files_or_mutate_fl(blank):
    result = project.create_blank_project(**dict(blank.args, apply=False))
    assert result["success"] and result["status"] == "preflighted"
    assert not Path(blank.args["path"]).exists()
    assert not Path(blank.args["receipt_file"]).exists()
    assert not blank.loads and not blank.saved
    assert all(p.get("allow_write") is not True for _, p in blank.calls)


def test_creates_loads_and_native_saves_the_named_blank(blank):
    result = project.create_blank_project(**blank.args)
    assert result["success"] and result["version"] == "26.1.0"
    assert result["desktop_input"] is False and len(blank.loads) == 1 and blank.saved
    assert Path(blank.args["path"]).read_bytes() == b"native-saved-fl26"
    assert json.loads(Path(blank.args["receipt_file"]).read_text())["status"] == "verified"
    assert [p["function"] for _, p in blank.calls if p.get("allow_write")] == [
        "patterns.setPatternName"]


@pytest.mark.parametrize("change", ["dirty", "named", "channels", "patterns", "revision",
                                    "playing", "recording", "template_clips"])
def test_refuses_existing_work_before_creating_any_file(blank, change):
    if change == "named":
        blank.main["title"] = "Existing.flp - FL Studio 2026"
    elif change == "channels":
        blank.state["context"]["channels"][0]["name"] = "My sound"
    elif change == "patterns":
        blank.pattern_count = 1
    elif change == "revision":
        blank.state["revision"] = "new"
    elif change == "template_clips":
        blank.events = [SimpleNamespace(id=233, payload=b"a clip")]
    else:
        blank.state[{"dirty": "dirty", "playing": "is_playing", "recording": "is_recording"}[
            change]] = 1
    with pytest.raises((ValueError, RuntimeError)):
        project.create_blank_project(**blank.args)
    assert not Path(blank.args["path"]).exists()
    assert not Path(blank.args["receipt_file"]).exists()
    assert not blank.loads and not blank.saved


def test_existing_destination_is_preserved(blank):
    destination = Path(blank.args["path"])
    destination.parent.mkdir()
    destination.write_bytes(b"user work")
    with pytest.raises(ValueError, match="NEW absolute"):
        project.create_blank_project(**blank.args)
    assert destination.read_bytes() == b"user work"
    assert not blank.loads


def test_unknown_load_is_not_retried_and_keeps_receipt(blank):
    blank.fail_load = True
    result = project.create_blank_project(**blank.args)
    assert not result["success"] and result["status"] == "unverified"
    assert len(blank.loads) == 1 and not blank.saved
    assert json.loads(Path(blank.args["receipt_file"]).read_text())["error"] == "Load was declined"
    assert not any(p.get("allow_write") for _, p in blank.calls)


def test_project_change_after_load_prevents_initialization(blank, monkeypatch):
    load = project._load

    def changed(*args):
        load(*args)
        blank.state["dirty"] = 1

    monkeypatch.setattr(project, "_load", changed)
    result = project.create_blank_project(**blank.args)
    assert not result["success"] and "changed before initialization" in result["error"]
    assert not blank.saved and not any(p.get("allow_write") for _, p in blank.calls)


def test_save_without_native_fl26_bytes_is_not_reported_as_success(blank, monkeypatch):
    def unchanged(conn, before, data):
        blank.state["dirty"] = 0
        return data, blank.info

    monkeypatch.setattr(project, "_save_and_read", unchanged)
    result = project.create_blank_project(**blank.args)
    assert not result["success"] and "not independently verified" in result["error"]
    assert json.loads(Path(blank.args["receipt_file"]).read_text())["success"] is False


def test_project_change_during_final_save_prevents_verified_result(blank, monkeypatch):
    save = project._save_and_read

    def changed(*args):
        result = save(*args)
        blank.state["dirty"] = 1
        return result

    monkeypatch.setattr(project, "_save_and_read", changed)
    result = project.create_blank_project(**blank.args)
    assert not result["success"] and "changed during final save" in result["error"]
