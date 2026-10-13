"""FL26 WAV rendering through inspected controls, with persistent job receipts.

No headless CLI or extra FL process: this uses FL's native Split mixer tracks,
preserving the project's full routing/sidechain graph while exporting.
"""

import asyncio
import json
import math
import re
import time
import uuid
from pathlib import Path

from .audio_files import file_hash, inspect_audio
from .native_windows import point_belongs_to, windows
from .project_lifecycle import _state, _stopped
from .project_windows import _window_state
from .protocol import atomic_json
from .screen_ocr import _ocr_file

# FL26.1.5, 100% display scaling. Coordinates are relative to the independently
# discovered TWAVRenderForm, never a desktop position or a remembered main HWND.
RECTS = dict(
    mode=(52, 55, 104, 24),
    tail=(209, 55, 115, 24),
    bit_depth=(97, 154, 80, 24),
    channels=(183, 154, 122, 24),
    resampling=(81, 298, 113, 27),
)
MENUS = dict(
    mode=(52, 80, 104, 46),
    tail=(209, 80, 115, 66),
    bit_depth=(97, 178, 80, 60),
    channels=(183, 178, 122, 90),
    resampling=(81, 325, 113, 226),
)
FLAGS = dict(
    hq_plugins=(21, 341),
    disable_max_polyphony=(197, 341),
    dithering=(21, 363),
    playlist_markers=(21, 420),
    loop_markers=(21, 444),
    note_markers=(21, 466),
    tempo_metadata=(21, 488),
    split_mixer_tracks=(197, 423),
    trim_pdc=(197, 444),
    insert_effects=(197, 466),
    master_effects=(197, 488),
    show_files=(17, 586),
    upload=(17, 519),
)
FORMATS = dict(
    wav=(17, 134), mp3=(71, 134), ogg=(123, 134), flac=(176, 134), midi=(229, 134), m4a=(282, 134)
)
CHOICES = dict(
    mode={"full_song": "Full song", "pattern": "Pattern"},
    tail={"cut": "Cut remainder", "wrap": "Wrap remainder", "leave": "Leave remainder"},
    bit_depth={"PCM_16": "16Bit int", "PCM_24": "24Bit int", "FLOAT": "32Bit float"},
    channels={
        "stereo": "Stereo",
        "mono": "Mono (merged)",
        "left": "Mono (left only)",
        "right": "Mono (right only)",
    },
    resampling={str(n): f"{n}-point sinc" for n in (16, 24, 32, 64, 128, 256, 512)},
)
DEFAULTS = dict(
    mode="full_song",
    tail="leave",
    bit_depth="FLOAT",
    channels="stereo",
    resampling="64",
    hq_plugins=True,
    disable_max_polyphony=True,
    dithering=False,
    playlist_markers=True,
    loop_markers=True,
    note_markers=False,
    tempo_metadata=True,
    split_mixer_tracks=False,
    trim_pdc=True,
    insert_effects=True,
    master_effects=True,
)


def settings(values):
    if not isinstance(values, dict) or set(values) - set(DEFAULTS):
        raise ValueError("Unknown render option; use the documented option names")
    result = {**DEFAULTS, **values}
    for key, choices in CHOICES.items():
        if result[key] not in choices:
            raise ValueError(f"{key} must be one of {list(choices)}")
    for key in set(DEFAULTS) - set(CHOICES):
        if type(result[key]) is not bool:
            raise ValueError(f"{key} must be boolean")
    if result["dithering"] and result["bit_depth"] != "PCM_16":
        raise ValueError("Dithering is available only with PCM_16")
    if result["split_mixer_tracks"] and not result["master_effects"]:
        raise ValueError("FL disables the Master FX option for split mixer rendering")
    return result


def _norm(text):
    return re.sub("[^a-z0-9]", "", text.lower())


def _render_dialog(fl, expected=None):
    main = _window_state(fl.handle)
    items = [
        w
        for w in windows(pid=main["pid"])
        if w["class"] == "TWAVRenderForm" and w["parent"] == str(fl.handle)
    ]
    if len(items) != 1:
        raise RuntimeError(f"Expected one owned render dialog; found {len(items)}")
    result = items[0]
    if result["dpi"] != 96 or result["bounds"][2:] != [433, 611]:
        raise RuntimeError("Render layout differs from the verified FL26 433x611 / 96 DPI profile")
    if expected is not None and (
        result["handle"] != expected["handle"]
        or result["title"] != expected["title"]
        or result["bounds"] != expected["bounds"]
    ):
        raise RuntimeError("Render dialog moved or changed identity; no further input sent")
    return result


async def _capture(fl, dialog, directory):
    from PIL import Image

    _render_dialog(fl, dialog)
    x, y, w, h = dialog["bounds"]
    if not all(
        point_belongs_to(p, dialog["handle"]) for p in ((x + 10, y + 10), (x + w - 10, y + h - 10))
    ):
        raise RuntimeError("Render dialog is obscured; no render input sent")
    path = directory / ("options-" + uuid.uuid4().hex + ".png")
    result = await fl.win(
        "screenshot_control",
        target="region",
        regionX=x,
        regionY=y,
        regionWidth=w,
        regionHeight=h,
        annotate=False,
        includeImage=False,
        imageFormat="png",
        outputMode="file",
        outputPath=str(path),
    )
    if (
        result.get("capture", {}).get("method") != "screen_copy"
        or result.get("width") != w
        or result.get("height") != h
    ):
        raise RuntimeError("Render capture dimensions/method are unverified")
    with Image.open(path) as original:
        pixels = original.convert("RGB")
    _render_dialog(fl, dialog)
    return pixels, path


async def _text(image, rect, directory):
    from PIL import Image, ImageOps

    x, y, w, h = rect
    crop = ImageOps.autocontrast(image.crop((x, y, x + w, y + h)).convert("L"))
    path = directory / ("ocr-" + uuid.uuid4().hex + ".png")
    crop.resize((w * 4, h * 4), Image.Resampling.LANCZOS).save(path)
    try:
        return await _ocr_file(path)
    finally:
        path.unlink(missing_ok=True)


async def _click(fl, dialog, point, operation_id=None, menu=None):
    _render_dialog(fl, dialog)
    x, y = point
    absolute = dialog["bounds"][0] + x, dialog["bounds"][1] + y
    target = dialog
    if menu is not None:
        current = [w for w in windows(pid=dialog["pid"]) if w["handle"] == menu["handle"]]
        if (
            current != [menu]
            or menu["class"] != "TQuickPopupMenuWindow"
            or menu["parent"] != str(fl.handle)
        ):
            raise RuntimeError("Render popup changed identity")
        target = menu
    if not point_belongs_to(absolute, target["handle"]):
        raise RuntimeError("Render control is obscured")
    return await fl.win(
        "ui_batch",
        windowHandle=dialog["handle"],
        stopOnError=True,
        operationId=operation_id or "fl-render-" + uuid.uuid4().hex,
        steps=json.dumps(
            [
                dict(
                    action="mouse",
                    mouseAction="click",
                    x=x,
                    y=y,
                    expectedProcessName="FL64",
                    expectedWindowTitle=dialog["title"],
                )
            ]
        ),
    )


async def _choose(fl, dialog, directory, key, value):
    label = CHOICES[key][value]
    image, _ = await _capture(fl, dialog, directory)
    old = await _text(image, RECTS[key], directory)
    if _norm(label) in _norm(old["text"]):
        return old["text"]
    x, y, w, h = RECTS[key]
    await _click(fl, dialog, (x + w // 2, y + h // 2))
    await asyncio.sleep(0.1)
    popups = [
        w
        for w in windows(pid=dialog["pid"])
        if w["class"] == "TQuickPopupMenuWindow" and w["parent"] == str(fl.handle)
    ]
    if len(popups) != 1:
        raise RuntimeError("Expected one owned render dropdown popup")
    image, _ = await _capture(fl, dialog, directory)
    menu = await _text(image, MENUS[key], directory)
    matches = [line for line in menu["lines"] if _norm(label) == _norm(line["text"])]
    if len(matches) != 1:
        raise RuntimeError(f"Cannot uniquely identify render menu item {label}: {menu['text']}")
    mx, my, mw, mh = matches[0]["bounds"]
    x, y, _, _ = MENUS[key]
    await _click(
        fl, dialog, (round(x + (mx + mw / 2) / 4), round(y + (my + mh / 2) / 4)), menu=popups[0]
    )
    image, _ = await _capture(fl, dialog, directory)
    after = await _text(image, RECTS[key], directory)
    if _norm(label) not in _norm(after["text"]):
        raise RuntimeError(f"Render {key} did not read back as {label}: {after['text']}")
    return after["text"]


def _rgb(image, point):
    x, y = point
    samples = [image.getpixel((x + dx, y + dy)) for dx in (-1, 0, 1) for dy in (-1, 0, 1)]
    return tuple(sum(p[c] for p in samples) / len(samples) for c in range(3))


def _flags(image):
    # Ctrl+R establishes the WAV export format. Use its illuminated center and
    # the MIDI radio's unlit center as palette references; reject low contrast.
    on, off = _rgb(image, FORMATS["wav"]), _rgb(image, FORMATS["midi"])
    distance = math.dist(on, off)
    if distance < 100 or sum(on) < sum(off) + 120:
        raise RuntimeError("Render checkbox palette is unverified; inspect the captured options")
    result = {}
    for key, point in {**FLAGS, **FORMATS}.items():
        pixel = _rgb(image, point)
        a, b = math.dist(pixel, on), math.dist(pixel, off)
        if min(a, b) > distance * 0.25 or abs(a - b) < distance * 0.4:
            result[key] = None  # Disabled/ambiguous is never interpreted as off.
        else:
            result[key] = a < b
    return result


async def configure(fl, dialog, directory, options):
    await fl.win("ui_snapshot", windowHandle=dialog["handle"], mode="auto")
    image, initial = await _capture(fl, dialog, directory)
    text = await _text(image, (0, 0, 433, 611), directory)
    for anchor in ("Project type", "Output format", "Quality", "Miscellaneous"):
        if _norm(anchor) not in _norm(text["text"]):
            raise RuntimeError("Render layout text anchors did not match")
    readbacks = {}
    # Bit depth first, so applicability of dither and output type is known.
    for key in ("bit_depth", "mode", "tail", "channels", "resampling"):
        readbacks[key] = await _choose(fl, dialog, directory, key, options[key])
    image, _ = await _capture(fl, dialog, directory)
    state = _flags(image)
    requested = {k: options[k] for k in FLAGS if k in options}
    requested.update(show_files=False, upload=False)
    if options["bit_depth"] != "PCM_16":
        requested.pop("dithering")  # Disabled by FL; the option has no audio effect.
    if options["split_mixer_tracks"]:
        requested.pop("master_effects")  # FL disables this for native split rendering.
    for key, desired in requested.items():
        if state[key] is None:
            raise RuntimeError(f"Render {key} checkbox is unreadable; not toggled")
        if state[key] != desired:
            await _click(fl, dialog, FLAGS[key])
    # FL can acknowledge the input queue before painting the changed checkbox.
    # Observe two agreeing frames; never repeat a toggle to force a readback.
    consecutive = 0
    for _ in range(10):
        image, final = await _capture(fl, dialog, directory)
        state = _flags(image)
        mismatches = {
            k: dict(expected=v, actual=state[k]) for k, v in requested.items() if state[k] != v
        }
        consecutive = consecutive + 1 if not mismatches else 0
        if consecutive == 2:
            break
        await asyncio.sleep(0.1)
    else:
        raise RuntimeError(
            f"Render checkbox readback mismatch: {mismatches}; Start was not pressed"
        )
    if any(state[k] is not (k == "wav") for k in FORMATS):
        raise RuntimeError("Expected WAV-only export; Start was not pressed")
    return dict(
        dropdowns=readbacks, flags=state, initial_image=str(initial), final_image=str(final)
    )


def _job(directory):
    directory = Path(directory).resolve(strict=True)
    report = json.loads((directory / "render-job.json").read_text(encoding="utf-8"))
    if report.get("schema") != "fl-render-job-v1" or Path(report["directory"]) != directory:
        raise ValueError("Not a matching FL render job directory")
    # Older receipts used success=False while rendering and omitted verified.
    # Normalize them without treating an accepted job as a completed artifact.
    _set_status(report, report["status"])
    return directory, report


def _set_status(report, status, **fields):
    """Separate successful async observation from verified output completion."""
    report.update(
        fields,
        status=status,
        success=status in ("preflighted", "rendering", "verifying", "verified"),
        verified=status == "verified",
    )


async def render_start(
    fl,
    directory,
    expected_revision,
    options=None,
    apply=False,
    expected_sample_rate=None,
    expected_min_seconds=None,
):
    options = settings(options or {})
    if type(apply) is not bool:
        raise ValueError("apply must be boolean")
    directory = Path(directory)
    if not directory.is_absolute() or directory.exists():
        raise ValueError("Render directory must be an absolute NEW directory")
    directory = directory.resolve()
    if expected_sample_rate is not None and (
        type(expected_sample_rate) is not int or not 8000 <= expected_sample_rate <= 384000
    ):
        raise ValueError("expected_sample_rate must be 8000..384000")
    if expected_min_seconds is not None and (
        type(expected_min_seconds) not in (int, float) or not 0 < expected_min_seconds <= 7200
    ):
        raise ValueError("expected_min_seconds must be >0 and <=7200")
    before = _state()
    _stopped(before)
    if before["revision"] != expected_revision:
        raise ValueError("Project revision changed; inspect again")
    await fl._project_guard()
    report = dict(
        schema="fl-render-job-v1",
        job_id=uuid.uuid4().hex,
        directory=str(directory),
        success=not apply,
        verified=False,
        status="preflighted",
        options=options,
        before=before,
        expected_sample_rate=expected_sample_rate,
        expected_min_seconds=expected_min_seconds,
        limitations=[
            "Sample rate comes from FL audio settings and is checked in output.",
            "Split mixer stems bypass Master FX; Master output includes them.",
            "FL render controls require the verified FL26/96 DPI layout.",
            "Global export preferences retain the selected options.",
        ],
    )
    if not apply:
        return report
    directory.mkdir()  # Exclusive reservation; no occupied audio paths can be overwritten.
    receipt = directory / "render-job.json"
    _set_status(report, "saving_snapshot")
    atomic_json(receipt, report)
    try:
        report["snapshot"] = await fl.save_project_as(directory / "project.flp")
        state = _state()
        _stopped(state)
        if state["context"] != before["context"] or state["dirty"]:
            raise RuntimeError("Project changed during snapshot save; render not started")
        report["main_handle"] = fl.handle
        report["main_pid"] = _window_state(fl.handle)["pid"]
        report["project_title"] = fl.project_title
        await fl._project_focus()
        await fl._save_project_dialog(directory / "render.wav", export_wav=True)
        deadline = time.monotonic() + 5
        while True:
            try:
                dialog = _render_dialog(fl)
                break
            except RuntimeError:
                if time.monotonic() > deadline:
                    raise
                await asyncio.sleep(0.1)
        if dialog["title"].replace("\ue409", "") != "Rendering to render.wav":
            raise RuntimeError("Render filename differs from requested destination; not started")
        report["dialog"] = dialog
        report["options_readback"] = await configure(fl, dialog, directory, options)
        _set_status(report, "starting", start_operation_id="render-start-" + report["job_id"])
        atomic_json(receipt, report)
        report["delivery"] = await _click(fl, dialog, (390, 587), report["start_operation_id"])
        if report["delivery"].get("success") is False:
            raise RuntimeError("Render Start delivery failed; do not replay without inspection")
        _set_status(report, "rendering", started_at=time.time())
    except Exception as exc:
        _set_status(report, "needs_inspection", error=str(exc))
    atomic_json(receipt, report)
    return report


async def render_status(fl, directory):
    directory, report = _job(directory)
    if report["status"] in ("verified", "failed", "cancelled", "needs_inspection"):
        return report
    if str(fl.handle) != str(report.get("main_handle")) or _window_state(fl.handle)[
        "pid"
    ] != report.get("main_pid"):
        _set_status(report, "needs_inspection", error="Render project/window identity changed")
    else:
        dialogs = [
            w
            for w in windows(pid=report["main_pid"])
            if w["class"] == "TWAVRenderForm" and w["parent"] == str(fl.handle)
        ]
        if dialogs:
            if len(dialogs) != 1 or dialogs[0]["handle"] != report["dialog"]["handle"]:
                _set_status(report, "needs_inspection", error="Render dialog identity changed")
            else:
                report["elapsed_seconds"] = time.time() - report.get("started_at", time.time())
                atomic_json(directory / "render-job.json", report)
                return report
        else:
            try:
                if report["status"] == "cancelling":
                    _set_status(
                        report, "cancelled",
                        partial_files=[str(p) for p in directory.glob("render*.wav")],
                    )
                    atomic_json(directory / "render-job.json", report)
                    return report
                if fl.project_title != report.get("project_title"):
                    raise RuntimeError("Render project title changed after dialog closure")
                await fl._project_guard()
                paths = sorted(directory.glob("render*.wav"))
                if not paths:
                    raise RuntimeError("Render dialog closed without producing WAV files")
                signatures = [(p.stat().st_size, p.stat().st_mtime_ns) for p in paths]
                await asyncio.sleep(0.25)
                if signatures != [(p.stat().st_size, p.stat().st_mtime_ns) for p in paths]:
                    _set_status(report, "verifying")
                    atomic_json(directory / "render-job.json", report)
                    return report
                audio = [inspect_audio(p) for p in paths]
                report["audio"] = audio
                opts = report["options"]
                for item in audio:
                    if item["subtype"] != opts["bit_depth"] or item["channels"] != (
                        2 if opts["channels"] == "stereo" else 1
                    ):
                        raise RuntimeError("Rendered format/channels differ from requested options")
                    if (
                        report["expected_sample_rate"] is not None
                        and item["sample_rate"] != report["expected_sample_rate"]
                    ):
                        raise RuntimeError("Rendered sample rate differs from expected rate")
                    if (
                        report["expected_min_seconds"] is not None
                        and item["duration_seconds"] + 0.01 < report["expected_min_seconds"]
                    ):
                        raise RuntimeError("Rendered audio is shorter than the expected minimum")
                if opts["split_mixer_tracks"]:
                    if len(audio) < 2 or (directory / "render.wav").exists():
                        raise RuntimeError("Split mixer output was not established")
                    if len({(a["frames"], a["sample_rate"]) for a in audio}) != 1:
                        raise RuntimeError("Stem duration/sample-rate alignment failed")
                elif [Path(a["path"]).name for a in audio] != ["render.wav"]:
                    raise RuntimeError("Expected exactly one master WAV")
                if all(a["silent"] for a in audio):
                    raise RuntimeError("Every rendered file is silent at the analysis threshold")
                if file_hash(directory / "project.flp") != report["snapshot"]["sha256"]:
                    raise RuntimeError("Saved render project changed during export")
                after = _state()
                if after["context"] != report["before"]["context"]:
                    raise RuntimeError("Live project identity changed during export")
                _set_status(report, "verified", after=after)
            except Exception as exc:
                _set_status(report, "failed", error=str(exc))
    atomic_json(directory / "render-job.json", report)
    return report


async def render_cancel(fl, directory):
    directory, report = _job(directory)
    if report["status"] != "rendering":
        raise ValueError("Only an observed running render can be cancelled")
    # While exporting, FL replaces the main title with "Rendering: x/y".
    # The saved main PID/HWND plus exact owned render dialog identify this job.
    if (
        str(fl.handle) != str(report["main_handle"])
        or _window_state(fl.handle)["pid"] != report["main_pid"]
    ):
        raise RuntimeError("Render project identity changed")
    if not any(
        w["class"] == "TWAVRenderForm" and w["parent"] == str(fl.handle)
        for w in windows(pid=report["main_pid"])
    ):
        return await render_status(fl, directory)  # Finished before cancellation; no input.
    dialog = _render_dialog(fl, report["dialog"])
    image, _ = await _capture(fl, dialog, directory)
    label = await _text(image, (357, 570, 69, 34), directory)
    if _norm(label["text"]) != "abort":
        raise RuntimeError("Exact Abort button was not observed; no input sent")
    _set_status(report, "cancelling")
    atomic_json(directory / "render-job.json", report)
    await _click(fl, dialog, (390, 587), "render-cancel-" + report["job_id"])
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if not any(w["handle"] == dialog["handle"] for w in windows(pid=report["main_pid"])):
            _set_status(
                report, "cancelled",
                partial_files=[str(p) for p in directory.glob("render*.wav")],
            )
            atomic_json(directory / "render-job.json", report)
            return report
        await asyncio.sleep(0.1)
    _set_status(report, "needs_inspection", error="Abort delivered; dialog closure unverified")
    atomic_json(directory / "render-job.json", report)
    return report
