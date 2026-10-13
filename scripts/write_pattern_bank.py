"""Use the same verified pattern-bank backend without restarting the MCP host."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from fl_studio_mcp.utils.pattern_bank import write_pattern_bank

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("bank", type=Path)
    parser.add_argument("--channel", type=int, required=True)
    parser.add_argument("--expected-channel", required=True)
    parser.add_argument("--receipt", required=True)
    args = parser.parse_args()
    result = write_pattern_bank(
        json.loads(args.bank.read_text()), args.channel, args.expected_channel, args.receipt
    )
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["success"] else 1)
