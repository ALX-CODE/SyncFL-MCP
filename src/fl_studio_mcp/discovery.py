"""Bounded discovery in front of the guarded server; never execute through its lease twice."""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated, Any

from fastmcp import FastMCP
from fastmcp.server import create_proxy
from fastmcp.server.context import Context
from fastmcp.server.transforms import GetToolNext
from fastmcp.server.transforms.catalog import CatalogTransform
from fastmcp.tools import Tool, ToolResult
from fastmcp.utilities.versions import VersionSpec
from pydantic import Field

from fl_studio_mcp.utils.operator_contract import (
    DESKTOP_INPUT_TOOLS,
    OFFLINE_TOOLS,
    READ_TOOLS,
    WORKFLOWS,
)

CATALOG = json.loads(Path(__file__).with_name("discovery_catalog.json").read_text("utf-8"))
PINNED = frozenset({"fl_build_info", "fl_session_inspect"})
DISCOVERY_INSTRUCTIONS = """
SyncFL exposes six entry tools and discovers the full FL Studio toolset on demand.
Start with fl_build_info and, for live work, fl_session_inspect. Use fl_discover for
the musical task, fl_workflows for supported routes, then fl_describe for the exact
selected contracts. Execute via fl_call using the original tool name and arguments.
Discovery is guidance, never authorization. The underlying guards remain authoritative.
Prefer native/backend workflows in both input modes. UI operations require interactive
permission. Reinspect exact target identities/revisions before writes; never reuse stale
state after a mutation. Read filtered parameter pages instead of whole catalogs.
After a timeout, partial result or unknown outcome, inspect the receipt and fresh state;
never automatically replay or fall back to UI. Keep originals and recovery evidence.
Normalized plugin values are not physical units; use qualified named controls for ms,
Hz and dB. Job start is not completion: poll its status. Host readback is not proof of
audio quality. fl_call preserves complete results, including errors and uncertainty.
"""

# Musical language chooses a *workflow*, not a raw setter. Its companion tools
# travel together so that a start/write does not hide its inspection/status path.
# Unknown vocabulary still falls back to BM25 over the current exact contracts.
ROUTES = {
    "background_piano": (
        "melody|notes|note|piano|piano roll|chord|score|arpeggio|compose",
        "fl_background_piano_inspect fl_background_piano_write fl_background_piano_select "
        "fl_piano_roll_prepare fl_send_notes",
    ),
    "tempo_analysis": ("bpm|tempo|beat detection|half time|double time", "fl_audio_bpm"),
    "key_analysis": ("key|tonality|scale|tonic|major|minor", "fl_audio_key"),
    "native_mixer_controls": (
        "built in eq|mixer eq|polarity|phase invert|swap channels|stereo swap",
        "fl_mixer_controls_inspect fl_mixer_controls_set",
    ),
    "session": (
        "connect|connection|session|controller|handshake|offline|health",
        "fl_session_inspect fl_connection_status fl_connect",
    ),
    "composition": (
        "step sequence|drum pattern|step sequencer|pattern bank|composewithllm|legacy piano",
        "fl_production_inspect fl_step_sequence_edit fl_piano_roll_prepare fl_send_notes",
    ),
    "midi_references": (
        "midi reference|midi file|transpose|quantize|humanize|reference melody",
        "fl_midi_reference_search fl_notes_transform",
    ),
    "assets_and_plugins": (
        "load sample|load instrument|load preset|find sample|sample library|plugin database",
        "fl_find_assets fl_instrument_add fl_sample_add fl_preset_load",
    ),
    "background_effect_loading": (
        "load effect|add effect|effect chain|empty slot|background effect",
        "fl_background_effect_inspect fl_background_effect_load fl_effect_add fl_plugin_chain",
    ),
    "mix_balance": (
        "volume|fader|balance|turn down|turn up|louder|quieter|lower|backing vocals|gain|db|pan",
        "fl_production_inspect fl_mixer_scene fl_mixer_meter_watch",
    ),
    "buses_sends_sidechains": (
        "sidechain|routing|route|bus|buses|send|sends|ducking",
        "fl_routing_inspect fl_routing_scene",
    ),
    "plugin_parameters": (
        "plugin|compressor|attack|release|threshold|ratio|hz|milliseconds|ms|cutoff|pro c|pro q|"
        "calibrated|named controls|physical|compression|equalizer",
        "fl_plugin_controls_inspect fl_plugin_controls_set fl_plugin_inspect fl_plugin_apply",
    ),
    "sound_design": (
        "serum|synth|oscillator|unison|wavetable|sound design|envelope",
        "fl_serum_inspect fl_serum_apply fl_serum_recipes fl_serum_probe",
    ),
    "arrangement": (
        "arrange|arrangement|playlist|clip|clips|place audio|timeline",
        "fl_playlist_file_inspect fl_playlist_file_edit fl_audio_clip_add",
    ),
    "automation": (
        "automation|automate|curve|curves|retarget|parameter link",
        "fl_automation_target_inspect fl_automation_links_inspect fl_automation_file_inspect "
        "fl_automation_file_edit fl_automation_file_retarget",
    ),
    "recording_and_comping": (
        "record|recording|microphone|mic|vocal take|arm|capture input",
        "fl_recording_inspect fl_recording_start fl_recording_status fl_recording_stop "
        "fl_recording_configure_file",
    ),
    "sample_editing": (
        "trim|reverse|fade|edit sample|edit audio|cut audio",
        "fl_audio_inspect fl_audio_edit",
    ),
    "render_and_stems": (
        "export|render|stems|bounce|mixdown",
        "fl_render_start fl_render_status fl_render_cancel",
    ),
    "mix_measurement": (
        "loudness|lufs|true peak|clipping|compare reference|mix diagnosis|stereo width",
        "fl_audio_inspect fl_audio_loudness fl_audio_compare fl_mix_diagnose",
    ),
    "save_and_delivery": (
        "save|save as|backup|package|deliver|project copy|archive project",
        "fl_project_save fl_project_native_inspect fl_project_save_native "
        "fl_project_save_as fl_project_package",
    ),
    "native_project_save": (
        "native save|background save|clean state",
        "fl_project_native_inspect fl_project_save_native",
    ),
    "ui_fallback": ("ui|dialog|window|screenshot|click|ocr", "fl_ui_inspect fl_ui_action"),
}

_STOP_WORDS = frozenset(
    "a an and are at be by for from i in is it of on or the this to with".split()
)


def _tokens(text: str) -> list[str]:
    return [token for token in re.findall(r"[a-z0-9]+", text.lower())
            if token not in _STOP_WORDS]


def _bm25(tools: Sequence[Tool], query: str) -> list[str]:
    """Small dependency-free fallback; rebuild from the authorized request catalog."""
    documents = [Counter(_tokens(" ".join((
        tool.name, tool.description or "", json.dumps(tool.parameters),
        CATALOG["tools"].get(tool.name, {}).get("purpose", ""),
    )))) for tool in tools]
    if not documents:
        return []
    lengths = [sum(doc.values()) for doc in documents]
    average = sum(lengths) / len(lengths) or 1
    frequency = Counter(token for doc in documents for token in doc)
    scores = [0.0] * len(tools)
    for token in set(_tokens(query)):
        inverse = math.log(1 + (len(tools) - frequency[token] + 0.5)
                           / (frequency[token] + 0.5))
        for index, doc in enumerate(documents):
            count = doc[token]
            scores[index] += inverse * count * 2.5 / (
                count + 1.5 * (0.25 + 0.75 * lengths[index] / average)
            )
    return [tools[i].name for i in sorted(range(len(tools)), key=lambda i: -scores[i])
            if scores[i] > 0]


def _workflow_tools(workflow: dict) -> list[str]:
    return list(dict.fromkeys(ROUTES[workflow["id"]][1].split() + workflow["tools"]))


def related_workflows(name: str) -> list[dict]:
    return [workflow for workflow in WORKFLOWS if name in _workflow_tools(workflow)]


def _summary(tool: Tool, available: set[str], reason: str) -> dict:
    metadata = CATALOG["tools"].get(tool.name, {})
    return {
        "name": tool.name,
        "domain": metadata.get("domain", "advanced"),
        "purpose": metadata.get("purpose", (tool.description or "").split("\n")[0]),
        "reason": reason,
        "policy": {
            "read_only_allowlisted": tool.name in READ_TOOLS,
            "offline": tool.name in OFFLINE_TOOLS,
            "explicit_desktop_input": tool.name in DESKTOP_INPUT_TOOLS,
        },
        "companions": list(dict.fromkeys(
            name for workflow in related_workflows(tool.name)
            for name in ROUTES[workflow["id"]][1].split()
            if name != tool.name and name in available
        )),
    }


def discover(tools: Sequence[Tool], query: str, domain: str | None, limit: int) -> dict:
    """Pure routing function, also used by the retrieval regression fixtures."""
    if domain is not None and domain not in CATALOG["domains"]:
        raise ValueError(f"Unknown domain {domain!r}; choose from {list(CATALOG['domains'])}")
    candidates = {tool.name: tool for tool in tools if domain is None
                  or CATALOG["tools"].get(tool.name, {}).get("domain") == domain}
    available = {tool.name for tool in tools}
    ranked: dict[str, str] = {}
    # A named tool is an explicit request, even when the rest of the sentence
    # happens to match a different workflow's vocabulary.
    for name in re.findall(r"\bfl_[a-z0-9_]+\b", query.lower()):
        if name in candidates:
            ranked[name] = "exact tool name"
    normalized = " " + " ".join(_tokens(query)) + " "
    if any(term in normalized for term in (
        " uncertain ", " timed out ", " timeout ", " receipt ", " unknown outcome ",
    )) and "fl_receipt" in candidates:
        ranked.setdefault("fl_receipt", "resolve uncertainty before another write")
    route_scores = []
    for workflow_id, (aliases, names) in ROUTES.items():
        score = sum(2 if " " in alias else 1 for alias in aliases.split("|")
                    if " " + " ".join(_tokens(alias)) + " " in normalized)
        if score and any(name in candidates for name in names.split()):
            route_scores.append((score, workflow_id))
    route_scores.sort(key=lambda item: -item[0])
    selected_routes = [identifier for score, identifier in route_scores
                       if score >= route_scores[0][0]][:2] if route_scores else []
    # Interleave bundles: "key and BPM" must return both analysis tools.
    bundles = [ROUTES[identifier][1].split() for identifier in selected_routes]
    for position in range(max(map(len, bundles), default=0)):
        for identifier, names in zip(selected_routes, bundles):
            if position < len(names) and names[position] in candidates:
                ranked.setdefault(names[position], f"workflow: {identifier}")
    for name in _bm25(list(candidates.values()), query):
        ranked.setdefault(name, "contract text match")
    chosen = list(ranked)[:limit]
    return {
        "query": query,
        "matches": [_summary(candidates[name], available, ranked[name]) for name in chosen],
        "workflow_ids": selected_routes,
        "more_available": len(ranked) > limit,
        "next": "Read exact contracts with fl_describe before calling through fl_call.",
    }


class DiscoveryTransform(CatalogTransform):
    """Hide definitions from the initial catalog without disabling original calls."""

    def __init__(self) -> None:
        super().__init__()
        read_annotations = {"readOnlyHint": True, "destructiveHint": False}

        async def fl_discover(
            query: Annotated[str, Field(min_length=1, max_length=500)],
            domain: str | None = None,
            limit: Annotated[int, Field(ge=1, le=10)] = 5,
            ctx: Context = None,
        ) -> dict:
            """Find FL tools for a musical task. Returns bounded summaries and companions;
            exact schemas are fetched separately with fl_describe. No DAW access.
            """
            return discover(await self.get_tool_catalog(ctx), query, domain, limit)

        async def fl_describe(
            names: Annotated[list[str], Field(min_length=1, max_length=5)],
            ctx: Context = None,
        ) -> dict:
            """Read exact input/output schemas and guards for 1-5 discovered tools.
            Descriptions are authoritative; discovery policy labels are only hints.
            """
            catalog = {tool.name: tool for tool in await self.get_tool_catalog(ctx)}
            missing = set(names) - catalog.keys()
            if missing:
                raise ValueError(f"Unknown or unavailable tools: {sorted(missing)}")
            return {"tools": [
                {"contract": catalog[name].to_mcp_tool().model_dump(
                    mode="json", exclude_none=True),
                 "guidance": _summary(catalog[name], set(catalog), "requested contract"),
                 "workflow_ids": [w["id"] for w in related_workflows(name)]}
                for name in dict.fromkeys(names)
            ]}

        async def fl_call(
            name: str,
            arguments: dict[str, Any] | None = None,
            ctx: Context = None,
        ) -> ToolResult:
            """Execute a discovered FL tool once with its exact arguments and original
            guards. May write files, modify FL, or use desktop input. Read fl_describe
            first. Preserve receipts/errors; never automatically retry uncertain writes.
            """
            catalog = {tool.name for tool in await self.get_tool_catalog(ctx)}
            if name not in catalog:
                raise ValueError(f"Unknown or unavailable underlying tool: {name}")
            return await ctx.fastmcp.call_tool(name, arguments)

        async def fl_workflows(workflow: str | None = None, ctx: Context = None) -> dict:
            """List compact workflow/domain names, or read one workflow's prerequisites,
            tools and verification limits. No live DAW access or execution.
            """
            available = {tool.name for tool in await self.get_tool_catalog(ctx)}
            visible = [item for item in WORKFLOWS
                       if any(name in available for name in _workflow_tools(item))]
            if workflow is not None:
                selected = next((item for item in visible if item["id"] == workflow), None)
                if selected is None:
                    raise ValueError(f"Unknown or unavailable workflow: {workflow}")
                return {"workflow": {**selected, "tools": [
                    name for name in selected["tools"] if name in available]},
                    "preferred_tools": [name for name in ROUTES[workflow][1].split()
                                        if name in available]}
            return {
                "domains": CATALOG["domains"],
                "workflows": [{"id": item["id"], "coverage": item["coverage"]}
                              for item in visible],
                "next": "Pass a workflow id for prerequisites and verification limits.",
            }

        self.tools = {
            fn.__name__: Tool.from_function(fn, annotations=annotations)
            for fn, annotations in (
                (fl_discover, read_annotations), (fl_describe, read_annotations),
                (fl_workflows, read_annotations),
                # A generic executor must not inherit a discovery read-only hint.
                (fl_call, {"readOnlyHint": False, "destructiveHint": True}),
            )
        }

    async def transform_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        return [*(tool for tool in tools if tool.name in PINNED), *self.tools.values()]

    async def get_tool(
        self, name: str, call_next: GetToolNext, *, version: VersionSpec | None = None
    ) -> Tool | None:
        if name in self.tools:
            return self.tools[name]
        return await call_next(name, version=version)


def create_discovery_server(backend: FastMCP) -> FastMCP:
    """A separate middleware boundary keeps each execution under exactly one DAW lease."""
    facade = create_proxy(backend, name="SyncFL", instructions=DISCOVERY_INSTRUCTIONS)
    facade.add_transform(DiscoveryTransform())
    return facade
