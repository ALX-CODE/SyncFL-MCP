"""Window hit tests must distinguish child windows from owned popups."""

from types import SimpleNamespace

import pytest

from fl_studio_mcp.utils import native_windows


@pytest.mark.parametrize(
    "hit, parents, owners, expected",
    [
        (200, {}, {}, True),
        (201, {201: 200}, {}, True),
        (202, {202: 201, 201: 200}, {}, True),
        (300, {}, {}, False),
        (300, {}, {300: 200}, False),
        (301, {301: 300}, {300: 200}, False),
        (300, {201: 200}, {300: 201}, False),
        (None, {}, {}, False),
    ],
    ids=[
        "mixer",
        "child",
        "nested-child",
        "unrelated-window",
        "owned-popup",
        "owned-popup-child",
        "popup-owned-by-child",
        "no-window",
    ],
)
def test_point_belongs_to_uses_child_ancestry(monkeypatch, hit, parents, owners, expected):
    contexts = []

    def dpi_context(value):
        contexts.append(getattr(value, "value", value))
        return 99

    def window_from_point(point):
        assert (point.x, point.y) == (1487, 240)
        return hit

    def ancestor(hwnd, flag):
        assert flag == 1  # GA_PARENT excludes popup ownership.
        return parents.get(hwnd, 0)

    api = SimpleNamespace(
        SetThreadDpiAwarenessContext=dpi_context,
        WindowFromPoint=window_from_point,
        GetAncestor=ancestor,
        # Model GetParent's owner fallback so the old implementation fails.
        GetParent=lambda hwnd: parents.get(hwnd, owners.get(hwnd, 0)),
    )
    monkeypatch.setattr(native_windows, "api", lambda: (api, None))

    assert native_windows.point_belongs_to([1487, 240], "200") is expected
    assert contexts == [native_windows.w.HANDLE(-4).value, 99]


def test_point_belongs_to_restores_dpi_context_after_error(monkeypatch):
    contexts = []

    def dpi_context(value):
        contexts.append(getattr(value, "value", value))
        return 99

    def fail(point):
        raise RuntimeError("hit test failed")

    api = SimpleNamespace(
        SetThreadDpiAwarenessContext=dpi_context,
        WindowFromPoint=fail,
    )
    monkeypatch.setattr(native_windows, "api", lambda: (api, None))

    with pytest.raises(RuntimeError, match="hit test failed"):
        native_windows.point_belongs_to([1487, 240], "200")
    assert contexts == [native_windows.w.HANDLE(-4).value, 99]


def test_expose_fl_uses_nonactivating_no_move_no_resize_flags(monkeypatch):
    row = dict(handle="100", pid=123, title="lab.flp - FL Studio", enabled=True,
               bounds=[0, 0, 1000, 700], **{"class": "TFruityLoopsMainForm"})
    calls = []
    monkeypatch.setattr(native_windows, "windows", lambda: [row])
    monkeypatch.setattr(native_windows, "api", lambda: (SimpleNamespace(
        SetWindowPos=lambda *args: calls.append(args) or True), None))
    native_windows.expose_fl_window(row)
    assert calls == [(100, 0, 0, 0, 0, 0, 0x213)]


def test_expose_fl_refuses_changed_identity_before_touching_window(monkeypatch):
    monkeypatch.setattr(native_windows, "windows", lambda: [])
    monkeypatch.setattr(native_windows, "api", lambda: pytest.fail("No window write expected"))
    with pytest.raises(RuntimeError, match="changed"):
        native_windows.expose_fl_window(dict(handle="100", pid=123, title="lab.flp"))
