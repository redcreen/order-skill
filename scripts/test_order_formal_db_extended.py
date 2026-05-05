#!/usr/bin/env python3
"""Extended formal-database E2E test for the OpenClaw order plugin.

The test writes into the configured formal order database, verifies the live
SQLite state, then restores a pre-test SQLite backup and removes test evidence
files. It is intended for pre-launch validation where the formal database can be
exercised but should not retain synthetic rows.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from test_order_business_cli_e2e import (
    DEFAULT_WRAPPER,
    CheckRecorder,
    CliHarness,
    all_rows,
    commit_linked_object,
    commit_sales_order,
    count_rows,
    expect,
    one,
    pending_association_for,
)


FORMAL_DATA_ROOT = Path.home() / "Documents" / "openclaw-order"
TEST_AS_OF_DATE = "2099-12-31"
TEST_DUE_SOON_DATE = "2100-01-01"


class FormalCliHarness(CliHarness):
    def __init__(self, *, actor_label: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.actor_label = actor_label

    def persist(
        self,
        *,
        msg_id: str,
        text: str,
        session_key: str | None = None,
        raw_payload: dict[str, Any] | None = None,
        attachments: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "text": text,
            "channel_type": "formal-cli-e2e",
            "channel_session_key": session_key or f"{self.actor_label}:default-session",
            "source_actor": self.actor_label,
            "source_message_id": msg_id,
            "raw_payload": raw_payload,
            "attachments": attachments,
        }
        return self.payload_runtime("persist-input", f"persist-{msg_id}", payload)["result"]

    def open_draft(
        self,
        *,
        inbox_item_id: str,
        intent_type: str,
        target_object_type: str,
        target_action: str = "create",
        summary_text: str,
        fields: dict[str, Any],
        candidate_links: list[dict[str, Any]] | None = None,
        pending_associations: list[dict[str, Any]] | None = None,
        required_fields: list[str] | None = None,
        thread: dict[str, str] | None = None,
        name: str = "draft",
    ) -> dict[str, Any]:
        payload = {
            "inbox_item_id": inbox_item_id,
            "intent_type": intent_type,
            "target_object_type": target_object_type,
            "target_action": target_action,
            "summary_text": summary_text,
            "draft_fields": fields,
            "candidate_links": candidate_links,
            "pending_associations": pending_associations,
            "required_fields": required_fields,
            "thread": thread,
            "actor_label": self.actor_label,
        }
        return self.payload_runtime("open-draft", name, payload)["result"]

    def prepare(self, workflow_draft_id: str) -> dict[str, Any]:
        return self.payload_runtime(
            "prepare-confirmation",
            f"prepare-{workflow_draft_id}",
            {"workflow_draft_id": workflow_draft_id, "actor_label": self.actor_label},
        )["result"]

    def commit(self, workflow_draft_id: str, confirm_token: str, *, expect_ok: bool = True) -> dict[str, Any]:
        result = self.payload_runtime(
            "commit-draft",
            f"commit-{workflow_draft_id}",
            {
                "workflow_draft_id": workflow_draft_id,
                "confirm_token": confirm_token,
                "actor_label": self.actor_label,
            },
            expect_ok=expect_ok,
        )
        return result["result"] if expect_ok else result

    def resolve_association(self, *, pending_association_id: str, target_key: str, reason_text: str) -> dict[str, Any]:
        payload = {
            "pending_association_id": pending_association_id,
            "target_key": target_key,
            "reason_text": reason_text,
            "actor_label": self.actor_label,
            "thread": {
                "object_type": "sales_order",
                "object_key": target_key,
                "title": f"{self.actor_label} sales_order:{target_key}",
            },
        }
        return self.payload_runtime("resolve-association", "resolve-association", payload)["result"]

    def allocate(
        self,
        *,
        cash_transaction_id: int,
        allocations: list[dict[str, Any]],
        replace_existing: bool = True,
        require_full_amount: bool = False,
        expect_ok: bool = True,
    ) -> dict[str, Any]:
        payload = {
            "cash_transaction_id": cash_transaction_id,
            "allocations": allocations,
            "replace_existing": replace_existing,
            "require_full_amount": require_full_amount,
            "actor_label": self.actor_label,
        }
        if not expect_ok:
            dry_run_payload = dict(payload)
            dry_run_payload["dry_run"] = True
            return self.payload_runtime("allocate", "allocate-dry-run-negative", dry_run_payload, expect_ok=False)
        direct = self.payload_runtime("allocate", "allocate-direct-blocked", payload, expect_ok=False)
        expect(
            "confirmation token" in direct["error"]["message"],
            f"Direct allocation without confirmation should fail: {direct}",
        )
        dry_run_payload = dict(payload)
        dry_run_payload["dry_run"] = True
        preview = self.payload_runtime("allocate", "allocate-dry-run", dry_run_payload)["result"]
        confirmed_payload = dict(payload)
        confirmed_payload["confirm_token"] = preview["confirmation"]["confirm_token"]
        result = self.payload_runtime("allocate", "allocate-confirmed", confirmed_payload)
        return result["result"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run extended order tests against the formal DB, then restore it.")
    parser.add_argument("--agent", default="order", help="Bound OpenClaw agent id.")
    parser.add_argument("--wrapper", default=str(DEFAULT_WRAPPER), help="Path to installed order_hard_execute.py.")
    parser.add_argument("--data-root", default=str(FORMAL_DATA_ROOT), help="Formal order data root.")
    parser.add_argument("--keep-test-db", action="store_true", help="Do not restore the DB backup after the run.")
    parser.add_argument("--keep-files", action="store_true", help="Do not delete test raw/evidence files after the run.")
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sqlite_backup(db_path: Path, backup_path: Path) -> None:
    source = sqlite3.connect(db_path)
    target = sqlite3.connect(backup_path)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()


def restore_backup(db_path: Path, backup_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        target = Path(str(db_path) + suffix)
        if target.exists():
            target.unlink()
    shutil.copy2(backup_path, db_path)


def connect(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def all_base_tables(connection: sqlite3.Connection) -> list[str]:
    return [
        str(row["name"])
        for row in connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
              AND name NOT LIKE 'sqlite_%'
            ORDER BY name
            """
        )
    ]


def table_counts(connection: sqlite3.Connection) -> dict[str, int]:
    return {table: count_rows(connection, table) for table in all_base_tables(connection)}


def collect_test_files(db_path: Path, actor_label: str) -> list[str]:
    if not db_path.exists():
        return []
    connection = connect(db_path)
    try:
        rows = connection.execute(
            """
            SELECT raw_archive_path
            FROM inbox_items
            WHERE source_actor = ?
            """,
            (actor_label,),
        ).fetchall()
        asset_rows = connection.execute(
            """
            SELECT e.local_path
            FROM evidence_assets e
            JOIN inbox_items i ON i.inbox_item_id = e.inbox_item_id
            WHERE i.source_actor = ?
            """,
            (actor_label,),
        ).fetchall()
    finally:
        connection.close()
    paths = [str(row["raw_archive_path"]) for row in rows if row["raw_archive_path"]]
    paths.extend(str(row["local_path"]) for row in asset_rows if row["local_path"])
    return paths


def remove_files(paths: list[str]) -> list[str]:
    removed: list[str] = []
    for raw in paths:
        path = Path(raw)
        if path.exists():
            path.unlink()
            removed.append(str(path))
    return removed


def run_extended_business_flow(harness: FormalCliHarness, prefix: str, checks: CheckRecorder) -> dict[str, Any]:
    session = f"{prefix}:session"
    customer_a = f"{prefix}-王总"
    customer_b = f"{prefix}-李总"
    product_rabbit = f"{prefix}-小兔子"
    product_bear = f"{prefix}-白熊"

    binding = harness.run("show-binding")
    expect(binding["status"] == "bound" and binding["targetAgent"] == harness.agent, f"Bad binding: {binding}")
    wrong_agent = harness.run("smoke-runtime", "--agent", f"{prefix}-wrong-agent", expect_ok=False, parse_json=False)
    expect("bound to agent" in wrong_agent["stderr"], f"Wrong agent should be rejected: {wrong_agent}")
    checks.ok("formal wrapper binding and wrong-agent guard")

    harness.runtime("init-runtime")
    incomplete = harness.persist(
        msg_id=f"{prefix}:incomplete-production",
        session_key=f"{prefix}:incomplete-session",
        text=f"{product_rabbit} 500 个说要先排期，但客户、工厂和对应订单都没补齐。",
    )
    incomplete_draft = harness.open_draft(
        inbox_item_id=incomplete["inbox_item_id"],
        intent_type="production_arrangement",
        target_object_type="work_order",
        summary_text=f"{product_rabbit} 500 个排期信息不完整。",
        fields={"product_name": product_rabbit, "qty": 500, "work_type": "平车", "planned_qty": 500, "planned_due_at": TEST_AS_OF_DATE},
        name="formal-incomplete-production",
    )
    expect(incomplete_draft["draft_status"] == "collecting", f"Incomplete draft should collect: {incomplete_draft}")
    blocked = harness.commit(str(incomplete_draft["workflow_draft_id"]), "confirm-fake", expect_ok=False)
    expect("open blockers" in blocked["error"]["message"], f"Incomplete draft should not commit: {blocked}")
    checks.ok("incomplete natural-language production input is blocked")

    order_a_id = commit_sales_order(
        harness,
        msg_id=f"{prefix}:sales-order-a",
        text=f"{customer_a} 确认 {product_rabbit} 1000 个，30% 定金，交期 {TEST_AS_OF_DATE}，冯杰平车。",
        order={
            "order_no": f"{prefix}-SO-A",
            "order_date": TEST_AS_OF_DATE,
            "order_type": "customer_order",
            "customer_name": customer_a,
            "product_name": product_rabbit,
            "spec_text": "18cm 粉色",
            "qty": 1000,
            "unit": "个",
            "confirmed_unit_price": 12.5,
            "confirmed_total_amount": 12500,
            "deposit_ratio": 0.3,
            "deposit_expected_amount": 3750,
            "promised_delivery_date": "2099-12-30",
            "current_factory": f"{prefix}-定远乡工厂/冯杰",
            "current_step": "平车",
            "progress_text": f"{prefix} 订单进入平车。",
            "total_cost": 5300,
            "order_status": "in_production",
            "notes": prefix,
        },
    )
    order_b_id = commit_sales_order(
        harness,
        msg_id=f"{prefix}:sales-order-b",
        text=f"{customer_a} 又确认 {product_bear} 600 个，义乌赵总车缝，徐凯充棉手工。",
        order={
            "order_no": f"{prefix}-SO-B",
            "order_date": TEST_AS_OF_DATE,
            "order_type": "customer_order",
            "customer_name": customer_a,
            "product_name": product_bear,
            "spec_text": "20cm 白色",
            "qty": 600,
            "unit": "个",
            "confirmed_unit_price": 18,
            "confirmed_total_amount": 10800,
            "promised_delivery_date": TEST_DUE_SOON_DATE,
            "current_factory": f"{prefix}-义乌赵总/徐凯",
            "current_step": "车缝",
            "progress_text": f"{prefix} 白熊生产中。",
            "order_status": "in_production",
            "notes": prefix,
        },
    )
    ecommerce_order_id = commit_sales_order(
        harness,
        msg_id=f"{prefix}:sales-order-ecommerce",
        text=f"自营电商 {product_bear} 300 个，回义乌云仓入库。",
        order={
            "order_no": f"{prefix}-SO-C",
            "order_date": TEST_AS_OF_DATE,
            "order_type": "ecommerce_self_sale",
            "customer_name": f"{prefix}-自营电商",
            "product_name": product_bear,
            "spec_text": "20cm 白色",
            "qty": 300,
            "unit": "个",
            "confirmed_unit_price": 19,
            "confirmed_total_amount": 5700,
            "promised_delivery_date": TEST_DUE_SOON_DATE,
            "current_factory": f"{prefix}-义乌赵总/徐凯",
            "current_step": "云仓入库",
            "progress_text": f"{prefix} 等待 ERP 入库。",
            "order_status": "in_production",
            "notes": prefix,
        },
    )
    checks.ok("three formal sales orders committed", f"{order_a_id},{order_b_id},{ecommerce_order_id}")

    updated_inbox = harness.persist(
        msg_id=f"{prefix}:sales-order-a-add-qty",
        session_key=session,
        text=f"{customer_a} 的 {product_rabbit} 加单到 1200 个，交期不变，当前进入充棉前准备。",
    )
    update_draft = harness.open_draft(
        inbox_item_id=updated_inbox["inbox_item_id"],
        intent_type="sales_order",
        target_object_type="sales_order",
        target_action="update",
        summary_text=f"{prefix}-SO-A 加单到 1200 个。",
        fields={
            "order_no": f"{prefix}-SO-A",
            "customer_name": customer_a,
            "product_name": product_rabbit,
            "qty": 1200,
            "unit": "个",
            "confirmed_unit_price": 12.5,
            "confirmed_total_amount": 15000,
            "promised_delivery_date": "2099-12-30",
            "current_factory": f"{prefix}-定远乡工厂/冯杰",
            "current_step": "充棉前准备",
            "progress_text": f"{prefix} 客户加单后更新数量。",
            "total_cost": 6300,
            "notes": prefix,
        },
        candidate_links=[{"target_type": "sales_order", "target_key": str(order_a_id), "confidence_score": 1.0}],
        name="formal-order-update",
    )
    harness.prepare_and_commit(str(update_draft["workflow_draft_id"]))
    checks.ok("existing order update committed without creating a new order")

    receipt_inbox = harness.persist(
        msg_id=f"{prefix}:ambiguous-receipt",
        session_key=session,
        text=f"{customer_a} 打来 7000，备注只写了定金，可能对应 {product_rabbit} 和 {product_bear} 两张单。",
        raw_payload={"entity_hints": {"sales_orders": [{"sales_order_id": order_a_id}, {"sales_order_id": order_b_id}]}},
    )
    receipt_draft = harness.open_draft(
        inbox_item_id=receipt_inbox["inbox_item_id"],
        intent_type="payment_receipt",
        target_object_type="cash_transaction",
        summary_text=f"{customer_a} 7000 收款，需要确认分配到两张订单的应收。",
        fields={
            "direction": "收款",
            "counterparty_name": customer_a,
            "amount": 7000,
            "transaction_date": TEST_AS_OF_DATE,
            "purpose": f"{prefix} 多订单定金",
            "payment_method": "bank_transfer",
            "notes": prefix,
        },
        name="formal-ambiguous-receipt",
    )
    with harness.connect() as connection:
        pending_id = pending_association_for(connection, receipt_inbox["inbox_item_id"])
    candidates = harness.runtime("association-candidates", "--pending-association-id", pending_id, "--limit", "5")["result"]
    expect(candidates["candidate_count"] >= 2, f"Expected multi-order candidates: {candidates}")
    harness.resolve_association(
        pending_association_id=pending_id,
        target_key=str(order_a_id),
        reason_text=f"{prefix} 先把收款主关联到兔子订单，后续通过分摊覆盖两张订单。",
    )
    receipt_commit = harness.prepare_and_commit(str(receipt_draft["workflow_draft_id"]))
    receipt_cash_id = int(receipt_commit["committed"]["committed_object"]["object_id"])
    checks.ok("ambiguous receipt produced candidates and was resolved")

    ar_a = commit_linked_object(
        harness,
        msg_id=f"{prefix}:ar-a-deposit",
        text=f"{prefix}-SO-A 定金应收 4500。",
        intent_type="receivable_record",
        target_object_type="receivable",
        fields={
            "receivable_no": f"{prefix}-AR-A",
            "receivable_type": "deposit",
            "amount_due": 4500,
            "due_date": TEST_AS_OF_DATE,
            "collection_mode": "bank_transfer",
            "notes": prefix,
        },
        sales_order_id=order_a_id,
        name="formal-ar-a",
    )
    ar_b = commit_linked_object(
        harness,
        msg_id=f"{prefix}:ar-b-deposit",
        text=f"{prefix}-SO-B 定金应收 2500。",
        intent_type="receivable_record",
        target_object_type="receivable",
        fields={
            "receivable_no": f"{prefix}-AR-B",
            "receivable_type": "deposit",
            "amount_due": 2500,
            "due_date": TEST_AS_OF_DATE,
            "collection_mode": "bank_transfer",
            "notes": prefix,
        },
        sales_order_id=order_b_id,
        name="formal-ar-b",
    )
    allocation = harness.allocate(
        cash_transaction_id=receipt_cash_id,
        allocations=[
            {"target_type": "receivable", "target_id": ar_a, "allocated_amount": 4500},
            {"target_type": "receivable", "target_id": ar_b, "allocated_amount": 2500},
        ],
        require_full_amount=True,
    )
    expect({target["status"] for target in allocation["targets"]} == {"received"}, f"AR allocation failed: {allocation}")
    over_allocation = harness.allocate(
        cash_transaction_id=receipt_cash_id,
        allocations=[{"target_type": "receivable", "target_id": ar_a, "allocated_amount": 8000}],
        expect_ok=False,
    )
    expect("exceed cash transaction amount" in over_allocation["error"]["message"], f"Over-allocation should fail: {over_allocation}")
    checks.ok("multi-order receipt allocation and over-allocation guard verified")

    payable_specs = [
        ("AP-FABRIC", f"{prefix}-布料供应商A", "material", 1800, TEST_AS_OF_DATE),
        ("AP-COMPOSITE", f"{prefix}-弘辉复合", "composite", 650, TEST_AS_OF_DATE),
        ("AP-LASER", f"{prefix}-刘旭", "laser_cut", 900, TEST_DUE_SOON_DATE),
        ("AP-EMB", f"{prefix}-朱昌良", "embroidery", 500, TEST_DUE_SOON_DATE),
        ("AP-SEW", f"{prefix}-冯杰", "processing", 3000, TEST_AS_OF_DATE),
        ("AP-REPAIR", f"{prefix}-冯杰", "repair", 220, TEST_DUE_SOON_DATE),
    ]
    payables: dict[str, int] = {}
    for suffix, supplier, payable_type, amount, due_date in payable_specs:
        payables[suffix] = commit_linked_object(
            harness,
            msg_id=f"{prefix}:{suffix.lower()}",
            text=f"{supplier} {payable_type} 应付 {amount}。",
            intent_type="payable_record",
            target_object_type="payable",
            fields={
                "payable_no": f"{prefix}-{suffix}",
                "supplier_name": supplier,
                "payable_type": payable_type,
                "amount_due": amount,
                "due_date": due_date,
                "billing_mode": "per_order",
                "notes": prefix,
            },
            sales_order_id=order_a_id,
            name=f"formal-{suffix}",
        )
    payout_id = commit_linked_object(
        harness,
        msg_id=f"{prefix}:supplier-payout",
        text=f"先付 {prefix} 供应商 3350，覆盖布料、复合、激光。",
        intent_type="cash_transaction_record",
        target_object_type="cash_transaction",
        fields={
            "direction": "付款",
            "counterparty_name": f"{prefix}-供应商组",
            "amount": 3350,
            "transaction_date": TEST_AS_OF_DATE,
            "purpose": f"{prefix} 材料复合激光付款",
            "payment_method": "bank_transfer",
            "notes": prefix,
        },
        sales_order_id=order_a_id,
        name="formal-payout",
    )
    partial_reject = harness.allocate(
        cash_transaction_id=payout_id,
        allocations=[{"target_type": "payable", "target_id": payables["AP-FABRIC"], "allocated_amount": 1800}],
        require_full_amount=True,
        expect_ok=False,
    )
    expect("fully consume" in partial_reject["error"]["message"], f"Full-amount guard should fail: {partial_reject}")
    payout_allocation = harness.allocate(
        cash_transaction_id=payout_id,
        allocations=[
            {"target_type": "payable", "target_id": payables["AP-FABRIC"], "allocated_amount": 1800},
            {"target_type": "payable", "target_id": payables["AP-COMPOSITE"], "allocated_amount": 650},
            {"target_type": "payable", "target_id": payables["AP-LASER"], "allocated_amount": 900},
        ],
        require_full_amount=True,
    )
    expect({target["status"] for target in payout_allocation["targets"]} == {"paid"}, f"AP allocation failed: {payout_allocation}")
    wrong_direction = harness.allocate(
        cash_transaction_id=receipt_cash_id,
        allocations=[{"target_type": "payable", "target_id": payables["AP-EMB"], "allocated_amount": 10}],
        expect_ok=False,
    )
    expect("cannot allocate to payable" in wrong_direction["error"]["message"], f"Direction guard should fail: {wrong_direction}")
    checks.ok("supplier payable allocation, full-amount guard, and direction guard verified")

    work_orders: dict[str, int] = {}
    for work_no, work_type, provider, due_at in [
        ("WO-SAMPLE", "打样", f"{prefix}-跟单", "2099-12-29"),
        ("WO-SEW", "平车", f"{prefix}-冯杰", TEST_AS_OF_DATE),
        ("WO-COTTON", "充棉", f"{prefix}-张时库", TEST_DUE_SOON_DATE),
        ("WO-HAND", "手工封口", f"{prefix}-定远乡工厂", TEST_DUE_SOON_DATE),
    ]:
        work_orders[work_no] = commit_linked_object(
            harness,
            msg_id=f"{prefix}:{work_no.lower()}",
            text=f"{provider} {work_type} 需要跟进。",
            intent_type="work_order_record",
            target_object_type="work_order",
            fields={
                "work_order_no": f"{prefix}-{work_no}",
                "work_type": work_type,
                "provider_name": provider,
                "planned_qty": 1200,
                "planned_due_at": due_at,
                "work_status": "planned",
                "notes": prefix,
            },
            sales_order_id=order_a_id,
            name=f"formal-{work_no}",
        )
    proof = harness.workspace / f"{prefix}-proof.txt"
    proof.write_text(f"{prefix} 物流/付款混合证据 OCR", encoding="utf-8")
    evidence_inbox = harness.persist(
        msg_id=f"{prefix}:evidence-only",
        session_key=session,
        text=f"{prefix} 上传一张付款和物流截图，需要后续关联。",
        raw_payload={"evidence_kind": "mixed_payment_logistics"},
        attachments=[{"path": str(proof), "mime_type": "text/plain", "extracted_text": f"{prefix} OCR evidence"}],
    )
    expect(evidence_inbox["attachment_count"] == 1, "Evidence attachment should persist.")

    for suffix, shipment_type, factory, finished_qty, cut_qty, order_id in [
        ("SHIP-CUT", "cut_pieces_to_factory", f"{prefix}-冯杰", None, 1200, order_a_id),
        ("SHIP-CUSTOMER", "customer_delivery", f"{prefix}-冯杰", 600, None, order_a_id),
        ("SHIP-WAREHOUSE", "warehouse_receipt", f"{prefix}-义乌赵总/徐凯", 300, None, ecommerce_order_id),
    ]:
        commit_linked_object(
            harness,
            msg_id=f"{prefix}:{suffix.lower()}",
            text=f"{prefix} {shipment_type} 发货记录。",
            intent_type="shipment",
            target_object_type="shipment",
            fields={
                "shipment_date": TEST_AS_OF_DATE,
                "shipment_type": shipment_type,
                "factory_name": factory,
                "finished_qty": finished_qty,
                "cut_qty": cut_qty,
                "shipment_status": "sent",
                "notes": prefix,
            },
            sales_order_id=order_id,
            name=f"formal-{suffix}",
        )
    checks.ok("production work orders, evidence, and logistics committed")

    return_case_id = commit_linked_object(
        harness,
        msg_id=f"{prefix}:return-case",
        text=f"{customer_a} 退回 {product_rabbit} 30 个，要退款 360，并考虑扣 {prefix}-冯杰 180。",
        intent_type="return_case",
        target_object_type="return_case",
        fields={
            "case_type": "repair_return",
            "opened_at": TEST_AS_OF_DATE,
            "customer_name": customer_a,
            "refund_expected_amount": 360,
            "supplier_deduction_expected_amount": 180,
            "reason_text": f"{prefix} 客户退回 30 个。",
            "notes": prefix,
        },
        sales_order_id=order_a_id,
        name="formal-return-case",
    )
    refund_inbox = harness.persist(
        msg_id=f"{prefix}:refund-record",
        session_key=session,
        text=f"{customer_a} 退货退款 360，先建退款账，随后付款平账。",
    )
    refund_draft = harness.open_draft(
        inbox_item_id=refund_inbox["inbox_item_id"],
        intent_type="refund_record",
        target_object_type="refund",
        summary_text=f"{prefix} 退货退款 360。",
        fields={"refund_amount": 360, "refund_status": "pending", "notes": prefix},
        candidate_links=[
            {"target_type": "sales_order", "target_key": str(order_a_id), "confidence_score": 1.0},
            {"target_type": "return_case", "target_key": str(return_case_id), "confidence_score": 1.0},
        ],
        name="formal-refund-record",
    )
    refund_commit = harness.prepare_and_commit(str(refund_draft["workflow_draft_id"]))
    refund_id = int(refund_commit["committed"]["committed_object"]["object_id"])
    refund_payout_id = commit_linked_object(
        harness,
        msg_id=f"{prefix}:refund-payout",
        text=f"退给 {customer_a} {prefix} 退货款 360。",
        intent_type="cash_transaction_record",
        target_object_type="cash_transaction",
        fields={
            "direction": "付款",
            "counterparty_name": customer_a,
            "amount": 360,
            "transaction_date": TEST_AS_OF_DATE,
            "purpose": f"{prefix} 客户退款",
            "payment_method": "bank_transfer",
            "notes": prefix,
        },
        sales_order_id=order_a_id,
        name="formal-refund-payout",
    )
    refund_allocation = harness.allocate(
        cash_transaction_id=refund_payout_id,
        allocations=[{"target_type": "refund", "target_id": refund_id, "allocated_amount": 360}],
        require_full_amount=True,
    )
    expect(refund_allocation["targets"][0]["status"] == "paid", f"Refund allocation failed: {refund_allocation}")
    deduction_inbox = harness.persist(
        msg_id=f"{prefix}:supplier-deduction",
        session_key=session,
        text=f"{prefix}-冯杰 因退货质量问题扣款 180，关联平车作业和退货 case。",
    )
    deduction_draft = harness.open_draft(
        inbox_item_id=deduction_inbox["inbox_item_id"],
        intent_type="supplier_deduction_record",
        target_object_type="supplier_deduction",
        summary_text=f"{prefix}-冯杰 质量扣款 180。",
        fields={
            "supplier_name": f"{prefix}-冯杰",
            "deduction_amount": 180,
            "deduction_reason": f"{prefix} 退货质量问题扣款。",
            "deduction_status": "pending",
        },
        candidate_links=[
            {"target_type": "return_case", "target_key": str(return_case_id), "confidence_score": 1.0},
            {"target_type": "work_order", "target_key": str(work_orders["WO-SEW"]), "confidence_score": 1.0},
        ],
        name="formal-supplier-deduction",
    )
    deduction_commit = harness.prepare_and_commit(str(deduction_draft["workflow_draft_id"]))
    deduction_id = int(deduction_commit["committed"]["committed_object"]["object_id"])
    checks.ok(
        "return/refund/deduction formal commit path verified",
        json.dumps({"return_case_id": return_case_id, "refund_id": refund_id, "deduction_id": deduction_id}, ensure_ascii=False),
    )

    history = harness.runtime("history-search", "--query", prefix, "--limit", "50")["result"]
    replay = harness.runtime("history-replay", "--channel-session-key", session, "--limit", "100")["result"]
    shown = harness.runtime("history-show", "--source-message-id", f"{prefix}:evidence-only", "--include-evidence-text")["result"]
    expect(history["result_count"] >= 20, f"History search should find test run rows: {history}")
    expect(replay["item_count"] >= 4, f"History replay should keep continuity for the active session: {replay}")
    expect(len(shown["item"]["evidence_assets"]) == 1, f"History evidence missing: {shown}")
    checks.ok("history search/show/replay verified on formal DB")

    control = harness.runtime("refresh-control-tower", "--as-of-date", TEST_AS_OF_DATE, "--actor-label", prefix)["result"]
    report = harness.runtime("daily-report", "--report-date", TEST_AS_OF_DATE, "--skip-refresh", "--actor-label", prefix)["result"]
    expect(control["exception_count"] >= 1, f"Expected overdue exceptions: {control}")
    expect(report["report_json"]["orders_in_production"] >= 3, f"Report should include production orders: {report}")
    expect(report["report_json"]["receivable_open_amount"] >= 0, "Report should calculate receivable amount.")
    checks.ok("formal control tower and daily report generated")

    with harness.connect() as connection:
        state = {
            "row_counts_for_prefix": {
                "sales_orders": one(connection, "SELECT COUNT(*) AS n FROM sales_orders WHERE order_no LIKE ?", (f"{prefix}-%",))["n"],
                "receivables": one(connection, "SELECT COUNT(*) AS n FROM receivables WHERE receivable_no LIKE ?", (f"{prefix}-%",))["n"],
                "payables": one(connection, "SELECT COUNT(*) AS n FROM payables WHERE payable_no LIKE ?", (f"{prefix}-%",))["n"],
                "work_orders": one(connection, "SELECT COUNT(*) AS n FROM work_orders WHERE work_order_no LIKE ?", (f"{prefix}-%",))["n"],
                "return_cases": one(connection, "SELECT COUNT(*) AS n FROM return_cases WHERE notes = ?", (prefix,))["n"],
                "refunds": one(connection, "SELECT COUNT(*) AS n FROM refunds WHERE notes = ?", (prefix,))["n"],
                "supplier_deductions": one(
                    connection,
                    "SELECT COUNT(*) AS n FROM supplier_deductions WHERE deduction_reason LIKE ?",
                    (f"%{prefix}%",),
                )["n"],
                "inbox_items": one(connection, "SELECT COUNT(*) AS n FROM inbox_items WHERE source_actor = ?", (prefix,))["n"],
            },
            "order_update": one(connection, "SELECT order_no, qty, confirmed_total_amount, current_step FROM sales_orders WHERE sales_order_id = ?", (order_a_id,)),
            "receivables": all_rows(
                connection,
                "SELECT receivable_no, amount_due, amount_received, receivable_status FROM receivables WHERE receivable_no LIKE ? ORDER BY receivable_no",
                (f"{prefix}-%",),
            ),
            "payables": all_rows(
                connection,
                "SELECT payable_no, amount_due, amount_paid, payable_status FROM payables WHERE payable_no LIKE ? ORDER BY payable_no",
                (f"{prefix}-%",),
            ),
            "finance": all_rows(
                connection,
                "SELECT order_no, payable_amount, cash_in_amount, cash_out_amount FROM v_order_finance_status WHERE order_no LIKE ? ORDER BY order_no",
                (f"{prefix}-%",),
            ),
            "forecast": all_rows(
                connection,
                "SELECT order_no, expected_cash_in, expected_cash_out FROM v_cash_forecast WHERE order_no LIKE ? ORDER BY order_no",
                (f"{prefix}-%",),
            ),
            "refunds": all_rows(
                connection,
                "SELECT refund_amount, refund_status FROM refunds WHERE notes = ? ORDER BY refund_id",
                (prefix,),
            ),
            "supplier_deductions": all_rows(
                connection,
                """
                SELECT deduction_amount, deduction_status
                FROM supplier_deductions
                WHERE deduction_reason LIKE ?
                ORDER BY supplier_deduction_id
                """,
                (f"%{prefix}%",),
            ),
            "followups": all_rows(
                connection,
                "SELECT followup_type, due_at, priority, notes FROM v_open_followups WHERE notes LIKE ? ORDER BY due_at, followup_type",
                (f"%{prefix}%",),
            ),
        }
    expect(state["row_counts_for_prefix"]["sales_orders"] == 3, f"Expected 3 prefixed orders: {state}")
    expect(state["row_counts_for_prefix"]["receivables"] == 2, f"Expected 2 prefixed receivables: {state}")
    expect(state["row_counts_for_prefix"]["payables"] == 6, f"Expected 6 prefixed payables: {state}")
    expect(state["row_counts_for_prefix"]["work_orders"] == 4, f"Expected 4 prefixed work orders: {state}")
    expect(state["row_counts_for_prefix"]["return_cases"] == 1, f"Expected 1 prefixed return case: {state}")
    expect(state["row_counts_for_prefix"]["refunds"] == 1, f"Expected 1 prefixed refund: {state}")
    expect(state["row_counts_for_prefix"]["supplier_deductions"] == 1, f"Expected 1 prefixed supplier deduction: {state}")
    expect(state["row_counts_for_prefix"]["inbox_items"] >= 26, f"Expected many prefixed inbox rows: {state}")
    expect(float(state["order_update"]["qty"]) == 1200.0, f"Order update did not persist: {state['order_update']}")
    receivable_statuses = {row["receivable_no"]: row["receivable_status"] for row in state["receivables"]}
    expect(set(receivable_statuses.values()) == {"received"}, f"Receivables should be received: {state['receivables']}")
    payable_statuses = {row["payable_no"]: row["payable_status"] for row in state["payables"]}
    expect(payable_statuses[f"{prefix}-AP-FABRIC"] == "paid", f"Fabric payable not paid: {state['payables']}")
    expect(payable_statuses[f"{prefix}-AP-SEW"] == "pending", f"Sewing payable should remain pending: {state['payables']}")
    finance_a = next(row for row in state["finance"] if row["order_no"] == f"{prefix}-SO-A")
    expect(float(finance_a["payable_amount"]) == 7430.0, f"Formal finance payable rollup wrong: {state['finance']}")
    expect(float(finance_a["cash_in_amount"]) == 4500.0, f"Formal finance cash-in rollup wrong: {state['finance']}")
    expect(float(finance_a["cash_out_amount"]) == 3710.0, f"Formal finance cash-out rollup wrong: {state['finance']}")
    finance_b = next(row for row in state["finance"] if row["order_no"] == f"{prefix}-SO-B")
    expect(float(finance_b["cash_in_amount"]) == 2500.0, f"Formal finance split receipt wrong: {state['finance']}")
    forecast_a = next(row for row in state["forecast"] if row["order_no"] == f"{prefix}-SO-A")
    forecast_b = next(row for row in state["forecast"] if row["order_no"] == f"{prefix}-SO-B")
    expect(float(forecast_a["expected_cash_in"]) == 10500.0, f"Formal cash-in forecast wrong: {state['forecast']}")
    expect(float(forecast_a["expected_cash_out"]) == 3720.0, f"Formal cash-out forecast wrong: {state['forecast']}")
    expect(float(forecast_b["expected_cash_in"]) == 8300.0, f"Formal split cash-in forecast wrong: {state['forecast']}")
    expect(state["refunds"][0]["refund_status"] == "paid", f"Formal refund not paid: {state['refunds']}")
    expect(state["supplier_deductions"][0]["deduction_status"] == "pending", f"Formal deduction wrong: {state['supplier_deductions']}")
    expect(len(state["followups"]) >= 5, f"Expected actionable followups: {state['followups']}")
    checks.ok("formal SQLite data state verified", json.dumps(state["row_counts_for_prefix"], ensure_ascii=False))

    return {
        "order_ids": [order_a_id, order_b_id, ecommerce_order_id],
        "receipt_cash_id": receipt_cash_id,
        "payout_cash_id": payout_id,
        "control_tower": control,
        "daily_report": report["report_json"],
        "state": state,
        "implemented_extension": "return_case/refund/supplier_deduction formal commit targets are verified.",
    }


def main() -> int:
    args = parse_args()
    data_root = Path(args.data_root).expanduser().resolve()
    wrapper = Path(args.wrapper).expanduser().resolve()
    expect(wrapper.exists(), f"Missing wrapper: {wrapper}")
    workspace = Path(tempfile.mkdtemp(prefix="order-formal-cli-payloads-"))
    prefix = "E2E-FORMAL-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    db_path = data_root / "db" / "order.db"
    backup_path = data_root / "db" / f"order.db.{prefix}.bak"
    test_files: list[str] = []
    cleanup: dict[str, Any] = {"db_restored": False, "files_removed": []}

    harness = FormalCliHarness(
        wrapper=wrapper,
        agent=args.agent,
        data_root=data_root,
        workspace=workspace,
        actor_label=prefix,
    )
    checks = CheckRecorder()
    harness.runtime("init-runtime")
    expect(db_path.exists(), f"Formal DB missing after init: {db_path}")

    with connect(db_path) as connection:
        before_counts = table_counts(connection)
        before_integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    expect(before_integrity == "ok", f"Formal DB integrity check failed before test: {before_integrity}")
    sqlite_backup(db_path, backup_path)
    before_hash = sha256_file(backup_path)

    result: dict[str, Any] | None = None
    error: str | None = None
    try:
        result = run_extended_business_flow(harness, prefix, checks)
        test_files = collect_test_files(db_path, prefix)
    except Exception as exc:  # noqa: BLE001 - cleanup must still run.
        error = repr(exc)
        try:
            test_files = collect_test_files(db_path, prefix)
        except Exception:
            test_files = []
        raise
    finally:
        if not args.keep_test_db and backup_path.exists():
            restore_backup(db_path, backup_path)
            cleanup["db_restored"] = True
            cleanup["restored_hash_matches_backup"] = sha256_file(db_path) == before_hash
            backup_path.unlink(missing_ok=True)
        if not args.keep_files:
            cleanup["files_removed"] = remove_files(test_files)

    with connect(db_path) as connection:
        after_counts = table_counts(connection)
        after_integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    expect(after_integrity == "ok", f"Formal DB integrity check failed after cleanup: {after_integrity}")
    if cleanup["db_restored"]:
        expect(after_counts == before_counts, "Formal DB table counts changed after backup restore.")
        expect(cleanup["restored_hash_matches_backup"], "Formal DB hash does not match restored backup.")

    output = {
        "status": "ok",
        "prefix": prefix,
        "data_root": str(data_root),
        "db_path": str(db_path),
        "workspace": str(workspace),
        "checks": checks.items,
        "before_counts": before_counts,
        "after_counts": after_counts,
        "test_result": result,
        "cleanup": cleanup,
        "error": error,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
