"""Immediate CLI for Serum tools without reconnecting an existing MCP host."""

import argparse
import json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("recipes")
    inspect = sub.add_parser("inspect")
    inspect.add_argument("channel", type=int)
    preset = sub.add_parser("preset")
    preset.add_argument("path")
    build = sub.add_parser("build")
    build.add_argument("recipe", choices=["kick", "kick_long", "808", "lead", "pad"])
    build.add_argument("output_path")
    build.add_argument("--settings", help="Path to JSON object of recipe settings")
    build.add_argument("--name")
    apply = sub.add_parser("apply")
    apply.add_argument("channel", type=int)
    apply.add_argument("changes", help="JSON file with named live controls")
    apply.add_argument("--execute", action="store_true", help="Actually write; otherwise dry run")
    analyze = sub.add_parser("analyze")
    analyze.add_argument("path")
    analyze.add_argument("--reference")
    probe = sub.add_parser("probe")
    probe.add_argument("channel", type=int)
    probe.add_argument("output_path")
    probe.add_argument("--note", type=int, default=32)
    probe.add_argument("--velocity", type=int, default=100)
    probe.add_argument("--hold", type=float, default=0.6)
    probe.add_argument("--reference")
    load = sub.add_parser("load")
    load.add_argument("channel", type=int)
    load.add_argument("path")
    args = parser.parse_args()
    from pathlib import Path

    from fl_studio_mcp.utils.serum_live import apply_serum, inspect_serum
    from fl_studio_mcp.utils.serum_presets import build_preset, inspect_preset, recipe_catalog

    if args.command == "recipes":
        result = recipe_catalog()
    elif args.command == "inspect":
        result = inspect_serum(args.channel)
    elif args.command == "preset":
        result = inspect_preset(args.path)
    elif args.command == "build":
        settings = json.loads(Path(args.settings).read_text("utf-8-sig")) if args.settings else None
        result = build_preset(args.recipe, args.output_path, settings, args.name)
    elif args.command == "apply":
        result = apply_serum(
            args.channel,
            json.loads(Path(args.changes).read_text("utf-8-sig")),
            dry_run=not args.execute,
        )
    elif args.command == "load":
        import asyncio

        from fl_studio_mcp.utils.serum_windows import load_serum_preset

        result = asyncio.run(load_serum_preset(args.channel, args.path))
    elif args.command == "analyze":
        from fl_studio_mcp.utils.serum_audio import analyze_audio

        result = analyze_audio(args.path, args.reference)
    else:
        from fl_studio_mcp.utils.serum_audio import capture_serum_probe

        result = capture_serum_probe(
            args.channel, args.output_path, args.note, args.velocity, args.hold, args.reference
        )
    print(json.dumps(result, indent=2, allow_nan=False))
    if result.get("success") is False:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
