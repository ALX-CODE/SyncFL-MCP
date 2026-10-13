"""Check or install the canonical FL controller and piano files; never operate FL."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import uuid
from contextlib import ExitStack
from pathlib import Path

from doctor import DEPLOYED_FILES, ROOT, controller_source_hash

from fl_studio_mcp.utils.paths import get_fl_settings_dir
from fl_studio_mcp.utils.protocol import transaction_lock


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _target(settings, relative):
    target = settings / relative
    if not target.resolve().is_relative_to(settings) or target.is_symlink():
        raise ValueError(f"Installation target escapes Settings or is a symlink: {relative}")
    if target.exists() and not target.is_file():
        raise ValueError(f"Installation target is not a regular file: {relative}")
    return target


def _sources():
    result = []
    for source, relative in DEPLOYED_FILES:
        path = ROOT / source
        data = path.read_bytes()
        if path.suffix in {".py", ".pyscript"}:
            compile(data, str(path), "exec")
        elif path.suffix == ".json":
            json.loads(data)
        result.append((source, relative, data))
    return result


def _snapshot(settings, sources):
    files, installed_digest = [], hashlib.sha256()
    complete = True
    for source, relative, data in sources:
        target = _target(settings, relative)
        actual = target.read_bytes() if target.is_file() else None
        files.append(
            dict(
                path=str(target),
                source_sha256=_sha(data),
                installed_sha256=_sha(actual) if actual is not None else None,
                matches_source=actual == data,
            )
        )
        if source.startswith("fl_controller/"):
            if actual is None:
                complete = False
            else:
                installed_digest.update(Path(source).name.encode("utf-8"))
                installed_digest.update(actual)
    expected_digest = controller_source_hash()
    actual_digest = installed_digest.hexdigest() if complete else None
    return dict(
        success=all(item["matches_source"] for item in files) and actual_digest == expected_digest,
        settings=str(settings),
        controller_source_sha256=expected_digest,
        installed_controller_sha256=actual_digest,
        files=files,
    )


def _write_atomic(target, data):
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=target.name + ".installing-", dir=target.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def _check_pending(settings):
    piano = settings / "Piano roll scripts"
    if (piano / "mcp-verification.json").exists():
        raise RuntimeError("Unresolved piano verification; inspect pending work before upgrading")
    queue = piano / "mcp_request.json"
    if queue.exists() and json.loads(queue.read_text(encoding="utf-8")) != []:
        raise RuntimeError("Pending piano request; finish it before upgrading")


def install(settings=None, *, apply=False):
    settings = Path(settings) if settings is not None else get_fl_settings_dir()
    if not settings.is_absolute() or not settings.is_dir():
        raise ValueError("Use the existing absolute FL Settings directory; launch FL once first")
    settings = settings.resolve(strict=True)
    sources = _sources()  # Validate every source before writing any target.
    report = _snapshot(settings, sources)
    report.update(applied=False, changed=0, backup=None)
    if not apply:
        return report

    with ExitStack() as stack:
        # GUI workflows can hold mcp-gui.lock while issuing ordinary MIDI calls.
        # Keep that same ordering and refuse a busy bridge instead of stopping it.
        for relative in (
            "Hardware/FLStudioMCP/mcp-gui.lock",
            "Hardware/FLStudioMCP/mcp.lock",
            "Piano roll scripts/pattern-bank.lock",
        ):
            lock = _target(settings, relative)
            lock.parent.mkdir(parents=True, exist_ok=True)
            stack.enter_context(transaction_lock(lock, 1))
        _check_pending(settings)
        if any((ROOT / source).read_bytes() != data for source, _, data in sources):
            raise RuntimeError(
                "Controller sources changed during preflight; run the installer again"
            )
        changes = []
        for _, relative, data in sources:
            target = _target(settings, relative)
            previous = target.read_bytes() if target.is_file() else None
            if previous != data:
                changes.append((relative, target, data, previous))
        backup = None
        if changes:
            backup = settings / "MCP-install-backups" / uuid.uuid4().hex
            if not backup.resolve().is_relative_to(settings):
                raise ValueError("Installation backup escaped Settings")
            backup.mkdir(parents=True)
            for relative, _, _, previous in changes:
                if previous is not None:
                    _write_atomic(backup / relative, previous)
        written = []
        try:
            for _, target, data, previous in changes:
                _write_atomic(target, data)
                written.append((target, data, previous))
            report = _snapshot(settings, sources)
            if not report["success"]:
                raise RuntimeError("Installed FL files failed their source hash checks")
        except Exception:
            # Restore only files still equal to our own writes; preserve concurrent edits.
            for target, data, previous in reversed(written):
                if target.read_bytes() != data:
                    continue
                if previous is None:
                    target.unlink()
                else:
                    _write_atomic(target, previous)
            raise
        report.update(applied=True, changed=len(changes), backup=str(backup) if backup else None)
        if backup:
            _write_atomic(backup / "installation.json", json.dumps(report, indent=2).encode())
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--check", action="store_true", help="Read-only; this is the default")
    path = parser.add_mutually_exclusive_group()
    path.add_argument("--settings-dir", type=Path, help="Existing absolute FL Settings directory")
    path.add_argument("--fl-user-data", help="Image-Line root containing FL Studio, not Settings")
    args = parser.parse_args()
    if args.fl_user_data:
        os.environ["FL_USER_DATA_FOLDER"] = args.fl_user_data
    try:
        report = install(args.settings_dir, apply=args.apply)
    except Exception as exc:
        report = dict(
            success=False,
            applied=None if args.apply else False,
            error=str(exc),
            outcome="unverified" if args.apply else "check_failed",
        )
    print(json.dumps(report, indent=2))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
