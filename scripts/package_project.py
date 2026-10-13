"""Create a portable FLP/sample/MIDI directory without touching the open project."""

import argparse
import json

from fl_studio_mcp.utils.project_bundle import BundleError, inspect_project, pack_project


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source")
    parser.add_argument("destination", nargs="?")
    parser.add_argument("--inspect", action="store_true")
    parser.add_argument("--search-root", action="append", default=[])
    parser.add_argument("--path-variable", action="append", default=[], metavar="NAME=PATH")
    args = parser.parse_args()
    variables = {}
    for pair in args.path_variable:
        if "=" not in pair:
            parser.error("--path-variable requires NAME=PATH")
        key, value = pair.split("=", 1)
        if not key or not value:
            parser.error("--path-variable requires a nonempty NAME and PATH")
        variables[key] = value
    try:
        if args.inspect:
            result = inspect_project(args.source)
        else:
            if not args.destination:
                parser.error("destination is required unless --inspect is used")
            result = pack_project(args.source, args.destination, search_roots=args.search_root,
                                  path_variables=variables)
        # Windows redirected stdout can still be cp1252; JSON escapes are lossless.
        print(json.dumps(result, ensure_ascii=True, indent=2))
    except (BundleError, OSError) as exc:
        parser.exit(1, f"Packaging failed: {exc}\n")


if __name__ == "__main__":
    main()
