#!/usr/bin/env python3
"""Open a guided backfill draft from one imported history item."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from datetime import datetime
from typing import Any

from history_common import load_inbox_record, normalized_text
from runtime_common import connect_db, open_guided_intake_draft, persist_input, resolve_data_root
from runtime_flow import prepare_draft_confirmation


SHIPMENT_KEYWORDS = ("发货", "物流", "快递", "回货", "物流单", "出库", "入库", "签收")
PAYABLE_KEYWORDS = ("供应商", "账单", "应付", "结算", "加工费", "材料费", "扣款")
PAYMENT_KEYWORDS = ("收款", "付款", "打款", "流水", "尾款", "预付款", "代收款")
PRODUCTION_KEYWORDS = ("生产", "排期", "工厂", "车缝", "充棉", "手工", "激光", "复合", "绣花", "打样")

AMOUNT_RE = re.compile(r"(?:¥|￥|金额[:：]?\s*|收款[:：]?\s*|付款[:：]?\s*|尾款[:：]?\s*|预付款[:：]?\s*|加工费[:：]?\s*|运费[:：]?\s*)(\d+(?:\.\d+)?)")
QTY_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(个|件|只|套|箱)")
ISO_DATE_RE = re.compile(r"(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})")
MONTH_DAY_RE = re.compile(r"(?<!\d)(\d{1,2})月(\d{1,2})日")
LABELED_VALUE_PATTERNS = {
    "supplier_name": [re.compile(r"(?:^|[\n\r])\s*(?:[-*]\s*)?供应商[:：]\s*([^\n\r]+)")],
    "customer_name": [
        re.compile(r"(?:^|[\n\r])\s*(?:[-*]\s*)?客户(?:是|名称[:：])\s*([^\n\r]+)"),
        re.compile(r"(?:^|[\n\r])\s*(?:[-*]\s*)?客户[:：]\s*([^\n\r]+)"),
    ],
    "product_name": [
        re.compile(r"(?:^|[\n\r])\s*(?:[-*]\s*)?产品(?:是|名称[:：])\s*([^\n\r]+)"),
        re.compile(r"(?:^|[\n\r])\s*(?:[-*]\s*)?产品[:：]\s*([^\n\r]+)"),
    ],
    "factory_name": [re.compile(r"(?:^|[\n\r])\s*(?:[-*]\s*)?工厂[:：]\s*([^\n\r]+)")],
}
SECONDARY_LABEL_MARKERS = [
    "，产品",
    " 产品",
    "，日期",
    " 日期",
    "，布料",
    " 布料",
    "，单价",
    " 单价",
    "，数量",
    " 数量",
    "，金额",
    " 金额",
    "，供应商",
    " 供应商",
    "，客户",
    " 客户",
    "####",
    "```",
]

INTENT_CONFIG = {
    "sales_order": {"target_object_type": "sales_order", "target_action": "create"},
    "shipment": {"target_object_type": "shipment", "target_action": "create"},
    "payment_receipt": {"target_object_type": "cash_transaction", "target_action": "create"},
    "supplier_payable": {"target_object_type": "payable", "target_action": "create"},
    "production_arrangement": {"target_object_type": "work_order", "target_action": "create"},
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Open a guided backfill draft from one imported history item.")
    parser.add_argument("--data-root", help="Override the default order data root.")
    parser.add_argument("--inbox-item-id", help="History inbox_item_id.")
    parser.add_argument("--source-message-id", help="History source_message_id.")
    parser.add_argument(
        "--intent-type",
        choices=sorted(INTENT_CONFIG.keys()),
        help="Override the inferred backfill intent.",
    )
    parser.add_argument(
        "--target-action",
        choices=("create", "update"),
        help="Override the inferred draft action.",
    )
    parser.add_argument("--actor-label", help="Audit actor label.")
    parser.add_argument(
        "--auto-prepare-confirmation",
        action="store_true",
        help="If the resulting draft is ready, immediately build the confirmation summary.",
    )
    return parser.parse_args()


def choose_unique(values: list[str]) -> str | None:
    cleaned = sorted({value.strip() for value in values if value and value.strip()})
    if len(cleaned) == 1:
        return cleaned[0]
    return None


def parse_legacy_date(text: str, *, fallback_year: int | None = None) -> str | None:
    match = ISO_DATE_RE.search(text)
    if match:
        year, month, day = (int(part) for part in match.groups())
        return f"{year:04d}-{month:02d}-{day:02d}"
    if fallback_year is not None:
        match = MONTH_DAY_RE.search(text)
        if match:
            month, day = (int(part) for part in match.groups())
            return f"{fallback_year:04d}-{month:02d}-{day:02d}"
    return None


def fallback_date_from_item(item: dict[str, Any]) -> tuple[str | None, int | None]:
    legacy_history = item.get("legacy_history") or {}
    for key in ("event_timestamp", "event_time"):
        value = legacy_history.get(key)
        if value:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return dt.date().isoformat(), dt.year
    received_at = item.get("received_at")
    if received_at:
        dt = datetime.fromisoformat(str(received_at).replace("Z", "+00:00"))
        return dt.date().isoformat(), dt.year
    return None, None


def infer_intent(text: str, *, category: str | None, explicit: str | None) -> str:
    if explicit:
        return explicit
    if any(keyword in text for keyword in SHIPMENT_KEYWORDS):
        return "shipment"
    if any(keyword in text for keyword in PAYABLE_KEYWORDS):
        return "supplier_payable"
    if any(keyword in text for keyword in PAYMENT_KEYWORDS):
        return "payment_receipt"
    if any(keyword in text for keyword in PRODUCTION_KEYWORDS):
        return "production_arrangement"
    if category == "legacy_finance_doc":
        return "supplier_payable"
    return "sales_order"


def infer_direction(text: str) -> str | None:
    has_receive = "收款" in text or "代收款" in text
    has_pay = "付款" in text or "打款" in text or "支付" in text
    if has_receive and not has_pay:
        return "收款"
    if has_pay and not has_receive:
        return "付款"
    return None


def infer_payable_type(text: str) -> str | None:
    if "材料" in text or "布料" in text or "配件" in text:
        return "material"
    if "物流" in text or "快递" in text or "货拉拉" in text:
        return "logistics"
    if "加工" in text or "工厂" in text or "车缝" in text or "充棉" in text or "手工" in text:
        return "processing"
    return None


def infer_shipment_type(text: str) -> str | None:
    if "回货" in text or "回义乌" in text:
        return "return_from_factory"
    if "发物流" in text or "发货" in text or "快递" in text:
        return "delivery"
    if "入库" in text:
        return "warehouse_receipt"
    return None


def infer_work_type(text: str) -> str | None:
    for keyword in ("打样", "激光", "绣花", "复合", "车缝", "充棉", "手工", "生产"):
        if keyword in text:
            return keyword
    return None


def extract_labeled_value(text: str, field_name: str) -> str | None:
    for pattern in LABELED_VALUE_PATTERNS.get(field_name, []):
        match = pattern.search(text)
        if match:
            value = match.group(1).strip().strip("`")
            for marker in SECONDARY_LABEL_MARKERS:
                if marker in value:
                    value = value.split(marker, 1)[0].strip()
            return value
    return None


def unique_sales_order_hint(item: dict[str, Any]) -> dict[str, Any] | None:
    payload = item.get("raw_payload") or {}
    hints = payload.get("entity_hints") if isinstance(payload, dict) else None
    rows = list((hints or {}).get("sales_orders") or [])
    if len(rows) == 1:
        return rows[0]
    return None


def build_single_order_thread(order_hint: dict[str, Any] | None) -> dict[str, str] | None:
    if not order_hint:
        return None
    title = " / ".join(
        value
        for value in [order_hint.get("order_no"), order_hint.get("customer_name"), order_hint.get("product_name")]
        if value
    )
    return {
        "object_type": "sales_order",
        "object_key": str(order_hint["sales_order_id"]),
        "title": title or f"订单#{order_hint['sales_order_id']}",
    }


def build_candidate_links(order_hint: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not order_hint:
        return []
    return [
        {
            "target_type": "sales_order",
            "target_key": str(order_hint["sales_order_id"]),
            "confidence_score": 0.82,
            "reason": "single high-confidence sales-order hint from imported history item",
        }
    ]


def build_pending_associations(item: dict[str, Any], intent_type: str, order_hint: dict[str, Any] | None) -> list[dict[str, Any]]:
    if intent_type not in {"shipment", "payment_receipt", "supplier_payable", "production_arrangement"}:
        return []
    if order_hint:
        return []
    payload = item.get("raw_payload") or {}
    hints = payload.get("entity_hints") if isinstance(payload, dict) else None
    order_hints = list((hints or {}).get("sales_orders") or [])
    if len(order_hints) > 1:
        candidates = ", ".join(
            filter(
                None,
                [
                    " / ".join(
                        value
                        for value in [row.get("order_no"), row.get("customer_name"), row.get("product_name")]
                        if value
                    )
                    for row in order_hints[:4]
                ],
            )
        )
        reason = f"历史线索命中了多个订单，需人工确认具体关联：{candidates}"
    else:
        reason = f"这条历史线索还没有明确关联订单，需要人工确认后补录。"
    return [{"target_type": "sales_order", "reason_text": reason}]


def extract_fields(item: dict[str, Any], *, intent_type: str) -> dict[str, str]:
    payload = item.get("raw_payload") or {}
    hints = payload.get("entity_hints") if isinstance(payload, dict) else {}
    raw_text = str(item.get("raw_text") or "")
    text = normalized_text(raw_text)
    first_asset_text = ""
    for asset in item.get("evidence_assets") or []:
        extracted = str(asset.get("extracted_text") or "")
        if extracted:
            first_asset_text = extracted
            break
    merged_text = "\n".join(part for part in [raw_text, first_asset_text] if part)
    merged_text_normalized = normalized_text(merged_text)

    fallback_date, fallback_year = fallback_date_from_item(item)
    parsed_date = parse_legacy_date(merged_text, fallback_year=fallback_year) or fallback_date

    order_hint = unique_sales_order_hint(item)
    customer_candidates = [row.get("party_name") for row in (hints.get("parties") or []) if row.get("party_role") == "customer"]
    factory_candidates = [row.get("party_name") for row in (hints.get("parties") or []) if row.get("party_role") == "factory"]
    supplier_candidates = [row.get("party_name") for row in (hints.get("parties") or []) if row.get("party_role") == "supplier"]
    product_candidates = [row.get("product_name") for row in (hints.get("products") or [])]

    qty_match = QTY_RE.search(merged_text_normalized)
    amount_match = AMOUNT_RE.search(merged_text_normalized)

    fields: dict[str, str] = {
        "notes": f"historical backfill source: {item.get('source_message_id') or item.get('inbox_item_id')}",
    }
    if order_hint:
        if order_hint.get("order_no"):
            fields["order_no"] = str(order_hint["order_no"])
        if order_hint.get("customer_name"):
            fields["customer_name"] = str(order_hint["customer_name"])
        if order_hint.get("product_name"):
            fields["product_name"] = str(order_hint["product_name"])
    if choose_unique(customer_candidates):
        fields.setdefault("customer_name", choose_unique(customer_candidates) or "")
    if choose_unique(product_candidates):
        fields.setdefault("product_name", choose_unique(product_candidates) or "")
    for labeled_field in ("supplier_name", "customer_name", "product_name", "factory_name"):
        labeled_value = extract_labeled_value(merged_text, labeled_field)
        if labeled_value:
            fields.setdefault(labeled_field, labeled_value)
    if qty_match:
        fields["qty"] = qty_match.group(1)
        if intent_type == "shipment":
            fields["finished_qty"] = qty_match.group(1)
        if intent_type == "production_arrangement":
            fields["planned_qty"] = qty_match.group(1)
    if parsed_date:
        if intent_type == "sales_order":
            fields["order_date"] = parsed_date
        elif intent_type == "shipment":
            fields["shipment_date"] = parsed_date
        elif intent_type == "payment_receipt":
            fields["transaction_date"] = parsed_date
        elif intent_type == "supplier_payable":
            fields["due_date"] = parsed_date
        elif intent_type == "production_arrangement":
            fields["planned_due_at"] = parsed_date

    if intent_type == "shipment":
        factory_name = fields.get("factory_name") or choose_unique(factory_candidates) or choose_unique(supplier_candidates)
        if factory_name:
            fields["factory_name"] = factory_name
        shipment_type = infer_shipment_type(merged_text_normalized)
        if shipment_type:
            fields["shipment_type"] = shipment_type

    if intent_type == "payment_receipt":
        direction = infer_direction(merged_text_normalized)
        if direction:
            fields["direction"] = direction
        if amount_match:
            fields["amount"] = amount_match.group(1)
        counterparty_name = fields.get("customer_name") or choose_unique(customer_candidates) or choose_unique(supplier_candidates) or choose_unique(factory_candidates)
        if counterparty_name:
            fields["counterparty_name"] = counterparty_name
        fields.setdefault("purpose", "historical backfill")

    if intent_type == "supplier_payable":
        supplier_name = fields.get("supplier_name") or choose_unique(supplier_candidates) or choose_unique(factory_candidates)
        if supplier_name:
            fields["supplier_name"] = supplier_name
        payable_type = infer_payable_type(merged_text_normalized)
        if payable_type:
            fields["payable_type"] = payable_type
        if amount_match:
            fields["amount"] = amount_match.group(1)
            fields["amount_due"] = amount_match.group(1)
        fields.pop("qty", None)

    if intent_type == "production_arrangement":
        factory_name = fields.get("factory_name") or choose_unique(factory_candidates) or choose_unique(supplier_candidates)
        if factory_name:
            fields["factory_name"] = factory_name
            fields["provider_name"] = factory_name
        work_type = infer_work_type(merged_text_normalized)
        if work_type:
            fields["work_type"] = work_type

    if intent_type == "sales_order":
        fields.setdefault("order_status", "historical_backfill")

    return {key: value for key, value in fields.items() if value not in (None, "", "None")}


def ensure_backfill_trigger_inbox(
    *,
    connection: sqlite3.Connection,
    data_root,
    history_item: dict[str, Any],
    intent_type: str,
    summary_text: str,
    actor_label: str | None,
) -> str:
    source_message_id = f"history-backfill:{history_item['inbox_item_id']}:{intent_type}"
    row = connection.execute(
        """
        SELECT inbox_item_id
        FROM inbox_items
        WHERE source_message_id = ?
        LIMIT 1
        """,
        (source_message_id,),
    ).fetchone()
    if row:
        return str(row["inbox_item_id"])
    result = persist_input(
        data_root=data_root,
        channel_type="history_backfill_trigger",
        channel_session_key=f"history-backfill:{history_item['inbox_item_id']}",
        source_actor=actor_label or "history-backfill",
        source_message_id=source_message_id,
        raw_text=summary_text,
        raw_payload={
            "history_backfill": {
                "source_inbox_item_id": history_item["inbox_item_id"],
                "source_message_id": history_item.get("source_message_id"),
                "intent_type": intent_type,
            }
        },
        attachments=None,
    )
    return str(result["inbox_item_id"])


def attach_original_history_source(
    connection: sqlite3.Connection,
    *,
    workflow_draft_id: str,
    intake_session_id: str,
    source_inbox_item_id: str,
    object_thread_id: str | None,
) -> None:
    now = datetime.now().astimezone().replace(microsecond=0).isoformat()
    connection.execute(
        """
        INSERT OR IGNORE INTO intake_session_items (
            intake_session_id, inbox_item_id, link_role, linked_at
        ) VALUES (?, ?, ?, ?)
        """,
        (intake_session_id, source_inbox_item_id, "history_source", now),
    )
    connection.execute(
        """
        INSERT OR IGNORE INTO draft_source_links (
            workflow_draft_id, inbox_item_id, link_role, linked_at
        ) VALUES (?, ?, ?, ?)
        """,
        (workflow_draft_id, source_inbox_item_id, "history_source", now),
    )
    if object_thread_id:
        connection.execute(
            """
            INSERT OR IGNORE INTO object_thread_items (
                object_thread_id, inbox_item_id, link_role, linked_at
            ) VALUES (?, ?, ?, ?)
            """,
            (object_thread_id, source_inbox_item_id, "history_source", now),
        )


def build_summary(history_item: dict[str, Any], intent_type: str) -> str:
    legacy_history = history_item.get("legacy_history") or {}
    source_label = legacy_history.get("primary_path") or legacy_history.get("session_path") or history_item.get("source_message_id")
    raw_text = normalized_text(history_item.get("raw_text"))
    excerpt = raw_text[:220] if raw_text else ""
    if raw_text and len(raw_text) > 220:
        excerpt += "..."
    return f"从历史资料补录 {intent_type}。来源：{source_label}。摘要：{excerpt}"


def main() -> int:
    args = parse_args()
    data_root = resolve_data_root(args.data_root)
    connection = connect_db(data_root)
    history_item = load_inbox_record(
        connection,
        inbox_item_id=args.inbox_item_id,
        source_message_id=args.source_message_id,
    )
    text = normalized_text(history_item.get("raw_text"))
    category = (history_item.get("legacy_history") or {}).get("category")
    intent_type = infer_intent(text, category=category, explicit=args.intent_type)
    if intent_type not in INTENT_CONFIG:
        raise SystemExit(f"Unsupported intent_type: {intent_type}")

    order_hint = unique_sales_order_hint(history_item)
    thread = build_single_order_thread(order_hint)
    candidate_links = build_candidate_links(order_hint)
    pending_associations = build_pending_associations(history_item, intent_type, order_hint)
    fields = extract_fields(history_item, intent_type=intent_type)
    summary_text = build_summary(history_item, intent_type)
    target_object_type = INTENT_CONFIG[intent_type]["target_object_type"]
    target_action = args.target_action or (
        "update" if intent_type == "sales_order" and order_hint else INTENT_CONFIG[intent_type]["target_action"]
    )

    backfill_inbox_item_id = ensure_backfill_trigger_inbox(
        connection=connection,
        data_root=data_root,
        history_item=history_item,
        intent_type=intent_type,
        summary_text=summary_text,
        actor_label=args.actor_label,
    )
    result = open_guided_intake_draft(
        data_root=data_root,
        inbox_item_id=backfill_inbox_item_id,
        intent_type=intent_type,
        target_object_type=target_object_type,
        target_action=target_action,
        summary_text=summary_text,
        draft_fields=fields,
        thread=thread,
        candidate_links=candidate_links,
        pending_targets=pending_associations,
        required_fields=None,
        actor_label=args.actor_label or "history-backfill",
    )

    attach_original_history_source(
        connection,
        workflow_draft_id=str(result["workflow_draft_id"]),
        intake_session_id=str(result["intake_session_id"]),
        source_inbox_item_id=str(history_item["inbox_item_id"]),
        object_thread_id=result.get("object_thread_id"),
    )
    connection.commit()
    connection.close()

    confirmation = None
    if args.auto_prepare_confirmation and result.get("draft_status") == "needs_confirmation":
        confirmation = prepare_draft_confirmation(
            data_root=data_root,
            workflow_draft_id=str(result["workflow_draft_id"]),
            actor_label=args.actor_label or "history-backfill",
        )

    output = {
        "status": "history_backfill_draft_opened",
        "source_inbox_item_id": history_item["inbox_item_id"],
        "backfill_inbox_item_id": backfill_inbox_item_id,
        "inferred_intent_type": intent_type,
        "target_object_type": target_object_type,
        "target_action": target_action,
        "used_sales_order_hint": order_hint,
        "extracted_fields": fields,
        "candidate_links": candidate_links,
        "pending_associations": pending_associations,
        "draft": result,
    }
    if confirmation:
        output["confirmation"] = confirmation
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
