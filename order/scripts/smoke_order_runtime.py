#!/usr/bin/env python3
"""Smoke test for the order runtime foundation."""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

from runtime_common import connect_db, initialize_runtime, open_guided_intake_draft, persist_input


def main() -> int:
    temp_root = Path(tempfile.mkdtemp(prefix="order-runtime-smoke-"))
    data_root = temp_root / "openclaw-order"
    initialize_runtime(data_root)

    sample_attachment = temp_root / "sample-proof.txt"
    sample_attachment.write_text("sample payment proof", encoding="utf-8")

    persisted = persist_input(
        data_root=data_root,
        channel_type="local-test",
        channel_session_key="session-smoke",
        source_actor="tester",
        source_message_id="msg-smoke-1",
        raw_text="王总那批狗先做 2000 个，付款截图晚点再补。",
        raw_payload={"intent_hint": "production-arrangement"},
        attachments=[{"path": str(sample_attachment), "mime_type": "text/plain"}],
    )
    draft_result = open_guided_intake_draft(
        data_root=data_root,
        inbox_item_id=str(persisted["inbox_item_id"]),
        intent_type="production_arrangement",
        target_object_type="sales_order",
        target_action="update",
        summary_text="王总小狗订单先安排 2000 个进入生产，工厂还没确认。",
        draft_fields={
            "customer_name": "王总",
            "product_name": "小狗",
            "qty": 2000,
        },
        thread={
            "object_type": "sales_order",
            "object_key": "dev/20260419/wang-xiaogou",
            "title": "王总 小狗 2000",
        },
        candidate_links=None,
        pending_targets=None,
        required_fields=None,
        actor_label="smoke-test",
    )

    connection = connect_db(data_root)
    inbox_count = connection.execute("SELECT COUNT(*) FROM inbox_items").fetchone()[0]
    asset_count = connection.execute("SELECT COUNT(*) FROM evidence_assets").fetchone()[0]
    intake_count = connection.execute("SELECT COUNT(*) FROM intake_sessions").fetchone()[0]
    draft_count = connection.execute("SELECT COUNT(*) FROM workflow_drafts").fetchone()[0]
    checkpoint_count = connection.execute(
        "SELECT COUNT(*) FROM draft_checkpoints WHERE checkpoint_status = 'open'"
    ).fetchone()[0]
    thread_count = connection.execute("SELECT COUNT(*) FROM object_threads").fetchone()[0]
    raw_archive_path = connection.execute(
        "SELECT raw_archive_path FROM inbox_items WHERE inbox_item_id = ?",
        (persisted["inbox_item_id"],),
    ).fetchone()[0]
    table_checks = {
        name: connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
            (name,),
        ).fetchone()[0]
        for name in [
            "inbox_items",
            "workflow_drafts",
            "sales_orders",
            "process_templates",
            "intake_session_items",
            "draft_source_links",
            "object_thread_items",
            "v_order_production_status",
            "v_order_finance_status",
        ]
    }
    connection.close()

    result = {
        "status": "ok",
        "data_root": str(data_root),
        "persisted": persisted,
        "guided_intake": draft_result,
        "row_counts": {
            "inbox_items": inbox_count,
            "evidence_assets": asset_count,
            "intake_sessions": intake_count,
            "workflow_drafts": draft_count,
            "open_draft_checkpoints": checkpoint_count,
            "object_threads": thread_count,
        },
        "raw_archive_exists": Path(str(raw_archive_path)).exists(),
        "table_checks": table_checks,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
