#!/usr/bin/env python3
"""Resolve one pending association without a payload file."""

from __future__ import annotations

import argparse
import json
import sqlite3

from runtime_common import connect_db, resolve_data_root
from runtime_flow import resolve_pending_association_item


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Resolve one pending association directly.")
    parser.add_argument("--data-root", help="Override the order data root.")
    parser.add_argument("--pending-association-id", required=True, help="Pending association id.")
    parser.add_argument("--target-key", required=True, help="Resolved target primary id or alternate key.")
    parser.add_argument("--reason-text", help="Optional resolution reason.")
    parser.add_argument("--actor-label", help="Optional actor label.")
    return parser.parse_args()


def auto_thread(connection: sqlite3.Connection, pending_association_id: str, target_key: str) -> dict[str, str] | None:
    row = connection.execute(
        """
        SELECT target_type
        FROM pending_associations
        WHERE pending_association_id = ?
        """,
        (pending_association_id,),
    ).fetchone()
    if not row:
        raise ValueError(f"Unknown pending_association_id: {pending_association_id}")
    target_type = str(row["target_type"])
    if target_type == "sales_order":
        order_row = connection.execute(
            """
            SELECT sales_order_id, order_no, customer_name, product_name
            FROM sales_orders
            WHERE sales_order_id = ?
            """,
            (target_key,),
        ).fetchone()
        if order_row:
            title = " / ".join(
                value for value in [order_row["order_no"], order_row["customer_name"], order_row["product_name"]] if value
            )
            return {
                "object_type": "sales_order",
                "object_key": str(order_row["sales_order_id"]),
                "title": title or f"订单#{order_row['sales_order_id']}",
            }
    if target_type == "production_lot":
        lot_row = connection.execute(
            """
            SELECT production_lot_id, lot_no, factory_name, product_name
            FROM production_lots
            WHERE production_lot_id = ?
            """,
            (target_key,),
        ).fetchone()
        if lot_row:
            title = " / ".join(
                value for value in [lot_row["lot_no"], lot_row["factory_name"], lot_row["product_name"]] if value
            )
            return {
                "object_type": "production_lot",
                "object_key": str(lot_row["production_lot_id"]),
                "title": title or f"生产批次#{lot_row['production_lot_id']}",
            }
    return None


def main() -> int:
    args = parse_args()
    data_root = resolve_data_root(args.data_root)
    connection = connect_db(data_root)
    try:
        thread = auto_thread(connection, args.pending_association_id, args.target_key)
    finally:
        connection.close()
    result = resolve_pending_association_item(
        data_root=data_root,
        pending_association_id=args.pending_association_id,
        target_key=str(args.target_key),
        reason_text=args.reason_text,
        actor_label=args.actor_label,
        thread=thread,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
