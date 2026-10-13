"""FL-owned GUI fallback: observed targets, one dispatch, no musical success claim."""

from __future__ import annotations

import asyncio
import base64
import ctypes as c
import hashlib
import json
import math
import os
import struct
import tempfile
import time
import uuid
from ctypes import wintypes as w
from pathlib import Path
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, StrictBool, TypeAdapter, model_validator

from fl_studio_mcp.utils.interaction_policy import interaction_mode, require_desktop_input
from fl_studio_mcp.utils.midi_connection import get_connection
from fl_studio_mcp.utils.native_windows import api, point_belongs_to, windows
from fl_studio_mcp.utils.protocol import atomic_json, transaction_lock
from fl_studio_mcp.utils.screen_ocr import _ocr_file
from fl_studio_mcp.utils.windows_workflows import WindowsFL, _workflow_lock, flattened

OBSERVATION_TTL = 60.0
OCR_LIMITS = dict(lines=200, words=400, characters=16000)
_observations: dict[str, dict] = {}
Pixel = Annotated[int, Field(strict=True, ge=0, le=32767)]
ShortText = Annotated[str, Field(strict=True, min_length=1, max_length=4096)]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UISelector(_Model):
    """Exact observed attributes; no indexes, fuzzy matching or stale element ids."""

    name: ShortText | None = None
    control_type: ShortText | None = None
    automation_id: ShortText | None = None
    class_name: ShortText | None = None

    @model_validator(mode="after")
    def nonempty(self):
        if not any(self.model_dump().values()):
            raise ValueError("Select an exact observed UI attribute")
        return self


class KeyAction(_Model):
    kind: Literal["key"]
    # Deliberately bounded; no Windows/Alt shortcuts, clipboard or arbitrary chords.
    key: Literal["enter", "tab", "escape", "up", "down", "left", "right",
                 "home", "end", "pageup", "pagedown", "backspace", "delete",
                 "space", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10"]
    shift: StrictBool = False


class TypeAction(_Model):
    kind: Literal["type"]
    text: ShortText
    selector: UISelector | None = None
    clear_first: StrictBool = False

    @model_validator(mode="after")
    def plain_text(self):
        if any(ord(char) < 32 or ord(char) == 127 for char in self.text):
            raise ValueError("Text must be one line without control characters")
        return self


class ClickAction(_Model):
    kind: Literal["click"]
    selector: UISelector | None = None
    x: Pixel | None = None
    y: Pixel | None = None
    button: Literal["left", "right"] = "left"
    double: StrictBool = False

    @model_validator(mode="after")
    def target(self):
        if self.selector is not None:
            if self.x is not None or self.y is not None:
                raise ValueError("Choose selector or coordinates, not both")
            if self.button != "left":
                raise ValueError("Semantic clicks support only the left button")
        elif self.x is None or self.y is None:
            raise ValueError("Coordinate clicks need both x and y")
        if self.double and self.button != "left":
            raise ValueError("Double clicks support only the left button")
        return self


class DragAction(_Model):
    kind: Literal["drag"]
    x: Pixel
    y: Pixel
    end_x: Pixel
    end_y: Pixel


class ScrollAction(_Model):
    kind: Literal["scroll"]
    x: Pixel
    y: Pixel
    direction: Literal["up", "down", "left", "right"]
    amount: Annotated[int, Field(strict=True, ge=1, le=10)] = 1


UIAction = Annotated[Union[KeyAction, TypeAction, ClickAction, DragAction, ScrollAction],
                     Field(discriminator="kind")]
_action_adapter = TypeAdapter(UIAction)


def _focus_state():
    """Observe focus without activating a window or sending input."""
    user, _ = api()

    class GUIThreadInfo(c.Structure):
        _fields_ = [("cbSize", w.DWORD), ("flags", w.DWORD),
                    ("hwndActive", w.HWND), ("hwndFocus", w.HWND),
                    ("hwndCapture", w.HWND), ("hwndMenuOwner", w.HWND),
                    ("hwndMoveSize", w.HWND), ("hwndCaret", w.HWND), ("rcCaret", w.RECT)]

    user.GetForegroundWindow.argtypes, user.GetForegroundWindow.restype = [], w.HWND
    user.GetGUIThreadInfo.argtypes = [w.DWORD, c.POINTER(GUIThreadInfo)]
    user.GetGUIThreadInfo.restype = w.BOOL
    info = GUIThreadInfo(cbSize=c.sizeof(GUIThreadInfo))
    if not user.GetGUIThreadInfo(0, c.byref(info)):
        raise RuntimeError("Cannot observe the foreground input focus")
    return {"foreground": str(user.GetForegroundWindow() or 0),
            "focus": str(info.hwndFocus or 0)}


def _project(conn):
    result = conn.send_command("production.inspect", {"scope": "project"}, 5)
    if result.get("success") is not True:
        raise RuntimeError("Cannot establish FL project identity: " + str(result))
    context = result.get("context", {})
    if not context.get("session") or "project_generation" not in context:
        raise RuntimeError("Controller must expose project session and generation")
    return {key: result.get(key) for key in
            ("context", "values", "dirty", "is_playing", "is_recording", "controller_build")}


def _handle(value):
    if not isinstance(value, str) or not value.isdecimal() or int(value) <= 0:
        raise ValueError("window_handle must be an observed decimal HWND string")
    return str(int(value))


def _census(fl, target):
    top = windows()
    main = [item for item in top if str(item["handle"]) == str(fl.handle)
            and item["class"] == "TFruityLoopsMainForm"]
    if len(main) != 1:
        raise RuntimeError("FL main window no longer exists")
    pid = main[0]["pid"]
    owned = {str(item["handle"]): item for item in top if item["pid"] == pid}
    for item in list(owned.values()):
        for child in windows(parent=item["handle"], pid=pid):
            owned[str(child["handle"])] = child
    if target not in owned:
        raise ValueError("Target is not a visible window in the inspected FL process")
    current = owned[target]
    if len(current["bounds"]) != 4 or min(current["bounds"][2:]) <= 0:
        raise RuntimeError("Target window has no visible geometry")
    return {"main": main[0], "target": current,
            "windows": sorted(owned.values(), key=lambda item: int(item["handle"]))}


def _stable_tree(value):
    if isinstance(value, list):
        return [_stable_tree(item) for item in value]
    if isinstance(value, dict):
        # Windows MCP starts a private helper per call. UIA ids are helper-local.
        return {key: _stable_tree(item) for key, item in value.items() if key != "id"}
    return value


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def _image_digest(images):
    # Compare the exact lossless image payload. No crop geometry or pixel tolerance
    # is guessed; animated owner-drawn areas may conservatively invalidate it.
    return _digest([[image.mimeType, image.data] for image in images]) if images else None


async def _capture(fl, target):
    response = await fl.client.call_tool("screenshot_control", dict(
        action="capture", target="window", windowHandle=target, annotate=False,
        includeImage=True, includeCursor=False, outputMode="inline", imageFormat="png",
        timeoutMs=5000,
    ), raise_on_error=False)
    images = [block for block in response.content if block.type == "image"]
    if response.is_error or not images:
        raise RuntimeError("Windows MCP did not return a window screenshot image")
    return images


def _ocr_bounds(value, size):
    if (not isinstance(value, list) or len(value) != 4
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in value)):
        raise RuntimeError("OCR returned invalid text bounds")
    x, y, width, height = value
    if (x < 0 or y < 0 or width <= 0 or height <= 0
            or x + width > size[0] or y + height > size[1]):
        raise RuntimeError("OCR text bounds fall outside the captured window")
    return value


def _ocr_report(raw, size):
    if (not isinstance(raw, dict) or raw.get("success") is not True
            or type(raw.get("width")) is not int or type(raw.get("height")) is not int
            or [raw["width"], raw["height"]] != size
            or not isinstance(raw.get("text"), str)
            or not isinstance(raw.get("lines"), list)):
        raise RuntimeError("OCR result/dimensions do not match the captured window")
    validated = []
    total_words = 0
    for line in raw["lines"]:
        if (not isinstance(line, dict) or not isinstance(line.get("text"), str)
                or not isinstance(line.get("words"), list)):
            raise RuntimeError("OCR returned an invalid text line")
        bounds = _ocr_bounds(line.get("bounds"), size)
        words = []
        for word in line["words"]:
            if not isinstance(word, dict) or not isinstance(word.get("text"), str):
                raise RuntimeError("OCR returned an invalid word")
            words.append(dict(text=word["text"], bounds=_ocr_bounds(word.get("bounds"), size)))
        validated.append(dict(text=line["text"], bounds=bounds, words=words))
        total_words += len(words)
    included, word_count, character_count = [], 0, 0
    for line in validated:
        characters = len(line["text"]) + sum(len(word["text"]) for word in line["words"])
        if (len(included) >= OCR_LIMITS["lines"]
                or word_count + len(line["words"]) > OCR_LIMITS["words"]
                or character_count + characters > OCR_LIMITS["characters"]):
            break
        included.append(line)
        word_count += len(line["words"])
        character_count += characters
    return dict(
        success=True, engine="Windows.Media.Ocr", coordinate_space="window_relative",
        image_size=size, bounds=[0, 0, *size], text="\n".join(line["text"] for line in included),
        lines=included, truncated=len(included) != len(validated),
        total_lines=len(validated), total_words=total_words, included_words=word_count,
        limits=dict(OCR_LIMITS), semantic_target_verified=False,
    )


async def _recognize(images, target):
    if len(images) != 1 or images[0].mimeType != "image/png":
        raise RuntimeError("OCR requires one lossless PNG window screenshot")
    encoded = images[0].data
    if len(encoded) > 48 * 1024 * 1024:
        raise RuntimeError("OCR screenshot exceeds the bounded image payload")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as exc:
        raise RuntimeError("OCR screenshot is not valid base64 PNG data") from exc
    if (len(data) < 33 or data[:8] != b"\x89PNG\r\n\x1a\n"
            or data[8:16] != b"\x00\x00\x00\rIHDR"):
        raise RuntimeError("OCR screenshot does not contain a PNG image header")
    size = list(struct.unpack(">II", data[16:24]))
    if size != target["bounds"][2:] or min(size) < 1 or size[0] * size[1] > 40_000_000:
        raise RuntimeError("OCR screenshot dimensions do not match target window bounds")
    # OCR this exact capture, without another desktop read, image transform or model.
    with tempfile.TemporaryDirectory(prefix="fl-ui-ocr-") as directory:
        path = Path(directory) / "window.png"
        path.write_bytes(data)
        raw = await _ocr_file(path)
    return _ocr_report(raw, size)


async def _observe(fl, conn, target, depth, include_screenshot=False, include_ocr=False):
    before = _project(conn)
    census = _census(fl, target)
    focus = _focus_state()
    snapshot = await fl.win("ui_snapshot", windowHandle=target, mode="auto",
                            maxDepth=depth, timeoutMs=10000)
    if snapshot.get("kind") != "full" or "tree" not in snapshot:
        snapshot = await fl.win("ui_snapshot", windowHandle=target, mode="full",
                                maxDepth=depth, timeoutMs=10000)
    if not isinstance(snapshot.get("tree"), list):
        raise RuntimeError("Windows MCP did not return a complete UIA tree")
    captured = await _capture(fl, target) if include_screenshot or include_ocr else []
    ocr = await _recognize(captured, census["target"]) if include_ocr else None
    images = captured if include_screenshot else []
    if before != _project(conn) or census != _census(fl, target) or focus != _focus_state():
        raise RuntimeError("FL changed during observation; inspect again")
    state = {"project": before, **census, "focus": focus,
             "tree": _stable_tree(snapshot["tree"]), "max_depth": depth}
    report = {"schema": "fl-ui-observation-v1", "success": True, "status": "observed",
              "project": before, **census, "focus": focus, "snapshot": snapshot,
              "screenshot_included": bool(images), "max_depth": depth,
              "ocr_included": include_ocr,
              "screenshot_sha256": _image_digest(images),
              "limitations": ["Owner-drawn controls may be absent from UI Automation.",
                              "Coordinate edits require an unchanged screenshot; animations "
                              "can conservatively prevent an edit.",
                              "A screenshot is visual evidence, not parameter/audio readback.",
                              "OCR recognizes visible text only, not a semantic control or "
                              "musical postcondition. OCR alone does not enable coordinate input."]}
    if ocr is not None:
        report["ocr"] = ocr
    return report, images, _digest(state)


async def inspect_ui(window_handle=None, include_screenshot=False, max_depth=6, connection=None,
                     *, include_ocr=False):
    if (type(include_screenshot) is not bool or type(include_ocr) is not bool
            or type(max_depth) is not int
            or not 1 <= max_depth <= 12):
        raise ValueError("Use boolean screenshot/OCR options and integer max_depth in 1..12")
    target = _handle(window_handle) if window_handle is not None else None
    conn = connection if connection is not None else get_connection()
    async with _workflow_lock:
        with transaction_lock(conn._hardware_dir / "mcp-gui.lock", 5):
            async with WindowsFL(read_only=True) as fl:
                target = target or _handle(str(fl.handle))
                report, images, digest = await _observe(fl, conn, target, max_depth,
                                                       include_screenshot, include_ocr)
                now = time.monotonic()
                for key, value in list(_observations.items()):
                    if now - value["created"] >= OBSERVATION_TTL:
                        del _observations[key]
                while len(_observations) >= 64:
                    del _observations[next(iter(_observations))]
                revision = _digest([digest, uuid.uuid4().hex])
                _observations[revision] = dict(created=now, digest=digest, target=target,
                                               depth=max_depth, screenshot=bool(images),
                                               image_digest=report["screenshot_sha256"])
                report.update(revision=revision, expires_in_seconds=OBSERVATION_TTL,
                              coordinate_space="physical pixels relative to target window top-left")
                return report, images


def _point(point, target):
    left, top, width, height = target["bounds"]
    x, y = point
    if not 0 <= x < width or not 0 <= y < height:
        raise ValueError("Coordinate is outside the observed target window")
    if not point_belongs_to((left + x, top + y), target["handle"]):
        raise RuntimeError("Target point is occluded or belongs to another window")


def _select(tree, selector):
    attributes = {"name": "name", "control_type": "type",
                  "automation_id": "automationId", "class_name": "className"}
    # FL exposes duplicate display-only labels beside actionable panes. Only
    # enabled, identified nodes with a click point participate in ambiguity checks.
    matches = [item for item in flattened(tree)
               if item.get("enabled") is True and item.get("id") and item.get("click") and all(
        str(item.get(attributes[key], "")).casefold() == value.casefold()
        for key, value in selector.items())]
    if len(matches) != 1:
        raise ValueError(
            f"Expected exactly one actionable observed UI element; found {len(matches)}")
    return matches[0]


def _dispatch(report, action, visual):
    target, focus = report["target"], report["focus"]
    handle = str(target["handle"])
    if target.get("enabled") is not True:
        raise ValueError("Target window is disabled, likely by a modal dialog")
    if (report["project"]["is_playing"] is not False
            or report["project"]["is_recording"] is not False):
        raise ValueError("Stop playback and recording before GUI edits")
    kind, selector = action["kind"], action.get("selector")
    if selector is not None:
        node = _select(report["snapshot"]["tree"], selector)
        left, top = target["bounds"][:2]
        _point((node["click"][0] - left, node["click"][1] - top), target)
        params = dict(windowHandle=handle, elementId=node["id"])
        if kind == "type":
            return "ui_type", {**params, "text": action["text"],
                               "clearFirst": action["clear_first"]}
        return "ui_click", {**params, "doubleClick": action["double"]}
    if kind in ("key", "type"):
        # Input to a HWND still goes to its focused descendant. Observe that exact
        # descendant instead of assuming the requested window owns keyboard focus.
        by_handle = {str(item["handle"]): item for item in report["windows"]}
        current, seen = focus["focus"], set()
        while current != handle and current in by_handle and current not in seen:
            seen.add(current)
            current = str(by_handle[current]["parent"])
        if current != handle or focus["foreground"] not in by_handle:
            raise ValueError("The target does not own the observed keyboard focus")
        if kind == "key":
            return "keyboard_control", dict(windowHandle=handle, action="press",
                                              key=action["key"],
                                              modifiers="shift" if action["shift"] else None)
        return "keyboard_control", dict(windowHandle=handle, action="type",
                                          text=action["text"], clearFirst=action["clear_first"])
    if not visual:
        raise ValueError("Coordinate actions require an inspection with include_screenshot=true")
    point = action["x"], action["y"]
    _point(point, target)
    if target["class"] == "TQuickPopupMenuWindow":
        # Activating FL's popup dismisses it. Dispatch against its already
        # foreground owner while retaining the observed popup hit-test guard.
        main = report["main"]
        if (kind != "click" or action["button"] != "left" or action["double"]
                or str(target["parent"]) != str(main["handle"])
                or focus["foreground"] != str(main["handle"])):
            raise ValueError("FL popup requires one left click and its foreground main owner")
        return "ui_batch", dict(
            windowHandle=str(main["handle"]), stopOnError=True,
            operationId="fl-popup-" + uuid.uuid4().hex,
            steps=json.dumps([dict(
                action="mouse", mouseAction="click",
                x=target["bounds"][0] + point[0] - main["bounds"][0],
                y=target["bounds"][1] + point[1] - main["bounds"][1],
                expectedProcessName="FL64", expectedWindowTitle=main["title"],
            )]),
        )
    params = dict(windowHandle=handle, expectedProcessName="FL64", x=point[0], y=point[1])
    if kind == "drag":
        end = action["end_x"], action["end_y"]
        # Validate every physical pixel along the straight stroke, including its end.
        extent = max(abs(end[0] - point[0]), abs(end[1] - point[1]), 1)
        for index in range(1, extent + 1):
            _point((round(point[0] + (end[0] - point[0]) * index / extent),
                    round(point[1] + (end[1] - point[1]) * index / extent)), target)
        return "mouse_control", dict(params, action="drag", endX=end[0], endY=end[1])
    if kind == "scroll":
        return "mouse_control", dict(params, action="scroll", direction=action["direction"],
                                       amount=action["amount"])
    click = "double_click" if action["double"] else (
        "right_click" if action["button"] == "right" else "click")
    return "mouse_control", dict(params, action=click)


async def action_ui(window_handle, expected_revision, action, verification_plan, apply=False,
                    receipt_file=None, connection=None):
    """A valid dispatch is only unverified; caller must independently check its postcondition."""
    target = _handle(window_handle)
    action = _action_adapter.validate_python(action).model_dump(exclude_none=True)
    if type(apply) is not bool:
        raise ValueError("apply must be boolean")
    if not isinstance(verification_plan, str) or not 8 <= len(verification_plan.strip()) <= 2048:
        raise ValueError("verification_plan must describe an independent readback or audio check")
    if not isinstance(expected_revision, str) or len(expected_revision) != 64:
        raise ValueError("Use a fresh revision from fl_ui_inspect")
    path = Path(receipt_file) if receipt_file else None
    if apply and (path is None or not path.is_absolute() or path.suffix.lower() != ".json"):
        raise ValueError("Apply requires a new absolute .json receipt_file")
    if apply:
        require_desktop_input("fl_ui_action apply")
    conn = connection if connection is not None else get_connection()
    async with _workflow_lock:
        with transaction_lock(conn._hardware_dir / "mcp-gui.lock", 5):
            saved = _observations.get(expected_revision)
            if (saved is None or time.monotonic() - saved["created"] >= OBSERVATION_TTL
                    or saved["target"] != target):
                raise ValueError("Unknown, expired, consumed or foreign observation; inspect again")
            coordinate_action = action["kind"] in ("click", "drag", "scroll") and not action.get(
                "selector")
            if coordinate_action and not saved["screenshot"]:
                raise ValueError(
                    "Coordinate actions need an inspection with include_screenshot=true")
            async with WindowsFL(read_only=not apply) as fl:
                before, _, digest = await _observe(fl, conn, target, saved["depth"],
                                                   include_screenshot=coordinate_action)
                if digest != saved["digest"]:
                    raise ValueError("FL project, focus, windows or UI changed; inspect again")
                if coordinate_action and before["screenshot_sha256"] != saved["image_digest"]:
                    raise ValueError(
                        "Target screenshot changed (possibly animation); inspect again")
                if time.monotonic() - saved["created"] >= OBSERVATION_TTL:
                    raise ValueError("Observation expired during preflight; inspect again")
                tool, params = _dispatch(before, action, saved["screenshot"])
                report = dict(schema="fl-ui-action-v1", success=not apply, status="preflighted",
                              success_scope="preflight",
                              operation_id=uuid.uuid4().hex, target=before["target"],
                              project=before["project"], expected_revision=expected_revision,
                              action=action, verification_plan=verification_plan,
                              screenshot_sha256=before["screenshot_sha256"],
                              requires_desktop_input=True,
                              would_be_blocked=interaction_mode() == "background",
                              verified=False, musical_state_verified=False,
                              recovery="Inspect fresh state and verify the postcondition. "
                                       "Never automatically replay an uncertain action.")
                if not apply:
                    return report
                report.update(success=False, success_scope=None, status="dispatching",
                              receipt_file=str(path))
                with path.open("x", encoding="utf-8") as stream:
                    json.dump(report, stream, indent=2, allow_nan=False)
                    stream.flush()
                    os.fsync(stream.fileno())
                # One use even if the helper times out or reports failure after input.
                del _observations[expected_revision]
                try:
                    report["dispatch_response"] = await fl.win(tool, **params)
                    if report["dispatch_response"].get("success") is True:
                        report.update(success=True, success_scope="input_delivery",
                                      status="dispatched_unverified")
                    else:
                        report.update(status="unknown",
                                      error="Helper did not confirm input delivery")
                except (Exception, asyncio.CancelledError) as exc:
                    report.update(status="unknown", error=str(exc) or type(exc).__name__)
                    if isinstance(exc, asyncio.CancelledError):
                        atomic_json(path, report)
                        raise
                try:
                    atomic_json(path, report)
                except Exception as exc:
                    report.update(success=False, success_scope=None, status="unknown",
                                  receipt_persistence_error=str(exc))
                return report
