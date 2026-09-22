#!/usr/bin/env python3
"""Validate a one-shot Chrome DevTools upload plan and its MCP result.

This guard does not invoke Chrome or close native dialogs.  It makes the
click-once/fresh-snapshot/direct-upload contract machine-checkable and emits a
structured cleanup requirement when the upload outcome is uncertain.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SHARED_DIR = Path(__file__).resolve().parents[2] / ".shared"
sys.path.insert(0, str(SHARED_DIR))

from bridge_store import BridgeError, atomic_write_text
from devtools_upload import (
    NativeChooserRisk,
    build_upload_action_plan,
    validate_upload_action_plan,
    verify_upload_result,
)


def _read_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BridgeError(f"Cannot read {label}: {path}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, help="Frozen DevTools upload action plan JSON")
    parser.add_argument("--result", type=Path, help="Normalized upload_file result JSON")
    parser.add_argument("--plan-output", type=Path, help="Write a newly-built plan to this path without overwriting")
    parser.add_argument(
        "--result-output",
        "--receipt-output",
        dest="result_output",
        type=Path,
        help="Persist the successful normalized upload receipt without overwriting",
    )
    parser.add_argument("--expected-output-dir", type=Path, help="Preparation/artifact owner for --plan-output")
    parser.add_argument("--page-id")
    parser.add_argument("--attachment-button-uid")
    parser.add_argument("--menu-snapshot-uid")
    parser.add_argument("--menu-item-uid")
    parser.add_argument("--windows-path")
    parser.add_argument("--staged-sha256")
    args = parser.parse_args()
    try:
        if args.result_output is not None and args.result is None:
            raise BridgeError("--result-output requires --result")
        if args.result_output is not None and args.expected_output_dir is None:
            raise BridgeError("--expected-output-dir is required with --result-output")
        if args.plan is not None:
            if args.plan_output is not None:
                raise BridgeError("--plan-output is only valid when constructing a new plan")
            if any(
                value is not None
                for value in (
                    args.page_id,
                    args.attachment_button_uid,
                    args.menu_snapshot_uid,
                    args.menu_item_uid,
                    args.windows_path,
                    args.staged_sha256,
                )
            ):
                raise BridgeError("--plan cannot be combined with plan-construction fields")
            plan = validate_upload_action_plan(_read_json(args.plan, "upload plan"))
        else:
            fields = (
                args.page_id,
                args.attachment_button_uid,
                args.menu_snapshot_uid,
                args.menu_item_uid,
                args.windows_path,
                args.staged_sha256,
            )
            if any(value is None for value in fields):
                parser.error("provide --plan or all plan-construction fields")
            plan = build_upload_action_plan(
                page_id=args.page_id,
                attachment_button_uid=args.attachment_button_uid,
                menu_snapshot_uid=args.menu_snapshot_uid,
                menu_item_uid=args.menu_item_uid,
                windows_path=args.windows_path,
                staged_sha256=args.staged_sha256,
            )
            plan = validate_upload_action_plan(plan)
            if args.plan_output is not None:
                if args.expected_output_dir is None:
                    raise BridgeError("--expected-output-dir is required with --plan-output")
                output = args.plan_output.expanduser()
                owner = args.expected_output_dir.expanduser().resolve()
                if not owner.is_dir():
                    raise BridgeError(f"--expected-output-dir must be an existing directory: {owner}")
                if not output.is_absolute():
                    raise BridgeError("--plan-output must be an absolute path")
                if output.is_symlink():
                    raise BridgeError(f"Refusing to overwrite symlink upload plan: {output}")
                output = output.resolve()
                if not output.is_relative_to(owner):
                    raise BridgeError("--plan-output must stay inside --expected-output-dir")
                if output.exists():
                    raise BridgeError(f"Refusing to overwrite upload plan: {output}")
                atomic_write_text(
                    output,
                    json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                )
        if args.result is None:
            output = {"status": "plan-valid", "plan": plan}
            if args.plan_output is not None:
                output["plan_file"] = str(args.plan_output.expanduser().resolve())
        else:
            output = verify_upload_result(plan, _read_json(args.result, "upload result"))
        if args.result_output is not None:
            result_output = args.result_output.expanduser()
            owner = args.expected_output_dir.expanduser().resolve()
            if not owner.is_dir():
                raise BridgeError(f"--expected-output-dir must be an existing directory: {owner}")
            if not result_output.is_absolute():
                raise BridgeError("--result-output must be an absolute path")
            if result_output.is_symlink():
                raise BridgeError(f"Refusing to overwrite symlink upload receipt: {result_output}")
            result_output = result_output.resolve()
            if not result_output.is_relative_to(owner):
                raise BridgeError("--result-output must stay inside --expected-output-dir")
            if result_output.exists():
                raise BridgeError(f"Refusing to overwrite upload receipt: {result_output}")
            atomic_write_text(
                result_output,
                json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            )
            output["result_file"] = str(result_output)
        print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except NativeChooserRisk as exc:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "reason": str(exc),
                    "native_chooser_risk": True,
                    "browser_ui_cleanup": "not-proven",
                    "upload_plan_identity": {
                        "page_id": plan["page_id"],
                        "windows_path": plan["windows_path"],
                        "staged_sha256": plan["staged_sha256"],
                        "attachment_name": plan["expected_attachment_name"],
                    },
                    "cleanup_requirements": exc.cleanup,
                    "next_action": "stop-upload-and-send; obtain explicit same-host chooser/page observation",
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return 2
    except (BridgeError, OSError, ValueError) as exc:
        parser.error(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
