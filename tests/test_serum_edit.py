import copy

import pytest

from fl_studio_mcp.utils.serum_presets import DATA, _apply, edit_preset, read_preset


def test_edit_preserves_other_dsp_and_source(tmp_path):
    source = DATA / "kick_long.SerumPreset"
    before_bytes = source.read_bytes()
    _, before = read_preset(source)
    result = edit_preset(source, tmp_path / "Edited.SerumPreset", {"amp_decay_ms": 2100})
    assert result["success"]
    _, after = read_preset(result["path"])
    expected = copy.deepcopy(before)
    expected["Env0"]["plainParams"]["kParamDecay"] = 2.1
    assert after == expected
    assert source.read_bytes() == before_bytes


@pytest.mark.parametrize(
    "change",
    [
        {"source": [1, 0]},
        {"destModuleID": 1},
        {"destModuleTypeString": "Filter"},
        {"destModuleParamName": "kParamVolume"},
    ],
)
def test_pitch_edit_rejects_different_modulation_route(change):
    _, body = read_preset(DATA / "kick.SerumPreset")
    body["ModSlot0"].update(change)
    with pytest.raises(ValueError, match="pitch envelope routing"):
        _apply(body, {"pitch_amount_percent": 10})


@pytest.mark.parametrize("settings", [None, [], {}, False])
def test_edit_rejects_invalid_settings(tmp_path, settings):
    with pytest.raises(ValueError, match="nonempty object"):
        edit_preset(DATA / "kick.SerumPreset", tmp_path / "Bad.SerumPreset", settings)
    assert not list(tmp_path.iterdir())
