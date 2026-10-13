"""Inspect or deploy the piano script and contract together, retaining prior files."""

import argparse
import hashlib
import json
import shutil
import time
from pathlib import Path

from fl_studio_mcp.utils.paths import get_fl_settings_dir
from fl_studio_mcp.utils.protocol import transaction_lock

ROOT = Path(__file__).resolve().parents[1]


def deploy(apply=False):
    directory = get_fl_settings_dir() / "Piano roll scripts"
    pairs = [
        (ROOT / "src/fl_studio_mcp/utils/note_protocol.py", "mcp_note_protocol.py"),
        (ROOT / "scripts/ComposeWithLLM.pyscript", "ComposeWithLLM.pyscript"),
    ]
    for source, _ in pairs:
        compile(source.read_text(encoding="utf-8"), str(source), "exec")
    if apply:
        directory.mkdir(parents=True, exist_ok=True)
        with transaction_lock(directory / "pattern-bank.lock", 5):
            if (directory / "mcp-verification.json").exists():
                raise RuntimeError(
                    "Unresolved committed-state verification: inspect before deployment"
                )
            queue = directory / "mcp_request.json"
            if queue.exists() and json.loads(queue.read_text(encoding="utf-8")) != []:
                raise RuntimeError("Pending piano work: inspect before deployment")
            backup = directory / "mcp-backups" / str(time.time_ns())
            backup.mkdir(parents=True)
            for source, name in pairs:
                target = directory / name
                if target.exists():
                    shutil.copy2(target, backup / name)
                temporary = target.with_suffix(target.suffix + ".deploying")
                shutil.copyfile(source, temporary)
                temporary.replace(target)
    status = []
    for source, name in pairs:
        target = directory / name
        matches = (
            target.is_file()
            and hashlib.sha256(source.read_bytes()).digest()
            == hashlib.sha256(target.read_bytes()).digest()
        )
        status.append({"path": str(target), "matches_source": matches})
    return {
        "success": all(s["matches_source"] for s in status),
        "files": status,
        "applied": apply,
        "backup": str(backup) if apply else None,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    print(json.dumps(deploy(parser.parse_args().apply), indent=2))
