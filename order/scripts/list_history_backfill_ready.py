#!/usr/bin/env python3
"""List history-backfill drafts and their confirmation readiness."""

from __future__ import annotations

import argparse
import json
import sqlite3
from typing import Any

from history_common import parse_json
from runtime_common import connect_db, initialize_runtime, resolve_data_root


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="List history-backfill drafts and readiness state.")
    parser.add_argument("--data-root", help="Override the order data root.")
    parser.add_argument("--limit", type=int, default=20, help="Maximum number of drafts to return.")
    parser.add_argument(
        "--status",
        action="append",
        dest="statuses",
        help="Filter by draft status. Repeatable.",
    )
    parser.add_argument(
        "--only-ready",
        action="store_true",
        help="Return only drafts that already have commit-ready confirmation.",
    )
    return parser.parse_args()


def is_history_backfill(preview: dict[str, Any]) -> bool:
    summary_text = str(preview.get("summary_text") or "")
    if summary_text.startswith("从历史资料补录"):
        return True
    captured_fields = preview.get("captured_fields") or {}
    notes = str(captured_fields.get("notes") or "")
    return "historical backfill source:" in notes


def main() -> int:
    args = parse_args()
    data_root = resolve_data_root(args.data_root)
    initialize_runtime(data_root)
    connection = connect_db(data_root)
    allowed_statuses = {status for status in (args.statuses or []) if status}

    rows = connection.execute(
        """
        SELECT workflow_draft_id, draft_status, preview_json, updated_at
        FROM workflow_drafts
        ORDER BY updated_at DESC, rowid DESC
        """
    ).fetchall()

    drafts: list[dict[str, Any]] = []
    for row in rows:
        preview = parse_json(row["preview_json"])
        if not isinstance(preview, dict):
            continue
        if not is_history_backfill(preview):
            continue
        if allowed_statuses and str(row["draft_status"]) not in allowed_statuses:
            continue
        confirmation = preview.get("confirmation") if isinstance(preview.get("confirmation"), dict) else {}
        ready = bool(confirmation.get("commit_ready"))
        if args.only_ready and not ready:
            continue
        drafts.append(
            {
                "workflow_draft_id": str(row["workflow_draft_id"]),
                "draft_status": str(row["draft_status"]),
                "intent_type": preview.get("intent_type"),
                "target_object_type": preview.get("target_object_type"),
                "target_action": preview.get("target_action"),
                "summary_text": preview.get("summary_text"),
                "updated_at": str(row["updated_at"]),
                "commit_ready": ready,
                "confirm_token": confirmation.get("confirm_token"),
                "missing_required_fields": preview.get("missing_required_fields") or [],
                "pending_associations": preview.get("pending_associations") or [],
                "candidate_links": preview.get("candidate_links") or [],
            }
        )

    result = {
        "status": "ok",
        "data_root": str(data_root),
        "draft_count": min(len(drafts), max(args.limit, 1)),
        "drafts": drafts[: max(args.limit, 1)],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
