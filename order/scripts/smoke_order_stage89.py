#!/usr/bin/env python3
"""End-to-end smoke test for Stage 8-9 order runtime behavior."""

from __future__ import annotations

import json
import tempfile
from datetime import date, timedelta
from pathlib import Path

from runtime_common import connect_db, initialize_runtime, open_guided_intake_draft, persist_input
from runtime_flow import (
    commit_workflow_draft,
    generate_daily_report,
    prepare_draft_confirmation,
    record_settlement_allocations,
    refresh_control_tower,
    resolve_pending_association_item,
)


def persist_and_open(
    *,
    data_root: Path,
    channel_session_key: str,
    source_message_id: str,
    raw_text: str,
    intent_type: str,
    target_object_type: str,
    target_action: str,
    summary_text: str,
    draft_fields: dict[str, object],
    candidate_links: list[dict[str, object]] | None,
    required_fields: list[str] | None = None,
) -> dict[str, object]:
    persisted = persist_input(
        data_root=data_root,
        channel_type="local-test",
        channel_session_key=channel_session_key,
        source_actor="stage89-smoke",
        source_message_id=source_message_id,
        raw_text=raw_text,
        raw_payload={"intent_hint": intent_type},
        attachments=None,
    )
    draft = open_guided_intake_draft(
        data_root=data_root,
        inbox_item_id=str(persisted["inbox_item_id"]),
        intent_type=intent_type,
        target_object_type=target_object_type,
        target_action=target_action,
        summary_text=summary_text,
        draft_fields=draft_fields,
        thread=None,
        candidate_links=candidate_links,
        pending_targets=None,
        required_fields=required_fields,
        actor_label="stage89-smoke",
    )
    return {"persisted": persisted, "draft": draft}


def prepare_and_commit(data_root: Path, workflow_draft_id: str) -> dict[str, object]:
    confirmation = prepare_draft_confirmation(
        data_root=data_root,
        workflow_draft_id=workflow_draft_id,
        actor_label="stage89-smoke",
    )
    token = str(confirmation["confirmation"]["confirm_token"])
    committed = commit_workflow_draft(
        data_root=data_root,
        workflow_draft_id=workflow_draft_id,
        confirm_token=token,
        actor_label="stage89-smoke",
    )
    return {"confirmation": confirmation, "committed": committed}


def main() -> int:
    temp_root = Path(tempfile.mkdtemp(prefix="order-stage89-smoke-"))
    data_root = temp_root / "openclaw-order"
    initialize_runtime(data_root)

    today = date.today()
    yesterday = today - timedelta(days=1)
    tomorrow = today + timedelta(days=1)

    sales_order_flow = persist_and_open(
        data_root=data_root,
        channel_session_key="stage89-order",
        source_message_id="msg-order-1",
        raw_text="王总的小兔子 100 个，单价 12.5，昨天交期，先建单。",
        intent_type="sales_order",
        target_object_type="sales_order",
        target_action="create",
        summary_text="王总小兔子订单，100 个，昨天交期。",
        draft_fields={
            "order_no": "SO-SMOKE-001",
            "customer_name": "王总",
            "product_name": "小兔子",
            "qty": 100,
            "unit": "个",
            "confirmed_unit_price": 12.5,
            "confirmed_total_amount": 1250,
            "promised_delivery_date": yesterday.isoformat(),
            "current_factory": "定远乡工厂",
            "current_step": "平车",
            "progress_text": "待安排平车",
        },
        candidate_links=None,
    )
    sales_order_commit = prepare_and_commit(data_root, str(sales_order_flow["draft"]["workflow_draft_id"]))
    sales_order_id = int(sales_order_commit["committed"]["committed_object"]["object_id"])

    pending_payment_flow = persist_and_open(
        data_root=data_root,
        channel_session_key="stage89-order",
        source_message_id="msg-order-2",
        raw_text="王总今天先打了 600，还没说是哪张单。",
        intent_type="payment_receipt",
        target_object_type="cash_transaction",
        target_action="create",
        summary_text="王总先打来 600 预付款，但还没显式指到具体订单。",
        draft_fields={
            "direction": "收款",
            "amount": 600,
            "counterparty_name": "王总",
            "transaction_date": today.isoformat(),
            "purpose": "预付款",
        },
        candidate_links=None,
    )

    connection = connect_db(data_root)
    pending_association_id = connection.execute(
        """
        SELECT pending_association_id
        FROM pending_associations
        WHERE inbox_item_id = ?
          AND association_status = 'unresolved'
        ORDER BY rowid DESC
        LIMIT 1
        """,
        (pending_payment_flow["persisted"]["inbox_item_id"],),
    ).fetchone()[0]
    connection.close()

    resolve_result = resolve_pending_association_item(
        data_root=data_root,
        pending_association_id=str(pending_association_id),
        target_key=str(sales_order_id),
        reason_text="手动确认这笔预付款对应 SO-SMOKE-001。",
        actor_label="stage89-smoke",
        thread={
            "object_type": "sales_order",
            "object_key": str(sales_order_id),
            "title": "SO-SMOKE-001",
        },
    )
    receipt_commit = prepare_and_commit(data_root, str(pending_payment_flow["draft"]["workflow_draft_id"]))
    receipt_cash_transaction_id = int(receipt_commit["committed"]["committed_object"]["object_id"])

    receivable_flow = persist_and_open(
        data_root=data_root,
        channel_session_key="stage89-order",
        source_message_id="msg-order-3",
        raw_text="这单预付款应收 600，今天到。",
        intent_type="receivable_record",
        target_object_type="receivable",
        target_action="create",
        summary_text="给 SO-SMOKE-001 建一笔 600 的预付款应收。",
        draft_fields={
            "receivable_no": "AR-SMOKE-001",
            "receivable_type": "deposit",
            "amount_due": 600,
            "due_date": today.isoformat(),
            "collection_mode": "bank_transfer",
        },
        candidate_links=[{"target_type": "sales_order", "target_key": str(sales_order_id), "confidence_score": 1.0}],
        required_fields=None,
    )
    receivable_commit = prepare_and_commit(data_root, str(receivable_flow["draft"]["workflow_draft_id"]))
    receivable_id = int(receivable_commit["committed"]["committed_object"]["object_id"])

    receipt_preview = record_settlement_allocations(
        data_root=data_root,
        cash_transaction_id=receipt_cash_transaction_id,
        allocations=[{"target_type": "receivable", "target_id": receivable_id, "allocated_amount": 600}],
        actor_label="stage89-smoke",
        replace_existing=True,
        require_full_amount=True,
        dry_run=True,
    )
    receipt_allocation = record_settlement_allocations(
        data_root=data_root,
        cash_transaction_id=receipt_cash_transaction_id,
        allocations=[{"target_type": "receivable", "target_id": receivable_id, "allocated_amount": 600}],
        actor_label="stage89-smoke",
        replace_existing=True,
        require_full_amount=True,
        confirm_token=str(receipt_preview["confirmation"]["confirm_token"]),
    )

    payable_one_flow = persist_and_open(
        data_root=data_root,
        channel_session_key="stage89-order",
        source_message_id="msg-order-4",
        raw_text="弘辉复合这单先记 120 应付。",
        intent_type="payable_record",
        target_object_type="payable",
        target_action="create",
        summary_text="给弘辉复合建一笔 120 的应付。",
        draft_fields={
            "payable_no": "AP-SMOKE-001",
            "supplier_name": "弘辉复合",
            "payable_type": "composite",
            "amount_due": 120,
            "due_date": today.isoformat(),
        },
        candidate_links=[{"target_type": "sales_order", "target_key": str(sales_order_id), "confidence_score": 1.0}],
        required_fields=None,
    )
    payable_one_commit = prepare_and_commit(data_root, str(payable_one_flow["draft"]["workflow_draft_id"]))
    payable_one_id = int(payable_one_commit["committed"]["committed_object"]["object_id"])

    payable_two_flow = persist_and_open(
        data_root=data_root,
        channel_session_key="stage89-order",
        source_message_id="msg-order-5",
        raw_text="刘旭那边再记 180 应付。",
        intent_type="payable_record",
        target_object_type="payable",
        target_action="create",
        summary_text="给刘旭建一笔 180 的应付。",
        draft_fields={
            "payable_no": "AP-SMOKE-002",
            "supplier_name": "刘旭",
            "payable_type": "laser_cut",
            "amount_due": 180,
            "due_date": tomorrow.isoformat(),
        },
        candidate_links=[{"target_type": "sales_order", "target_key": str(sales_order_id), "confidence_score": 1.0}],
        required_fields=None,
    )
    payable_two_commit = prepare_and_commit(data_root, str(payable_two_flow["draft"]["workflow_draft_id"]))
    payable_two_id = int(payable_two_commit["committed"]["committed_object"]["object_id"])

    pay_out_flow = persist_and_open(
        data_root=data_root,
        channel_session_key="stage89-order",
        source_message_id="msg-order-6",
        raw_text="今天统一付出去 300 给供应商组。",
        intent_type="cash_transaction_record",
        target_object_type="cash_transaction",
        target_action="create",
        summary_text="记一笔 300 的付款，后续分摊到两笔应付。",
        draft_fields={
            "direction": "付款",
            "amount": 300,
            "counterparty_name": "供应商组",
            "transaction_date": today.isoformat(),
            "purpose": "加工费用",
            "payment_method": "bank_transfer",
        },
        candidate_links=[{"target_type": "sales_order", "target_key": str(sales_order_id), "confidence_score": 0.9}],
        required_fields=None,
    )
    pay_out_commit = prepare_and_commit(data_root, str(pay_out_flow["draft"]["workflow_draft_id"]))
    pay_out_cash_transaction_id = int(pay_out_commit["committed"]["committed_object"]["object_id"])

    payout_preview = record_settlement_allocations(
        data_root=data_root,
        cash_transaction_id=pay_out_cash_transaction_id,
        allocations=[
            {"target_type": "payable", "target_id": payable_one_id, "allocated_amount": 120},
            {"target_type": "payable", "target_id": payable_two_id, "allocated_amount": 180},
        ],
        actor_label="stage89-smoke",
        replace_existing=True,
        require_full_amount=True,
        dry_run=True,
    )
    payout_allocation = record_settlement_allocations(
        data_root=data_root,
        cash_transaction_id=pay_out_cash_transaction_id,
        allocations=[
            {"target_type": "payable", "target_id": payable_one_id, "allocated_amount": 120},
            {"target_type": "payable", "target_id": payable_two_id, "allocated_amount": 180},
        ],
        actor_label="stage89-smoke",
        replace_existing=True,
        require_full_amount=True,
        confirm_token=str(payout_preview["confirmation"]["confirm_token"]),
    )

    work_order_flow = persist_and_open(
        data_root=data_root,
        channel_session_key="stage89-order",
        source_message_id="msg-order-7",
        raw_text="冯杰这边平车今天要跟进，100 个。",
        intent_type="work_order_record",
        target_object_type="work_order",
        target_action="create",
        summary_text="给冯杰安排一笔平车作业，今天到期。",
        draft_fields={
            "customer_name": "王总",
            "product_name": "小兔子",
            "qty": 100,
            "factory_name": "冯杰",
            "work_type": "平车",
            "planned_qty": 100,
            "planned_due_at": today.isoformat(),
        },
        candidate_links=[{"target_type": "sales_order", "target_key": str(sales_order_id), "confidence_score": 1.0}],
        required_fields=None,
    )
    work_order_commit = prepare_and_commit(data_root, str(work_order_flow["draft"]["workflow_draft_id"]))

    control_tower = refresh_control_tower(
        data_root=data_root,
        as_of_date=today.isoformat(),
        actor_label="stage89-smoke",
    )
    daily_report = generate_daily_report(
        data_root=data_root,
        report_date=today.isoformat(),
        actor_label="stage89-smoke",
        refresh_first=False,
    )

    connection = connect_db(data_root)
    row_counts = {
        "sales_orders": connection.execute("SELECT COUNT(*) FROM sales_orders").fetchone()[0],
        "receivables": connection.execute("SELECT COUNT(*) FROM receivables").fetchone()[0],
        "payables": connection.execute("SELECT COUNT(*) FROM payables").fetchone()[0],
        "cash_transactions": connection.execute("SELECT COUNT(*) FROM cash_transactions").fetchone()[0],
        "work_orders": connection.execute("SELECT COUNT(*) FROM work_orders").fetchone()[0],
        "followup_items": connection.execute("SELECT COUNT(*) FROM v_open_followups").fetchone()[0],
        "exception_cases": connection.execute("SELECT COUNT(*) FROM v_open_exceptions").fetchone()[0],
        "alerts": connection.execute("SELECT COUNT(*) FROM v_open_alerts").fetchone()[0],
        "daily_reports": connection.execute("SELECT COUNT(*) FROM daily_reports").fetchone()[0],
    }
    receivable_status = dict(
        connection.execute(
            "SELECT receivable_status, amount_received FROM receivables WHERE receivable_id = ?",
            (receivable_id,),
        ).fetchone()
    )
    payable_statuses = [
        dict(row)
        for row in connection.execute(
            "SELECT payable_no, payable_status, amount_paid FROM payables ORDER BY payable_id"
        ).fetchall()
    ]
    draft_statuses = [
        dict(row)
        for row in connection.execute(
            "SELECT workflow_draft_id, draft_status FROM workflow_drafts ORDER BY rowid"
        ).fetchall()
    ]
    connection.close()

    result = {
        "status": "ok",
        "data_root": str(data_root),
        "sales_order_commit": sales_order_commit,
        "pending_association_resolution": resolve_result,
        "receipt_commit": receipt_commit,
        "receivable_commit": receivable_commit,
        "receipt_allocation": receipt_allocation,
        "payables": [payable_one_commit, payable_two_commit],
        "payout_commit": pay_out_commit,
        "payout_allocation": payout_allocation,
        "work_order_commit": work_order_commit,
        "control_tower": control_tower,
        "daily_report_summary": daily_report["report_json"],
        "row_counts": row_counts,
        "receivable_status": receivable_status,
        "payable_statuses": payable_statuses,
        "draft_statuses": draft_statuses,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
