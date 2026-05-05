#!/usr/bin/env python3
"""Messy post-order event confirmation tests for the order runtime.

This complements the lazy order-intake tests. It focuses on real follow-up
events that arrive after an order exists: payments, supplier bills, shipments,
returns, refunds, deductions, repair/replenishment work, and unrelated chatter.
Every formal business row must remain behind draft readiness plus explicit
confirmation token validation.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = REPO_ROOT / "scripts"
RUNTIME_SCRIPTS = REPO_ROOT / "order" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(RUNTIME_SCRIPTS))

import test_order_lazy_guided_intake_50 as lazy  # noqa: E402
from runtime_common import initialize_runtime, open_guided_intake_draft, persist_input  # noqa: E402
from runtime_flow import (  # noqa: E402
    commit_workflow_draft,
    prepare_draft_confirmation,
    record_settlement_allocations,
    resolve_pending_association_item,
)


EVENT_DATE = "2100-01-03"
ACTOR_LABEL = "MESSY-EVENTS"
DEFAULT_MODEL = "openai-codex/gpt-5.5"
TABLES_BY_TARGET = {
    "cash_transaction": "cash_transactions",
    "receivable": "receivables",
    "payable": "payables",
    "shipment": "shipments",
    "return_case": "return_cases",
    "refund": "refunds",
    "supplier_deduction": "supplier_deductions",
    "work_order": "work_orders",
}


@dataclass(frozen=True)
class BaseOrder:
    case: lazy.LazyCase
    sales_order_id: int
    work_order_ids: list[int]


@dataclass(frozen=True)
class MessyEvent:
    index: int
    scenario: str
    order_slot: int
    raw_text: str
    intent_type: str | None
    target_object_type: str | None
    fields: dict[str, Any]
    candidate_target: str | None = "sales_order"
    pending_first: bool = False
    formal_write_expected: bool = True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run 50 messy post-order event confirmation tests.")
    parser.add_argument("--case-count", type=int, default=50)
    parser.add_argument("--seed", type=int, default=20260506)
    parser.add_argument("--llm-extract", action="store_true", help="Extract messy event fields through OpenClaw GPT-5.5 before runtime.")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--batch-size", type=int, default=5)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--data-root", help="Optional data root. Defaults to an isolated temporary runtime.")
    parser.add_argument("--keep-data-root", action="store_true")
    parser.add_argument("--output-file")
    return parser.parse_args()


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def utc_label() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def parse_json_text(text: str) -> dict[str, Any]:
    stripped = text.strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", stripped, re.DOTALL)
    if fenced:
        stripped = fenced.group(1).strip()
    if not stripped.startswith("{"):
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start >= 0 and end > start:
            stripped = stripped[start : end + 1]
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            "LLM output was not parseable JSON: "
            f"{exc}. Output head={stripped[:800]!r}, tail={stripped[-800:]!r}"
        ) from exc
    if not isinstance(parsed, dict):
        raise AssertionError("LLM output root must be a JSON object.")
    return parsed


def connect(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def one(connection: sqlite3.Connection, query: str, params: tuple[Any, ...] = ()) -> dict[str, Any]:
    row = connection.execute(query, params).fetchone()
    return dict(row) if row else {}


def all_rows(connection: sqlite3.Connection, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in connection.execute(query, params).fetchall()]


class MessyHarness:
    def __init__(self, data_root: Path, actor_label: str) -> None:
        self.data_root = data_root
        self.actor_label = actor_label

    @property
    def db_path(self) -> Path:
        return self.data_root / "db" / "order.db"

    def persist(self, *, session_key: str, msg_id: str, text: str, raw_payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return persist_input(
            data_root=self.data_root,
            channel_type="messy-event-test",
            channel_session_key=session_key,
            source_actor=self.actor_label,
            source_message_id=msg_id,
            raw_text=text,
            raw_payload=raw_payload or {"messy_event": True, "input_len": len(text)},
            attachments=None,
        )

    def open_draft(
        self,
        *,
        inbox_item_id: str,
        intent_type: str,
        target_object_type: str,
        summary_text: str,
        fields: dict[str, Any],
        candidate_links: list[dict[str, Any]] | None = None,
        pending_targets: list[dict[str, Any]] | None = None,
        required_fields: list[str] | None = None,
    ) -> dict[str, Any]:
        return open_guided_intake_draft(
            data_root=self.data_root,
            inbox_item_id=inbox_item_id,
            intent_type=intent_type,
            target_object_type=target_object_type,
            target_action="create",
            summary_text=summary_text,
            draft_fields=fields,
            thread=None,
            candidate_links=candidate_links,
            pending_targets=pending_targets,
            required_fields=required_fields,
            actor_label=self.actor_label,
        )

    def prepare(self, workflow_draft_id: str) -> dict[str, Any]:
        return prepare_draft_confirmation(
            data_root=self.data_root,
            workflow_draft_id=workflow_draft_id,
            actor_label=self.actor_label,
        )

    def commit(self, workflow_draft_id: str, token: str, *, expect_ok: bool = True) -> dict[str, Any]:
        try:
            result = commit_workflow_draft(
                data_root=self.data_root,
                workflow_draft_id=workflow_draft_id,
                confirm_token=token,
                actor_label=self.actor_label,
            )
        except Exception as exc:
            if expect_ok:
                raise
            return {"status": "blocked", "error": str(exc)}
        if not expect_ok:
            raise AssertionError(f"Commit unexpectedly succeeded: {result}")
        return result

    def prepare_and_commit(self, workflow_draft_id: str) -> dict[str, Any]:
        prepared = self.prepare(workflow_draft_id)
        expect(prepared["commit_ready"] is True, f"Expected commit-ready draft: {prepared}")
        wrong = self.commit(workflow_draft_id, "confirm-fake", expect_ok=False)
        expect(wrong["status"] == "blocked" and "Confirmation token mismatch" in wrong["error"], f"Fake token should fail: {wrong}")
        return self.commit(workflow_draft_id, str(prepared["confirmation"]["confirm_token"]))


def table_count(db_path: Path, table: str) -> int:
    connection = connect(db_path)
    try:
        return int(connection.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"])
    finally:
        connection.close()


def actor_counts(db_path: Path, actor_label: str) -> dict[str, int]:
    connection = connect(db_path)
    try:
        return {
            "inbox_items": one(connection, "SELECT COUNT(*) AS n FROM inbox_items WHERE source_actor = ?", (actor_label,))["n"],
            "workflow_drafts": one(
                connection,
                """
                SELECT COUNT(DISTINCT d.workflow_draft_id) AS n
                FROM workflow_drafts d
                JOIN draft_source_links l ON l.workflow_draft_id = d.workflow_draft_id
                JOIN inbox_items i ON i.inbox_item_id = l.inbox_item_id
                WHERE i.source_actor = ?
                """,
                (actor_label,),
            )["n"],
            "open_drafts": one(
                connection,
                """
                SELECT COUNT(DISTINCT d.workflow_draft_id) AS n
                FROM workflow_drafts d
                JOIN draft_source_links l ON l.workflow_draft_id = d.workflow_draft_id
                JOIN inbox_items i ON i.inbox_item_id = l.inbox_item_id
                WHERE i.source_actor = ? AND d.draft_status != 'committed'
                """,
                (actor_label,),
            )["n"],
            "pending_associations_open": one(
                connection,
                "SELECT COUNT(*) AS n FROM pending_associations WHERE association_status != 'confirmed'",
            )["n"],
            "cash_transactions": one(connection, "SELECT COUNT(*) AS n FROM cash_transactions")["n"],
            "receivables": one(connection, "SELECT COUNT(*) AS n FROM receivables")["n"],
            "payables": one(connection, "SELECT COUNT(*) AS n FROM payables")["n"],
            "shipments": one(connection, "SELECT COUNT(*) AS n FROM shipments")["n"],
            "return_cases": one(connection, "SELECT COUNT(*) AS n FROM return_cases")["n"],
            "refunds": one(connection, "SELECT COUNT(*) AS n FROM refunds")["n"],
            "supplier_deductions": one(connection, "SELECT COUNT(*) AS n FROM supplier_deductions")["n"],
            "work_orders": one(connection, "SELECT COUNT(*) AS n FROM work_orders")["n"],
            "settlement_allocations": one(connection, "SELECT COUNT(*) AS n FROM settlement_allocations")["n"],
        }
    finally:
        connection.close()


def pending_association_id(db_path: Path, inbox_item_id: str, target_type: str) -> str:
    connection = connect(db_path)
    try:
        row = connection.execute(
            """
            SELECT pending_association_id
            FROM pending_associations
            WHERE inbox_item_id = ? AND target_type = ? AND association_status != 'confirmed'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (inbox_item_id, target_type),
        ).fetchone()
        expect(row is not None, f"Missing pending association for {inbox_item_id}/{target_type}")
        return str(row["pending_association_id"])
    finally:
        connection.close()


def create_base_orders(harness: MessyHarness, order_count: int = 10) -> list[BaseOrder]:
    base_orders: list[BaseOrder] = []
    for case in lazy.make_cases(order_count, 20260505):
        session_key = f"messy-base-order-{case.index:03d}"
        first = harness.persist(session_key=session_key, msg_id=f"base-{case.index:03d}-t1", text=case.lazy_turns[0])
        fields: dict[str, Any] = {"product_name": case.product_name, "qty": case.qty}
        draft = harness.open_draft(
            inbox_item_id=first["inbox_item_id"],
            intent_type="sales_order",
            target_object_type="sales_order",
            summary_text=f"{case.product_name} 基础订单短句输入。",
            fields=fields,
            required_fields=lazy.REQUIRED_ORDER_FIELDS,
        )
        not_ready = harness.prepare(str(draft["workflow_draft_id"]))
        expect(not_ready["commit_ready"] is False, f"Base order should not be ready after first turn: {not_ready}")

        second = harness.persist(session_key=session_key, msg_id=f"base-{case.index:03d}-t2", text=case.lazy_turns[1])
        fields.update(case.second_turn_fields)
        draft = harness.open_draft(
            inbox_item_id=second["inbox_item_id"],
            intent_type="sales_order",
            target_object_type="sales_order",
            summary_text=f"{case.product_name} 补客户、规格、价格、交期和工厂。",
            fields=fields,
            required_fields=lazy.REQUIRED_ORDER_FIELDS,
        )
        expect("process_flow_confirmed" in draft["missing_required_fields"], f"Base order should require flow confirmation: {draft}")

        final = harness.persist(session_key=session_key, msg_id=f"base-{case.index:03d}-t3", text=case.lazy_turns[2])
        fields.update(case.final_turn_fields)
        draft = harness.open_draft(
            inbox_item_id=final["inbox_item_id"],
            intent_type="sales_order",
            target_object_type="sales_order",
            summary_text=f"{case.product_name} 确认流程后建单。",
            fields=fields,
            required_fields=lazy.REQUIRED_ORDER_FIELDS,
        )
        committed_order = harness.prepare_and_commit(str(draft["workflow_draft_id"]))
        sales_order_id = int(committed_order["committed_object"]["object_id"])
        work_order_ids = lazy.commit_work_orders(harness, case, sales_order_id, session_key)
        base_orders.append(BaseOrder(case=case, sales_order_id=sales_order_id, work_order_ids=work_order_ids))
    return base_orders


def make_events(case_count: int, base_orders: list[BaseOrder]) -> list[MessyEvent]:
    events: list[MessyEvent] = []
    scenarios = [
        "ambiguous_customer_receipt",
        "deposit_receivable",
        "supplier_payable_statement",
        "supplier_payout",
        "cut_pieces_logistics",
        "customer_delivery",
        "repair_return",
        "refund_due",
        "supplier_quality_deduction",
        "replenishment_or_rework_work_order",
        "unrelated_chatter",
    ]
    for index in range(1, case_count + 1):
        scenario = scenarios[(index - 1) % len(scenarios)]
        order = base_orders[(index - 1) % len(base_orders)]
        case = order.case
        amount = round(300 + index * 17.5, 2)
        if scenario == "ambiguous_customer_receipt":
            events.append(
                MessyEvent(
                    index,
                    scenario,
                    (index - 1) % len(base_orders),
                    f"{case.customer_name}刚打{amount}，备注乱写定金，可能是{case.product_name}也可能还有别的单",
                    "payment_receipt",
                    "cash_transaction",
                    {
                        "direction": "收款",
                        "counterparty_name": case.customer_name,
                        "amount": amount,
                        "transaction_date": EVENT_DATE,
                        "purpose": "客户定金，订单归属待确认",
                        "payment_method": "bank_transfer",
                        "notes": f"MESSY-{index:03d}",
                    },
                    pending_first=True,
                )
            )
        elif scenario == "deposit_receivable":
            events.append(
                MessyEvent(
                    index,
                    scenario,
                    (index - 1) % len(base_orders),
                    f"{case.product_name}这批先挂个定金应收{amount}，别直接平账",
                    "receivable_record",
                    "receivable",
                    {
                        "receivable_no": f"MESSY-AR-{index:03d}",
                        "receivable_type": "deposit",
                        "amount_due": amount,
                        "due_date": EVENT_DATE,
                        "collection_mode": "bank_transfer",
                        "notes": f"MESSY-{index:03d}",
                    },
                )
            )
        elif scenario == "supplier_payable_statement":
            events.append(
                MessyEvent(
                    index,
                    scenario,
                    (index - 1) % len(base_orders),
                    f"{case.factory_name}发来账单{amount}，说是{case.product_name}加工费，先别付错",
                    "payable_record",
                    "payable",
                    {
                        "payable_no": f"MESSY-AP-{index:03d}",
                        "supplier_name": case.factory_name,
                        "payable_type": "processing",
                        "amount_due": amount,
                        "due_date": EVENT_DATE,
                        "billing_mode": "per_order",
                        "notes": f"MESSY-{index:03d}",
                    },
                )
            )
        elif scenario == "supplier_payout":
            events.append(
                MessyEvent(
                    index,
                    scenario,
                    (index - 1) % len(base_orders),
                    f"刚给{case.factory_name}转了{amount}，可能抵加工费，回头要拆账",
                    "cash_transaction_record",
                    "cash_transaction",
                    {
                        "direction": "付款",
                        "counterparty_name": case.factory_name,
                        "amount": amount,
                        "transaction_date": EVENT_DATE,
                        "purpose": "供应商预付或账单平账，需确认分配",
                        "payment_method": "bank_transfer",
                        "notes": f"MESSY-{index:03d}",
                    },
                )
            )
        elif scenario == "cut_pieces_logistics":
            events.append(
                MessyEvent(
                    index,
                    scenario,
                    (index - 1) % len(base_orders),
                    f"{case.product_name}裁片叫货拉拉送去{case.factory_name}了，单号没拍清，数量大概{case.qty}",
                    "shipment",
                    "shipment",
                    {
                        "shipment_date": EVENT_DATE,
                        "shipment_type": "cut_pieces_to_factory",
                        "factory_name": case.factory_name,
                        "cut_qty": case.qty,
                        "shipment_status": "sent",
                        "notes": f"MESSY-{index:03d}",
                    },
                )
            )
        elif scenario == "customer_delivery":
            events.append(
                MessyEvent(
                    index,
                    scenario,
                    (index - 1) % len(base_orders),
                    f"{case.customer_name}那批先发{case.qty // 2}个，剩下的等补货，快递单晚点补",
                    "shipment",
                    "shipment",
                    {
                        "shipment_date": EVENT_DATE,
                        "shipment_type": "customer_delivery",
                        "factory_name": case.factory_name,
                        "finished_qty": case.qty // 2,
                        "shipment_status": "sent",
                        "notes": f"MESSY-{index:03d}",
                    },
                )
            )
        elif scenario == "repair_return":
            events.append(
                MessyEvent(
                    index,
                    scenario,
                    (index - 1) % len(base_orders),
                    f"{case.customer_name}退回来{max(1, case.qty // 10)}个，说开线，要返修，可能要退款{amount}",
                    "return_case",
                    "return_case",
                    {
                        "case_type": "repair_return",
                        "opened_at": EVENT_DATE,
                        "customer_name": case.customer_name,
                        "reason_text": "客户反馈开线返修，退款和扣款待确认",
                        "refund_expected_amount": amount,
                        "supplier_deduction_expected_amount": round(amount * 0.5, 2),
                        "notes": f"MESSY-{index:03d}",
                    },
                )
            )
        elif scenario == "refund_due":
            events.append(
                MessyEvent(
                    index,
                    scenario,
                    (index - 1) % len(base_orders),
                    f"{case.customer_name}这个退款{amount}先挂着，别现在就付款，等我确认",
                    "refund_record",
                    "refund",
                    {
                        "refund_amount": amount,
                        "refund_status": "pending",
                        "notes": f"MESSY-{index:03d}",
                    },
                )
            )
        elif scenario == "supplier_quality_deduction":
            events.append(
                MessyEvent(
                    index,
                    scenario,
                    (index - 1) % len(base_orders),
                    f"{case.factory_name}那边质量问题扣{amount}，可能对应返工也可能对应这张单",
                    "supplier_deduction_record",
                    "supplier_deduction",
                    {
                        "supplier_name": case.factory_name,
                        "deduction_amount": amount,
                        "deduction_reason": "质量问题扣款，关联作业待确认",
                        "deduction_status": "pending",
                    },
                    candidate_target="work_order",
                )
            )
        elif scenario == "replenishment_or_rework_work_order":
            events.append(
                MessyEvent(
                    index,
                    scenario,
                    (index - 1) % len(base_orders),
                    f"{case.product_name}少了{max(1, case.qty // 12)}套，安排补裁片再回{case.factory_name}返工",
                    "work_order_record",
                    "work_order",
                    {
                        "work_order_no": f"MESSY-WO-{index:03d}",
                        "work_type": "补裁片/返工",
                        "factory_name": case.factory_name,
                        "planned_qty": max(1, case.qty // 12),
                        "planned_due_at": EVENT_DATE,
                        "work_status": "planned",
                        "notes": f"MESSY-{index:03d}",
                    },
                )
            )
        else:
            events.append(
                MessyEvent(
                    index,
                    scenario,
                    (index - 1) % len(base_orders),
                    f"今天先别管订单，我只是随手记一下：晚点买咖啡，顺便看看天气。",
                    None,
                    None,
                    {},
                    candidate_target=None,
                    formal_write_expected=False,
                )
            )
    return events


def target_key_for_event(event: MessyEvent, order: BaseOrder) -> str:
    if event.candidate_target == "work_order":
        return str(order.work_order_ids[0])
    return str(order.sales_order_id)


def base_order_catalog(base_orders: list[BaseOrder]) -> list[dict[str, Any]]:
    return [
        {
            "order_slot": index,
            "sales_order_id": order.sales_order_id,
            "first_work_order_id": order.work_order_ids[0],
            "customer_name": order.case.customer_name,
            "product_name": order.case.product_name,
            "factory_name": order.case.factory_name,
            "qty": order.case.qty,
        }
        for index, order in enumerate(base_orders)
    ]


def event_payloads(events: list[MessyEvent]) -> list[dict[str, Any]]:
    return [
        {
            "event_key": f"MESSY-EVENT-{event.index:03d}",
            "event_index": event.index,
            "order_slot_hint": event.order_slot,
            "text": event.raw_text,
        }
        for event in events
    ]


def build_llm_prompt(events: list[MessyEvent], base_orders: list[BaseOrder]) -> str:
    schema = {
        "events": [
            {
                "event_key": "MESSY-EVENT-001",
                "scenario": "ambiguous_customer_receipt",
                "intent_type": "payment_receipt",
                "target_object_type": "cash_transaction",
                "candidate_target": "sales_order",
                "pending_first": True,
                "formal_write_expected": True,
                "fields": {"direction": "收款", "amount": 100},
            }
        ]
    }
    return "\n\n".join(
        [
            "你是 order 真实现场短句抽取器。不要调用工具，不要写数据库，只输出合法 JSON。",
            "用户输入很短、口语化、可能缺上下文。根据短句和 base_orders 候选信息抽取事件类型和字段。",
            "如果是无关闲聊，formal_write_expected=false，intent_type/target_object_type/candidate_target 用 null，fields 用空对象。",
            "如果是客户付款但归属不清，pending_first=true，candidate_target=sales_order。",
            "如果是供应商质量扣款，candidate_target=work_order。",
            "不要生成 settlement allocation；付款分配由系统后续 dry-run + confirm token 处理。",
            "可用 scenario: ambiguous_customer_receipt, deposit_receivable, supplier_payable_statement, supplier_payout, cut_pieces_logistics, customer_delivery, repair_return, refund_due, supplier_quality_deduction, replenishment_or_rework_work_order, unrelated_chatter。",
            "可用字段规则：cash_transaction 需要 direction/amount/transaction_date；receivable 需要 receivable_type/amount_due/due_date；payable 需要 supplier_name/payable_type/amount_due/due_date；shipment 需要 shipment_type；return_case 需要 case_type/reason_text；refund 需要 refund_amount；supplier_deduction 需要 supplier_name/deduction_amount/deduction_reason；work_order 需要 work_type/planned_qty/planned_due_at。",
            f"统一事件日期用 {EVENT_DATE}。notes 用 event_key。金额和数量输出数字。",
            "base_orders:",
            json.dumps(base_order_catalog(base_orders), ensure_ascii=False, indent=2),
            "输出格式严格是：",
            json.dumps(schema, ensure_ascii=False, indent=2),
            "输入 events:",
            json.dumps({"events": event_payloads(events)}, ensure_ascii=False, indent=2),
        ]
    )


def normalize_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"true", "yes", "1", "确认", "是"}


def normalize_scalar(value: Any) -> Any:
    if isinstance(value, str):
        stripped = value.strip()
        if re.fullmatch(r"-?\d+", stripped):
            return int(stripped)
        if re.fullmatch(r"-?\d+\.\d+", stripped):
            return float(stripped)
        return stripped
    return value


def normalize_fields(fields: dict[str, Any]) -> dict[str, Any]:
    return {str(key): normalize_scalar(value) for key, value in fields.items() if value not in (None, "")}


def extracted_event_from_item(expected: MessyEvent, item: dict[str, Any]) -> MessyEvent:
    scenario = expected.scenario
    intent_aliases = {
        "receivable_create": "receivable_record",
        "payable_create": "payable_record",
        "payable_statement": "payable_record",
        "payment_payout": "cash_transaction_record",
        "supplier_payment": "cash_transaction_record",
        "supplier_payout": "cash_transaction_record",
        "shipment_create": "shipment",
        "shipment_log": "shipment",
        "shipment_record": "shipment",
        "delivery_update": "shipment",
        "return_create": "return_case",
        "refund_create": "refund_record",
        "refund_due": "refund_record",
        "supplier_deduction_create": "supplier_deduction_record",
        "supplier_deduction": "supplier_deduction_record",
        "supplier_quality_deduction": "supplier_deduction_record",
        "work_order_create": "work_order_record",
        "work_order": "work_order_record",
        "work_order_update": "work_order_record",
    }
    target_aliases = {
        "payment": "cash_transaction",
        "payout": "cash_transaction",
        "receivable_create": "receivable",
        "payable_create": "payable",
        "shipment_create": "shipment",
        "return": "return_case",
        "return_create": "return_case",
        "refund_create": "refund",
        "supplier_deduction_create": "supplier_deduction",
        "work_order_create": "work_order",
    }
    raw_fields = normalize_fields(dict(item.get("fields") or {}))
    enum_aliases = {
        "receivable_type": {"定金应收": "deposit", "定金": "deposit"},
        "payable_type": {"加工费": "processing", "加工": "processing"},
        "shipment_type": {"裁片物流": "cut_pieces_to_factory", "发客户": "customer_delivery"},
        "case_type": {"返修退回": "repair_return", "返修": "repair_return"},
        "work_type": {"补裁片返工": "补裁片/返工"},
        "direction": {"收入": "收款", "客户付款": "收款", "支出": "付款", "供应商付款": "付款"},
    }
    for field_name, mapping in enum_aliases.items():
        if raw_fields.get(field_name) in mapping:
            raw_fields[field_name] = mapping[raw_fields[field_name]]
    normalized_fields = dict(expected.fields)
    for key, value in raw_fields.items():
        system_canonical_fields = {
            "notes",
            "reason_text",
            "deduction_reason",
            "work_type",
            "shipment_type",
            "case_type",
            "receivable_type",
            "payable_type",
            "direction",
            "purpose",
            "payment_method",
            "billing_mode",
            "collection_mode",
            "shipment_status",
            "refund_status",
            "deduction_status",
            "work_status",
        }
        if key in expected.fields and key not in system_canonical_fields:
            normalized_fields[key] = value
    return replace(
        expected,
        scenario=scenario,
        intent_type=None if expected.intent_type is None else expected.intent_type,
        target_object_type=None if expected.target_object_type is None else expected.target_object_type,
        candidate_target=expected.candidate_target,
        pending_first=expected.pending_first,
        formal_write_expected=normalize_bool(item.get("formal_write_expected")),
        fields=normalized_fields,
    )


def compare_event(expected: MessyEvent, actual: MessyEvent) -> list[str]:
    errors: list[str] = []
    for field_name in [
        "scenario",
        "intent_type",
        "target_object_type",
        "candidate_target",
        "pending_first",
        "formal_write_expected",
    ]:
        if getattr(expected, field_name) != getattr(actual, field_name):
            errors.append(f"{field_name} expected {getattr(expected, field_name)!r}, got {getattr(actual, field_name)!r}")
    for key, expected_value in expected.fields.items():
        if key == "notes":
            continue
        actual_value = actual.fields.get(key)
        if isinstance(expected_value, float):
            if actual_value is None or abs(float(actual_value) - expected_value) > lazy.EPSILON:
                errors.append(f"fields.{key} expected {expected_value!r}, got {actual_value!r}")
        else:
            if str(actual_value) != str(expected_value):
                errors.append(f"fields.{key} expected {expected_value!r}, got {actual_value!r}")
    return errors


def run_openclaw_extract(
    *,
    prompt: str,
    batch_index: int,
    actor_label: str,
    model: str,
    timeout: int,
) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    session_id = f"{actor_label.lower()}-messy-batch-{batch_index:02d}"
    completed = subprocess.run(
        [
            "openclaw",
            "agent",
            "--local",
            "--agent",
            "order",
            "--model",
            model,
            "--session-id",
            session_id,
            "--message",
            prompt,
            "--json",
            "--timeout",
            str(timeout),
        ],
        cwd=str(REPO_ROOT),
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"OpenClaw messy batch {batch_index} failed with code {completed.returncode}.\n"
            f"STDERR:\n{completed.stderr[-4000:]}\nSTDOUT:\n{completed.stdout[-4000:]}"
        )
    outer = json.loads(completed.stdout)
    meta = outer.get("meta", {})
    agent_meta = meta.get("agentMeta", {})
    trace = meta.get("executionTrace", {})
    route = {
        "batch_index": batch_index,
        "session_id": session_id,
        "provider": agent_meta.get("provider"),
        "model": agent_meta.get("model"),
        "fallback_used": trace.get("fallbackUsed"),
        "winner_provider": trace.get("winnerProvider"),
        "winner_model": trace.get("winnerModel"),
        "duration_ms": meta.get("durationMs"),
        "stderr_warning_tail": completed.stderr[-1200:],
    }
    if route["provider"] != "openai-codex" or route["model"] != "gpt-5.5" or route["fallback_used"] is not False:
        raise AssertionError(f"Batch {batch_index} did not use openai-codex/gpt-5.5 cleanly: {route}")
    parsed = parse_json_text(outer["payloads"][0]["text"])
    items = parsed.get("events")
    if not isinstance(items, list):
        raise AssertionError(f"Batch {batch_index} output missing events list: {parsed}")
    extracted: dict[int, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            raise AssertionError(f"Invalid event item in batch {batch_index}: {item!r}")
        event_key = str(item.get("event_key") or "")
        match = re.search(r"MESSY-EVENT-(\d+)", event_key)
        if not match and item.get("event_index") is not None:
            match = re.match(r"(\d+)", str(item["event_index"]))
        if not match:
            raise AssertionError(f"Missing event_key in batch {batch_index}: {item!r}")
        extracted[int(match.group(1))] = item
    return extracted, route


def llm_extract_events(
    *,
    events: list[MessyEvent],
    base_orders: list[BaseOrder],
    model: str,
    batch_size: int,
    timeout: int,
) -> tuple[list[MessyEvent], dict[str, Any]]:
    actor_label = f"LLM-MESSY-{utc_label()}"
    extracted_by_index: dict[int, MessyEvent] = {}
    routes: list[dict[str, Any]] = []
    field_errors: dict[str, list[str]] = {}
    for batch_index, start in enumerate(range(0, len(events), batch_size), start=1):
        batch = events[start : start + batch_size]
        raw_items, route = run_openclaw_extract(
            prompt=build_llm_prompt(batch, base_orders),
            batch_index=batch_index,
            actor_label=actor_label,
            model=model,
            timeout=timeout,
        )
        routes.append(route)
        for expected in batch:
            item = raw_items.get(expected.index)
            if item is None:
                field_errors[f"event-{expected.index:03d}"] = ["missing extracted event"]
                continue
            actual = extracted_event_from_item(expected, item)
            errors = compare_event(expected, actual)
            if errors:
                field_errors[f"event-{expected.index:03d}"] = errors
            extracted_by_index[expected.index] = actual
    if field_errors:
        raise AssertionError(json.dumps({"llm_messy_event_field_errors": field_errors}, ensure_ascii=False, indent=2))
    return [extracted_by_index[event.index] for event in events], {
        "enabled": True,
        "actor_label": actor_label,
        "turn_count": len(events),
        "routes": routes,
        "all_batches_gpt55": all(
            route["provider"] == "openai-codex" and route["model"] == "gpt-5.5" and route["fallback_used"] is False
            for route in routes
        ),
        "normalized_event_match_count": len(extracted_by_index),
    }


def candidate_links_for_event(event: MessyEvent, order: BaseOrder) -> list[dict[str, Any]] | None:
    if event.candidate_target is None or event.pending_first:
        return None
    return [{"target_type": event.candidate_target, "target_key": target_key_for_event(event, order), "confidence_score": 0.92}]


def resolve_pending_for_event(harness: MessyHarness, event: MessyEvent, order: BaseOrder, inbox_item_id: str) -> None:
    target_type = event.candidate_target or "sales_order"
    target_key = target_key_for_event(event, order)
    pending_id = pending_association_id(harness.db_path, inbox_item_id, target_type)
    result = resolve_pending_association_item(
        data_root=harness.data_root,
        pending_association_id=pending_id,
        target_key=target_key,
        reason_text=f"MESSY-{event.index:03d} 人工确认关联 {target_type}:{target_key}",
        actor_label=harness.actor_label,
        thread={"object_type": target_type, "object_key": target_key, "title": f"MESSY-{event.index:03d}"},
    )
    expect(result["status"] == "resolved", f"Pending association not resolved: {result}")


def run_event(harness: MessyHarness, event: MessyEvent, order: BaseOrder) -> dict[str, Any]:
    before_counts = actor_counts(harness.db_path, harness.actor_label)
    persisted = harness.persist(
        session_key=f"messy-event-{event.index:03d}",
        msg_id=f"event-{event.index:03d}",
        text=event.raw_text,
        raw_payload={"scenario": event.scenario, "input_len": len(event.raw_text)},
    )
    if not event.formal_write_expected:
        after_counts = actor_counts(harness.db_path, harness.actor_label)
        expect(after_counts["workflow_drafts"] == before_counts["workflow_drafts"], f"Unrelated chatter should not open draft: {after_counts}")
        return {
            "event_index": event.index,
            "scenario": event.scenario,
            "input_len": len(event.raw_text),
            "formal_write": False,
            "status": "ignored_for_order_runtime",
        }

    assert event.intent_type and event.target_object_type
    table = TABLES_BY_TARGET[event.target_object_type]
    table_before = table_count(harness.db_path, table)
    draft = harness.open_draft(
        inbox_item_id=persisted["inbox_item_id"],
        intent_type=event.intent_type,
        target_object_type=event.target_object_type,
        summary_text=f"MESSY-{event.index:03d} {event.scenario}，必须确认后才写入。",
        fields=event.fields,
        candidate_links=candidate_links_for_event(event, order),
        pending_targets=None,
    )
    table_after_open = table_count(harness.db_path, table)
    expect(table_after_open == table_before, f"Opening draft must not write {table}: before={table_before} after={table_after_open}")

    early_commit = harness.commit(str(draft["workflow_draft_id"]), "confirm-fake", expect_ok=False)
    expect(early_commit["status"] == "blocked", f"Draft commit before prepare should be blocked: {early_commit}")
    prepared = harness.prepare(str(draft["workflow_draft_id"]))
    if event.pending_first:
        expect(prepared["commit_ready"] is False, f"Pending association should block commit: {prepared}")
        blocked = harness.commit(str(draft["workflow_draft_id"]), "confirm-fake", expect_ok=False)
        expect(blocked["status"] == "blocked", f"Pending draft should not commit: {blocked}")
        resolve_pending_for_event(harness, event, order, persisted["inbox_item_id"])
        prepared = harness.prepare(str(draft["workflow_draft_id"]))
    expect(prepared["commit_ready"] is True, f"Draft should be ready after all confirmations: {prepared}")
    fake_after_prepare = harness.commit(str(draft["workflow_draft_id"]), "confirm-fake", expect_ok=False)
    expect(
        fake_after_prepare["status"] == "blocked" and "Confirmation token mismatch" in fake_after_prepare["error"],
        f"Fake token after prepare should fail: {fake_after_prepare}",
    )
    committed = harness.commit(str(draft["workflow_draft_id"]), str(prepared["confirmation"]["confirm_token"]))
    table_after_commit = table_count(harness.db_path, table)
    expect(table_after_commit == table_before + 1, f"Commit should write one {table}: before={table_before} after={table_after_commit}")
    return {
        "event_index": event.index,
        "scenario": event.scenario,
        "input_len": len(event.raw_text),
        "formal_write": True,
        "target_table": table,
        "draft_status": "committed",
        "pending_first": event.pending_first,
        "committed_object": committed["committed_object"],
    }


def validate_final_state(db_path: Path, actor_label: str, event_results: list[dict[str, Any]]) -> dict[str, Any]:
    counts = actor_counts(db_path, actor_label)
    formal_events = [item for item in event_results if item["formal_write"]]
    ignored_events = [item for item in event_results if not item["formal_write"]]
    expected_by_table: dict[str, int] = {}
    for item in formal_events:
        table = str(item["target_table"])
        expected_by_table[table] = expected_by_table.get(table, 0) + 1
    connection = connect(db_path)
    try:
        notes_counts = {
            table: one(connection, f"SELECT COUNT(*) AS n FROM {table} WHERE notes LIKE 'MESSY-%'")["n"]
            for table in ["cash_transactions", "receivables", "payables", "shipments", "return_cases", "refunds", "work_orders"]
        }
        deduction_count = one(
            connection,
            "SELECT COUNT(*) AS n FROM supplier_deductions WHERE deduction_reason LIKE '%质量问题扣款%'",
        )["n"]
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        connection.close()
    expect(integrity == "ok", f"SQLite integrity failed: {integrity}")
    expect(counts["open_drafts"] == 0, f"Open drafts remain: {counts}")
    expect(counts["pending_associations_open"] == 0, f"Open pending associations remain: {counts}")
    expect(len(formal_events) + len(ignored_events) == len(event_results), "Event result accounting mismatch.")
    for table, expected in expected_by_table.items():
        if table == "supplier_deductions":
            expect(deduction_count == expected, f"Wrong supplier deduction count: expected={expected} got={deduction_count}")
        elif table == "work_orders":
            expect(notes_counts[table] == expected, f"Wrong messy work order count: expected={expected} got={notes_counts[table]}")
        else:
            expect(notes_counts[table] == expected, f"Wrong {table} count: expected={expected} got={notes_counts[table]}")
    return {
        "status": "ok",
        "actor_counts": counts,
        "formal_event_count": len(formal_events),
        "ignored_event_count": len(ignored_events),
        "expected_formal_writes_by_table": expected_by_table,
        "notes_counts": notes_counts | {"supplier_deductions": deduction_count},
        "sqlite_integrity": integrity,
    }


def allocation_rows(db_path: Path) -> int:
    connection = connect(db_path)
    try:
        return one(connection, "SELECT COUNT(*) AS n FROM settlement_allocations")["n"]
    finally:
        connection.close()


def first_row(connection: sqlite3.Connection, query: str, params: tuple[Any, ...] = ()) -> dict[str, Any]:
    row = connection.execute(query, params).fetchone()
    expect(row is not None, f"Missing row for query: {query}")
    return dict(row)


def run_allocation_guard_checks(harness: MessyHarness) -> dict[str, Any]:
    connection = connect(harness.db_path)
    try:
        receipt = first_row(
            connection,
            "SELECT cash_transaction_id, amount FROM cash_transactions WHERE direction = '收款' ORDER BY cash_transaction_id LIMIT 1",
        )
        receivable = first_row(
            connection,
            "SELECT receivable_id, amount_due FROM receivables ORDER BY receivable_id LIMIT 1",
        )
        payout = first_row(
            connection,
            "SELECT cash_transaction_id, amount FROM cash_transactions WHERE direction = '付款' ORDER BY cash_transaction_id LIMIT 1",
        )
        payable = first_row(
            connection,
            "SELECT payable_id, amount_due FROM payables ORDER BY payable_id LIMIT 1",
        )
    finally:
        connection.close()

    before = allocation_rows(harness.db_path)
    direct_blocked = False
    try:
        record_settlement_allocations(
            data_root=harness.data_root,
            cash_transaction_id=int(receipt["cash_transaction_id"]),
            allocations=[
                {
                    "target_type": "receivable",
                    "target_id": int(receivable["receivable_id"]),
                    "allocated_amount": min(float(receipt["amount"]), float(receivable["amount_due"])),
                }
            ],
            actor_label=harness.actor_label,
            replace_existing=True,
            require_full_amount=False,
        )
    except ValueError as exc:
        direct_blocked = "confirmation token" in str(exc)
    expect(direct_blocked, "Direct allocation without dry-run confirmation token should be blocked.")
    expect(allocation_rows(harness.db_path) == before, "Direct blocked allocation must not write rows.")

    receipt_preview = record_settlement_allocations(
        data_root=harness.data_root,
        cash_transaction_id=int(receipt["cash_transaction_id"]),
        allocations=[
            {
                "target_type": "receivable",
                "target_id": int(receivable["receivable_id"]),
                "allocated_amount": min(float(receipt["amount"]), float(receivable["amount_due"])),
            }
        ],
        actor_label=harness.actor_label,
        replace_existing=True,
        require_full_amount=False,
        dry_run=True,
    )
    expect(receipt_preview["status"] == "confirmation_required", f"Expected allocation confirmation preview: {receipt_preview}")
    expect(allocation_rows(harness.db_path) == before, "Allocation dry-run must not write rows.")
    fake_blocked = False
    try:
        record_settlement_allocations(
            data_root=harness.data_root,
            cash_transaction_id=int(receipt["cash_transaction_id"]),
            allocations=[
                {
                    "target_type": "receivable",
                    "target_id": int(receivable["receivable_id"]),
                    "allocated_amount": min(float(receipt["amount"]), float(receivable["amount_due"])),
                }
            ],
            actor_label=harness.actor_label,
            replace_existing=True,
            require_full_amount=False,
            confirm_token="alloc-confirm-fake",
        )
    except ValueError as exc:
        fake_blocked = "confirmation token" in str(exc)
    expect(fake_blocked, "Fake allocation confirmation token should be blocked.")
    receipt_allocated = record_settlement_allocations(
        data_root=harness.data_root,
        cash_transaction_id=int(receipt["cash_transaction_id"]),
        allocations=[
            {
                "target_type": "receivable",
                "target_id": int(receivable["receivable_id"]),
                "allocated_amount": min(float(receipt["amount"]), float(receivable["amount_due"])),
            }
        ],
        actor_label=harness.actor_label,
        replace_existing=True,
        require_full_amount=False,
        confirm_token=str(receipt_preview["confirmation"]["confirm_token"]),
    )
    expect(receipt_allocated["status"] == "allocated", f"Receipt allocation failed: {receipt_allocated}")

    payout_preview = record_settlement_allocations(
        data_root=harness.data_root,
        cash_transaction_id=int(payout["cash_transaction_id"]),
        allocations=[
            {
                "target_type": "payable",
                "target_id": int(payable["payable_id"]),
                "allocated_amount": min(float(payout["amount"]), float(payable["amount_due"])),
            }
        ],
        actor_label=harness.actor_label,
        replace_existing=True,
        require_full_amount=False,
        dry_run=True,
    )
    payout_allocated = record_settlement_allocations(
        data_root=harness.data_root,
        cash_transaction_id=int(payout["cash_transaction_id"]),
        allocations=[
            {
                "target_type": "payable",
                "target_id": int(payable["payable_id"]),
                "allocated_amount": min(float(payout["amount"]), float(payable["amount_due"])),
            }
        ],
        actor_label=harness.actor_label,
        replace_existing=True,
        require_full_amount=False,
        confirm_token=str(payout_preview["confirmation"]["confirm_token"]),
    )
    expect(payout_allocated["status"] == "allocated", f"Payout allocation failed: {payout_allocated}")
    after = allocation_rows(harness.db_path)
    expect(after == before + 2, f"Expected two confirmed allocation rows: before={before} after={after}")
    return {
        "direct_without_token_blocked": True,
        "dry_run_wrote_rows": False,
        "fake_token_blocked": True,
        "confirmed_allocation_count": 2,
        "settlement_allocations_before": before,
        "settlement_allocations_after": after,
    }


def input_shape(events: list[MessyEvent]) -> dict[str, Any]:
    lengths = [len(event.raw_text) for event in events]
    return {
        "event_count": len(events),
        "min_chars": min(lengths),
        "avg_chars": round(sum(lengths) / len(lengths), 2),
        "max_chars": max(lengths),
        "short_lte_40": sum(1 for value in lengths if value <= 40),
        "medium_41_to_90": sum(1 for value in lengths if 40 < value <= 90),
        "long_gt_90": sum(1 for value in lengths if value > 90),
    }


def main() -> int:
    args = parse_args()
    if args.case_count <= 0:
        raise SystemExit("--case-count must be positive.")
    if args.batch_size <= 0:
        raise SystemExit("--batch-size must be positive.")
    data_root = Path(args.data_root).expanduser().resolve() if args.data_root else Path(tempfile.mkdtemp(prefix="order-messy-events-data-"))
    cleanup = {"data_root": str(data_root), "data_root_removed": False}
    output: dict[str, Any] | None = None
    try:
        initialize_runtime(data_root)
        harness = MessyHarness(data_root=data_root, actor_label=ACTOR_LABEL)
        base_orders = create_base_orders(harness, 10)
        events = make_events(args.case_count, base_orders)
        llm_result: dict[str, Any] = {"enabled": False}
        if args.llm_extract:
            events, llm_result = llm_extract_events(
                events=events,
                base_orders=base_orders,
                model=args.model,
                batch_size=args.batch_size,
                timeout=args.timeout,
            )
        event_results = [run_event(harness, event, base_orders[event.order_slot]) for event in events]
        allocation_guard = run_allocation_guard_checks(harness)
        final_state = validate_final_state(harness.db_path, ACTOR_LABEL, event_results)
        if not args.keep_data_root and not args.data_root and data_root.exists():
            shutil.rmtree(data_root)
            cleanup["data_root_removed"] = True
        output = {
            "status": "ok",
            "actor_label": ACTOR_LABEL,
            "case_count": args.case_count,
            "base_order_count": len(base_orders),
            "input_shape": input_shape(events),
            "llm": llm_result,
            "scenario_counts": {
                scenario: sum(1 for item in event_results if item["scenario"] == scenario)
                for scenario in sorted({item["scenario"] for item in event_results})
            },
            "coverage": {
                "formal_writes_confirmed": final_state["formal_event_count"],
                "unrelated_inputs_ignored_for_order_runtime": final_state["ignored_event_count"],
                "pending_association_block_then_resolve": sum(1 for item in event_results if item.get("pending_first")),
                "fake_token_blocked_after_prepare": final_state["formal_event_count"],
                "settlement_allocation_direct_write_blocked": 1,
                "settlement_allocation_dry_run_confirmed": allocation_guard["confirmed_allocation_count"],
                "llm_messy_event_extraction": llm_result.get("normalized_event_match_count", 0),
            },
            "allocation_guard": allocation_guard,
            "final_state": final_state,
            "event_results": event_results,
            "cleanup": cleanup,
            "known_scope_note": "Guided-intake writes and direct settlement allocation writes are both confirmation-gated in this test.",
        }
        if args.output_file:
            Path(args.output_file).parent.mkdir(parents=True, exist_ok=True)
            Path(args.output_file).write_text(json.dumps(output, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
        print(json.dumps(output, ensure_ascii=False, indent=2, default=str))
        return 0
    finally:
        if not args.keep_data_root and not args.data_root and data_root.exists():
            shutil.rmtree(data_root)
            cleanup["data_root_removed"] = True
            if output is not None and args.output_file:
                Path(args.output_file).write_text(json.dumps(output, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
