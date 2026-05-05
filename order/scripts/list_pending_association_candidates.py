#!/usr/bin/env python3
"""List practical candidate targets for one pending association."""

from __future__ import annotations

import argparse
import json
import sqlite3
from typing import Any

from history_common import load_inbox_record
from runtime_common import connect_db, initialize_runtime, resolve_data_root


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="List candidate targets for one pending association.")
    parser.add_argument("--data-root", help="Override the order data root.")
    parser.add_argument("--pending-association-id", required=True, help="Pending association id.")
    parser.add_argument("--limit", type=int, default=10, help="Maximum candidate count.")
    return parser.parse_args()


def load_pending_row(connection: sqlite3.Connection, pending_association_id: str) -> sqlite3.Row:
    row = connection.execute(
        """
        SELECT pending_association_id, inbox_item_id, target_type, target_key, association_status, reason_text, created_at
        FROM pending_associations
        WHERE pending_association_id = ?
        """,
        (pending_association_id,),
    ).fetchone()
    if not row:
        raise ValueError(f"Unknown pending_association_id: {pending_association_id}")
    return row


def latest_related_draft(connection: sqlite3.Connection, inbox_item_id: str) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT d.workflow_draft_id, d.preview_json, d.updated_at
        FROM draft_source_links l
        JOIN workflow_drafts d ON d.workflow_draft_id = l.workflow_draft_id
        WHERE l.inbox_item_id = ?
        ORDER BY d.updated_at DESC, d.rowid DESC
        LIMIT 1
        """,
        (inbox_item_id,),
    ).fetchone()


def parse_preview_json(row: sqlite3.Row | None) -> dict[str, Any]:
    if not row or not row["preview_json"]:
        return {}
    try:
        value = json.loads(str(row["preview_json"]))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def draft_field_map(connection: sqlite3.Connection, workflow_draft_id: str | None) -> dict[str, str]:
    if not workflow_draft_id:
        return {}
    rows = connection.execute(
        """
        SELECT field_name, field_value
        FROM draft_field_values
        WHERE workflow_draft_id = ?
          AND field_value IS NOT NULL
          AND field_value != ''
        """,
        (workflow_draft_id,),
    ).fetchall()
    return {str(row["field_name"]): str(row["field_value"]) for row in rows}


def build_sales_order_label(row: sqlite3.Row) -> str:
    parts = [
        str(row["sales_order_id"]),
        str(row["order_no"] or ""),
        str(row["customer_name"] or ""),
        str(row["product_name"] or ""),
    ]
    return " / ".join(part for part in parts if part)


def build_production_lot_label(row: sqlite3.Row) -> str:
    parts = [
        str(row["production_lot_id"]),
        str(row["lot_no"] or ""),
        str(row["factory_name"] or ""),
        str(row["product_name"] or ""),
        str(row["status"] or ""),
    ]
    return " / ".join(part for part in parts if part)


def sales_order_candidates(connection: sqlite3.Connection, inbox_item: dict[str, Any], fields: dict[str, str], limit: int) -> list[dict[str, Any]]:
    hints = ((inbox_item.get("raw_payload") or {}).get("entity_hints") or {}).get("sales_orders") or []
    scored: dict[int, dict[str, Any]] = {}

    def add_candidate(row: sqlite3.Row, *, reason: str, score_delta: float) -> None:
        candidate = scored.setdefault(
            int(row["sales_order_id"]),
            {
                "target_key": str(row["sales_order_id"]),
                "target_type": "sales_order",
                "label": build_sales_order_label(row),
                "order_no": row["order_no"],
                "customer_name": row["customer_name"],
                "product_name": row["product_name"],
                "qty": row["qty"],
                "promised_delivery_date": row["promised_delivery_date"],
                "score": 0.0,
                "reasons": [],
            },
        )
        candidate["score"] += score_delta
        candidate["reasons"].append(reason)

    for item in hints:
        row = connection.execute(
            """
            SELECT sales_order_id, order_no, customer_name, product_name, qty, promised_delivery_date
            FROM sales_orders
            WHERE sales_order_id = ?
            """,
            (item["sales_order_id"],),
        ).fetchone()
        if row:
            add_candidate(row, reason="legacy history entity hint", score_delta=0.7)

    if fields.get("order_no"):
        for row in connection.execute(
            """
            SELECT sales_order_id, order_no, customer_name, product_name, qty, promised_delivery_date
            FROM sales_orders
            WHERE order_no = ?
            """,
            (fields["order_no"],),
        ):
            add_candidate(row, reason="order_no exact match", score_delta=1.0)

    if fields.get("customer_name"):
        for row in connection.execute(
            """
            SELECT sales_order_id, order_no, customer_name, product_name, qty, promised_delivery_date
            FROM sales_orders
            WHERE customer_name = ?
            """,
            (fields["customer_name"],),
        ):
            add_candidate(row, reason="customer_name match", score_delta=0.25)

    if fields.get("product_name"):
        for row in connection.execute(
            """
            SELECT sales_order_id, order_no, customer_name, product_name, qty, promised_delivery_date
            FROM sales_orders
            WHERE product_name = ?
            """,
            (fields["product_name"],),
        ):
            add_candidate(row, reason="product_name match", score_delta=0.25)
        normalized_target = fields["product_name"].replace("色", "")
        for row in connection.execute(
            """
            SELECT sales_order_id, order_no, customer_name, product_name, qty, promised_delivery_date
            FROM sales_orders
            """
        ):
            product_name = str(row["product_name"] or "")
            normalized_product = product_name.replace("色", "")
            if normalized_target and normalized_product and (
                normalized_target in normalized_product or normalized_product in normalized_target
            ):
                add_candidate(row, reason="product_name fuzzy match", score_delta=0.18)

    candidates = sorted(scored.values(), key=lambda item: (item["score"], item["target_key"]), reverse=True)
    return candidates[:limit]


def production_lot_candidates(
    connection: sqlite3.Connection,
    inbox_item: dict[str, Any],
    fields: dict[str, str],
    draft_preview: dict[str, Any],
    limit: int,
) -> list[dict[str, Any]]:
    hints = ((inbox_item.get("raw_payload") or {}).get("entity_hints") or {}).get("sales_orders") or []
    seed_sales_order_ids = {int(item["sales_order_id"]) for item in hints if item.get("sales_order_id")}
    for item in draft_preview.get("candidate_links") or []:
        if item.get("target_type") == "sales_order" and str(item.get("target_key") or "").isdigit():
            seed_sales_order_ids.add(int(str(item.get("target_key"))))
    scored: dict[int, dict[str, Any]] = {}

    def add_candidate(row: sqlite3.Row, *, reason: str, score_delta: float) -> None:
        candidate = scored.setdefault(
            int(row["production_lot_id"]),
            {
                "target_key": str(row["production_lot_id"]),
                "target_type": "production_lot",
                "label": build_production_lot_label(row),
                "lot_no": row["lot_no"],
                "factory_name": row["factory_name"],
                "product_name": row["product_name"],
                "qty_total": row["qty_total"],
                "status": row["status"],
                "score": 0.0,
                "reasons": [],
            },
        )
        candidate["score"] += score_delta
        candidate["reasons"].append(reason)

    for sales_order_id in sorted(seed_sales_order_ids):
        for row in connection.execute(
            """
            SELECT pl.production_lot_id, pl.lot_no, pl.factory_name, pl.product_name, pl.qty_total, pl.status
            FROM production_lot_order_links pll
            JOIN production_lots pl ON pl.production_lot_id = pll.production_lot_id
            WHERE pll.sales_order_id = ?
            """,
            (sales_order_id,),
        ):
            add_candidate(row, reason=f"linked to hinted sales_order {sales_order_id}", score_delta=0.8)

    if fields.get("product_name"):
        for row in connection.execute(
            """
            SELECT production_lot_id, lot_no, factory_name, product_name, qty_total, status
            FROM production_lots
            WHERE product_name = ?
            """,
            (fields["product_name"],),
        ):
            add_candidate(row, reason="product_name match", score_delta=0.25)

    if fields.get("factory_name"):
        for row in connection.execute(
            """
            SELECT production_lot_id, lot_no, factory_name, product_name, qty_total, status
            FROM production_lots
            WHERE factory_name = ?
            """,
            (fields["factory_name"],),
        ):
            add_candidate(row, reason="factory_name match", score_delta=0.25)

    candidates = sorted(scored.values(), key=lambda item: (item["score"], item["target_key"]), reverse=True)
    return candidates[:limit]


def main() -> int:
    args = parse_args()
    data_root = resolve_data_root(args.data_root)
    initialize_runtime(data_root)
    connection = connect_db(data_root)
    pending_row = load_pending_row(connection, args.pending_association_id)
    inbox_item = load_inbox_record(connection, inbox_item_id=str(pending_row["inbox_item_id"]))
    draft_row = latest_related_draft(connection, str(pending_row["inbox_item_id"]))
    draft_preview = parse_preview_json(draft_row)
    fields = draft_field_map(connection, str(draft_row["workflow_draft_id"]) if draft_row else None)

    if str(pending_row["target_type"]) == "sales_order":
        candidates = sales_order_candidates(connection, inbox_item, fields, args.limit)
    elif str(pending_row["target_type"]) == "production_lot":
        candidates = production_lot_candidates(connection, inbox_item, fields, draft_preview, args.limit)
    else:
        candidates = []

    result = {
        "status": "ok",
        "pending_association": {
            "pending_association_id": str(pending_row["pending_association_id"]),
            "inbox_item_id": str(pending_row["inbox_item_id"]),
            "target_type": str(pending_row["target_type"]),
            "target_key": pending_row["target_key"],
            "association_status": str(pending_row["association_status"]),
            "reason_text": pending_row["reason_text"],
            "created_at": str(pending_row["created_at"]),
        },
        "related_workflow_draft_id": str(draft_row["workflow_draft_id"]) if draft_row else None,
        "draft_fields": fields,
        "candidate_count": len(candidates),
        "candidates": candidates,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
