import json
from types import SimpleNamespace

import pytest

from fl_studio_mcp.utils import mix_profiles


def test_trackspacer_kilohertz_display():
    from fl_studio_mcp.utils.parameter_values import display_number

    assert display_number("20.00k Hz", "Hz") == 20000
    with pytest.raises(ValueError):
        display_number("20.00k Hz", "ms")


@pytest.fixture
def host(tmp_path, monkeypatch):
    state = {"value": 0.5, "writes": 0}

    def send(action, params, timeout):
        if action == "transport.getStatus":
            return {"success": True}
        if action == "plugins.parameters":
            return {
                "success": True,
                "parameters": [
                    {
                        "name": "Gain",
                        "value": state["value"],
                        "display": "Known",
                    }
                ],
            }
        state["writes"] += 1
        state["value"] = params["changes"][0]["value"]
        return {"success": True}

    monkeypatch.setattr(
        mix_profiles,
        "get_connection",
        lambda: SimpleNamespace(_hardware_dir=tmp_path, send_command=send),
    )
    monkeypatch.setattr(mix_profiles.time, "sleep", lambda _: None)
    return state


def control(**changes):
    return dict(track=1, slot=0, parameter=0, plugin="Test", name="Gain", value=0.7) | changes


def test_preflight_rejects_later_bad_control_without_writes(host, tmp_path):
    with pytest.raises(ValueError):
        mix_profiles.apply_profile(
            {"controls": [control(), control(parameter=1, name="Wrong")]},
            True,
            tmp_path / "receipt.json",
        )
    assert host["writes"] == 0


def test_restore_recipe_precedes_write_and_file_is_not_overwritten(host, tmp_path):
    path = tmp_path / "receipt.json"
    result = mix_profiles.apply_profile({"controls": [control()]}, True, path)
    assert result["success"] and host["value"] == 0.7
    assert result["status"] == "completed"
    assert json.loads(path.read_text())["restore_profile"]["controls"][0]["value"] == 0.5
    with pytest.raises(FileExistsError):
        mix_profiles.apply_profile({"controls": [control(value=0.2)]}, True, path)
    assert host["writes"] == 1


def test_interruption_never_leaves_a_success_receipt(host, tmp_path, monkeypatch):
    path = tmp_path / "interrupted.json"

    def interrupted(_):
        raise KeyboardInterrupt

    monkeypatch.setattr(mix_profiles.time, "sleep", interrupted)
    with pytest.raises(KeyboardInterrupt):
        mix_profiles.apply_profile({"controls": [control()]}, True, path)
    receipt = json.loads(path.read_text())
    assert receipt["success"] is False
    assert receipt["status"] == "applying"
    assert receipt["restore_profile"]["controls"][0]["value"] == 0.5


def test_partial_failure_keeps_restore_and_actual_result(host, tmp_path):
    result = mix_profiles.apply_profile(
        {"controls": [control(display="Other")]}, True, tmp_path / "receipt.json"
    )
    assert not result["success"] and len(result["applied"]) == 1
    assert result["restore_profile"]["controls"][0]["value"] == 0.5
    assert host["value"] == 0.7  # A failed label check is not a claimed rollback.


@pytest.mark.parametrize(
    "controls",
    [
        [control(), control()],
        [control(value=float("nan"))],
        [control(value=1.1)],
        [control(target=12)],
    ],
)
def test_invalid_profile_never_writes(host, controls):
    with pytest.raises(ValueError):
        mix_profiles.apply_profile({"controls": controls})
    assert host["writes"] == 0
