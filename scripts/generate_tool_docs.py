"""Generate the public tool reference from the registered, unconnected MCP server."""

from __future__ import annotations

import argparse
import ast
import asyncio
import json
import sys
from pathlib import Path

from fastmcp import Client

ROOT = Path(__file__).resolve().parents[1]


def source_locations() -> dict[str, tuple[str, int]]:
    locations = {}
    for path in sorted((ROOT / "src/fl_studio_mcp").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
                "fl_"
            ):
                locations[node.name] = (path.relative_to(ROOT).as_posix(), node.lineno)
    return locations


def type_label(schema: dict) -> str:
    if "anyOf" in schema:
        return " or ".join(type_label(item) for item in schema["anyOf"])
    if "enum" in schema:
        return " / ".join(f"`{item}`" for item in schema["enum"])
    kind = schema.get("type", "object")
    if kind == "array":
        return f"array of {type_label(schema.get('items', {}))}"
    return kind


def cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


async def collect_tools() -> list[dict]:
    # Importing and listing tools does not connect to MIDI or call any DAW tools.
    from fl_studio_mcp.server import mcp

    async with Client(mcp) as client:
        return sorted(
            (tool.model_dump(mode="json", exclude_none=True) for tool in await client.list_tools()),
            key=lambda tool: tool["name"],
        )


def render(tools: list[dict]) -> str:
    from fl_studio_mcp.utils.operator_contract import OFFLINE_TOOLS, READ_TOOLS

    locations = source_locations()
    lines = [
        "# MCP tool reference",
        "",
        f"**{len(tools)} registered tools.** Generated from the server's actual MCP definitions.",
        "Run `python scripts/generate_tool_docs.py --check` to detect drift.",
        "",
        "Start with [capabilities](../CAPABILITIES.md) for tasks and limitations,",
        "or [workflows](../WORKFLOWS.md) for examples. The reference below retains",
        "each tool's own description, input schema and defaults.",
        "The [JSON reference](tools.json) contains the complete machine-readable schemas.",
        "",
        "## How to read this reference",
        "",
        "- **Offline:** the runtime catalog classifies this tool as independent of the live DAW.",
        "- **Read policy:** the runtime explicitly permits observation; other tools are treated as",
        "  potential writes. These labels are scheduling hints, not permission grants.",
        "- **Input mode:** background is the default. Availability depends on the requested",
        "  operation and its internal calls. A tool absent from the explicit desktop list",
        "  is not automatically guaranteed to be background-safe.",
        "- Parameter names, units and target indices are exact. Omitted optional parameters",
        "  use the defaults below. Nested structures are described by the tool and JSON schema.",
        "- Previews, receipts and revisions are tool-specific. Read the full description before",
        "  applying a mutation; a failed or timed-out call can have a partial outcome.",
        "",
        "## Index",
        "",
    ]
    lines.extend(f"- [`{tool['name']}`](#{tool['name']})" for tool in tools)
    for tool in tools:
        name = tool["name"]
        lines += ["", f"## {name}", ""]
        flags = ["Offline" if name in OFFLINE_TOOLS else "Live / operation-dependent"]
        flags.append("Read policy" if name in READ_TOOLS else "Potential write policy")
        lines += [" · ".join(flags), "", tool.get("description", ""), ""]
        if name in locations:
            source, line = locations[name]
            lines += [f"[Implementation](../../{source}#L{line})", ""]
        schema = tool.get("inputSchema", {})
        props = schema.get("properties", {})
        if not props:
            lines += ["No input parameters."]
            continue
        required = schema.get("required", [])
        lines += ["| Parameter | Type | Required | Default |", "|---|---|---|---|"]
        descriptions = []
        for key, prop in props.items():
            default = json.dumps(prop["default"], ensure_ascii=False) if "default" in prop else "—"
            lines.append(
                f"| `{key}` | {cell(type_label(prop))} | {'Yes' if key in required else 'No'}"
                f" | {cell(default)} |"
            )
            if prop.get("description"):
                descriptions.append(f"- `{key}`: {prop['description']}")
        if descriptions:
            lines.extend(["", *descriptions])
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="Check committed files without writing"
    )
    args = parser.parse_args()
    tools = asyncio.run(collect_tools())
    outputs = {
        ROOT / "docs/reference/TOOLS.md": render(tools),
        ROOT / "docs/reference/tools.json": json.dumps(tools, indent=2, ensure_ascii=False) + "\n",
    }
    if args.check:
        stale = [
            str(p.relative_to(ROOT))
            for p, content in outputs.items()
            if not p.exists() or p.read_text(encoding="utf-8") != content
        ]
        if stale:
            print("Tool reference is stale: " + ", ".join(stale), file=sys.stderr)
            return 1
    else:
        for path, content in outputs.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
    print(f"Tool reference {'checked' if args.check else 'generated'}: {len(tools)} tools")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
