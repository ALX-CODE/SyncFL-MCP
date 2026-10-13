import asyncio
import copy
import struct
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from test_project_bundle import event, project, text

from fl_studio_mcp.utils import automation_windows as aw
from fl_studio_mcp.utils.audio_files import file_hash


def linked_project(name="Kick - Channel volume"):
    points = (
        b"\0" * 17
        + struct.pack("<I", 2)
        + struct.pack("<ddfI", 0, 0.5, 0, 0)
        + struct.pack("<ddfI", 4, 0.5, 0, 0)
    )
    return project(
        event(227, struct.pack("<5I", 2 << 16, 0, 65536, 8, 469))
        + event(64, struct.pack("<H", 2))
        + text(203, name)
        + event(234, points)
    )


def test_saved_link_inspection_exposes_channel_event_and_opaque_bytes(tmp_path):
    p = tmp_path / "linked.flp"
    p.write_bytes(linked_project())
    link = aw.saved_links(p)[0]
    assert link["channel"] == 2 and link["controller_index"] == 0 and link["event_id"] == 65536
    assert len(bytes.fromhex(link["raw"])) == 20


@pytest.fixture
def rig(tmp_path, monkeypatch):
    channels = [dict(index=0, name="Sampler", type=0), dict(index=1, name="Kick", type=0)]
    state = dict(
        revision="fresh",
        context=dict(session="s", project_generation=1, channels=channels),
        dirty=0,
        is_playing=False,
        is_recording=False,
    )
    created = False
    ticks = 0
    existing_link = False

    def read():
        nonlocal ticks
        result = copy.deepcopy(state)
        if created:
            ticks += 1
            if ticks == 1:
                raise RuntimeError("FL reports unsafe to edit (modal operation or busy)")
            result["context"]["channels"].append(
                dict(index=2, type=5, name="Automation" if ticks == 2 else "Kick - Channel volume")
            )
        return result

    def call(function, *args, **kwargs):
        if function == "channels.getRecEventId":
            assert args == (1, True)
            return 65536
        if function == "channels.channelCount":
            return 2
        if function == "channels.getChannelIndex":
            return args[0]
        return None

    class FL:
        handle = "1"
        project_title = "before.flp - FL Studio"
        _project_guard = AsyncMock()
        inputs = []

        async def save_project_as(self, path):
            path = Path(path)
            path.write_bytes(linked_project() if created or existing_link else project())
            return dict(success=True, path=str(path), sha256=file_hash(path))

        async def win(self, name, **kwargs):
            nonlocal created
            self.inputs.append((name, kwargs))
            if name == "ui_batch":
                created = True
            return dict(success=True)

    rack = dict(handle="2", pid=42, dpi=96, bounds=[100, 100, 500, 200])
    menu = dict(
        handle="3",
        pid=42,
        parent="1",
        bounds=[10, 10, 100, 100],
        **{"class": "TQuickPopupMenuWindow"},
    )
    monkeypatch.setattr(aw, "_state", read)
    monkeypatch.setattr(aw, "call", call)
    monkeypatch.setattr(aw.StudioLoading, "guard", AsyncMock())
    monkeypatch.setattr(aw, "unique_control", lambda *a: rack)
    monkeypatch.setattr(aw, "channel_drop_target", lambda *a: (300, 200))
    monkeypatch.setattr(aw, "point_belongs_to", lambda *a: True)
    monkeypatch.setattr(
        aw,
        "windows",
        lambda *a, **kw: [
            menu,
            dict(handle="1", bounds=[0, 0, 1920, 1080], **{"class": "TFruityLoopsMainForm"}),
        ],
    )
    monkeypatch.setattr(
        aw,
        "read_region",
        AsyncMock(
            return_value={"lines": [dict(text="Create automation clip", bounds=[20, 30, 60, 10])]}
        ),
    )
    args = dict(
        channel=1,
        expected_name="Kick",
        parameter="volume",
        expected_revision="fresh",
        directory=str(tmp_path / "job"),
        apply=True,
    )
    return FL(), args


def test_creation_waits_through_busy_and_default_name_without_replaying(rig):
    fl, args = rig
    report = asyncio.run(aw.create_channel_automation(fl, **args))
    assert report["success"] and report["status"] == "verified"
    assert len(report["readbacks"]) == 4
    assert sum(name == "ui_batch" for name, _ in fl.inputs) == 1
    assert report["link"]["event_id"] == 65536


@pytest.mark.parametrize(
    "changes",
    [
        dict(expected_name="Renamed"),
        dict(expected_revision="old"),
        dict(parameter="pitch"),
        dict(channel=True),
    ],
)
def test_identity_preflight_never_creates_files_or_ui_input(rig, changes):
    fl, args = rig
    args.update(changes)
    with pytest.raises(ValueError):
        asyncio.run(aw.create_channel_automation(fl, **args))
    assert not Path(args["directory"]).exists() and not fl.inputs
