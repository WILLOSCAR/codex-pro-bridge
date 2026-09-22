#!/usr/bin/env python3
"""Validate and persist one bounded ChatGPT model-control trace."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SHARED_DIR = Path(__file__).resolve().parents[2] / ".shared"
sys.path.insert(0, str(SHARED_DIR))

from bridge_store import BridgeError, atomic_write_text
from model_controls import validate_model_control_trace


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--receipt-output", type=Path, required=True)
    parser.add_argument("--expected-output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        owner = args.expected_output_dir.expanduser().resolve()
        source = args.trace.expanduser()
        output = args.receipt_output.expanduser()
        if not owner.is_dir():
            raise BridgeError("expected output directory does not exist")
        for path, label in ((source, "trace"), (output, "receipt")):
            if not path.is_absolute() or path.is_symlink():
                raise BridgeError(f"model-control {label} must be an absolute non-symlink path")
            if not path.resolve().is_relative_to(owner):
                raise BridgeError(f"model-control {label} must stay inside expected output directory")
        if output.exists():
            raise BridgeError("refusing to overwrite model-control receipt")
        trace = json.loads(source.read_text(encoding="utf-8"))
        receipt = validate_model_control_trace(trace)
        atomic_write_text(
            output,
            json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
        print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
        return 0
    except (BridgeError, OSError, ValueError) as exc:
        parser.error(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
