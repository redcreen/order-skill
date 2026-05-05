#!/usr/bin/env python3
"""Lazy-input guided-intake tests for the order runtime.

This test focuses on the real usage pattern where humans type short, incomplete
messages. It verifies that incomplete inputs stay in draft/checkpoint state,
the system can keep asking for missing information, and formal rows are only
created after explicit confirmation.
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
import sqlite3
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_SCRIPTS = REPO_ROOT / "order" / "scripts"
sys.path.insert(0, str(RUNTIME_SCRIPTS))

from runtime_common import initialize_runtime, open_guided_intake_draft, persist_input  # noqa: E402
from runtime_flow import commit_workflow_draft, prepare_draft_confirmation  # noqa: E402


AS_OF_DATE = "2099-12-31"
DUE_DATE = "2100-01-02"
EPSILON = 1e-6
REQUIRED_ORDER_FIELDS = [
    "order_no",
    "customer_name",
    "product_name",
    "spec",
    "qty",
    "unit_price",
    "promised_delivery_date",
    "factory_name",
    "process_flow_confirmed",
]
PROCESS_LABELS = {
    "sample": "打样",
    "material": "采购布料",
    "composite": "复合",
    "laser_cut": "激光下料",
    "position_cut": "整版刺绣定位切割",
    "embroidery": "刺绣",
    "accessory": "配件",
    "replenish_accessory": "补配件",
    "replenish_cut": "补裁片",
    "sewing": "平车",
    "sewing_full": "车缝充棉手工全流程",
    "cotton": "充棉",
    "handwork": "手工封口",
    "replacement_goods": "补货重做",
    "rework": "返工",
    "qc": "质检",
}


@dataclass(frozen=True)
class LazyCase:
    index: int
    customer_name: str
    product_name: str
    spec_text: str
    qty: int
    unit_price: float
    deposit_amount: float
    factory_name: str
    warehouse_name: str
    flow_name: str
    process_steps: tuple[str, ...]
    lazy_turns: tuple[str, str, str]
    first_turn_fields: dict[str, Any]
    second_turn_fields: dict[str, Any]
    final_turn_fields: dict[str, Any]

    @property
    def order_no(self) -> str:
        return f"LAZY-{self.index:03d}"

    @property
    def total_amount(self) -> float:
        return round(self.qty * self.unit_price, 2)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run lazy-input guided-intake tests against an isolated order DB.")
    parser.add_argument("--case-count", type=int, default=10)
    parser.add_argument("--seed", type=int, default=20260505)
    parser.add_argument("--data-root", help="Optional data root. Defaults to a temporary isolated runtime.")
    parser.add_argument("--keep-data-root", action="store_true")
    parser.add_argument("--output-file")
    return parser.parse_args()


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


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


def work_type_for_step(step: str) -> str:
    return PROCESS_LABELS[step]


def flow_templates() -> list[tuple[str, tuple[str, ...]]]:
    return [
        ("基础激光全流程", ("material", "laser_cut", "sewing_full", "qc")),
        ("复合后激光切割", ("material", "composite", "laser_cut", "sewing", "cotton", "handwork", "qc")),
        ("刺绣后切割", ("material", "embroidery", "laser_cut", "sewing", "cotton", "handwork", "qc")),
        ("整版刺绣定位切", ("material", "embroidery", "position_cut", "sewing_full", "qc")),
        ("补裁片二次进厂", ("material", "laser_cut", "replenish_cut", "sewing_full", "qc")),
        ("补配件后继续生产", ("material", "laser_cut", "accessory", "replenish_accessory", "sewing", "cotton", "handwork", "qc")),
        ("分批交货加补货", ("material", "laser_cut", "sewing_full", "qc", "replacement_goods")),
        ("返工回厂再发", ("material", "embroidery", "laser_cut", "sewing_full", "rework", "qc")),
        ("电商云仓入库", ("material", "laser_cut", "sewing", "cotton", "handwork", "qc")),
        ("普通平车分厂", ("material", "laser_cut", "sewing", "cotton", "handwork", "qc")),
    ]


def make_cases(count: int, seed: int) -> list[LazyCase]:
    rng = random.Random(seed)
    customers = ["王总", "李姐", "陈总", "赵姐", "孙总", "周姐", "郑总", "吴总", "冯姐", "刘总"]
    products = [
        ("小兔子", "18cm 粉色"),
        ("白熊", "20cm 白色"),
        ("趴趴狗", "22cm 咖色"),
        ("猫咪挂件", "12cm 米白"),
        ("龙年公仔", "25cm 红色"),
        ("熊猫钥匙扣", "10cm 黑白"),
        ("狐狸抱枕", "30cm 橘色"),
        ("恐龙公仔", "28cm 绿色"),
        ("鲸鱼趴趴枕", "35cm 蓝色"),
        ("企鹅玩偶", "16cm 灰白"),
    ]
    factories = ["冯杰", "邓总", "江西A厂", "义乌赵总", "张时库", "徐凯", "江西B厂", "罗山快反厂", "义乌临时厂", "定远乡工厂"]
    warehouses = ["义乌云仓", "客户仓", "线下批发仓", "义乌临时仓"]
    cases: list[LazyCase] = []
    for index in range(1, count + 1):
        customer = f"{customers[(index - 1) % len(customers)]}{index:02d}"
        product, spec = products[(index - 1) % len(products)]
        product_name = f"{product}{index:02d}"
        flow_name, steps = flow_templates()[(index - 1) % len(flow_templates())]
        qty = 80 + index * 7
        unit_price = round(8.8 + (index % 6) * 1.35, 2)
        total = round(qty * unit_price, 2)
        deposit = round(total * 0.3, 2)
        factory = factories[(index - 1) % len(factories)]
        warehouse = warehouses[(index - 1) % len(warehouses)]
        turn_variants = [
            (
                f"{customer}{product_name}{qty}个，按上次做，定金到了",
                f"{spec}，单价{unit_price}，交期{DUE_DATE}，{factory}先排",
                f"流程按系统带的{flow_name}确认，发{warehouse}",
            ),
            (
                f"{product_name}{qty}个客户要了，先别落错",
                f"客户{customer}，{spec}，{factory}做，单价{unit_price}",
                f"交期{DUE_DATE}，流程确认按老模板，仓库{warehouse}",
            ),
            (
                f"{factory}说{product_name}可以排，数量{qty}",
                f"客户是{customer}，规格{spec}，价格{unit_price}",
                f"按{flow_name}走，我确认，{DUE_DATE}前好",
            ),
        ]
        turns = rng.choice(turn_variants)
        cases.append(
            LazyCase(
                index=index,
                customer_name=customer,
                product_name=product_name,
                spec_text=spec,
                qty=qty,
                unit_price=unit_price,
                deposit_amount=deposit,
                factory_name=factory,
                warehouse_name=warehouse,
                flow_name=flow_name,
                process_steps=steps,
                lazy_turns=turns,
                first_turn_fields={"product_name": product_name, "qty": qty},
                second_turn_fields={
                    "customer_name": customer,
                    "spec": spec,
                    "unit_price": unit_price,
                    "factory_name": factory,
                    "promised_delivery_date": DUE_DATE,
                },
                final_turn_fields={
                    "order_no": f"LAZY-{index:03d}",
                    "process_flow_confirmed": "yes",
                    "flow_name": flow_name,
                    "process_steps": " > ".join(steps),
                    "warehouse_name": warehouse,
                    "deposit_amount": deposit,
                    "confirmed_total_amount": total,
                    "order_status": "in_production",
                    "current_step": flow_name,
                    "current_factory": factory,
                    "notes": "lazy guided intake confirmed by user",
                },
            )
        )
    return cases


class LazyHarness:
    def __init__(self, data_root: Path, actor_label: str) -> None:
        self.data_root = data_root
        self.actor_label = actor_label

    @property
    def db_path(self) -> Path:
        return self.data_root / "db" / "order.db"

    def persist(self, *, session_key: str, msg_id: str, text: str) -> dict[str, Any]:
        return persist_input(
            data_root=self.data_root,
            channel_type="lazy-guided-test",
            channel_session_key=session_key,
            source_actor=self.actor_label,
            source_message_id=msg_id,
            raw_text=text,
            raw_payload={"input_len": len(text), "lazy_input": True},
            attachments=None,
        )

    def open_draft(
        self,
        *,
        inbox_item_id: str,
        summary_text: str,
        fields: dict[str, Any],
        candidate_links: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return open_guided_intake_draft(
            data_root=self.data_root,
            inbox_item_id=inbox_item_id,
            intent_type="sales_order",
            target_object_type="sales_order",
            target_action="create",
            summary_text=summary_text,
            draft_fields=fields,
            thread=None,
            candidate_links=candidate_links,
            pending_targets=None,
            required_fields=REQUIRED_ORDER_FIELDS,
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


def table_counts(db_path: Path, actor_label: str) -> dict[str, int]:
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
            "sales_orders": one(connection, "SELECT COUNT(*) AS n FROM sales_orders WHERE order_no LIKE 'LAZY-%'")["n"],
            "work_orders": one(connection, "SELECT COUNT(*) AS n FROM work_orders WHERE work_order_no LIKE 'LAZY-%-WO-%'")["n"],
        }
    finally:
        connection.close()


def open_checkpoint_types(db_path: Path, workflow_draft_id: str) -> list[str]:
    connection = connect(db_path)
    try:
        return [
            str(row["checkpoint_type"])
            for row in connection.execute(
                """
                SELECT checkpoint_type
                FROM draft_checkpoints
                WHERE workflow_draft_id = ? AND checkpoint_status = 'open'
                ORDER BY checkpoint_type
                """,
                (workflow_draft_id,),
            )
        ]
    finally:
        connection.close()


def prepare_and_commit(harness: LazyHarness, workflow_draft_id: str) -> dict[str, Any]:
    prepared = harness.prepare(workflow_draft_id)
    expect(prepared["commit_ready"] is True, f"Expected commit-ready draft: {prepared}")
    return harness.commit(workflow_draft_id, str(prepared["confirmation"]["confirm_token"]))


def commit_work_orders(harness: LazyHarness, case: LazyCase, sales_order_id: int, session_key: str) -> list[int]:
    work_order_ids: list[int] = []
    for position, step in enumerate(case.process_steps, start=1):
        msg = f"{case.product_name}{work_type_for_step(step)}确认排期"
        persisted = harness.persist(session_key=session_key, msg_id=f"case-{case.index:03d}-wo-{position:02d}", text=msg)
        fields = {
            "work_order_no": f"{case.order_no}-WO-{position:02d}-{step.upper()}",
            "work_type": work_type_for_step(step),
            "factory_name": case.factory_name,
            "planned_qty": case.qty,
            "planned_due_at": DUE_DATE,
            "work_status": "planned",
            "notes": f"flow={case.flow_name}; step={step}",
        }
        draft = open_guided_intake_draft(
            data_root=harness.data_root,
            inbox_item_id=persisted["inbox_item_id"],
            intent_type="work_order_record",
            target_object_type="work_order",
            target_action="create",
            summary_text=msg,
            draft_fields=fields,
            thread=None,
            candidate_links=[{"target_type": "sales_order", "target_key": str(sales_order_id), "confidence_score": 1.0}],
            pending_targets=None,
            required_fields=None,
            actor_label=harness.actor_label,
        )
        committed = prepare_and_commit(harness, str(draft["workflow_draft_id"]))
        work_order_ids.append(int(committed["committed_object"]["object_id"]))
    return work_order_ids


def validate_case_rows(db_path: Path, case: LazyCase) -> dict[str, Any]:
    connection = connect(db_path)
    try:
        order = one(connection, "SELECT * FROM sales_orders WHERE order_no = ?", (case.order_no,))
        expect(order, f"Missing order {case.order_no}")
        expect(order["customer_name"] == case.customer_name, f"Wrong customer for {case.order_no}: {order}")
        expect(order["product_name"] == case.product_name, f"Wrong product for {case.order_no}: {order}")
        expect(abs(float(order["qty"]) - case.qty) < EPSILON, f"Wrong qty for {case.order_no}: {order}")
        rows = all_rows(
            connection,
            """
            SELECT work_order_no, work_type, planned_qty, planned_due_at
            FROM work_orders
            WHERE work_order_no LIKE ?
            ORDER BY work_order_no
            """,
            (f"{case.order_no}-WO-%",),
        )
        expected_types = [work_type_for_step(step) for step in case.process_steps]
        actual_types = [str(row["work_type"]) for row in rows]
        expect(actual_types == expected_types, f"Wrong work order sequence for {case.order_no}: {actual_types} != {expected_types}")
        for row in rows:
            expect(abs(float(row["planned_qty"]) - case.qty) < EPSILON, f"Wrong planned qty: {row}")
            expect(row["planned_due_at"] == DUE_DATE, f"Wrong due date: {row}")
        return {"order_no": case.order_no, "work_order_count": len(rows), "work_types": actual_types}
    finally:
        connection.close()


def run_case(harness: LazyHarness, case: LazyCase) -> dict[str, Any]:
    session_key = f"lazy-case-{case.index:03d}"
    collected: dict[str, Any] = {}

    first = harness.persist(session_key=session_key, msg_id=f"case-{case.index:03d}-turn-1", text=case.lazy_turns[0])
    collected.update(case.first_turn_fields)
    first_draft = harness.open_draft(
        inbox_item_id=first["inbox_item_id"],
        summary_text=f"{case.product_name} 懒人输入，系统先按产品模板提出流程并追问缺失信息。",
        fields=collected,
    )
    expect(first_draft["draft_status"] == "collecting", f"First draft should collect: {first_draft}")
    expect(first_draft["missing_required_fields"], f"First turn should have missing fields: {first_draft}")
    expect(table_counts(harness.db_path, harness.actor_label)["sales_orders"] == case.index - 1, "First turn must not commit order.")
    not_ready = harness.prepare(str(first_draft["workflow_draft_id"]))
    expect(not_ready["commit_ready"] is False, f"Incomplete draft should not be ready: {not_ready}")
    blocked = harness.commit(str(first_draft["workflow_draft_id"]), "confirm-fake", expect_ok=False)
    expect(blocked["status"] == "blocked", f"Incomplete commit should be blocked: {blocked}")

    second = harness.persist(session_key=session_key, msg_id=f"case-{case.index:03d}-turn-2", text=case.lazy_turns[1])
    collected.update(case.second_turn_fields)
    second_draft = harness.open_draft(
        inbox_item_id=second["inbox_item_id"],
        summary_text=f"{case.product_name} 已补客户、规格、价格、交期和工厂，仍需确认流程。",
        fields=collected,
    )
    expect(second_draft["workflow_draft_id"] == first_draft["workflow_draft_id"], "Second turn should update same draft.")
    expect(
        "process_flow_confirmed" in second_draft["missing_required_fields"],
        f"Second turn should still require process confirmation: {second_draft}",
    )

    final = harness.persist(session_key=session_key, msg_id=f"case-{case.index:03d}-turn-3", text=case.lazy_turns[2])
    collected.update(case.final_turn_fields)
    final_draft = harness.open_draft(
        inbox_item_id=final["inbox_item_id"],
        summary_text=f"{case.product_name} 用户确认流程：{case.flow_name}。",
        fields=collected,
    )
    expect(final_draft["workflow_draft_id"] == first_draft["workflow_draft_id"], "Final turn should update same draft.")
    expect(final_draft["draft_status"] == "needs_confirmation", f"Final draft should need confirmation: {final_draft}")
    expect(final_draft["missing_required_fields"] == [], f"Final draft should have no missing fields: {final_draft}")
    checkpoint_types = open_checkpoint_types(harness.db_path, str(final_draft["workflow_draft_id"]))
    expect(
        all(not item.startswith("missing_field:") for item in checkpoint_types),
        f"Missing-field checkpoints should be resolved: {checkpoint_types}",
    )
    committed_order = prepare_and_commit(harness, str(final_draft["workflow_draft_id"]))
    sales_order_id = int(committed_order["committed_object"]["object_id"])
    work_order_ids = commit_work_orders(harness, case, sales_order_id, session_key)
    row_state = validate_case_rows(harness.db_path, case)
    return {
        "case_index": case.index,
        "lazy_turn_lengths": [len(text) for text in case.lazy_turns],
        "first_missing_required_fields": first_draft["missing_required_fields"],
        "second_missing_required_fields": second_draft["missing_required_fields"],
        "final_draft_status": final_draft["draft_status"],
        "sales_order_id": sales_order_id,
        "work_order_ids": work_order_ids,
        "row_state": row_state,
    }


def validate_lazy_input_shape(cases: list[LazyCase]) -> dict[str, Any]:
    lengths = [len(turn) for case in cases for turn in case.lazy_turns]
    short = sum(1 for value in lengths if value <= 30)
    medium = sum(1 for value in lengths if 30 < value <= 80)
    long = sum(1 for value in lengths if value > 80)
    expect(max(lengths) <= 120, f"Lazy input should stay short, got max={max(lengths)}")
    expect(short + medium == len(lengths), f"Unexpected long lazy inputs: long={long}")
    return {
        "turn_count": len(lengths),
        "min_chars": min(lengths),
        "avg_chars": round(sum(lengths) / len(lengths), 2),
        "max_chars": max(lengths),
        "short_turns_lte_30": short,
        "medium_turns_31_to_80": medium,
        "long_turns_gt_80": long,
    }


def main() -> int:
    args = parse_args()
    if args.case_count <= 0:
        raise SystemExit("--case-count must be positive.")
    actor_label = "LAZY-GUIDED"
    data_root = Path(args.data_root).expanduser().resolve() if args.data_root else Path(tempfile.mkdtemp(prefix="order-lazy-guided-data-"))
    cleanup = {"data_root": str(data_root), "data_root_removed": False}
    output: dict[str, Any] | None = None
    try:
        initialize_runtime(data_root)
        harness = LazyHarness(data_root=data_root, actor_label=actor_label)
        cases = make_cases(args.case_count, args.seed)
        input_shape = validate_lazy_input_shape(cases)
        case_results = [run_case(harness, case) for case in cases]
        counts = table_counts(harness.db_path, actor_label)
        expected_work_orders = sum(len(case.process_steps) for case in cases)
        expect(counts["sales_orders"] == args.case_count, f"Sales order count wrong: {counts}")
        expect(counts["work_orders"] == expected_work_orders, f"Work order count wrong: {counts}")
        expect(counts["open_drafts"] == 0, f"Open drafts remain: {counts}")
        output = {
            "status": "ok",
            "case_count": args.case_count,
            "actor_label": actor_label,
            "input_shape": input_shape,
            "counts": counts,
            "expected": {
                "sales_orders": args.case_count,
                "work_orders": expected_work_orders,
                "turns_per_case": 3,
            },
            "coverage": {
                "draft_blocks_before_required_fields": args.case_count,
                "process_confirmation_required": args.case_count,
                "confirmed_then_committed": args.case_count,
                "work_order_sequence_checked": expected_work_orders,
            },
            "case_results": case_results,
            "cleanup": cleanup,
        }
        if not args.keep_data_root and not args.data_root and data_root.exists():
            shutil.rmtree(data_root)
            cleanup["data_root_removed"] = True
        if args.output_file:
            Path(args.output_file).parent.mkdir(parents=True, exist_ok=True)
            Path(args.output_file).write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    finally:
        if not args.keep_data_root and not args.data_root and data_root.exists():
            shutil.rmtree(data_root)
            cleanup["data_root_removed"] = True
            if output is not None and args.output_file:
                Path(args.output_file).write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
