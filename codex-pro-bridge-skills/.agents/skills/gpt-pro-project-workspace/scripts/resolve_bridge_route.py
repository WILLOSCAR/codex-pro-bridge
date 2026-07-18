#!/usr/bin/env python3
"""Resolve and optionally attach one explainable Bridge route."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


SHARED_DIR = Path(__file__).resolve().parents[2] / ".shared"
sys.path.insert(0, str(SHARED_DIR))

from bridge_store import BridgeError  # noqa: E402
from project_router import EVIDENCE_MODES, ROUTE_SCOPES, resolve_route  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Choose local-only, standalone, or Project Bridge execution."
    )
    parser.add_argument("--repo", default=".")
    parser.add_argument("--task", required=True)
    parser.add_argument(
        "--scope",
        default="auto",
        choices=sorted(scope.replace("_", "-") for scope in ROUTE_SCOPES),
    )
    intent = parser.add_mutually_exclusive_group()
    intent.add_argument("--external-reasoning", action="store_true")
    intent.add_argument("--local-only", action="store_true")
    parser.add_argument("--bridge-project-id", default="")
    parser.add_argument("--bridge-thread-id", default="")
    parser.add_argument("--evidence-mode", choices=sorted(EVIDENCE_MODES), default="auto")
    parser.add_argument("--force-new-thread", action="store_true")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Attach a ready Project route. Omit for a side-effect-free preview.",
    )
    args = parser.parse_args()
    try:
        requested_scope = args.scope.replace("-", "_")
        if requested_scope == "auto":
            if not args.external_reasoning and not args.local_only:
                raise BridgeError(
                    "auto scope requires --external-reasoning or --local-only "
                    "from Codex's task-level decision"
                )
            requires_external_reasoning = args.external_reasoning
        elif requested_scope == "local_only":
            if args.external_reasoning:
                raise BridgeError(
                    "--scope local-only cannot be combined with --external-reasoning"
                )
            requires_external_reasoning = False
        else:
            if args.local_only:
                raise BridgeError(
                    f"--scope {args.scope} cannot be combined with --local-only"
                )
            requires_external_reasoning = True
        decision = resolve_route(
            Path(args.repo),
            task=args.task,
            requested_scope=args.scope,
            requires_external_reasoning=requires_external_reasoning,
            bridge_thread_id=args.bridge_thread_id,
            bridge_project_id=args.bridge_project_id,
            evidence_mode=args.evidence_mode,
            force_new_thread=args.force_new_thread,
            attach=args.apply,
        )
        print(json.dumps(decision.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
        return 2 if decision.requires_confirmation else 0
    except (BridgeError, OSError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
