import asyncio
import json
from unittest.mock import AsyncMock

import numpy as np
import pytest
import soundfile as sf
from PIL import Image
from test_project_bundle import project

from fl_studio_mcp.utils import render_windows as rw
from fl_studio_mcp.utils.audio_files import file_hash


@pytest.mark.parametrize(
    "values",
    [
        dict(bogus=True),
        dict(tail="end"),
        dict(dithering=1),
        dict(dithering=True),
        dict(split_mixer_tracks=True, master_effects=False),
        dict(channels="surround"),
        dict(resampling="3"),
    ],
)
def test_invalid_options_fail_before_desktop_access(values):
    with pytest.raises(ValueError):
        rw.settings(values)


def test_radio_readback_declines_ambiguous_palette():
    image = Image.new("RGB", (433, 611), (40, 30, 50))
    for key in ("wav",):
        x, y = rw.FORMATS[key]
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                image.putpixel((x + dx, y + dy), (240, 190, 150))
    result = rw._flags(image)
    assert result["wav"] is True and result["midi"] is False
    x, y = rw.FLAGS["upload"]
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            image.putpixel((x + dx, y + dy), (130, 100, 100))
    assert rw._flags(image)["upload"] is None
    with pytest.raises(RuntimeError, match="palette"):
        rw._flags(Image.new("RGB", (433, 611), "gray"))


def test_popup_selection_verifies_foreground_without_activating_popup(monkeypatch):
    dialog = dict(handle="2", pid=42, title="Rendering to render.wav", bounds=[100, 100, 433, 611])
    menu = dict(
        handle="3",
        pid=42,
        className="",
        title="",
        bounds=[200, 250, 100, 100],
        parent="1",
        **{"class": "TQuickPopupMenuWindow"},
    )
    fl = type("FL", (), dict(handle="1", win=AsyncMock()))()
    monkeypatch.setattr(rw, "_render_dialog", lambda *a: dialog)
    monkeypatch.setattr(rw, "windows", lambda **kw: [menu])
    monkeypatch.setattr(rw, "point_belongs_to", lambda p, h: p == (220, 270) and h == "3")
    asyncio.run(rw._click(fl, dialog, (120, 170), menu=menu))
    args = fl.win.call_args.kwargs
    assert args["windowHandle"] == "2"  # Activating FL's popup closes it before selection.
    step = json.loads(args["steps"])[0]
    assert step["expectedWindowTitle"] == dialog["title"] and step["expectedProcessName"] == "FL64"
    assert (step["x"], step["y"]) == (120, 170)


@pytest.fixture
def job(tmp_path, monkeypatch):
    root = tmp_path / "job"
    root.mkdir()
    (root / "project.flp").write_bytes(project())
    report = dict(
        schema="fl-render-job-v1",
        directory=str(root),
        status="rendering",
        success=False,
        options=rw.settings({}),
        main_handle="1",
        main_pid=42,
        project_title="project.flp - FL",
        dialog=dict(handle="2"),
        before=dict(context={"generation": 1}),
        expected_sample_rate=8000,
        expected_min_seconds=0.5,
        snapshot=dict(sha256=file_hash(root / "project.flp")),
    )
    (root / "render-job.json").write_text(json.dumps(report))
    monkeypatch.setattr(rw, "_window_state", lambda h: dict(pid=42))
    monkeypatch.setattr(rw, "windows", lambda **kw: [])
    monkeypatch.setattr(rw, "_state", lambda: dict(context={"generation": 1}))
    fl = type(
        "FL",
        (),
        dict(handle="1", project_title=report["project_title"], _project_guard=AsyncMock()),
    )()
    return root, report, fl


def emit(path, seconds=1, channels=2, subtype="FLOAT", silent=False):
    sf.write(
        path,
        np.full((int(8000 * seconds), channels), 0.0 if silent else 0.1),
        8000,
        subtype=subtype,
    )


def test_completed_render_requires_decoded_audio(job):
    root, report, fl = job
    emit(root / "render.wav")
    result = asyncio.run(rw.render_status(fl, root))
    assert result["success"] and result["status"] == "verified" and result["verified"] is True
    assert result["audio"][0]["sample_rate"] == 8000


@pytest.mark.parametrize(
    "failure", ["missing", "silent", "depth", "channels", "short", "changed_source"]
)
def test_bad_render_is_never_reported_success(job, failure):
    root, report, fl = job
    if failure != "missing":
        emit(
            root / "render.wav",
            seconds=0.25 if failure == "short" else 1,
            channels=1 if failure == "channels" else 2,
            subtype="PCM_16" if failure == "depth" else "FLOAT",
            silent=failure == "silent",
        )
    if failure == "changed_source":
        (root / "project.flp").write_bytes(project() + b"changed")
    result = asyncio.run(rw.render_status(fl, root))
    assert not result["success"] and result["status"] == "failed"
    assert result["verified"] is False


def test_stem_alignment_is_checked(job):
    root, report, fl = job
    report["options"]["split_mixer_tracks"] = True
    (root / "render-job.json").write_text(json.dumps(report))
    emit(root / "render_Master.wav")
    emit(root / "render_Insert 2.wav", seconds=1.1)
    result = asyncio.run(rw.render_status(fl, root))
    assert result["status"] == "failed" and "alignment" in result["error"]


def test_delayed_checkbox_paint_polls_readback_without_replaying_toggle(tmp_path, monkeypatch):
    options = rw.settings({})
    state = {**{k: options.get(k, False) for k in rw.FLAGS}, **{k: k == "wav" for k in rw.FORMATS}}
    state["split_mixer_tracks"] = True
    clicks = []
    frames_after_click = 0

    async def capture(*args):
        nonlocal frames_after_click
        if clicks:
            frames_after_click += 1
            if frames_after_click >= 3:
                state["split_mixer_tracks"] = False
        return dict(state), tmp_path / "frame.png"

    async def click(fl, dialog, point):
        clicks.append(point)

    monkeypatch.setattr(rw, "_capture", capture)
    monkeypatch.setattr(rw, "_flags", lambda image: image)
    monkeypatch.setattr(
        rw,
        "_text",
        AsyncMock(return_value=dict(text="Project type Output format Quality Miscellaneous")),
    )
    monkeypatch.setattr(rw, "_choose", AsyncMock(return_value="verified"))
    monkeypatch.setattr(rw, "_click", click)
    fl = type("FL", (), dict(win=AsyncMock()))()
    result = asyncio.run(rw.configure(fl, dict(handle="2"), tmp_path, options))
    assert result["flags"]["split_mixer_tracks"] is False
    assert clicks == [rw.FLAGS["split_mixer_tracks"]]
    assert frames_after_click == 4


def test_visible_dialog_keeps_job_running_without_analyzing_partial_audio(job, monkeypatch):
    root, report, fl = job
    fl.project_title = "Rendering: 2/2048"
    monkeypatch.setattr(
        rw, "windows", lambda **kw: [dict(handle="2", parent="1", **{"class": "TWAVRenderForm"})]
    )
    (root / "render.wav").write_bytes(b"partial")
    result = asyncio.run(rw.render_status(fl, root))
    assert result["status"] == "rendering"
    assert result["success"] is True and result["verified"] is False
    assert "audio" not in result
    assert json.loads((root / "render-job.json").read_text())["verified"] is False


def test_cancel_intent_survives_reconnect_and_never_verifies_partial_as_complete(job):
    root, report, fl = job
    report["status"] = "cancelling"
    (root / "render-job.json").write_text(json.dumps(report))
    (root / "render.wav").write_bytes(b"partial")
    result = asyncio.run(rw.render_status(fl, root))
    assert result["status"] == "cancelled" and result["success"] is False
    assert result["verified"] is False
    assert result["partial_files"] == [str(root / "render.wav")]


def test_cancel_observes_abort_and_dispatches_once_despite_progress_title(job, monkeypatch):
    root, report, fl = job
    report["job_id"] = "cancel-id"
    (root / "render-job.json").write_text(json.dumps(report))
    fl.project_title = "Rendering: 2/2048"
    dialog = dict(handle="2", parent="1", **{"class": "TWAVRenderForm"})
    visible = [dialog]
    clicks = []
    monkeypatch.setattr(rw, "windows", lambda **kw: list(visible))
    monkeypatch.setattr(rw, "_render_dialog", lambda *args: dialog)
    monkeypatch.setattr(rw, "_capture", AsyncMock(return_value=(None, None)))
    monkeypatch.setattr(rw, "_text", AsyncMock(return_value=dict(text="Abort")))

    async def click(*args):
        clicks.append(args)
        visible.clear()

    monkeypatch.setattr(rw, "_click", click)
    result = asyncio.run(rw.render_cancel(fl, root))
    assert result["status"] == "cancelled" and len(clicks) == 1
    assert result["success"] is False and result["verified"] is False


def test_cancel_never_presses_start_in_an_idle_dialog(job, monkeypatch):
    root, report, fl = job
    dialog = dict(handle="2", parent="1", **{"class": "TWAVRenderForm"})
    monkeypatch.setattr(rw, "windows", lambda **kw: [dialog])
    monkeypatch.setattr(rw, "_render_dialog", lambda *args: dialog)
    monkeypatch.setattr(rw, "_capture", AsyncMock(return_value=(None, None)))
    monkeypatch.setattr(rw, "_text", AsyncMock(return_value=dict(text="Start")))
    click = AsyncMock()
    monkeypatch.setattr(rw, "_click", click)
    with pytest.raises(RuntimeError, match="Abort"):
        asyncio.run(rw.render_cancel(fl, root))
    click.assert_not_called()


def test_cancel_finished_job_observes_result_without_input(job):
    root, report, fl = job
    emit(root / "render.wav")
    result = asyncio.run(rw.render_cancel(fl, root))
    assert result["status"] == "verified"


def test_different_project_title_after_render_closure_fails(job):
    root, report, fl = job
    fl.project_title = "different.flp - FL"
    emit(root / "render.wav")
    result = asyncio.run(rw.render_status(fl, root))
    assert result["status"] == "failed" and "title changed" in result["error"]


@pytest.fixture
def start_job(tmp_path, monkeypatch):
    root = tmp_path / "new-job"
    state = dict(revision="current", context={"generation": 1}, dirty=False,
                 is_playing=False, is_recording=False)
    dialog = dict(handle="2", title="Rendering to render.wav")
    fl = type("FL", (), dict(
        handle="1", project_title="project.flp - FL", _project_guard=AsyncMock(),
        _project_focus=AsyncMock(), _save_project_dialog=AsyncMock(),
        save_project_as=AsyncMock(return_value={"sha256": "snapshot"}),
    ))()
    monkeypatch.setattr(rw, "_state", lambda: dict(state))
    monkeypatch.setattr(rw, "_window_state", lambda handle: dict(pid=42))
    monkeypatch.setattr(rw, "_render_dialog", lambda *args: dialog)
    monkeypatch.setattr(rw, "configure", AsyncMock(return_value={"observed": True}))
    monkeypatch.setattr(rw, "_click", AsyncMock(return_value={"success": True}))
    return root, fl


def test_render_preview_is_successful_but_has_no_verified_artifact(start_job):
    root, fl = start_job
    result = asyncio.run(rw.render_start(fl, root, "current"))
    assert result["success"] is True and result["verified"] is False
    assert result["status"] == "preflighted"
    assert not root.exists()
    fl.save_project_as.assert_not_called()


def test_accepted_start_is_pollable_success_without_output_proof(start_job):
    root, fl = start_job
    result = asyncio.run(rw.render_start(fl, root, "current", apply=True))
    assert result["success"] is True and result["verified"] is False
    assert result["status"] == "rendering" and "audio" not in result
    assert json.loads((root / "render-job.json").read_text()) == result
    rw._click.assert_awaited_once()


@pytest.mark.parametrize("failure", ["snapshot", "configure", "start_exception", "start_rejected"])
def test_failed_start_cannot_become_accepted_or_verified(start_job, monkeypatch, failure):
    root, fl = start_job
    if failure == "snapshot":
        fl.save_project_as.side_effect = RuntimeError("Snapshot failed")
    elif failure == "configure":
        monkeypatch.setattr(rw, "configure", AsyncMock(side_effect=RuntimeError("Readback failed")))
    elif failure == "start_exception":
        monkeypatch.setattr(rw, "_click", AsyncMock(side_effect=RuntimeError("Delivery uncertain")))
    else:
        monkeypatch.setattr(rw, "_click", AsyncMock(return_value={"success": False}))
    result = asyncio.run(rw.render_start(fl, root, "current", apply=True))
    assert result["success"] is False and result["verified"] is False
    assert result["status"] == "needs_inspection" and result["error"]
    assert "started_at" not in result
    assert json.loads((root / "render-job.json").read_text()) == result
    assert rw._click.await_count == (0 if failure in ("snapshot", "configure") else 1)


def test_growing_output_persists_successful_pending_verification(job, monkeypatch):
    root, _, fl = job
    output = root / "render.wav"
    output.write_bytes(b"partial")

    async def continue_writing(_):
        output.write_bytes(b"still rendering")

    monkeypatch.setattr(rw.asyncio, "sleep", continue_writing)
    inspect = AsyncMock(side_effect=AssertionError("Must not analyze growing output"))
    monkeypatch.setattr(rw, "inspect_audio", inspect)
    result = asyncio.run(rw.render_status(fl, root))
    assert result["success"] is True and result["verified"] is False
    assert result["status"] == "verifying" and "audio" not in result
    assert json.loads((root / "render-job.json").read_text()) == result
    inspect.assert_not_called()


@pytest.mark.parametrize("identity", ["window", "dialog"])
def test_pending_job_identity_failure_is_not_success(job, monkeypatch, identity):
    root, _, fl = job
    if identity == "window":
        monkeypatch.setattr(rw, "_window_state", lambda handle: dict(pid=99))
    else:
        monkeypatch.setattr(rw, "windows", lambda **kw: [
            dict(handle="different", parent="1", **{"class": "TWAVRenderForm"}),
        ])
    result = asyncio.run(rw.render_status(fl, root))
    assert result["status"] == "needs_inspection"
    assert result["success"] is False and result["verified"] is False


@pytest.mark.parametrize("status", ["verified", "failed", "cancelled", "needs_inspection"])
def test_legacy_terminal_receipts_receive_explicit_verified_field(job, status):
    root, report, fl = job
    report.update(status=status, success=status == "verified")
    (root / "render-job.json").write_text(json.dumps(report))
    result = asyncio.run(rw.render_status(fl, root))
    assert result["success"] is (status == "verified")
    assert result["verified"] is (status == "verified")


def test_visible_cancelled_intent_stays_unsuccessful_and_unverified(job, monkeypatch):
    root, report, fl = job
    report.update(status="cancelling", success=True)
    (root / "render-job.json").write_text(json.dumps(report))
    monkeypatch.setattr(rw, "windows", lambda **kw: [
        dict(handle="2", parent="1", **{"class": "TWAVRenderForm"}),
    ])
    result = asyncio.run(rw.render_status(fl, root))
    assert result["status"] == "cancelling"
    assert result["success"] is False and result["verified"] is False
