#!/usr/bin/env python3
"""Preview or commit one history-backfill draft with explicit confirmation."""

from __future__ import annotations

import argparse
import json
from typing import Any

from history_common import parse_json
from runtime_common import connect_db, initialize_runtime, resolve_data_root
from runtime_flow import commit_workflow_draft, prepare_draft_confirmation


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Preview or commit one history-backfill draft.")
    parser.add_argument("--data-root", help="Override the order data root.")
    parser.add_argument("--workflow-draft-id", required=True, help="Workflow draft id.")
    parser.add_argument("--actor-label", help="Actor label for audit log.")
    parser.add_argument("--confirm-token", help="Confirmation token from the prepared summary.")
    parser.add_argument(
        "--confirm-commit",
        action="store_true",
        help="Required explicit flag before commit is allowed.",
    )
    parser.add_argument(
        "--refresh-confirmation",
        action="store_true",
        help="Always regenerate confirmation before returning preview/commit.",
    )
    return parser.parse_args()


def load_preview(connection, workflow_draft_id: str) -> dict[str, Any]:
    row = connection.execute(
        """
        SELECT preview_json, draft_status
        FROM workflow_drafts
        WHERE workflow_draft_id = ?
        """,
        (workflow_draft_id,),
    ).fetchone()
    if not row:
        raise ValueError(f"Unknown workflow_draft_id: {workflow_draft_id}")
    preview = parse_json(row["preview_json"])
    if not isinstance(preview, dict):
        preview = {}
    return {"draft_status": str(row["draft_status"]), "preview": preview}


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
    state = load_preview(connection, args.workflow_draft_id)
    connection.close()
    if not is_history_backfill(state["preview"]):
        raise SystemExit("This workflow_draft_id is not a history-backfill draft.")

    confirmation = state["preview"].get("confirmation") if isinstance(state["preview"].get("confirmation"), dict) else None
    if args.refresh_confirmation or not confirmation:
        prepared = prepare_draft_confirmation(
            data_root=data_root,
            workflow_draft_id=args.workflow_draft_id,
            actor_label=args.actor_label,
        )
        confirmation = prepared["confirmation"]
        state["draft_status"] = prepared["draft_status"]
    else:
        prepared = {
            "status": "confirmation_available",
            "workflow_draft_id": args.workflow_draft_id,
            "draft_status": state["draft_status"],
            "commit_ready": bool(confirmation.get("commit_ready")),
            "confirmation": confirmation,
        }

    if not args.confirm_token:
        print(json.dumps({"status": "preview", **prepared}, ensure_ascii=False, indent=2))
        return 0

    if not args.confirm_commit:
        raise SystemExit("Committing a history backfill draft requires --confirm-commit together with --confirm-token.")

    committed = commit_workflow_draft(
        data_root=data_root,
        workflow_draft_id=args.workflow_draft_id,
        confirm_token=args.confirm_token,
        actor_label=args.actor_label,
    )
    print(json.dumps({"status": "committed", "prepared": prepared, "committed": committed}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
