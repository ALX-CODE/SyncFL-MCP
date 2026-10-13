"""Bounded discovery of loadable FL assets, without loading or changing FL.

Plugin names resolve only to an exact, unambiguous Plugin database .fst entry.
Search results expose full paths so callers can disambiguate deliberately.
"""

from __future__ import annotations

import os
import stat
import time
from pathlib import Path
from typing import Any

from fl_studio_mcp.utils.paths import get_fl_settings_dir

SAMPLE_EXTENSIONS = frozenset({".wav", ".mp3", ".ogg", ".flac", ".aif", ".aiff"})
KINDS = frozenset({"all", "sample", "preset", "plugin", "generator", "effect"})
MAX_RESULTS = 100
MAX_ROOTS = 16
MAX_ENTRIES = 100_000
MAX_DIRECTORIES = 5_000
MAX_DEPTH = 16
MAX_SECONDS = 10.0


def _is_link(path: Path) -> bool:
    """Also exclude Windows junctions, on Python versions before is_junction."""
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def _default_roots() -> list[Path]:
    user = get_fl_settings_dir().parent
    candidates = [user / "Presets", user / "Audio"]
    # Only enumerate installation folders immediately below Image-Line; never
    # recursively search Program Files, user profiles, or entire drives.
    install_parents = [
        Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Image-Line",
        Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "Image-Line",
    ] if os.name == "nt" else []
    for parent in install_parents:
        try:
            with os.scandir(parent) as entries:
                for index, entry in enumerate(entries):
                    if index >= 100:
                        break
                    path = Path(entry.path)
                    if entry.name.casefold().startswith("fl studio ") and not _is_link(path):
                        candidates.append(path / "Data" / "Patches")
        except OSError:
            continue
    return [path for path in candidates if path.is_dir() and not _is_link(path)]


def _roots(roots: list[str | Path] | None) -> list[Path]:
    raw = _default_roots() if roots is None else roots
    if not isinstance(raw, (list, tuple)) or len(raw) > MAX_ROOTS:
        raise ValueError(f"roots must be a list of at most {MAX_ROOTS} asset directories")
    result: list[Path] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, (str, Path)) or not str(item).strip():
            raise ValueError("Each asset root must be a nonempty absolute directory path")
        path = Path(item).expanduser()
        if not path.is_absolute():
            raise ValueError(f"Asset root must be absolute: {path}")
        if path == Path(path.anchor):
            raise ValueError("Drive/filesystem roots are not permitted; choose an asset directory")
        try:
            if _is_link(path):
                raise ValueError(f"Asset root must not be a symlink or junction: {path}")
            path = path.resolve(strict=True)
            if not path.is_dir():
                raise ValueError(f"Asset root is not a directory: {path}")
        except OSError as exc:
            raise ValueError(f"Asset root is unavailable: {path}") from exc
        if path == Path(path.anchor):
            raise ValueError("Drive/filesystem roots are not permitted; choose an asset directory")
        key = os.path.normcase(str(path))
        if key not in seen:
            seen.add(key)
            result.append(path)
    return sorted(result, key=lambda path: str(path).casefold())


def _asset(path: Path) -> dict[str, str] | None:
    suffix = path.suffix.casefold()
    if suffix in SAMPLE_EXTENSIONS:
        kind, plugin_kind = "sample", None
    elif suffix == ".serumpreset":
        kind, plugin_kind = "preset", None
    elif suffix == ".fst":
        parts = [part.casefold() for part in path.parent.parts]
        plugin_kind = None
        if "plugin database" in parts:
            tail = parts[parts.index("plugin database") + 1:]
            if "generators" in tail:
                plugin_kind = "generator"
            elif "effects" in tail:
                plugin_kind = "effect"
        kind = "plugin" if plugin_kind else "preset"
    else:
        return None
    result = {"name": path.stem, "path": str(path), "kind": kind}
    if plugin_kind:
        result["plugin_kind"] = plugin_kind
    if suffix == ".serumpreset":
        result["preset_format"] = "serum"
    return result


def _rank(asset: dict[str, str], query: str) -> tuple[int, str, str]:
    name = asset["name"].casefold()
    filename = Path(asset["path"]).name.casefold()
    priority = 0 if query in (name, filename) else 1 if name.startswith(query) else 2
    return priority, name, asset["path"].casefold()


def find_assets(
    query: str,
    kind: str = "all",
    roots: list[str | Path] | None = None,
    limit: int = 30,
) -> dict[str, Any]:
    """Find samples, .fst/.serumpreset presets and Plugin database entries.

    Query matches file names case-insensitively (an empty query lists assets).
    Results rank exact names first and include exact absolute paths. Traversal
    skips symlinks/junctions and is bounded by entries, directories, depth and
    elapsed time. ``scan_complete`` distinguishes a complete search from a
    truncated scan; ``truncated`` also covers a result limit. Custom roots must
    be absolute asset directories, not drive roots. No global index is cached.
    """
    if not isinstance(query, str) or len(query) > 500 or "\0" in query:
        raise ValueError("query must be a string of at most 500 characters without NUL")
    if not isinstance(kind, str) or kind not in KINDS:
        raise ValueError(f"kind must be one of: {', '.join(sorted(KINDS))}")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_RESULTS:
        raise ValueError(f"limit must be an integer from 1 to {MAX_RESULTS}")
    if roots is None and kind in {"plugin", "generator", "effect"}:
        roots = [
            root / "Plugin database" for root in _default_roots()
            if (root / "Plugin database").is_dir()
        ]
    selected = _roots(roots)
    needle = query.strip().casefold()
    matches: list[dict[str, str]] = []
    warnings: list[str] = []
    visited: set[str] = set()
    seen_files: set[str] = set()
    scanned_files = entries_seen = matching_files = skipped_links = 0
    started = time.monotonic()
    stack = [(root, root, 0) for root in reversed(selected)]

    def expired() -> bool:
        return time.monotonic() - started >= MAX_SECONDS

    while stack:
        if entries_seen >= MAX_ENTRIES or len(visited) >= MAX_DIRECTORIES or expired():
            warnings.append("Asset scan reached its work/time budget; narrow the roots")
            break
        directory, root, depth = stack.pop()
        key = os.path.normcase(str(directory))
        if key in visited:
            continue
        visited.add(key)
        try:
            if _is_link(directory):
                skipped_links += 1
                continue
            if not directory.resolve(strict=True).is_relative_to(root):
                warnings.append(f"Skipped directory outside its asset root: {directory}")
                continue
            with os.scandir(directory) as entries:
                children = []
                for entry in entries:
                    if entries_seen >= MAX_ENTRIES or expired():
                        warnings.append("Asset scan reached its work/time budget; narrow the roots")
                        break
                    entries_seen += 1
                    path = Path(entry.path)
                    try:
                        if _is_link(path):
                            skipped_links += 1
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            if depth >= MAX_DEPTH:
                                warnings.append(f"Asset scan depth limit reached: {path}")
                            else:
                                children.append((path, root, depth + 1))
                            continue
                        if not entry.is_file(follow_symlinks=False):
                            continue
                        scanned_files += 1
                        if needle not in path.name.casefold():
                            continue
                        asset = _asset(path)
                        if asset is None or (
                            kind != "all" and kind not in (asset["kind"], asset.get("plugin_kind"))
                        ):
                            continue
                        resolved = path.resolve(strict=True)
                        if not resolved.is_relative_to(root):
                            warnings.append(f"Skipped file outside its asset root: {path}")
                            continue
                        file_key = os.path.normcase(str(resolved))
                        if file_key in seen_files:
                            continue
                        seen_files.add(file_key)
                        asset["path"] = str(resolved)
                        matching_files += 1
                        matches.append(asset)
                        matches.sort(key=lambda item: _rank(item, needle))
                        if len(matches) > limit:
                            matches.pop()
                    except OSError:
                        warnings.append(f"Could not inspect asset entry: {path}")
                stack.extend(sorted(
                    children, key=lambda item: str(item[0]).casefold(), reverse=True,
                ))
        except OSError:
            warnings.append(f"Could not read asset directory: {directory}")

    return {
        "assets": matches,
        "roots": [str(root) for root in selected],
        "query": query,
        "kind": kind,
        "matching_files": matching_files,
        "scanned_files": scanned_files,
        "scan_complete": not warnings,
        "truncated": bool(warnings) or matching_files > limit,
        "skipped_links": skipped_links,
        "warnings": list(dict.fromkeys(warnings)),
    }


def resolve_plugin(
    name: str, kind: str, *, roots: list[str | Path] | None = None,
) -> dict[str, str]:
    """Resolve an exact plugin name or basename; never select a fuzzy match.

    ``kind`` is ``generator`` or ``effect``. Without explicit roots, search the
    active user's matching favorites first, then all Plugin database roots only
    if there is no exact favorite. Multiple exact entries within a search tier
    require an explicit path; incomplete scans cannot establish uniqueness.
    Explicit roots disable the favorites preference.
    """
    if kind not in {"generator", "effect"}:
        raise ValueError("Plugin kind must be 'generator' or 'effect'")
    if not isinstance(name, str) or not name.strip() or "/" in name or "\\" in name:
        raise ValueError("Plugin name must be a nonempty exact name, not a path")
    needle = name.strip().casefold()
    searches = [roots]
    if roots is None:
        folder = "Generators" if kind == "generator" else "Effects"
        favorites = get_fl_settings_dir().parent / "Presets" / "Plugin database" / folder
        if favorites.is_dir():
            searches.insert(0, [favorites])
    for search_roots in searches:
        result = find_assets(name, kind, roots=search_roots, limit=MAX_RESULTS)
        if not result["scan_complete"]:
            raise ValueError(
                "Plugin search was incomplete; narrow roots or use an explicit .fst path",
            )
        matches = [
            asset for asset in result["assets"]
            if needle in (asset["name"].casefold(), Path(asset["path"]).name.casefold())
        ]
        if len(matches) > 1:
            paths = "; ".join(asset["path"] for asset in matches[:10])
            raise ValueError(f"Ambiguous {kind} name {name!r}; choose an exact .fst path: {paths}")
        if matches:
            return matches[0]
    raise ValueError(f"No exact {kind} Plugin database entry named {name!r}")
