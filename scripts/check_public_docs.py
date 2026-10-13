"""Check exported Markdown links, API examples, configuration snippets and branding."""

from __future__ import annotations

import importlib.util
import json
import re
import struct
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

import tomllib
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "public_export", ROOT / "scripts/export_public_release.py"
)
exporter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(exporter)


def anchors(text: str) -> set[str]:
    found = set()
    for title in re.findall(r"^#{1,6}\s+(.+)$", text, re.MULTILINE):
        anchor = re.sub(r"[^\w\- ]", "", title.lower()).replace(" ", "-")
        original = anchor
        suffix = 1
        while anchor in found:
            anchor = f"{original}-{suffix}"
            suffix += 1
        found.add(anchor)
    return found


def check(root: Path = ROOT) -> dict:
    selected = set(exporter.selected_paths(root))
    schemas = {
        t["name"]: t["inputSchema"]
        for t in json.loads((root / "docs/reference/tools.json").read_text(encoding="utf-8"))
    }
    errors = []
    links = examples = documents = 0
    for name in sorted(selected):
        if not name.endswith(".md"):
            continue
        documents += 1
        path = root / name
        text = path.read_text(encoding="utf-8-sig")
        for destination in re.findall(r"!?\[[^\]\n]*\]\(([^\n)]+)\)", text):
            # A Markdown URL can be wrapped in angle brackets or followed by a title.
            destination = destination.strip().split(' "', 1)[0].strip("<>")
            url = urlsplit(destination)
            if url.scheme or url.netloc:
                continue
            target = (path.parent / unquote(url.path)).resolve() if url.path else path.resolve()
            if not target.is_relative_to(root.resolve()):
                errors.append(f"{name}: link escapes source: {destination}")
                continue
            relative = target.relative_to(root.resolve()).as_posix()
            if relative not in selected and not any(
                p.startswith(relative.rstrip("/") + "/") for p in selected
            ):
                errors.append(f"{name}: link absent from public export: {destination}")
                continue
            links += 1
            if url.fragment and target.is_file():
                fragment = unquote(url.fragment)
                if re.fullmatch(r"L\d+", fragment):
                    if int(fragment[1:]) > len(target.read_text(encoding="utf-8").splitlines()):
                        errors.append(f"{name}: source line out of range: {destination}")
                elif target.suffix == ".md" and fragment not in anchors(
                    target.read_text(encoding="utf-8-sig")
                ):
                    errors.append(f"{name}: missing heading anchor: {destination}")
        # Only the new public guides promise machine-valid configuration/API blocks.
        if path.parent == root / "docs" and name not in {
            "docs/PLUGIN-CONTROL-GUIDE.md",
            "docs/BACKGROUND-CONTROL-AUDIT.md",
            "docs/NATIVE-SAVE-CONTRACT.md",
            "docs/INSTALLERS.md",
        }:
            for language, snippet in re.findall(r"```(json|toml)\n(.*?)```", text, re.DOTALL):
                try:
                    value = json.loads(snippet) if language == "json" else tomllib.loads(snippet)
                    if isinstance(value, dict) and "tool" in value:
                        if value["tool"] not in schemas:
                            raise ValueError("Unknown example tool: " + value["tool"])
                        Draft202012Validator(schemas[value["tool"]]).validate(value["arguments"])
                    examples += 1
                except Exception as exc:
                    errors.append(f"{name}: invalid {language} example: {exc}")
    logo = (root / "docs/assets/syncfl-logo.png").read_bytes()
    if len(logo) < 24 or logo[:8] != b"\x89PNG\r\n\x1a\n" or logo[12:16] != b"IHDR":
        errors.append("The SyncFL logo must have a valid PNG header")
    elif not all(struct.unpack(">II", logo[16:24])):
        errors.append("The SyncFL logo must have positive dimensions")
    return {
        "success": not errors,
        "documents": documents,
        "local_links": links,
        "examples": examples,
        "tools": len(schemas),
        "errors": errors,
    }


if __name__ == "__main__":
    try:
        result = check()
    except (OSError, ValueError) as exc:
        result = {"success": False, "errors": [str(exc)]}
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["success"] else 1)
