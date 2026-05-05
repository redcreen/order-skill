#!/usr/bin/env python3
"""List batch backfill queue grouped by imported history source."""

from __future__ import annotations

import argparse
import json
from typing import Any

from history_common import parse_json, snippet, tokenize_query
from runtime_common import connect_db, initialize_runtime, resolve_data_root


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="List history backfill queue grouped by source item.")
    parser.add_argument("--data-root", help="Override the order data root.")
    parser.add_argument("--limit", type=int, default=30, help="Maximum queue items to return.")
    parser.add_argument(
        "--queue-status",
        action="append",
        dest="queue_statuses",
        help="Filter by queue status: unstarted, in_progress, ready, committed, mixed. Repeatable.",
    )
    parser.add_argument("--query", nargs="+", help="Optional free-text filter across source preview and identifiers.")
    parser.add_argument("--source-message-id", help="Optional exact source_message_id filter.")
    return parser.parse_args()


def is_history_source(row: dict[str, Any]) -> bool:
    return row["channel_type"] in {"legacy_history_artifact", "legacy_order_session_message"}


def infer_suggested_intents(row: dict[str, Any]) -> list[str]:
    category = row.get("category")
    if row["channel_type"] == "legacy_order_session_message":
        return ["sales_order"]
    mapping = {
        "legacy_finance_doc": ["supplier_payable", "payment_receipt"],
        "legacy_communication_log": ["sales_order", "production_arrangement"],
        "legacy_work_artifact": ["production_arrangement", "supplier_payable"],
        "legacy_product_artifact": ["sales_order", "production_arrangement"],
        "legacy_memory": ["sales_order", "production_arrangement"],
        "legacy_csv_snapshot": ["sales_order", "shipment"],
    }
    return mapping.get(category, ["sales_order"])


def derive_queue_status(drafts: list[dict[str, Any]]) -> str:
    if not drafts:
        return "unstarted"
    statuses = {draft["draft_status"] for draft in drafts}
    if statuses == {"committed"}:
        return "committed"
    if any(draft["commit_ready"] for draft in drafts):
        return "ready"
    if statuses == {"collecting"}:
        return "in_progress"
    return "mixed"


def next_action(queue_status: str) -> str:
    mapping = {
        "unstarted": "history-backfill",
        "in_progress": "history-show / history-backfill / association-candidates / resolve-pending",
        "ready": "backfill-ready / backfill-finalize",
        "committed": "history-show / review committed record",
        "mixed": "inspect drafts and continue pending backfill steps",
    }
    return mapping.get(queue_status, "inspect source")


def main() -> int:
    args = parse_args()
    data_root = resolve_data_root(args.data_root)
    initialize_runtime(data_root)
    connection = connect_db(data_root)

    queue_status_filter = {status for status in (args.queue_statuses or []) if status}
    query = " ".join(args.query) if args.query else None
    tokens = tokenize_query(query)

    source_rows = [
        dict(row)
        for row in connection.execute(
            """
            SELECT inbox_item_id, channel_type, channel_session_key, source_actor, source_message_id,
                   raw_text, raw_payload_json, received_at
            FROM inbox_items
            ORDER BY received_at DESC, rowid DESC
            """
        )
    ]

    draft_rows = connection.execute(
        """
        SELECT l.inbox_item_id AS source_inbox_item_id,
               d.workflow_draft_id,
               d.draft_status,
               d.preview_json,
               d.updated_at
        FROM draft_source_links l
        JOIN workflow_drafts d ON d.workflow_draft_id = l.workflow_draft_id
        ORDER BY d.updated_at DESC, d.rowid DESC
        """
    ).fetchall()

    drafts_by_source: dict[str, list[dict[str, Any]]] = {}
    for row in draft_rows:
        preview = parse_json(row["preview_json"])
        if not isinstance(preview, dict):
            preview = {}
        drafts_by_source.setdefault(str(row["source_inbox_item_id"]), []).append(
            {
                "workflow_draft_id": str(row["workflow_draft_id"]),
                "draft_status": str(row["draft_status"]),
                "intent_type": preview.get("intent_type"),
                "target_object_type": preview.get("target_object_type"),
                "target_action": preview.get("target_action"),
                "summary_text": preview.get("summary_text"),
                "updated_at": str(row["updated_at"]),
                "commit_ready": bool((preview.get("confirmation") or {}).get("commit_ready")),
                "confirm_token": (preview.get("confirmation") or {}).get("confirm_token"),
                "missing_required_fields": preview.get("missing_required_fields") or [],
                "pending_associations": preview.get("pending_associations") or [],
                "candidate_links": preview.get("candidate_links") or [],
                "committed": preview.get("committed"),
            }
        )

    queue_items: list[dict[str, Any]] = []
    for row in source_rows:
        if not is_history_source(row):
            continue
        payload = parse_json(row["raw_payload_json"])
        if not isinstance(payload, dict):
            payload = {}
        category = (payload.get("legacy_history") or {}).get("category")
        drafts = drafts_by_source.get(str(row["inbox_item_id"]), [])
        queue_status = derive_queue_status(drafts)
        if queue_status_filter and queue_status not in queue_status_filter:
            continue
        preview_text = str(row.get("raw_text") or "")
        text_haystack = "\n".join(
            [
                str(row.get("source_message_id") or ""),
                str(category or ""),
                preview_text,
                json.dumps(payload.get("legacy_history") or {}, ensure_ascii=False),
            ]
        ).lower()
        if args.source_message_id and str(row.get("source_message_id")) != args.source_message_id:
            continue
        if tokens and not all(token in text_haystack for token in tokens):
            continue

        queue_items.append(
            {
                "source_inbox_item_id": str(row["inbox_item_id"]),
                "channel_type": str(row["channel_type"]),
                "category": category,
                "source_message_id": row["source_message_id"],
                "received_at": str(row["received_at"]),
                "preview": snippet(preview_text, tokens, width=180),
                "queue_status": queue_status,
                "suggested_intents": infer_suggested_intents({**row, "category": category}),
                "next_action": next_action(queue_status),
                "draft_count": len(drafts),
                "drafts": drafts,
            }
        )

    result = {
        "status": "ok",
        "data_root": str(data_root),
        "queue_count": min(len(queue_items), max(args.limit, 1)),
        "queue_items": queue_items[: max(args.limit, 1)],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
