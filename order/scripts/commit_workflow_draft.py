#!/usr/bin/env python3
"""Commit a confirmed workflow draft into formal business tables."""

from __future__ import annotations

import argparse
import json

from runtime_common import resolve_data_root
from runtime_flow import commit_workflow_draft


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Commit a confirmed order workflow draft.")
    parser.add_argument("--data-root", help="Override the default order data root.")
    parser.add_argument("--workflow-draft-id", required=True, help="Workflow draft id.")
    parser.add_argument("--confirm-token", required=True, help="Confirmation token from the prepared summary.")
    parser.add_argument("--actor-label", help="Actor label for audit log.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = commit_workflow_draft(
        data_root=resolve_data_root(args.data_root),
        workflow_draft_id=args.workflow_draft_id,
        confirm_token=args.confirm_token,
        actor_label=args.actor_label,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
