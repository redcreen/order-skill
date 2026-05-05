#!/usr/bin/env python3
"""Generate a confirmation summary for a guided-intake draft."""

from __future__ import annotations

import argparse
import json

from runtime_common import resolve_data_root
from runtime_flow import prepare_draft_confirmation


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare a reviewable confirmation summary for a workflow draft.")
    parser.add_argument("--data-root", help="Override the default order data root.")
    parser.add_argument("--workflow-draft-id", required=True, help="Workflow draft id.")
    parser.add_argument("--actor-label", help="Actor label for audit log.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = prepare_draft_confirmation(
        data_root=resolve_data_root(args.data_root),
        workflow_draft_id=args.workflow_draft_id,
        actor_label=args.actor_label,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
