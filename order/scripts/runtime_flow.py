#!/usr/bin/env python3
"""Stage 8-9 runtime helpers for guided intake, commit guards, and control tower."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from runtime_common import connect_db, initialize_runtime, json_dumps, upsert_object_thread, utc_now


CONFIRMATION_CHECKPOINT_TYPE = "confirmation_required"
EPSILON = 1e-6
DERIVED_ALERT_PREFIX = "derived:"
DERIVED_NOTES_PREFIX = "[derived]"
DERIVED_COMMITMENT_TYPES = {"delivery_due", "receivable_due", "payable_due", "work_due"}


def parse_json(text: str | None) -> dict[str, Any] | list[Any] | None:
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def as_float(value: Any) -> float | None:
    if value in (None, "", "None"):
        return None
    try:
        return float(str(value).strip())
    except ValueError:
        return None


def first_field(fields: dict[str, Any], *names: str) -> Any:
    for name in names:
        value = fields.get(name)
        if value not in (None, "", "None"):
            return value
    return None


def as_ratio(value: Any) -> float | None:
    if value in (None, "", "None"):
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("%"):
        number = as_float(text[:-1])
        return None if number is None else number / 100
    number = as_float(text)
    if number is None:
        return None
    return number / 100 if number > 1 else number


def as_date_text(value: Any) -> str | None:
    if value in (None, "", "None"):
        return None
    text = str(value).strip()
    if not text:
        return None
    if "T" in text:
        return text.split("T", 1)[0]
    return text


def as_int(value: Any) -> int | None:
    number = as_float(value)
    if number is None:
        return None
    return int(number)


def iso_today() -> str:
    return date.today().isoformat()


def is_open_business_status(status: str | None) -> bool:
    return status not in (None, "", "closed", "cancelled", "done", "paid", "received", "已取消", "已完成", "已关闭", "已结清")


def field_value_map(connection: sqlite3.Connection, workflow_draft_id: str) -> dict[str, str]:
    rows = connection.execute(
        """
        SELECT field_name, field_value
        FROM draft_field_values
        WHERE workflow_draft_id = ?
          AND field_value IS NOT NULL
          AND field_value != ''
        ORDER BY rowid
        """,
        (workflow_draft_id,),
    ).fetchall()
    values: dict[str, str] = {}
    for row in rows:
        values[str(row["field_name"])] = str(row["field_value"])
    return values


def source_inbox_ids(connection: sqlite3.Connection, workflow_draft_id: str) -> list[str]:
    return [
        str(row["inbox_item_id"])
        for row in connection.execute(
            """
            SELECT inbox_item_id
            FROM draft_source_links
            WHERE workflow_draft_id = ?
            ORDER BY rowid
            """,
            (workflow_draft_id,),
        )
    ]


def open_checkpoints(connection: sqlite3.Connection, workflow_draft_id: str) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT draft_checkpoint_id, checkpoint_type, prompt_text
        FROM draft_checkpoints
        WHERE workflow_draft_id = ?
          AND checkpoint_status = 'open'
        ORDER BY rowid
        """,
        (workflow_draft_id,),
    ).fetchall()


def fetch_draft_context(connection: sqlite3.Connection, workflow_draft_id: str) -> dict[str, Any]:
    draft_row = connection.execute(
        """
        SELECT d.*, s.channel_type, s.channel_session_key, s.intent_type
        FROM workflow_drafts d
        JOIN intake_sessions s ON s.intake_session_id = d.intake_session_id
        WHERE d.workflow_draft_id = ?
        """,
        (workflow_draft_id,),
    ).fetchone()
    if not draft_row:
        raise ValueError(f"Unknown workflow_draft_id: {workflow_draft_id}")
    preview = parse_json(draft_row["preview_json"]) or {}
    if not isinstance(preview, dict):
        preview = {}
    return {
        "draft_row": draft_row,
        "preview": preview,
        "fields": field_value_map(connection, workflow_draft_id),
        "source_inbox_ids": source_inbox_ids(connection, workflow_draft_id),
        "open_checkpoints": open_checkpoints(connection, workflow_draft_id),
    }


def draft_readiness(context: dict[str, Any]) -> dict[str, Any]:
    preview = context["preview"]
    blockers = [
        {
            "checkpoint_type": str(row["checkpoint_type"]),
            "prompt_text": str(row["prompt_text"]),
        }
        for row in context["open_checkpoints"]
        if row["checkpoint_type"] != CONFIRMATION_CHECKPOINT_TYPE
    ]
    missing_required = list(preview.get("missing_required_fields") or [])
    pending_associations = list(preview.get("pending_associations") or [])
    commit_ready = not blockers and not missing_required and not pending_associations
    return {
        "commit_ready": commit_ready,
        "blockers": blockers,
        "missing_required_fields": missing_required,
        "pending_associations": pending_associations,
    }


def refresh_draft_status(connection: sqlite3.Connection, workflow_draft_id: str, *, now: str) -> str:
    row = connection.execute(
        """
        SELECT intake_session_id, draft_status
        FROM workflow_drafts
        WHERE workflow_draft_id = ?
        """,
        (workflow_draft_id,),
    ).fetchone()
    if not row:
        raise ValueError(f"Unknown workflow_draft_id: {workflow_draft_id}")
    if row["draft_status"] == "committed":
        return "committed"
    blockers = [
        checkpoint
        for checkpoint in open_checkpoints(connection, workflow_draft_id)
        if checkpoint["checkpoint_type"] != CONFIRMATION_CHECKPOINT_TYPE
    ]
    new_status = "collecting" if blockers else "needs_confirmation"
    connection.execute(
        """
        UPDATE workflow_drafts
        SET draft_status = ?, updated_at = ?
        WHERE workflow_draft_id = ?
        """,
        (new_status, now, workflow_draft_id),
    )
    connection.execute(
        """
        UPDATE intake_sessions
        SET session_status = ?, last_active_at = ?
        WHERE intake_session_id = ?
        """,
        (new_status, now, row["intake_session_id"]),
    )
    return new_status


def set_confirmation_checkpoint(
    connection: sqlite3.Connection,
    workflow_draft_id: str,
    *,
    summary_text: str,
    now: str,
) -> str:
    row = connection.execute(
        """
        SELECT draft_checkpoint_id
        FROM draft_checkpoints
        WHERE workflow_draft_id = ?
          AND checkpoint_type = ?
        ORDER BY rowid DESC
        LIMIT 1
        """,
        (workflow_draft_id, CONFIRMATION_CHECKPOINT_TYPE),
    ).fetchone()
    if row:
        draft_checkpoint_id = str(row["draft_checkpoint_id"])
        connection.execute(
            """
            UPDATE draft_checkpoints
            SET prompt_text = ?, checkpoint_status = 'open', created_at = ?, resolved_at = NULL
            WHERE draft_checkpoint_id = ?
            """,
            (summary_text, now, draft_checkpoint_id),
        )
        return draft_checkpoint_id

    draft_checkpoint_id = f"dcp_{uuid.uuid4().hex}"
    connection.execute(
        """
        INSERT INTO draft_checkpoints (
            draft_checkpoint_id, workflow_draft_id, checkpoint_type,
            prompt_text, checkpoint_status, created_at, resolved_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            draft_checkpoint_id,
            workflow_draft_id,
            CONFIRMATION_CHECKPOINT_TYPE,
            summary_text,
            "open",
            now,
            None,
        ),
    )
    return draft_checkpoint_id


def resolve_confirmation_checkpoint(connection: sqlite3.Connection, workflow_draft_id: str, *, now: str) -> None:
    connection.execute(
        """
        UPDATE draft_checkpoints
        SET checkpoint_status = 'resolved', resolved_at = ?
        WHERE workflow_draft_id = ?
          AND checkpoint_type = ?
          AND checkpoint_status = 'open'
        """,
        (now, workflow_draft_id, CONFIRMATION_CHECKPOINT_TYPE),
    )


def ensure_party(connection: sqlite3.Connection, party_name: str | None, party_role: str) -> int | None:
    if not party_name:
        return None
    row = connection.execute(
        """
        SELECT party_id
        FROM parties
        WHERE party_name = ? AND party_role = ?
        LIMIT 1
        """,
        (party_name, party_role),
    ).fetchone()
    if row:
        return int(row["party_id"])
    cursor = connection.execute(
        """
        INSERT INTO parties (party_name, party_role, source)
        VALUES (?, ?, ?)
        """,
        (party_name, party_role, "local_first"),
    )
    return int(cursor.lastrowid)


def ensure_product(connection: sqlite3.Connection, product_name: str | None, spec_text: str | None) -> int | None:
    if not product_name:
        return None
    row = connection.execute(
        """
        SELECT product_id
        FROM products
        WHERE product_name = ?
          AND COALESCE(spec_text, '') = COALESCE(?, '')
        LIMIT 1
        """,
        (product_name, spec_text),
    ).fetchone()
    if row:
        return int(row["product_id"])
    cursor = connection.execute(
        """
        INSERT INTO products (product_name, spec_text, source)
        VALUES (?, ?, ?)
        """,
        (product_name, spec_text, "local_first"),
    )
    return int(cursor.lastrowid)


def resolve_target_row_id(connection: sqlite3.Connection, target_type: str, target_key: str | None) -> int | None:
    if not target_key:
        return None
    lookup = {
        "sales_order": ("sales_orders", "sales_order_id", ("order_no", "legacy_record_id")),
        "production_lot": ("production_lots", "production_lot_id", ("lot_no", "legacy_record_id")),
        "receivable": ("receivables", "receivable_id", ("receivable_no",)),
        "payable": ("payables", "payable_id", ("payable_no",)),
        "return_case": ("return_cases", "return_case_id", ()),
        "refund": ("refunds", "refund_id", ()),
        "supplier_deduction": ("supplier_deductions", "supplier_deduction_id", ()),
        "work_order": ("work_orders", "work_order_id", ("work_order_no",)),
        "cash_transaction": ("cash_transactions", "cash_transaction_id", ("legacy_record_id",)),
        "shipment": ("shipments", "shipment_id", ("legacy_record_id",)),
    }
    if target_type not in lookup:
        return None
    table_name, pk_name, alt_fields = lookup[target_type]
    normalized_key = str(target_key)
    if normalized_key.startswith(f"{target_type}:"):
        normalized_key = normalized_key.split(":", 1)[1]
    if normalized_key.isdigit():
        row = connection.execute(
            f"SELECT {pk_name} FROM {table_name} WHERE {pk_name} = ? LIMIT 1",
            (int(normalized_key),),
        ).fetchone()
        if row:
            return int(row[pk_name])
    for field_name in alt_fields:
        row = connection.execute(
            f"SELECT {pk_name} FROM {table_name} WHERE {field_name} = ? LIMIT 1",
            (str(target_key),),
        ).fetchone()
        if row:
            return int(row[pk_name])
    return None


def resolve_preview_target_id(connection: sqlite3.Connection, preview: dict[str, Any], target_type: str) -> int | None:
    for item in preview.get("candidate_links") or []:
        if item.get("target_type") != target_type:
            continue
        target_id = resolve_target_row_id(connection, target_type, str(item.get("target_key")))
        if target_id is not None:
            return target_id
    thread = preview.get("thread") or {}
    if thread.get("object_type") == target_type:
        target_id = resolve_target_row_id(connection, target_type, str(thread.get("object_key")))
        if target_id is not None:
            return target_id
    return None


def describe_object(connection: sqlite3.Connection, object_type: str, object_id: str) -> str:
    if object_type == "sales_order":
        row = connection.execute(
            """
            SELECT order_no, customer_name, product_name, qty, promised_delivery_date
            FROM sales_orders
            WHERE sales_order_id = ?
            """,
            (object_id,),
        ).fetchone()
        if row:
            parts = [row["order_no"] or f"订单#{object_id}", row["customer_name"], row["product_name"]]
            if row["qty"]:
                parts.append(f"{row['qty']}件")
            if row["promised_delivery_date"]:
                parts.append(f"交期 {row['promised_delivery_date']}")
            return " / ".join([str(part) for part in parts if part])
    if object_type == "receivable":
        row = connection.execute(
            """
            SELECT receivable_no, receivable_type, amount_due, due_date
            FROM receivables
            WHERE receivable_id = ?
            """,
            (object_id,),
        ).fetchone()
        if row:
            return f"{row['receivable_no'] or ('应收#' + str(object_id))} / {row['receivable_type']} / {row['amount_due'] or 0} / 截止 {row['due_date'] or '未定'}"
    if object_type == "payable":
        row = connection.execute(
            """
            SELECT p.payable_no, p.payable_type, p.amount_due, p.due_date, party.party_name
            FROM payables p
            LEFT JOIN parties party ON party.party_id = p.party_id
            WHERE p.payable_id = ?
            """,
            (object_id,),
        ).fetchone()
        if row:
            return f"{row['payable_no'] or ('应付#' + str(object_id))} / {row['party_name'] or '未指定供应商'} / {row['payable_type']} / {row['amount_due'] or 0} / 截止 {row['due_date'] or '未定'}"
    if object_type == "work_order":
        row = connection.execute(
            """
            SELECT work_order_no, work_type, planned_due_at, planned_qty
            FROM work_orders
            WHERE work_order_id = ?
            """,
            (object_id,),
        ).fetchone()
        if row:
            return f"{row['work_order_no'] or ('作业#' + str(object_id))} / {row['work_type']} / {row['planned_qty'] or 0} / 截止 {row['planned_due_at'] or '未定'}"
    if object_type == "cash_transaction":
        row = connection.execute(
            """
            SELECT direction, counterparty_name, amount, transaction_date
            FROM cash_transactions
            WHERE cash_transaction_id = ?
            """,
            (object_id,),
        ).fetchone()
        if row:
            return f"{row['direction']} / {row['counterparty_name'] or '未指定对手方'} / {row['amount'] or 0} / {row['transaction_date'] or '未定'}"
    if object_type == "return_case":
        row = connection.execute(
            """
            SELECT case_type, opened_at, case_status, refund_expected_amount, supplier_deduction_expected_amount
            FROM return_cases
            WHERE return_case_id = ?
            """,
            (object_id,),
        ).fetchone()
        if row:
            return (
                f"{row['case_type']} / {row['case_status']} / {row['opened_at']} / "
                f"预计退款 {row['refund_expected_amount'] or 0} / 预计扣款 {row['supplier_deduction_expected_amount'] or 0}"
            )
    if object_type == "refund":
        row = connection.execute(
            """
            SELECT refund_amount, refund_status
            FROM refunds
            WHERE refund_id = ?
            """,
            (object_id,),
        ).fetchone()
        if row:
            return f"退款#{object_id} / {row['refund_amount'] or 0} / {row['refund_status']}"
    if object_type == "supplier_deduction":
        row = connection.execute(
            """
            SELECT d.deduction_amount, d.deduction_status, d.deduction_reason, p.party_name
            FROM supplier_deductions d
            LEFT JOIN parties p ON p.party_id = d.party_id
            WHERE d.supplier_deduction_id = ?
            """,
            (object_id,),
        ).fetchone()
        if row:
            return (
                f"供应商扣款#{object_id} / {row['party_name'] or '未指定供应商'} / "
                f"{row['deduction_amount'] or 0} / {row['deduction_status']}"
            )
    return f"{object_type}#{object_id}"


def confirmation_summary_lines(connection: sqlite3.Connection, context: dict[str, Any]) -> list[str]:
    preview = context["preview"]
    fields = context["fields"]
    target_object_type = preview.get("target_object_type") or preview.get("intent_type") or "record"
    target_action = preview.get("target_action") or "create"
    summary_lines = [f"准备{target_action} {target_object_type}。"]
    if preview.get("summary_text"):
        summary_lines.append(f"当前理解：{preview['summary_text']}")

    focus_fields = {
        "sales_order": [
            "order_no",
            "customer_name",
            "product_name",
            "spec_text",
            "qty",
            "unit",
            "confirmed_unit_price",
            "confirmed_total_amount",
            "promised_delivery_date",
        ],
        "receivable": ["receivable_type", "amount_due", "due_date", "collection_mode", "sales_order_ref"],
        "payable": ["supplier_name", "payable_type", "amount_due", "due_date", "billing_mode"],
        "cash_transaction": ["direction", "counterparty_name", "amount", "transaction_date", "purpose"],
        "shipment": ["shipment_type", "shipment_date", "factory_name", "finished_qty", "cut_qty"],
        "work_order": ["work_type", "provider_name", "planned_qty", "planned_due_at"],
        "return_case": [
            "case_type",
            "opened_at",
            "opened_by",
            "customer_name",
            "reason_text",
            "refund_expected_amount",
            "supplier_deduction_expected_amount",
            "case_status",
        ],
        "refund": ["refund_amount", "refund_status", "notes"],
        "supplier_deduction": ["supplier_name", "deduction_amount", "deduction_reason", "deduction_status"],
    }.get(str(target_object_type), [])

    rendered_fields = []
    seen = set()
    for field_name in focus_fields + sorted(fields.keys()):
        if field_name in seen or field_name not in fields:
            continue
        seen.add(field_name)
        rendered_fields.append(f"- {field_name}: {fields[field_name]}")
    if rendered_fields:
        summary_lines.append("已识别字段：")
        summary_lines.extend(rendered_fields)

    candidate_links = preview.get("candidate_links") or []
    if candidate_links:
        summary_lines.append("候选关联：")
        for item in candidate_links:
            summary_lines.append(
                f"- {item.get('target_type')}: {item.get('target_key')} (置信度 {item.get('confidence_score') or '未给出'})"
            )
    pending_associations = preview.get("pending_associations") or []
    if pending_associations:
        summary_lines.append("待关联对象：")
        for item in pending_associations:
            summary_lines.append(f"- {item.get('target_type')}: {item.get('reason_text') or '待确认'}")
    return summary_lines


def prepare_draft_confirmation(
    *,
    data_root: Path,
    workflow_draft_id: str,
    actor_label: str | None,
) -> dict[str, Any]:
    initialize_runtime(data_root)
    connection = connect_db(data_root)
    now = utc_now()
    context = fetch_draft_context(connection, workflow_draft_id)
    readiness = draft_readiness(context)
    preview = dict(context["preview"])
    summary_lines = confirmation_summary_lines(connection, context)
    summary_text = "\n".join(summary_lines)

    confirmation_payload: dict[str, Any] = {
        "summary_lines": summary_lines,
        "summary_text": summary_text,
        "generated_at": now,
        "commit_ready": readiness["commit_ready"],
        "blockers": readiness["blockers"],
        "missing_required_fields": readiness["missing_required_fields"],
        "pending_associations": readiness["pending_associations"],
    }
    if readiness["commit_ready"]:
        confirmation_payload["confirm_token"] = f"confirm-{uuid.uuid4().hex[:10]}"
        set_confirmation_checkpoint(connection, workflow_draft_id, summary_text=summary_text, now=now)
    else:
        resolve_confirmation_checkpoint(connection, workflow_draft_id, now=now)

    preview["confirmation"] = confirmation_payload
    connection.execute(
        """
        UPDATE workflow_drafts
        SET preview_json = ?, updated_at = ?
        WHERE workflow_draft_id = ?
        """,
        (json_dumps(preview, indent=2), now, workflow_draft_id),
    )
    draft_status = refresh_draft_status(connection, workflow_draft_id, now=now)
    connection.execute(
        """
        INSERT INTO audit_log (
            object_type, object_id, action_type, actor_label, new_value_json, reason_text, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "workflow_draft",
            workflow_draft_id,
            "confirmation_prepared",
            actor_label,
            json_dumps(confirmation_payload),
            "Prepared a reviewable confirmation summary before formal commit.",
            now,
        ),
    )
    connection.commit()
    connection.close()
    return {
        "status": "confirmation_prepared",
        "workflow_draft_id": workflow_draft_id,
        "draft_status": draft_status,
        "commit_ready": readiness["commit_ready"],
        "confirmation": confirmation_payload,
    }


def insert_sales_order(connection: sqlite3.Connection, context: dict[str, Any], now: str) -> dict[str, str]:
    preview = context["preview"]
    fields = context["fields"]
    customer_name = first_field(fields, "customer_name", "customer", "buyer_name", "客户", "客户名")
    product_name = first_field(fields, "product_name", "product", "item_name", "产品", "款式")
    spec_text = first_field(fields, "spec_text", "spec", "规格", "款式规格")
    qty = as_float(first_field(fields, "qty", "quantity", "order_qty", "数量"))
    confirmed_unit_price = as_float(
        first_field(fields, "confirmed_unit_price", "unit_price", "price", "confirmed_price", "单价", "确认单价")
    )
    confirmed_total_amount = as_float(
        first_field(fields, "confirmed_total_amount", "total_amount", "order_amount", "amount", "订单金额", "总金额")
    )
    if confirmed_total_amount is None and qty is not None and confirmed_unit_price is not None:
        confirmed_total_amount = qty * confirmed_unit_price
    deposit_ratio = as_ratio(first_field(fields, "deposit_ratio", "deposit_rate", "deposit_percent", "定金比例", "预付款比例"))
    deposit_expected = as_float(
        first_field(fields, "deposit_expected_amount", "expected_deposit_amount", "deposit_amount_expected", "应收定金")
    )
    if deposit_expected is None and deposit_ratio is not None and confirmed_total_amount is not None:
        deposit_expected = confirmed_total_amount * deposit_ratio
    deposit_received_amount = as_float(
        first_field(fields, "deposit_received_amount", "deposit_paid_amount", "deposit_amount", "paid_deposit_amount", "已收定金")
    )
    received_amount = as_float(first_field(fields, "received_amount", "paid_amount", "收款金额"))
    if received_amount is None:
        received_amount = deposit_received_amount or 0.0
    outstanding_amount = as_float(first_field(fields, "outstanding_amount", "unpaid_amount", "balance_amount", "尾款"))
    if outstanding_amount is None and confirmed_total_amount is not None:
        outstanding_amount = confirmed_total_amount - received_amount

    ensure_party(connection, customer_name, "customer")
    ensure_product(connection, product_name, spec_text)

    existing_id = resolve_preview_target_id(connection, preview, "sales_order")
    object_key = fields.get("order_no")
    title = " / ".join([value for value in [object_key, customer_name, product_name] if value])
    raw_fields_json = json_dumps(
        {
            "draft_id": context["draft_row"]["workflow_draft_id"],
            "fields": fields,
            "preview_summary": preview.get("summary_text"),
        }
    )

    payload = {
        "order_no": fields.get("order_no"),
        "order_date": as_date_text(fields.get("order_date")) or iso_today(),
        "order_type": fields.get("order_type") or "customer_order",
        "customer_name": customer_name,
        "product_name": product_name,
        "spec_text": spec_text,
        "qty": qty,
        "unit": first_field(fields, "unit", "单位") or "个",
        "confirmed_unit_price": confirmed_unit_price,
        "confirmed_total_amount": confirmed_total_amount,
        "promised_delivery_date": as_date_text(fields.get("promised_delivery_date")),
        "order_status": fields.get("order_status") or "draft",
        "deposit_ratio": deposit_ratio,
        "deposit_expected_amount": deposit_expected,
        "deposit_received_amount": deposit_received_amount,
        "received_amount": received_amount,
        "outstanding_amount": outstanding_amount,
        "receipt_status": fields.get("receipt_status") or ("received" if outstanding_amount is not None and outstanding_amount <= EPSILON else "pending"),
        "invoice_type": fields.get("invoice_type"),
        "invoice_status": fields.get("invoice_status") or "pending",
        "invoice_amount": as_float(fields.get("invoice_amount")),
        "notes": first_field(fields, "notes", "remark", "备注"),
        "current_step": first_field(fields, "current_step", "production_step", "当前环节"),
        "current_factory": first_field(fields, "current_factory", "factory_name", "provider_name", "工厂"),
        "progress_text": first_field(fields, "progress_text", "summary_text", "remark", "进度"),
        "processing_cost": as_float(fields.get("processing_cost")),
        "material_cost": as_float(fields.get("material_cost")),
        "delivered_qty": as_float(fields.get("delivered_qty")),
        "total_cost": as_float(fields.get("total_cost")),
        "cut_pieces_sent_qty": as_float(fields.get("cut_pieces_sent_qty")),
        "finished_goods_returned_qty": as_float(fields.get("finished_goods_returned_qty")),
        "raw_fields_json": raw_fields_json,
    }

    if existing_id is not None and preview.get("target_action") == "update":
        assignments = ", ".join(f"{column} = ?" for column in payload)
        connection.execute(
            f"UPDATE sales_orders SET {assignments} WHERE sales_order_id = ?",
            tuple(payload.values()) + (existing_id,),
        )
        sales_order_id = existing_id
    else:
        columns = ", ".join(payload.keys())
        placeholders = ", ".join("?" for _ in payload)
        cursor = connection.execute(
            f"INSERT INTO sales_orders ({columns}) VALUES ({placeholders})",
            tuple(payload.values()),
        )
        sales_order_id = int(cursor.lastrowid)
        if not object_key:
            object_key = f"sales_order:{sales_order_id}"

    return {
        "object_type": "sales_order",
        "object_id": str(sales_order_id),
        "object_key": object_key or f"sales_order:{sales_order_id}",
        "title": title or f"订单#{sales_order_id}",
    }


def insert_receivable(connection: sqlite3.Connection, context: dict[str, Any], now: str) -> dict[str, str]:
    preview = context["preview"]
    fields = context["fields"]
    sales_order_id = resolve_preview_target_id(connection, preview, "sales_order")
    cursor = connection.execute(
        """
        INSERT INTO receivables (
            receivable_no, sales_order_id, receivable_type, due_date, amount_due,
            amount_received, receivable_status, trigger_object_type, trigger_object_id,
            collection_mode, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            fields.get("receivable_no"),
            sales_order_id,
            fields.get("receivable_type") or "tail",
            as_date_text(fields.get("due_date")),
            as_float(fields.get("amount_due")),
            as_float(fields.get("amount_received")) or 0,
            fields.get("receivable_status") or "pending",
            "workflow_draft",
            context["draft_row"]["workflow_draft_id"],
            fields.get("collection_mode"),
            fields.get("notes"),
        ),
    )
    receivable_id = int(cursor.lastrowid)
    return {
        "object_type": "receivable",
        "object_id": str(receivable_id),
        "object_key": fields.get("receivable_no") or f"receivable:{receivable_id}",
        "title": fields.get("receivable_no") or f"应收#{receivable_id}",
    }


def insert_payable(connection: sqlite3.Connection, context: dict[str, Any], now: str) -> dict[str, str]:
    preview = context["preview"]
    fields = context["fields"]
    party_id = ensure_party(connection, fields.get("supplier_name") or fields.get("counterparty_name"), "supplier")
    sales_order_id = resolve_preview_target_id(connection, preview, "sales_order")
    production_lot_id = resolve_preview_target_id(connection, preview, "production_lot")
    cursor = connection.execute(
        """
        INSERT INTO payables (
            payable_no, party_id, sales_order_id, production_lot_id, payable_type,
            due_date, amount_due, amount_paid, payable_status, trigger_object_type,
            trigger_object_id, billing_mode, statement_cycle, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            fields.get("payable_no"),
            party_id,
            sales_order_id,
            production_lot_id,
            fields.get("payable_type") or "processing",
            as_date_text(fields.get("due_date")),
            as_float(fields.get("amount_due")) or as_float(fields.get("amount")),
            as_float(fields.get("amount_paid")) or 0,
            fields.get("payable_status") or "pending",
            "workflow_draft",
            context["draft_row"]["workflow_draft_id"],
            fields.get("billing_mode"),
            fields.get("statement_cycle"),
            fields.get("notes"),
        ),
    )
    payable_id = int(cursor.lastrowid)
    return {
        "object_type": "payable",
        "object_id": str(payable_id),
        "object_key": fields.get("payable_no") or f"payable:{payable_id}",
        "title": fields.get("payable_no") or f"应付#{payable_id}",
    }


def insert_cash_transaction(connection: sqlite3.Connection, context: dict[str, Any], now: str) -> dict[str, str]:
    preview = context["preview"]
    fields = context["fields"]
    cursor = connection.execute(
        """
        INSERT INTO cash_transactions (
            transaction_date, direction, counterparty_name, amount, purpose,
            payment_method, is_marked_paid, expected_payment_date, notes, raw_fields_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            as_date_text(fields.get("transaction_date")) or iso_today(),
            fields.get("direction") or "收款",
            fields.get("counterparty_name") or fields.get("customer_name") or fields.get("supplier_name"),
            as_float(fields.get("amount")),
            fields.get("purpose"),
            fields.get("payment_method"),
            fields.get("is_marked_paid") or "yes",
            as_date_text(fields.get("expected_payment_date")),
            fields.get("notes"),
            json_dumps({"draft_id": context["draft_row"]["workflow_draft_id"], "fields": fields}),
        ),
    )
    cash_transaction_id = int(cursor.lastrowid)
    sales_order_id = resolve_preview_target_id(connection, preview, "sales_order")
    if sales_order_id is not None:
        connection.execute(
            """
            INSERT OR IGNORE INTO cash_transaction_order_links (
                cash_transaction_id, sales_order_id, relation_text
            ) VALUES (?, ?, ?)
            """,
            (cash_transaction_id, sales_order_id, fields.get("purpose") or preview.get("summary_text")),
        )
    return {
        "object_type": "cash_transaction",
        "object_id": str(cash_transaction_id),
        "object_key": f"cash_transaction:{cash_transaction_id}",
        "title": f"{fields.get('direction') or '收款'} {fields.get('amount') or ''}",
    }


def insert_shipment(connection: sqlite3.Connection, context: dict[str, Any], now: str) -> dict[str, str]:
    preview = context["preview"]
    fields = context["fields"]
    cursor = connection.execute(
        """
        INSERT INTO shipments (
            shipment_date, shipment_type, factory_name, cut_detail, cut_qty,
            finished_qty, shipment_status, notes, raw_fields_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            as_date_text(fields.get("shipment_date")) or iso_today(),
            fields.get("shipment_type") or "delivery",
            fields.get("factory_name"),
            fields.get("cut_detail"),
            as_float(fields.get("cut_qty")),
            as_float(fields.get("finished_qty")),
            fields.get("shipment_status") or "planned",
            fields.get("notes"),
            json_dumps({"draft_id": context["draft_row"]["workflow_draft_id"], "fields": fields}),
        ),
    )
    shipment_id = int(cursor.lastrowid)
    sales_order_id = resolve_preview_target_id(connection, preview, "sales_order")
    if sales_order_id is not None:
        connection.execute(
            """
            INSERT OR IGNORE INTO shipment_order_links (
                shipment_id, sales_order_id, relation_text
            ) VALUES (?, ?, ?)
            """,
            (shipment_id, sales_order_id, preview.get("summary_text")),
        )
    return {
        "object_type": "shipment",
        "object_id": str(shipment_id),
        "object_key": f"shipment:{shipment_id}",
        "title": fields.get("shipment_type") or f"发货#{shipment_id}",
    }


def insert_work_order(connection: sqlite3.Connection, context: dict[str, Any], now: str) -> dict[str, str]:
    preview = context["preview"]
    fields = context["fields"]
    provider_name = fields.get("provider_name") or fields.get("factory_name") or fields.get("supplier_name")
    provider_id = ensure_party(connection, provider_name, "factory" if fields.get("factory_name") else "supplier")
    cursor = connection.execute(
        """
        INSERT INTO work_orders (
            work_order_no, work_type, source_object_type, source_object_id,
            provider_party_id, work_status, planned_qty, completed_qty,
            planned_due_at, actual_finished_at, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            fields.get("work_order_no"),
            fields.get("work_type") or preview.get("intent_type") or "production",
            preview.get("target_object_type"),
            resolve_preview_target_id(connection, preview, "sales_order"),
            provider_id,
            fields.get("work_status") or "planned",
            as_float(fields.get("planned_qty")) or as_float(fields.get("qty")),
            as_float(fields.get("completed_qty")) or 0,
            as_date_text(fields.get("planned_due_at")) or as_date_text(fields.get("promised_delivery_date")),
            as_date_text(fields.get("actual_finished_at")),
            fields.get("notes"),
        ),
    )
    work_order_id = int(cursor.lastrowid)
    return {
        "object_type": "work_order",
        "object_id": str(work_order_id),
        "object_key": fields.get("work_order_no") or f"work_order:{work_order_id}",
        "title": fields.get("work_type") or f"作业#{work_order_id}",
    }


def insert_return_case(connection: sqlite3.Connection, context: dict[str, Any], now: str) -> dict[str, str]:
    preview = context["preview"]
    fields = context["fields"]
    sales_order_id = resolve_preview_target_id(connection, preview, "sales_order") or as_int(fields.get("sales_order_id"))
    opened_by_party_id = ensure_party(
        connection,
        fields.get("opened_by") or fields.get("opened_by_party_name") or fields.get("customer_name"),
        "customer",
    )
    cursor = connection.execute(
        """
        INSERT INTO return_cases (
            sales_order_id, case_type, opened_at, opened_by_party_id, reason_text,
            case_status, refund_expected_amount, supplier_deduction_expected_amount, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            sales_order_id,
            fields.get("case_type") or "return",
            as_date_text(fields.get("opened_at")) or iso_today(),
            opened_by_party_id,
            fields.get("reason_text"),
            fields.get("case_status") or "open",
            as_float(fields.get("refund_expected_amount")),
            as_float(fields.get("supplier_deduction_expected_amount")),
            fields.get("notes"),
        ),
    )
    return_case_id = int(cursor.lastrowid)
    return {
        "object_type": "return_case",
        "object_id": str(return_case_id),
        "object_key": f"return_case:{return_case_id}",
        "title": f"{fields.get('case_type') or '退货/返修'}#{return_case_id}",
    }


def insert_refund(connection: sqlite3.Connection, context: dict[str, Any], now: str) -> dict[str, str]:
    preview = context["preview"]
    fields = context["fields"]
    return_case_id = resolve_preview_target_id(connection, preview, "return_case") or as_int(fields.get("return_case_id"))
    sales_order_id = resolve_preview_target_id(connection, preview, "sales_order") or as_int(fields.get("sales_order_id"))
    if sales_order_id is None and return_case_id is not None:
        row = connection.execute(
            "SELECT sales_order_id FROM return_cases WHERE return_case_id = ?",
            (return_case_id,),
        ).fetchone()
        sales_order_id = int(row["sales_order_id"]) if row and row["sales_order_id"] is not None else None
    cursor = connection.execute(
        """
        INSERT INTO refunds (
            return_case_id, sales_order_id, refund_amount, refund_status, notes
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (
            return_case_id,
            sales_order_id,
            as_float(fields.get("refund_amount") or fields.get("amount")),
            fields.get("refund_status") or "pending",
            fields.get("notes"),
        ),
    )
    refund_id = int(cursor.lastrowid)
    return {
        "object_type": "refund",
        "object_id": str(refund_id),
        "object_key": f"refund:{refund_id}",
        "title": f"退款 {fields.get('refund_amount') or fields.get('amount') or ''}".strip(),
    }


def insert_supplier_deduction(connection: sqlite3.Connection, context: dict[str, Any], now: str) -> dict[str, str]:
    preview = context["preview"]
    fields = context["fields"]
    party_id = ensure_party(connection, fields.get("supplier_name") or fields.get("counterparty_name"), "supplier")
    return_case_id = resolve_preview_target_id(connection, preview, "return_case") or as_int(fields.get("return_case_id"))
    work_order_id = resolve_preview_target_id(connection, preview, "work_order") or as_int(fields.get("work_order_id"))
    cursor = connection.execute(
        """
        INSERT INTO supplier_deductions (
            party_id, return_case_id, work_order_id, deduction_amount,
            deduction_reason, deduction_status
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            party_id,
            return_case_id,
            work_order_id,
            as_float(fields.get("deduction_amount") or fields.get("amount")),
            fields.get("deduction_reason") or fields.get("reason_text"),
            fields.get("deduction_status") or "pending",
        ),
    )
    supplier_deduction_id = int(cursor.lastrowid)
    return {
        "object_type": "supplier_deduction",
        "object_id": str(supplier_deduction_id),
        "object_key": f"supplier_deduction:{supplier_deduction_id}",
        "title": f"供应商扣款 {fields.get('deduction_amount') or fields.get('amount') or ''}".strip(),
    }


def commit_draft_payload(connection: sqlite3.Connection, context: dict[str, Any], now: str) -> dict[str, str]:
    preview = context["preview"]
    target_object_type = str(preview.get("target_object_type") or "")
    intent_type = str(preview.get("intent_type") or "")
    if target_object_type == "sales_order" or intent_type == "sales_order":
        return insert_sales_order(connection, context, now)
    if target_object_type == "receivable":
        return insert_receivable(connection, context, now)
    if target_object_type == "payable" or intent_type == "supplier_payable":
        return insert_payable(connection, context, now)
    if target_object_type == "cash_transaction" or intent_type == "payment_receipt":
        return insert_cash_transaction(connection, context, now)
    if target_object_type == "shipment" or intent_type == "shipment":
        return insert_shipment(connection, context, now)
    if target_object_type == "work_order" or intent_type == "production_arrangement":
        return insert_work_order(connection, context, now)
    if target_object_type == "return_case" or intent_type == "return_case":
        return insert_return_case(connection, context, now)
    if target_object_type == "refund" or intent_type == "refund_record":
        return insert_refund(connection, context, now)
    if target_object_type == "supplier_deduction" or intent_type == "supplier_deduction_record":
        return insert_supplier_deduction(connection, context, now)
    raise ValueError(f"Unsupported commit target: {target_object_type or intent_type}")


def commit_workflow_draft(
    *,
    data_root: Path,
    workflow_draft_id: str,
    confirm_token: str,
    actor_label: str | None,
) -> dict[str, Any]:
    initialize_runtime(data_root)
    connection = connect_db(data_root)
    now = utc_now()
    context = fetch_draft_context(connection, workflow_draft_id)
    preview = dict(context["preview"])
    readiness = draft_readiness(context)
    if readiness["blockers"]:
        connection.close()
        raise ValueError("Draft still has open blockers and cannot be committed.")
    confirmation = preview.get("confirmation") or {}
    if not isinstance(confirmation, dict) or not confirmation.get("commit_ready"):
        connection.close()
        raise ValueError("Draft has no ready confirmation preview.")
    if str(confirmation.get("confirm_token")) != confirm_token:
        connection.close()
        raise ValueError("Confirmation token mismatch.")

    commit_result = commit_draft_payload(connection, context, now)
    object_thread_id = upsert_object_thread(
        connection,
        object_type=commit_result["object_type"],
        object_key=commit_result["object_key"],
        title=commit_result["title"],
        last_summary=preview.get("summary_text"),
        now=now,
    )
    for inbox_item_id in context["source_inbox_ids"]:
        connection.execute(
            """
            INSERT OR IGNORE INTO object_thread_items (
                object_thread_id, inbox_item_id, link_role, linked_at
            ) VALUES (?, ?, ?, ?)
            """,
            (object_thread_id, inbox_item_id, "source", now),
        )

    resolve_confirmation_checkpoint(connection, workflow_draft_id, now=now)
    preview["committed"] = {
        "object_type": commit_result["object_type"],
        "object_id": commit_result["object_id"],
        "object_key": commit_result["object_key"],
        "object_thread_id": object_thread_id,
        "committed_at": now,
        "actor_label": actor_label,
    }
    connection.execute(
        """
        UPDATE workflow_drafts
        SET draft_status = 'committed', preview_json = ?, updated_at = ?
        WHERE workflow_draft_id = ?
        """,
        (json_dumps(preview, indent=2), now, workflow_draft_id),
    )
    connection.execute(
        """
        UPDATE intake_sessions
        SET session_status = 'committed', last_active_at = ?
        WHERE intake_session_id = ?
        """,
        (now, context["draft_row"]["intake_session_id"]),
    )
    connection.execute(
        """
        INSERT INTO audit_log (
            object_type, object_id, action_type, actor_label, new_value_json, reason_text, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            commit_result["object_type"],
            commit_result["object_id"],
            "committed_from_draft",
            actor_label,
            json_dumps(
                {
                    "workflow_draft_id": workflow_draft_id,
                    "object_thread_id": object_thread_id,
                    "object_key": commit_result["object_key"],
                }
            ),
            "Committed formal business data from a confirmed draft.",
            now,
        ),
    )
    connection.commit()
    connection.close()
    return {
        "status": "committed",
        "workflow_draft_id": workflow_draft_id,
        "object_thread_id": object_thread_id,
        "committed_object": commit_result,
    }


def resolve_pending_association_item(
    *,
    data_root: Path,
    pending_association_id: str,
    target_key: str,
    reason_text: str | None,
    actor_label: str | None,
    thread: dict[str, str] | None,
) -> dict[str, Any]:
    initialize_runtime(data_root)
    connection = connect_db(data_root)
    now = utc_now()
    pending_row = connection.execute(
        """
        SELECT pending_association_id, inbox_item_id, target_type
        FROM pending_associations
        WHERE pending_association_id = ?
        """,
        (pending_association_id,),
    ).fetchone()
    if not pending_row:
        connection.close()
        raise ValueError(f"Unknown pending_association_id: {pending_association_id}")

    connection.execute(
        """
        UPDATE pending_associations
        SET target_key = ?, association_status = 'confirmed', reason_text = COALESCE(?, reason_text)
        WHERE pending_association_id = ?
        """,
        (target_key, reason_text, pending_association_id),
    )
    existing_candidate = connection.execute(
        """
        SELECT link_candidate_id
        FROM link_candidates
        WHERE inbox_item_id = ? AND target_type = ? AND target_key = ?
        LIMIT 1
        """,
        (pending_row["inbox_item_id"], pending_row["target_type"], target_key),
    ).fetchone()
    if not existing_candidate:
        connection.execute(
            """
            INSERT INTO link_candidates (
                link_candidate_id, inbox_item_id, target_type, target_key,
                confidence_score, candidate_reason, candidate_status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"lc_{uuid.uuid4().hex}",
                pending_row["inbox_item_id"],
                pending_row["target_type"],
                target_key,
                1.0,
                reason_text or "Resolved manually.",
                "confirmed",
                now,
            ),
        )

    related_draft_ids = [
        str(row["workflow_draft_id"])
        for row in connection.execute(
            """
            SELECT DISTINCT workflow_draft_id
            FROM draft_source_links
            WHERE inbox_item_id = ?
            """,
            (pending_row["inbox_item_id"],),
        )
    ]
    updated_drafts: list[str] = []
    object_thread_id = None
    if thread and thread.get("object_type") and thread.get("object_key"):
        object_thread_id = upsert_object_thread(
            connection,
            object_type=str(thread["object_type"]),
            object_key=str(thread["object_key"]),
            title=thread.get("title"),
            last_summary=reason_text,
            now=now,
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO object_thread_items (
                object_thread_id, inbox_item_id, link_role, linked_at
            ) VALUES (?, ?, ?, ?)
            """,
            (object_thread_id, pending_row["inbox_item_id"], "source", now),
        )

    for workflow_draft_id in related_draft_ids:
        context = fetch_draft_context(connection, workflow_draft_id)
        preview = dict(context["preview"])
        candidate_links = list(preview.get("candidate_links") or [])
        if not any(
            item.get("target_type") == pending_row["target_type"] and str(item.get("target_key")) == target_key
            for item in candidate_links
        ):
            candidate_links.append(
                {
                    "target_type": str(pending_row["target_type"]),
                    "target_key": target_key,
                    "confidence_score": 1.0,
                    "reason": reason_text or "Resolved manually.",
                    "candidate_status": "confirmed",
                }
            )
        preview["candidate_links"] = candidate_links
        preview["pending_associations"] = [
            item
            for item in preview.get("pending_associations") or []
            if item.get("target_type") != pending_row["target_type"]
        ]
        if thread and object_thread_id:
            preview["thread"] = {
                "object_thread_id": object_thread_id,
                "object_type": thread["object_type"],
                "object_key": thread["object_key"],
                "title": thread.get("title"),
            }
        connection.execute(
            """
            UPDATE workflow_drafts
            SET preview_json = ?, updated_at = ?
            WHERE workflow_draft_id = ?
            """,
            (json_dumps(preview, indent=2), now, workflow_draft_id),
        )
        connection.execute(
            """
            UPDATE draft_checkpoints
            SET checkpoint_status = 'resolved', resolved_at = ?
            WHERE workflow_draft_id = ?
              AND checkpoint_status = 'open'
              AND checkpoint_type LIKE ?
            """,
            (now, workflow_draft_id, f"pending_association:{pending_row['target_type']}:%"),
        )
        refresh_draft_status(connection, workflow_draft_id, now=now)
        updated_drafts.append(workflow_draft_id)

    connection.execute(
        """
        INSERT INTO audit_log (
            object_type, object_id, action_type, actor_label, new_value_json, reason_text, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "pending_association",
            pending_association_id,
            "resolved",
            actor_label,
            json_dumps({"target_type": pending_row["target_type"], "target_key": target_key, "drafts": updated_drafts}),
            reason_text or "Resolved pending association.",
            now,
        ),
    )
    connection.commit()
    connection.close()
    return {
        "status": "resolved",
        "pending_association_id": pending_association_id,
        "target_type": str(pending_row["target_type"]),
        "target_key": target_key,
        "updated_drafts": updated_drafts,
        "object_thread_id": object_thread_id,
    }


def allocation_allowed(direction: str | None, target_type: str) -> bool:
    if direction == "收款":
        return target_type == "receivable"
    if direction == "付款":
        return target_type in {"payable", "refund"}
    return target_type in {"receivable", "payable", "refund"}


def update_receivable_rollup(connection: sqlite3.Connection, receivable_id: int) -> dict[str, Any]:
    row = connection.execute(
        """
        SELECT amount_due
        FROM receivables
        WHERE receivable_id = ?
        """,
        (receivable_id,),
    ).fetchone()
    total_received = connection.execute(
        """
        SELECT COALESCE(SUM(allocated_amount), 0)
        FROM settlement_allocations
        WHERE target_type = 'receivable' AND target_id = ?
        """,
        (str(receivable_id),),
    ).fetchone()[0]
    amount_due = float(row["amount_due"] or 0)
    if total_received <= EPSILON:
        status = "pending"
    elif total_received + EPSILON < amount_due:
        status = "partial"
    else:
        status = "received"
    connection.execute(
        """
        UPDATE receivables
        SET amount_received = ?, receivable_status = ?
        WHERE receivable_id = ?
        """,
        (total_received, status, receivable_id),
    )
    return {"target_type": "receivable", "target_id": receivable_id, "amount_received": total_received, "status": status}


def update_payable_rollup(connection: sqlite3.Connection, payable_id: int) -> dict[str, Any]:
    row = connection.execute(
        """
        SELECT amount_due
        FROM payables
        WHERE payable_id = ?
        """,
        (payable_id,),
    ).fetchone()
    total_paid = connection.execute(
        """
        SELECT COALESCE(SUM(allocated_amount), 0)
        FROM settlement_allocations
        WHERE target_type = 'payable' AND target_id = ?
        """,
        (str(payable_id),),
    ).fetchone()[0]
    amount_due = float(row["amount_due"] or 0)
    if total_paid <= EPSILON:
        status = "pending"
    elif total_paid + EPSILON < amount_due:
        status = "partial"
    else:
        status = "paid"
    connection.execute(
        """
        UPDATE payables
        SET amount_paid = ?, payable_status = ?
        WHERE payable_id = ?
        """,
        (total_paid, status, payable_id),
    )
    return {"target_type": "payable", "target_id": payable_id, "amount_paid": total_paid, "status": status}


def update_refund_rollup(connection: sqlite3.Connection, refund_id: int) -> dict[str, Any]:
    row = connection.execute(
        """
        SELECT refund_amount
        FROM refunds
        WHERE refund_id = ?
        """,
        (refund_id,),
    ).fetchone()
    total_paid = connection.execute(
        """
        SELECT COALESCE(SUM(allocated_amount), 0)
        FROM settlement_allocations
        WHERE target_type = 'refund' AND target_id = ?
        """,
        (str(refund_id),),
    ).fetchone()[0]
    refund_amount = float(row["refund_amount"] or 0)
    status = "paid" if total_paid + EPSILON >= refund_amount else ("partial" if total_paid > EPSILON else "pending")
    connection.execute(
        """
        UPDATE refunds
        SET refund_status = ?
        WHERE refund_id = ?
        """,
        (status, refund_id),
    )
    return {"target_type": "refund", "target_id": refund_id, "amount_paid": total_paid, "status": status}


def link_cash_transaction_to_target_order(
    connection: sqlite3.Connection, cash_transaction_id: int, target_type: str, target_id: int
) -> None:
    sales_order_id = None
    if target_type == "receivable":
        row = connection.execute("SELECT sales_order_id FROM receivables WHERE receivable_id = ?", (target_id,)).fetchone()
        sales_order_id = row["sales_order_id"] if row else None
    elif target_type == "payable":
        row = connection.execute("SELECT sales_order_id FROM payables WHERE payable_id = ?", (target_id,)).fetchone()
        sales_order_id = row["sales_order_id"] if row else None
    elif target_type == "refund":
        row = connection.execute("SELECT sales_order_id FROM refunds WHERE refund_id = ?", (target_id,)).fetchone()
        sales_order_id = row["sales_order_id"] if row else None
    if sales_order_id is None:
        return
    connection.execute(
        """
        INSERT OR IGNORE INTO cash_transaction_order_links (
            cash_transaction_id, sales_order_id, relation_text
        ) VALUES (?, ?, ?)
        """,
        (cash_transaction_id, sales_order_id, "derived from settlement allocation"),
    )


def record_settlement_allocations(
    *,
    data_root: Path,
    cash_transaction_id: int,
    allocations: list[dict[str, Any]],
    actor_label: str | None,
    replace_existing: bool,
    require_full_amount: bool,
    dry_run: bool = False,
    confirm_token: str | None = None,
) -> dict[str, Any]:
    initialize_runtime(data_root)
    connection = connect_db(data_root)
    now = utc_now()
    cash_row = connection.execute(
        """
        SELECT direction, amount
        FROM cash_transactions
        WHERE cash_transaction_id = ?
        """,
        (cash_transaction_id,),
    ).fetchone()
    if not cash_row:
        connection.close()
        raise ValueError(f"Unknown cash_transaction_id: {cash_transaction_id}")

    allocation_total = 0.0
    resolved_targets: list[tuple[str, int, float]] = []
    for item in allocations:
        target_type = str(item["target_type"])
        if not allocation_allowed(cash_row["direction"], target_type):
            connection.close()
            raise ValueError(f"Direction {cash_row['direction']} cannot allocate to {target_type}.")
        target_id = resolve_target_row_id(connection, target_type, str(item["target_id"]))
        if target_id is None:
            connection.close()
            raise ValueError(f"Unknown allocation target: {target_type} {item['target_id']}")
        amount = as_float(item.get("allocated_amount"))
        if amount is None or amount <= 0:
            connection.close()
            raise ValueError("Allocation amount must be positive.")
        allocation_total += amount
        resolved_targets.append((target_type, target_id, amount))

    if allocation_total - float(cash_row["amount"] or 0) > EPSILON:
        connection.close()
        raise ValueError("Allocations exceed cash transaction amount.")
    if require_full_amount and abs(allocation_total - float(cash_row["amount"] or 0)) > EPSILON:
        connection.close()
        raise ValueError("Allocations must fully consume the cash transaction amount.")

    normalized_payload = {
        "cash_transaction_id": cash_transaction_id,
        "allocations": [
            {"target_type": target_type, "target_id": str(target_id), "allocated_amount": amount}
            for target_type, target_id, amount in resolved_targets
        ],
        "replace_existing": replace_existing,
        "require_full_amount": require_full_amount,
        "allocation_total": allocation_total,
    }
    if dry_run:
        token = f"alloc-confirm-{uuid.uuid4().hex[:12]}"
        connection.execute(
            "INSERT OR REPLACE INTO runtime_metadata (key, value) VALUES (?, ?)",
            (
                f"allocation_confirmation:{token}",
                json_dumps(
                    {
                        "generated_at": now,
                        "actor_label": actor_label,
                        "payload": normalized_payload,
                    },
                    indent=2,
                ),
            ),
        )
        connection.commit()
        connection.close()
        return {
            "status": "confirmation_required",
            "cash_transaction_id": cash_transaction_id,
            "allocation_total": allocation_total,
            "remaining_amount": float(cash_row["amount"] or 0) - allocation_total,
            "target_count": len(resolved_targets),
            "confirmation": {
                "commit_ready": True,
                "confirm_token": token,
                "summary_text": f"准备分配流水 {cash_transaction_id} 金额 {allocation_total}，确认后才正式写入。",
            },
            "planned_allocations": normalized_payload["allocations"],
        }

    if not confirm_token:
        connection.close()
        raise ValueError("Settlement allocation requires dry-run confirmation token.")
    token_key = f"allocation_confirmation:{confirm_token}"
    token_row = connection.execute(
        "SELECT value FROM runtime_metadata WHERE key = ?",
        (token_key,),
    ).fetchone()
    if not token_row:
        connection.close()
        raise ValueError("Settlement allocation confirmation token mismatch or expired.")
    token_payload = parse_json(str(token_row["value"]))
    if not isinstance(token_payload, dict) or token_payload.get("payload") != normalized_payload:
        connection.close()
        raise ValueError("Settlement allocation confirmation token does not match current allocation payload.")

    if replace_existing:
        connection.execute("DELETE FROM settlement_allocations WHERE cash_transaction_id = ?", (cash_transaction_id,))

    for target_type, target_id, amount in resolved_targets:
        connection.execute(
            """
            INSERT INTO settlement_allocations (
                cash_transaction_id, target_type, target_id, allocated_amount
            ) VALUES (?, ?, ?, ?)
            """,
            (cash_transaction_id, target_type, str(target_id), amount),
        )
        link_cash_transaction_to_target_order(connection, cash_transaction_id, target_type, target_id)

    summaries: list[dict[str, Any]] = []
    for target_type, target_id, _amount in resolved_targets:
        if target_type == "receivable":
            summaries.append(update_receivable_rollup(connection, target_id))
        elif target_type == "payable":
            summaries.append(update_payable_rollup(connection, target_id))
        elif target_type == "refund":
            summaries.append(update_refund_rollup(connection, target_id))

    connection.execute(
        """
        INSERT INTO audit_log (
            object_type, object_id, action_type, actor_label, new_value_json, reason_text, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "cash_transaction",
            str(cash_transaction_id),
            "allocation_recorded",
            actor_label,
            json_dumps({"allocations": resolved_targets, "summaries": summaries}),
            "Recorded settlement allocations for a cash transaction.",
            now,
        ),
    )
    connection.execute("DELETE FROM runtime_metadata WHERE key = ?", (token_key,))
    connection.commit()
    connection.close()
    return {
        "status": "allocated",
        "cash_transaction_id": cash_transaction_id,
        "allocation_total": allocation_total,
        "remaining_amount": float(cash_row["amount"] or 0) - allocation_total,
        "targets": summaries,
    }


def upsert_commitment(
    connection: sqlite3.Connection,
    *,
    object_type: str,
    object_id: str,
    commitment_type: str,
    due_at: str | None,
    notes: str,
) -> int:
    row = connection.execute(
        """
        SELECT commitment_item_id
        FROM commitment_items
        WHERE object_type = ? AND object_id = ? AND commitment_type = ?
        ORDER BY rowid DESC
        LIMIT 1
        """,
        (object_type, object_id, commitment_type),
    ).fetchone()
    if row:
        connection.execute(
            """
            UPDATE commitment_items
            SET due_at = ?, commitment_status = 'open', notes = ?
            WHERE commitment_item_id = ?
            """,
            (due_at, notes, row["commitment_item_id"]),
        )
        return int(row["commitment_item_id"])
    cursor = connection.execute(
        """
        INSERT INTO commitment_items (
            object_type, object_id, commitment_type, due_at, commitment_status, notes
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (object_type, object_id, commitment_type, due_at, "open", notes),
    )
    return int(cursor.lastrowid)


def upsert_followup(
    connection: sqlite3.Connection,
    *,
    object_type: str,
    object_id: str,
    followup_type: str,
    due_at: str | None,
    priority: str,
    notes: str,
) -> int:
    row = connection.execute(
        """
        SELECT followup_item_id
        FROM followup_items
        WHERE object_type = ? AND object_id = ? AND followup_type = ?
        ORDER BY rowid DESC
        LIMIT 1
        """,
        (object_type, object_id, followup_type),
    ).fetchone()
    if row:
        connection.execute(
            """
            UPDATE followup_items
            SET followup_status = 'open', due_at = ?, priority = ?, notes = ?
            WHERE followup_item_id = ?
            """,
            (due_at, priority, notes, row["followup_item_id"]),
        )
        return int(row["followup_item_id"])
    cursor = connection.execute(
        """
        INSERT INTO followup_items (
            object_type, object_id, followup_type, followup_status, due_at, priority, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (object_type, object_id, followup_type, "open", due_at, priority, notes),
    )
    return int(cursor.lastrowid)


def upsert_exception(
    connection: sqlite3.Connection,
    *,
    object_type: str,
    object_id: str,
    exception_type: str,
    severity: str,
    notes: str,
    now: str,
) -> int:
    row = connection.execute(
        """
        SELECT exception_case_id
        FROM exception_cases
        WHERE object_type = ? AND object_id = ? AND exception_type = ?
        ORDER BY rowid DESC
        LIMIT 1
        """,
        (object_type, object_id, exception_type),
    ).fetchone()
    if row:
        connection.execute(
            """
            UPDATE exception_cases
            SET exception_status = 'open', severity = ?, notes = ?
            WHERE exception_case_id = ?
            """,
            (severity, notes, row["exception_case_id"]),
        )
        return int(row["exception_case_id"])
    cursor = connection.execute(
        """
        INSERT INTO exception_cases (
            object_type, object_id, exception_type, severity, exception_status, notes, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (object_type, object_id, exception_type, severity, "open", notes, now),
    )
    return int(cursor.lastrowid)


def upsert_alert(
    connection: sqlite3.Connection,
    *,
    alert_type: str,
    object_type: str,
    object_id: str,
    alert_text: str,
    now: str,
) -> int:
    row = connection.execute(
        """
        SELECT alert_id
        FROM alerts
        WHERE alert_type = ? AND object_type = ? AND object_id = ?
        ORDER BY rowid DESC
        LIMIT 1
        """,
        (alert_type, object_type, object_id),
    ).fetchone()
    if row:
        connection.execute(
            """
            UPDATE alerts
            SET alert_status = 'open', alert_text = ?, created_at = ?
            WHERE alert_id = ?
            """,
            (alert_text, now, row["alert_id"]),
        )
        return int(row["alert_id"])
    cursor = connection.execute(
        """
        INSERT INTO alerts (
            alert_type, object_type, object_id, alert_status, alert_text, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (alert_type, object_type, object_id, "open", alert_text, now),
    )
    return int(cursor.lastrowid)


def close_stale_derived_rows(
    connection: sqlite3.Connection,
    *,
    table_name: str,
    id_field: str,
    key_fields: tuple[str, str, str],
    active_keys: set[tuple[str, str, str]],
    status_field: str,
    closed_value: str,
    filter_clause: str,
) -> None:
    rows = connection.execute(
        f"""
        SELECT {id_field}, {key_fields[0]} AS key1, {key_fields[1]} AS key2, {key_fields[2]} AS key3
        FROM {table_name}
        WHERE {filter_clause}
        """
    ).fetchall()
    for row in rows:
        key = (str(row["key1"]), str(row["key2"]), str(row["key3"]))
        if key in active_keys:
            continue
        connection.execute(
            f"UPDATE {table_name} SET {status_field} = ? WHERE {id_field} = ?",
            (closed_value, row[id_field]),
        )


def derive_commitments(connection: sqlite3.Connection, as_of: date, now: str) -> dict[str, Any]:
    commitment_ids: list[int] = []
    commitment_keys: set[tuple[str, str, str]] = set()

    for row in connection.execute(
        """
        SELECT sales_order_id, promised_delivery_date
        FROM sales_orders
        WHERE promised_delivery_date IS NOT NULL
          AND COALESCE(order_status, '') NOT IN ('closed', 'cancelled', 'done', '已取消', '已完成')
        """
    ):
        object_id = str(row["sales_order_id"])
        commitment_keys.add(("sales_order", object_id, "delivery_due"))
        commitment_ids.append(
            upsert_commitment(
                connection,
                object_type="sales_order",
                object_id=object_id,
                commitment_type="delivery_due",
                due_at=str(row["promised_delivery_date"]),
                notes=f"{DERIVED_NOTES_PREFIX} 订单交期承诺",
            )
        )

    for row in connection.execute(
        """
        SELECT receivable_id, due_date
        FROM receivables
        WHERE due_date IS NOT NULL
          AND COALESCE(receivable_status, 'pending') NOT IN ('received', 'closed')
        """
    ):
        object_id = str(row["receivable_id"])
        commitment_keys.add(("receivable", object_id, "receivable_due"))
        commitment_ids.append(
            upsert_commitment(
                connection,
                object_type="receivable",
                object_id=object_id,
                commitment_type="receivable_due",
                due_at=str(row["due_date"]),
                notes=f"{DERIVED_NOTES_PREFIX} 应收承诺",
            )
        )

    for row in connection.execute(
        """
        SELECT payable_id, due_date
        FROM payables
        WHERE due_date IS NOT NULL
          AND COALESCE(payable_status, 'pending') NOT IN ('paid', 'closed')
        """
    ):
        object_id = str(row["payable_id"])
        commitment_keys.add(("payable", object_id, "payable_due"))
        commitment_ids.append(
            upsert_commitment(
                connection,
                object_type="payable",
                object_id=object_id,
                commitment_type="payable_due",
                due_at=str(row["due_date"]),
                notes=f"{DERIVED_NOTES_PREFIX} 应付承诺",
            )
        )

    for row in connection.execute(
        """
        SELECT work_order_id, planned_due_at
        FROM work_orders
        WHERE planned_due_at IS NOT NULL
          AND COALESCE(work_status, 'draft') NOT IN ('done', 'cancelled')
        """
    ):
        object_id = str(row["work_order_id"])
        commitment_keys.add(("work_order", object_id, "work_due"))
        commitment_ids.append(
            upsert_commitment(
                connection,
                object_type="work_order",
                object_id=object_id,
                commitment_type="work_due",
                due_at=str(row["planned_due_at"]),
                notes=f"{DERIVED_NOTES_PREFIX} 作业承诺",
            )
        )

    close_stale_derived_rows(
        connection,
        table_name="commitment_items",
        id_field="commitment_item_id",
        key_fields=("object_type", "object_id", "commitment_type"),
        active_keys=commitment_keys,
        status_field="commitment_status",
        closed_value="closed",
        filter_clause="commitment_type IN ('delivery_due', 'receivable_due', 'payable_due', 'work_due')",
    )
    return {"commitment_count": len(commitment_ids)}


def derive_followups_and_alerts(connection: sqlite3.Connection, as_of: date, now: str) -> dict[str, Any]:
    followup_keys: set[tuple[str, str, str]] = set()
    exception_keys: set[tuple[str, str, str]] = set()
    alert_keys: set[tuple[str, str, str]] = set()

    followup_count = 0
    exception_count = 0
    alert_count = 0
    for commitment in connection.execute(
        """
        SELECT commitment_item_id, object_type, object_id, commitment_type, due_at
        FROM commitment_items
        WHERE commitment_status = 'open'
        """
    ):
        due_date_text = as_date_text(commitment["due_at"])
        if not due_date_text:
            continue
        due_date = date.fromisoformat(due_date_text)
        days_delta = (due_date - as_of).days
        source_description = describe_object(connection, str(commitment["object_type"]), str(commitment["object_id"]))
        followup_type = None
        priority = "low"
        if days_delta < 0:
            followup_type = "overdue"
            priority = "high"
        elif days_delta == 0:
            followup_type = "due_today"
            priority = "medium"
        elif days_delta <= 2:
            followup_type = "due_soon"
            priority = "low"
        if followup_type:
            followup_keys.add(("commitment_item", str(commitment["commitment_item_id"]), followup_type))
            upsert_followup(
                connection,
                object_type="commitment_item",
                object_id=str(commitment["commitment_item_id"]),
                followup_type=followup_type,
                due_at=due_date_text,
                priority=priority,
                notes=f"{DERIVED_NOTES_PREFIX} {source_description}",
            )
            followup_count += 1

        if days_delta < 0:
            exception_keys.add(("commitment_item", str(commitment["commitment_item_id"]), "overdue_commitment"))
            upsert_exception(
                connection,
                object_type="commitment_item",
                object_id=str(commitment["commitment_item_id"]),
                exception_type="overdue_commitment",
                severity="high",
                notes=f"{DERIVED_NOTES_PREFIX} {source_description}",
                now=now,
            )
            exception_count += 1
            alert_type = f"{DERIVED_ALERT_PREFIX}overdue_commitment"
            alert_keys.add((alert_type, "commitment_item", str(commitment["commitment_item_id"])))
            upsert_alert(
                connection,
                alert_type=alert_type,
                object_type="commitment_item",
                object_id=str(commitment["commitment_item_id"]),
                alert_text=f"已逾期: {source_description}",
                now=now,
            )
            alert_count += 1
        elif days_delta == 0:
            alert_type = f"{DERIVED_ALERT_PREFIX}due_today"
            alert_keys.add((alert_type, "commitment_item", str(commitment["commitment_item_id"])))
            upsert_alert(
                connection,
                alert_type=alert_type,
                object_type="commitment_item",
                object_id=str(commitment["commitment_item_id"]),
                alert_text=f"今日到期: {source_description}",
                now=now,
            )
            alert_count += 1

    close_stale_derived_rows(
        connection,
        table_name="followup_items",
        id_field="followup_item_id",
        key_fields=("object_type", "object_id", "followup_type"),
        active_keys=followup_keys,
        status_field="followup_status",
        closed_value="closed",
        filter_clause="object_type = 'commitment_item'",
    )
    close_stale_derived_rows(
        connection,
        table_name="exception_cases",
        id_field="exception_case_id",
        key_fields=("object_type", "object_id", "exception_type"),
        active_keys=exception_keys,
        status_field="exception_status",
        closed_value="resolved",
        filter_clause="object_type = 'commitment_item'",
    )
    close_stale_derived_rows(
        connection,
        table_name="alerts",
        id_field="alert_id",
        key_fields=("alert_type", "object_type", "object_id"),
        active_keys=alert_keys,
        status_field="alert_status",
        closed_value="resolved",
        filter_clause="alert_type LIKE 'derived:%'",
    )
    return {
        "followup_count": followup_count,
        "exception_count": exception_count,
        "alert_count": alert_count,
    }


def refresh_control_tower(
    *,
    data_root: Path,
    as_of_date: str | None,
    actor_label: str | None,
) -> dict[str, Any]:
    initialize_runtime(data_root)
    connection = connect_db(data_root)
    now = utc_now()
    as_of = date.fromisoformat(as_of_date or iso_today())
    commitment_info = derive_commitments(connection, as_of, now)
    followup_info = derive_followups_and_alerts(connection, as_of, now)
    connection.execute(
        """
        INSERT INTO audit_log (
            object_type, object_id, action_type, actor_label, new_value_json, reason_text, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "control_tower",
            as_of.isoformat(),
            "refreshed",
            actor_label,
            json_dumps({**commitment_info, **followup_info}),
            "Refreshed derived commitments, followups, exceptions, and alerts.",
            now,
        ),
    )
    connection.commit()
    connection.close()
    return {
        "status": "refreshed",
        "as_of_date": as_of.isoformat(),
        **commitment_info,
        **followup_info,
    }


def build_action_suggestions(connection: sqlite3.Connection) -> list[str]:
    suggestions: list[str] = []
    overdue_rows = connection.execute(
        """
        SELECT f.followup_item_id, c.object_type, c.object_id, c.due_at
        FROM followup_items f
        JOIN commitment_items c
          ON c.commitment_item_id = CAST(f.object_id AS INTEGER)
        WHERE f.object_type = 'commitment_item'
          AND f.followup_status = 'open'
          AND f.followup_type = 'overdue'
        ORDER BY c.due_at ASC
        LIMIT 3
        """
    ).fetchall()
    for row in overdue_rows:
        suggestions.append(
            f"优先处理逾期事项：{describe_object(connection, str(row['object_type']), str(row['object_id']))}"
        )

    due_today_rows = connection.execute(
        """
        SELECT f.followup_item_id, c.object_type, c.object_id, c.due_at
        FROM followup_items f
        JOIN commitment_items c
          ON c.commitment_item_id = CAST(f.object_id AS INTEGER)
        WHERE f.object_type = 'commitment_item'
          AND f.followup_status = 'open'
          AND f.followup_type = 'due_today'
        ORDER BY c.due_at ASC
        LIMIT 2
        """
    ).fetchall()
    for row in due_today_rows:
        suggestions.append(
            f"今天跟进：{describe_object(connection, str(row['object_type']), str(row['object_id']))}"
        )
    return suggestions[:5]


def generate_daily_report(
    *,
    data_root: Path,
    report_date: str | None,
    actor_label: str | None,
    refresh_first: bool,
) -> dict[str, Any]:
    if refresh_first:
        refresh_control_tower(data_root=data_root, as_of_date=report_date, actor_label=actor_label)

    initialize_runtime(data_root)
    connection = connect_db(data_root)
    now = utc_now()
    report_day = report_date or iso_today()

    production_rows = connection.execute(
        """
        SELECT order_no, customer_name, product_name, qty, promised_delivery_date, current_factory, current_step
        FROM v_order_production_status
        WHERE COALESCE(order_status, '') NOT IN ('closed', 'cancelled', 'done', '已取消', '已完成')
          AND (linked_lot_count > 0 OR COALESCE(current_factory, '') != '')
        ORDER BY promised_delivery_date IS NULL, promised_delivery_date ASC
        LIMIT 5
        """
    ).fetchall()
    overdue_rows = connection.execute(
        """
        SELECT c.object_type, c.object_id, c.due_at
        FROM followup_items f
        JOIN commitment_items c
          ON c.commitment_item_id = CAST(f.object_id AS INTEGER)
        WHERE f.object_type = 'commitment_item'
          AND f.followup_status = 'open'
          AND f.followup_type = 'overdue'
        ORDER BY c.due_at ASC
        LIMIT 5
        """
    ).fetchall()
    due_today_rows = connection.execute(
        """
        SELECT c.object_type, c.object_id, c.due_at
        FROM followup_items f
        JOIN commitment_items c
          ON c.commitment_item_id = CAST(f.object_id AS INTEGER)
        WHERE f.object_type = 'commitment_item'
          AND f.followup_status = 'open'
          AND f.followup_type = 'due_today'
        ORDER BY c.due_at ASC
        LIMIT 5
        """
    ).fetchall()
    finance_row = connection.execute(
        """
        SELECT
          COALESCE(SUM(CASE WHEN receivable_status NOT IN ('received', 'closed') THEN amount_due - COALESCE(amount_received, 0) END), 0) AS receivable_open,
          COALESCE(SUM(CASE WHEN payable_status NOT IN ('paid', 'closed') THEN amount_due - COALESCE(amount_paid, 0) END), 0) AS payable_open
        FROM (
          SELECT amount_due, amount_received, receivable_status, NULL AS amount_paid, NULL AS payable_status
          FROM receivables
          UNION ALL
          SELECT amount_due, NULL AS amount_received, NULL AS receivable_status, amount_paid, payable_status
          FROM payables
          UNION ALL
          SELECT
            refund_amount AS amount_due,
            NULL AS amount_received,
            NULL AS receivable_status,
            COALESCE((
              SELECT SUM(allocated_amount)
              FROM settlement_allocations sa
              WHERE sa.target_type = 'refund'
                AND sa.target_id = CAST(refunds.refund_id AS TEXT)
            ), 0) AS amount_paid,
            refund_status AS payable_status
          FROM refunds
        )
        """
    ).fetchone()
    suggestion_lines = build_action_suggestions(connection)

    report_json = {
        "report_date": report_day,
        "orders_in_production": len(production_rows),
        "overdue_followups": len(overdue_rows),
        "due_today_followups": len(due_today_rows),
        "receivable_open_amount": float(finance_row["receivable_open"] or 0),
        "payable_open_amount": float(finance_row["payable_open"] or 0),
        "action_suggestions": suggestion_lines,
    }

    body_lines = [
        f"# Order 日报 {report_day}",
        "",
        "## 今日概览",
        f"- 在产订单: {len(production_rows)}",
        f"- 已逾期待跟进: {len(overdue_rows)}",
        f"- 今日到期待跟进: {len(due_today_rows)}",
        "",
        "## 关键变化",
    ]
    if production_rows:
        for row in production_rows:
            body_lines.append(
                f"- {row['order_no'] or '未编号订单'} / {row['customer_name'] or '未指明客户'} / {row['product_name'] or '未指明产品'} / {row['qty'] or 0} / {row['current_factory'] or '待分配工厂'} / {row['current_step'] or '待确认工序'} / 交期 {row['promised_delivery_date'] or '未定'}"
            )
    else:
        body_lines.append("- 今天没有识别到明确在产订单。")

    body_lines.extend(["", "## 风险与阻塞"])
    if overdue_rows:
        for row in overdue_rows:
            body_lines.append(f"- 逾期: {describe_object(connection, str(row['object_type']), str(row['object_id']))}")
    else:
        body_lines.append("- 当前没有已识别的逾期事项。")

    body_lines.extend(["", "## 资金视图"])
    body_lines.append(f"- 当前待收金额: {float(finance_row['receivable_open'] or 0):.2f}")
    body_lines.append(f"- 当前待付金额: {float(finance_row['payable_open'] or 0):.2f}")

    body_lines.extend(["", "## 明日行动建议"])
    if suggestion_lines:
        for line in suggestion_lines:
            body_lines.append(f"- {line}")
    else:
        body_lines.append("- 继续补齐草稿确认和关键关联，保持今天的跟进节奏。")

    report_body = "\n".join(body_lines)
    connection.execute(
        """
        INSERT INTO daily_reports (report_date, report_body, report_json, created_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(report_date) DO UPDATE SET
          report_body = excluded.report_body,
          report_json = excluded.report_json,
          created_at = excluded.created_at
        """,
        (report_day, report_body, json_dumps(report_json, indent=2), now),
    )
    connection.execute(
        """
        INSERT INTO audit_log (
            object_type, object_id, action_type, actor_label, new_value_json, reason_text, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "daily_report",
            report_day,
            "generated",
            actor_label,
            json_dumps(report_json),
            "Generated daily order operating report.",
            now,
        ),
    )
    connection.commit()
    connection.close()
    return {
        "status": "generated",
        "report_date": report_day,
        "report_json": report_json,
        "report_body": report_body,
    }
