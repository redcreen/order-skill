#!/usr/bin/env python3
"""50-case unordered stress test for the installed order runtime.

The test writes synthetic production/order/finance data into the formal SQLite
database, validates state stability, then restores a pre-test backup.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import shutil
import sqlite3
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
HOST_RUNTIME_SCRIPTS = REPO_ROOT / "order" / "scripts"
sys.path.insert(0, str(HOST_RUNTIME_SCRIPTS))

from runtime_common import initialize_runtime, open_guided_intake_draft, persist_input  # noqa: E402
from runtime_flow import (  # noqa: E402
    commit_workflow_draft,
    generate_daily_report,
    prepare_draft_confirmation,
    record_settlement_allocations,
    refresh_control_tower,
    resolve_pending_association_item,
)


FORMAL_DATA_ROOT = Path.home() / "Documents" / "openclaw-order"
AS_OF_DATE = "2099-12-31"
DUE_SOON_DATE = "2100-01-02"
EPSILON = 1e-6


@dataclass(frozen=True)
class CaseProfile:
    index: int
    scenario_name: str
    customer_name: str
    product_name: str
    spec_text: str
    flow_name: str
    process_steps: tuple[str, ...]
    sewing_factory_name: str
    cotton_factory_name: str
    handwork_factory_name: str
    cutting_factory_name: str
    embroidery_factory_name: str
    composite_factory_name: str
    material_supplier_name: str
    accessory_supplier_name: str
    warehouse_name: str
    primary_shipment_type: str
    special_event: str
    order_type: str
    issue_text: str
    qty: int
    lost_qty: int
    replenish_qty: int
    rework_qty: int
    unit_price: float
    total_amount: float
    deposit_amount: float
    refund_amount: float
    supplier_deduction_amount: float
    supplier_paid_amount: float


class StressHarness:
    def __init__(self, *, data_root: Path, actor_label: str, workspace: Path) -> None:
        self.data_root = data_root
        self.actor_label = actor_label
        self.workspace = workspace
        self.payload_index = 0

    @property
    def db_path(self) -> Path:
        return self.data_root / "db" / "order.db"

    def persist(
        self,
        *,
        case_key: str,
        event_key: str,
        text: str,
        raw_payload: dict[str, Any] | None = None,
        attachments: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        return persist_input(
            data_root=self.data_root,
            channel_type="chaos-stress",
            channel_session_key=f"{self.actor_label}:{case_key}",
            source_actor=self.actor_label,
            source_message_id=f"{case_key}:{event_key}",
            raw_text=text,
            raw_payload=raw_payload,
            attachments=attachments,
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
            required_fields=None,
            actor_label=self.actor_label,
        )

    def prepare_and_commit(self, workflow_draft_id: str) -> dict[str, Any]:
        prepared = prepare_draft_confirmation(
            data_root=self.data_root,
            workflow_draft_id=workflow_draft_id,
            actor_label=self.actor_label,
        )
        expect(prepared["commit_ready"] is True, f"Draft not ready: {prepared}")
        return commit_workflow_draft(
            data_root=self.data_root,
            workflow_draft_id=workflow_draft_id,
            confirm_token=str(prepared["confirmation"]["confirm_token"]),
            actor_label=self.actor_label,
        )

    def resolve_to_order(self, *, inbox_item_id: str, target_order_id: int, order_no: str, reason_text: str) -> None:
        pending_id = pending_association_id(self.db_path, inbox_item_id, "sales_order")
        result = resolve_pending_association_item(
            data_root=self.data_root,
            pending_association_id=pending_id,
            target_key=str(target_order_id),
            reason_text=reason_text,
            actor_label=self.actor_label,
            thread={"object_type": "sales_order", "object_key": str(target_order_id), "title": order_no},
        )
        expect(result["status"] == "resolved", f"Pending association not resolved: {result}")

    def allocate(
        self,
        *,
        cash_transaction_id: int,
        allocations: list[dict[str, Any]],
        require_full_amount: bool = True,
    ) -> dict[str, Any]:
        preview = record_settlement_allocations(
            data_root=self.data_root,
            cash_transaction_id=cash_transaction_id,
            allocations=allocations,
            actor_label=self.actor_label,
            replace_existing=True,
            require_full_amount=require_full_amount,
            dry_run=True,
        )
        return record_settlement_allocations(
            data_root=self.data_root,
            cash_transaction_id=cash_transaction_id,
            allocations=allocations,
            actor_label=self.actor_label,
            replace_existing=True,
            require_full_amount=require_full_amount,
            confirm_token=str(preview["confirmation"]["confirm_token"]),
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run 50 unordered large order scenarios against the formal DB.")
    parser.add_argument("--data-root", default=str(FORMAL_DATA_ROOT), help="Formal order data root.")
    parser.add_argument("--case-count", type=int, default=50, help="Number of generated large stress cases.")
    parser.add_argument("--seed", type=int, default=20260504, help="Deterministic random seed.")
    parser.add_argument("--keep-test-db", action="store_true", help="Do not restore DB backup after the run.")
    parser.add_argument("--keep-files", action="store_true", help="Do not remove copied raw/evidence files after the run.")
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


def table_names(connection: sqlite3.Connection) -> list[str]:
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
    return {name: int(connection.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]) for name in table_names(connection)}


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


def collect_test_files(db_path: Path, actor_label: str) -> list[str]:
    if not db_path.exists():
        return []
    connection = connect(db_path)
    try:
        raw_rows = connection.execute(
            "SELECT raw_archive_path FROM inbox_items WHERE source_actor = ?",
            (actor_label,),
        ).fetchall()
        evidence_rows = connection.execute(
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
    paths = [str(row["raw_archive_path"]) for row in raw_rows if row["raw_archive_path"]]
    paths.extend(str(row["local_path"]) for row in evidence_rows if row["local_path"])
    return paths


def remove_files(paths: list[str]) -> list[str]:
    removed: list[str] = []
    for raw_path in paths:
        path = Path(raw_path)
        if path.exists():
            path.unlink()
            removed.append(str(path))
    return removed


def pending_association_id(db_path: Path, inbox_item_id: str, target_type: str) -> str:
    connection = connect(db_path)
    try:
        row = connection.execute(
            """
            SELECT pending_association_id
            FROM pending_associations
            WHERE inbox_item_id = ?
              AND target_type = ?
              AND association_status = 'unresolved'
            ORDER BY rowid DESC
            LIMIT 1
            """,
            (inbox_item_id, target_type),
        ).fetchone()
    finally:
        connection.close()
    expect(row is not None, f"Missing pending association for {inbox_item_id}/{target_type}.")
    return str(row["pending_association_id"])


def make_profiles(prefix: str, count: int) -> list[CaseProfile]:
    sewing_factories = [
        "定远乡工厂/冯杰",
        "罗山县县城/邓总",
        "江西合作厂A",
        "江西合作厂B",
        "义乌赵总车缝",
        "河南新桥小单厂",
        "安徽临泉临时加工点",
        "商丘快反加工厂",
    ]
    cotton_factories = ["张时库充棉", "徐凯充棉", "江西合作厂A充棉", "定远乡工厂充棉", "潢川快反充棉"]
    handwork_factories = ["定远乡手工组", "徐凯手工组", "江西合作厂B手工组", "罗山手工组"]
    cutting_factories = ["刘旭激光下料", "杨总激光定位切割", "义乌北苑激光厂", "城西临时切割厂", "罗山激光补裁片点"]
    embroidery_factories = ["朱昌良刺绣", "廿三里刺绣A", "义乌电脑绣花B", "罗山小绣花厂"]
    composite_factories = ["弘辉复合", "诚信二区复合A", "义乌复合B"]
    material_suppliers = ["布料供应商A", "短毛绒供应商B", "宋锦面料供应商C", "摇粒绒供应商D"]
    accessory_suppliers = ["眼睛鼻子供应商A", "玻璃珠供应商B", "魔术贴供应商C", "毛球BB叫供应商D"]
    warehouses = ["义乌云仓", "义乌临时仓", "客户指定仓", "线下批发仓"]
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
        ("草莓熊", "18cm 红粉"),
        ("宋锦小马", "20cm 宋锦拼色"),
    ]
    flow_definitions = [
        ("基础激光全流程", ("sample", "material", "laser_cut", "sewing_full", "qc"), "customer_delivery", "normal_delivery"),
        ("复合后激光切割", ("sample", "material", "composite", "laser_cut", "sewing", "cotton", "handwork", "qc"), "customer_delivery", "rework"),
        ("刺绣后切割", ("sample", "material", "embroidery", "laser_cut", "sewing", "cotton", "handwork", "qc"), "direct_customer_delivery", "short_delivery"),
        ("整版刺绣定位切", ("material", "embroidery", "position_cut", "sewing_full", "qc"), "warehouse_receipt", "warehouse_arrival"),
        ("电商云仓入库", ("material", "laser_cut", "sewing", "cotton", "handwork", "qc"), "warehouse_receipt", "warehouse_lost"),
        ("补裁片二次进厂", ("material", "composite", "laser_cut", "replenish_cut", "sewing_full", "qc"), "cut_pieces_to_factory", "replenish_cut"),
        ("补配件后继续生产", ("material", "laser_cut", "accessory", "replenish_accessory", "sewing", "cotton", "handwork", "qc"), "factory_to_cotton", "replenish_accessory"),
        ("分批交货加补货", ("sample", "material", "laser_cut", "sewing_full", "qc", "replacement_goods"), "split_customer_delivery", "replacement_goods"),
        ("物流丢货重做", ("material", "composite", "laser_cut", "sewing", "cotton", "handwork", "qc", "replacement_goods"), "lost_goods_exception", "lost_goods"),
        ("返工回厂再发", ("material", "embroidery", "laser_cut", "sewing_full", "rework", "qc"), "rework_return_to_factory", "return_rework"),
    ]
    issues = ["退货返修", "少发补发", "客户改规格", "裁片不够补切", "配件质量问题", "物流丢货", "到仓短少", "补货补发"]
    profiles: list[CaseProfile] = []
    for index in range(1, count + 1):
        flow_name, process_steps, primary_shipment_type, special_event = flow_definitions[(index - 1) % len(flow_definitions)]
        product_base, spec_text = products[(index - 1) % len(products)]
        issue_text = issues[(index - 1) % len(issues)]
        qty = 120 + index * 4
        lost_qty = (index % 5 + 1) * 2 if special_event in {"lost_goods", "warehouse_lost", "short_delivery"} else 0
        replenish_qty = max(lost_qty, (index % 4 + 1) * 3) if special_event in {"replenish_cut", "replenish_accessory", "replacement_goods", "lost_goods", "short_delivery"} else 0
        rework_qty = (index % 6 + 1) * 4 if special_event in {"rework", "return_rework"} else 0
        unit_price = 9.5 + (index % 7) * 1.25
        total_amount = round(qty * unit_price, 2)
        deposit_amount = round(total_amount * 0.3, 2)
        refund_amount = round(35 + (index % 6) * 12.5, 2)
        supplier_deduction_amount = round(refund_amount * 0.5, 2)
        estimated_payable_total = round(
            140
            + index * 9
            + qty * 1.45
            + len(process_steps) * 55
            + replenish_qty * 2.3
            + rework_qty * 3.1
            + lost_qty * unit_price * 0.4,
            2,
        )
        if index % 3 == 0:
            supplier_paid_amount = estimated_payable_total
        elif index % 3 == 1:
            supplier_paid_amount = round(estimated_payable_total * 0.45, 2)
        else:
            supplier_paid_amount = 0.0
        profiles.append(
            CaseProfile(
                index=index,
                scenario_name=f"{flow_name}+{issue_text}+{special_event}+乱序补录+严格确认",
                customer_name=f"{prefix}-客户{index:02d}",
                product_name=f"{prefix}-{product_base}-{index:02d}",
                spec_text=spec_text,
                flow_name=flow_name,
                process_steps=process_steps,
                sewing_factory_name=f"{prefix}-{sewing_factories[(index - 1) % len(sewing_factories)]}",
                cotton_factory_name=f"{prefix}-{cotton_factories[(index + 1) % len(cotton_factories)]}",
                handwork_factory_name=f"{prefix}-{handwork_factories[(index + 2) % len(handwork_factories)]}",
                cutting_factory_name=f"{prefix}-{cutting_factories[(index - 1) % len(cutting_factories)]}",
                embroidery_factory_name=f"{prefix}-{embroidery_factories[(index + 1) % len(embroidery_factories)]}",
                composite_factory_name=f"{prefix}-{composite_factories[(index + 2) % len(composite_factories)]}",
                material_supplier_name=f"{prefix}-{material_suppliers[(index - 1) % len(material_suppliers)]}",
                accessory_supplier_name=f"{prefix}-{accessory_suppliers[(index + 1) % len(accessory_suppliers)]}",
                warehouse_name=f"{prefix}-{warehouses[(index - 1) % len(warehouses)]}",
                primary_shipment_type=primary_shipment_type,
                special_event=special_event,
                order_type="ecommerce_self_sale" if index % 5 == 0 else "customer_order",
                issue_text=issue_text,
                qty=qty,
                lost_qty=lost_qty,
                replenish_qty=replenish_qty,
                rework_qty=rework_qty,
                unit_price=unit_price,
                total_amount=total_amount,
                deposit_amount=deposit_amount,
                refund_amount=refund_amount,
                supplier_deduction_amount=supplier_deduction_amount,
                supplier_paid_amount=supplier_paid_amount,
            )
        )
    return profiles


def process_provider(profile: CaseProfile, step_code: str) -> str:
    if step_code in {"laser_cut", "position_cut", "replenish_cut"}:
        return profile.cutting_factory_name
    if step_code == "embroidery":
        return profile.embroidery_factory_name
    if step_code == "composite":
        return profile.composite_factory_name
    if step_code in {"sewing", "sewing_full", "replacement_goods", "rework"}:
        return profile.sewing_factory_name
    if step_code == "cotton":
        return profile.cotton_factory_name
    if step_code == "handwork":
        return profile.handwork_factory_name
    if step_code == "material":
        return profile.material_supplier_name
    if step_code in {"accessory", "replenish_accessory"}:
        return profile.accessory_supplier_name
    if step_code == "qc":
        return "内部质检"
    return "跟单内部"


def work_type_for_step(step_code: str) -> str:
    return {
        "sample": "打样",
        "material": "采购布料",
        "composite": "复合",
        "laser_cut": "激光下料",
        "position_cut": "整版刺绣定位切割",
        "embroidery": "刺绣",
        "accessory": "采购配件",
        "replenish_accessory": "补配件",
        "replenish_cut": "补裁片",
        "sewing": "平车",
        "sewing_full": "车缝充棉手工全流程",
        "cotton": "充棉",
        "handwork": "手工封口",
        "replacement_goods": "补货重做",
        "rework": "返工修理",
        "qc": "质检",
    }.get(step_code, step_code)


def payable_type_for_step(step_code: str) -> str:
    return {
        "sample": "sample",
        "material": "material",
        "composite": "composite",
        "laser_cut": "laser_cut",
        "position_cut": "position_cut",
        "embroidery": "embroidery",
        "accessory": "accessory",
        "replenish_accessory": "accessory_replenishment",
        "replenish_cut": "cut_piece_replenishment",
        "sewing": "processing",
        "sewing_full": "processing_full",
        "cotton": "cotton_fill",
        "handwork": "handwork",
        "replacement_goods": "replacement_goods",
        "rework": "repair",
        "qc": "quality_check",
    }.get(step_code, "other")


def payable_amount_for_step(profile: CaseProfile, step_code: str) -> float:
    qty = float(profile.qty)
    base = {
        "sample": 45,
        "material": 0.82 * qty,
        "composite": 0.28 * qty,
        "laser_cut": 0.36 * qty,
        "position_cut": 0.52 * qty,
        "embroidery": 0.48 * qty,
        "accessory": 0.22 * qty,
        "replenish_accessory": 2.4 * max(profile.replenish_qty, 1),
        "replenish_cut": 1.9 * max(profile.replenish_qty, 1),
        "sewing": 1.15 * qty,
        "sewing_full": 2.05 * qty,
        "cotton": 0.42 * qty,
        "handwork": 0.38 * qty,
        "replacement_goods": max(profile.replenish_qty, profile.lost_qty, 1) * 3.8,
        "rework": max(profile.rework_qty, 1) * 4.6,
        "qc": 0,
    }.get(step_code, 30)
    return round(base + profile.index * 0.7, 2) if base else 0.0


def work_step_specs(profile: CaseProfile) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    for position, step_code in enumerate(profile.process_steps, start=1):
        planned_qty = profile.qty
        if step_code in {"replenish_cut", "replenish_accessory", "replacement_goods"}:
            planned_qty = max(profile.replenish_qty, profile.lost_qty, 1)
        elif step_code == "rework":
            planned_qty = max(profile.rework_qty, 1)
        specs.append(
            {
                "suffix": f"WO-{position:02d}-{step_code.upper()}",
                "step_code": step_code,
                "work_type": work_type_for_step(step_code),
                "provider_name": process_provider(profile, step_code),
                "planned_qty": planned_qty,
                "planned_due_at": DUE_SOON_DATE,
            }
        )
    return specs


def payable_specs(profile: CaseProfile) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    for position, step_code in enumerate(profile.process_steps, start=1):
        amount = payable_amount_for_step(profile, step_code)
        if amount <= EPSILON:
            continue
        specs.append(
            {
                "suffix": f"AP-{position:02d}-{step_code.upper()}",
                "step_code": step_code,
                "supplier_name": process_provider(profile, step_code),
                "payable_type": payable_type_for_step(step_code),
                "amount_due": amount,
            }
        )
    return specs


def shipment_specs(profile: CaseProfile) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = [
        {
            "suffix": "SHIP-PRIMARY",
            "shipment_type": profile.primary_shipment_type,
            "factory_name": profile.sewing_factory_name,
            "finished_qty": None if profile.primary_shipment_type == "cut_pieces_to_factory" else profile.qty,
            "cut_qty": profile.qty if profile.primary_shipment_type == "cut_pieces_to_factory" else None,
            "shipment_status": "sent",
        }
    ]
    if any(step in profile.process_steps for step in ("laser_cut", "position_cut", "replenish_cut")):
        specs.append(
            {
                "suffix": "SHIP-CUT-PIECES",
                "shipment_type": "cut_pieces_to_factory",
                "factory_name": profile.cutting_factory_name,
                "finished_qty": None,
                "cut_qty": profile.qty + max(profile.replenish_qty, 0),
                "shipment_status": "sent",
            }
        )
    if "cotton" in profile.process_steps:
        specs.append(
            {
                "suffix": "SHIP-TO-COTTON",
                "shipment_type": "factory_to_cotton",
                "factory_name": profile.cotton_factory_name,
                "finished_qty": profile.qty,
                "cut_qty": None,
                "shipment_status": "sent",
            }
        )
    if profile.order_type == "ecommerce_self_sale" or profile.special_event in {"warehouse_arrival", "warehouse_lost"}:
        specs.append(
            {
                "suffix": "SHIP-WAREHOUSE",
                "shipment_type": "warehouse_receipt",
                "factory_name": profile.warehouse_name,
                "finished_qty": max(profile.qty - profile.lost_qty, 0),
                "cut_qty": None,
                "shipment_status": "arrived" if profile.special_event != "warehouse_lost" else "short_received",
            }
        )
    if profile.special_event in {"lost_goods", "warehouse_lost"}:
        specs.append(
            {
                "suffix": "SHIP-LOST",
                "shipment_type": "lost_goods_exception",
                "factory_name": profile.warehouse_name,
                "finished_qty": profile.lost_qty,
                "cut_qty": None,
                "shipment_status": "lost",
            }
        )
    if profile.special_event in {"replacement_goods", "lost_goods", "short_delivery"}:
        specs.append(
            {
                "suffix": "SHIP-REPLACEMENT",
                "shipment_type": "replacement_delivery",
                "factory_name": profile.sewing_factory_name,
                "finished_qty": max(profile.replenish_qty, profile.lost_qty, 1),
                "cut_qty": None,
                "shipment_status": "planned",
            }
        )
    if profile.special_event in {"rework", "return_rework"}:
        specs.append(
            {
                "suffix": "SHIP-REWORK",
                "shipment_type": "rework_return_to_factory",
                "factory_name": profile.sewing_factory_name,
                "finished_qty": profile.rework_qty,
                "cut_qty": None,
                "shipment_status": "sent",
            }
        )
    return specs


def primary_payable_spec(profile: CaseProfile) -> dict[str, Any]:
    specs = payable_specs(profile)
    for item in specs:
        if item["step_code"] in {"sewing", "sewing_full"}:
            return item
    return specs[0]


def total_payable_amount(profile: CaseProfile) -> float:
    return round(sum(float(item["amount_due"]) for item in payable_specs(profile)), 2)


def supplier_paid_amount(profile: CaseProfile) -> float:
    payable_total = total_payable_amount(profile)
    if profile.index % 3 == 0:
        return payable_total
    if profile.index % 3 == 1:
        return round(payable_total * 0.45, 2)
    return 0.0


def allocation_plan(total_to_pay: float, payable_ids: list[tuple[int, float]]) -> list[dict[str, Any]]:
    remaining = round(total_to_pay, 2)
    allocations: list[dict[str, Any]] = []
    for payable_id, amount_due in payable_ids:
        if remaining <= EPSILON:
            break
        allocated = min(round(amount_due, 2), remaining)
        allocations.append({"target_type": "payable", "target_id": payable_id, "allocated_amount": allocated})
        remaining = round(remaining - allocated, 2)
    expect(abs(remaining) < EPSILON, f"Supplier payment could not be fully allocated, remaining={remaining}")
    return allocations


TYPO_VARIANTS = {
    "定金": ("订金", "定斤"),
    "预付款": ("预付", "先打的款"),
    "裁片": ("栽片", "才片", "裁pian"),
    "补裁": ("补才", "补栽片"),
    "返工": ("反工", "返修回去"),
    "发货": ("发或", "发出去了"),
    "入库": ("入仓", "入云苍"),
    "云仓": ("云苍", "义乌仓"),
    "充棉": ("冲棉", "充绵"),
    "刺绣": ("绣花", "电脑绣"),
    "激光": ("激广", "激光下料"),
    "复合": ("贴合", "复个合"),
    "物流": ("货拉拉/物流", "物留"),
    "订单号": ("单号", "订单号没写"),
}


def roughen_text(text: str, rng: random.Random) -> str:
    for source, variants in TYPO_VARIANTS.items():
        if source in text and rng.random() < 0.38:
            text = text.replace(source, rng.choice(variants), 1)
    if rng.random() < 0.45:
        text = text.replace("，", " ")
    if rng.random() < 0.35:
        text = text.replace("。", "")
    prefix = rng.choice(["", "先记一下：", "刚想起来，", "这个别漏，", "我不确定是不是这个单，"])
    suffix = rng.choice(["", "，回头截图补", "，先挂着别乱入", "，等我确认", "，大概是这样"])
    if rng.random() < 0.25:
        suffix += rng.choice(["；上次那个也像", "；客户催得急", "；别和上一批混了"])
    return f"{prefix}{text}{suffix}"


def messy_event_text(profile: CaseProfile, key: str, rng: random.Random) -> str:
    main_payable = primary_payable_spec(profile)
    flow_text = " / ".join(work_type_for_step(step) for step in profile.process_steps)
    templates = {
        "payment": [
            f"截图里 {profile.customer_name} 打了 {profile.deposit_amount}，备注就写{profile.product_name}定金，没写订单号",
            f"{profile.customer_name} 这笔 {profile.deposit_amount} 应该是 {profile.product_name} 的预付款，截图糊的，订单号看不到",
            f"客户款到了吧，{profile.deposit_amount}，像 {profile.product_name} 那个 30%，先放待确认",
        ],
        "main_factory_statement": [
            f"{main_payable['supplier_name']} 发账单 {main_payable['amount_due']}，只写了{profile.product_name}一批，没配订单号",
            f"主加工那边说这批加工费先记 {main_payable['amount_due']}，厂家 {main_payable['supplier_name']}，产品名写得很乱",
            f"{main_payable['supplier_name']} 账单来了，金额 {main_payable['amount_due']}，不知道是不是 {profile.customer_name} 这个单",
        ],
        "cutting_statement": [
            f"{profile.cutting_factory_name} 说 {profile.product_name} 的裁片做了，流程 {profile.flow_name}，可能还有补裁",
            f"刘旭/切割那边类似的单，{profile.product_name}，定位切还是普通切没写清，先记 {profile.cutting_factory_name}",
            f"切割厂回了点信息：{profile.spec_text} 的裁片数量可能多切，别直接当成发货",
        ],
        "process_plan": [
            f"这批流程先按 {flow_text} 走，不是每一步都有，后面可能插补配件",
            f"{profile.product_name} 加工路线大概：{flow_text}，有人补录的，不保证顺序",
            f"这个款不是标准流程，先打样/材料后面按 {flow_text}，中间可能返工",
        ],
        "shipment": [
            f"{profile.sewing_factory_name} 说货发了，类型 {profile.primary_shipment_type}，数量差不多 {profile.qty}，物流单还没全",
            f"工厂发出来一批，可能是 {profile.product_name}，{profile.qty} 左右，走 {profile.primary_shipment_type}",
            f"物流点回了，{profile.primary_shipment_type}，但只看到数量 {profile.qty}，订单号缺",
        ],
        "exception_logistics": [
            f"异常 {profile.special_event}，丢 {profile.lost_qty}，要补 {profile.replenish_qty}，返工 {profile.rework_qty}",
            f"这批有点乱：少/丢 {profile.lost_qty}，补货补裁 {profile.replenish_qty}，返工数 {profile.rework_qty}",
            f"客户说数量不对，事件像 {profile.special_event}，具体丢货补货返修都先挂着",
        ],
        "warehouse": [
            f"{profile.warehouse_name} 说到仓/入库有反馈，{profile.primary_shipment_type}，可能短少",
            f"义乌仓那边回了，像 {profile.product_name} 到了点，入库数还没核",
            f"仓库只说到了，没给完整入库单，仓是 {profile.warehouse_name}",
        ],
        "return": [
            f"{profile.customer_name} 反馈 {profile.issue_text}，可能退 {profile.refund_amount}，供应商那边扣 {profile.supplier_deduction_amount}",
            f"售后来了，{profile.issue_text}，客户要处理退款，金额大概 {profile.refund_amount}",
            f"这单可能要返修/退，原因 {profile.issue_text}，供应商扣款也要记",
        ],
        "order": [
            f"{profile.customer_name} 确认 {profile.product_name} {profile.spec_text} {profile.qty} 个，单价 {profile.unit_price}，30%定金，{profile.sewing_factory_name} 先排",
            f"客户说 {profile.product_name} 做 {profile.qty}，{profile.spec_text}，单 {profile.unit_price}，先收三成，工厂先找 {profile.sewing_factory_name}",
            f"正式单大概定了：{profile.customer_name} / {profile.product_name} / {profile.qty}个 / 单价{profile.unit_price}，别漏交期",
        ],
        "work": [
            f"{profile.sewing_factory_name}、{profile.cotton_factory_name}、{profile.handwork_factory_name} 按这批流程排期，交期 {DUE_SOON_DATE}",
            f"生产安排先给 {profile.sewing_factory_name}，后面可能转 {profile.cotton_factory_name} 和 {profile.handwork_factory_name}",
            f"跟单提醒：这批不是一个厂全做，车缝/充棉/手工分开排，最晚 {DUE_SOON_DATE}",
        ],
    }
    return roughen_text(rng.choice(templates[key]), rng)


def open_pre_order_drafts(
    harness: StressHarness,
    *,
    profile: CaseProfile,
    case_key: str,
    persisted: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    main_payable = primary_payable_spec(profile)
    primary_shipment = shipment_specs(profile)[0]
    payment_draft = harness.open_draft(
        inbox_item_id=persisted["payment"]["inbox_item_id"],
        intent_type="payment_receipt",
        target_object_type="cash_transaction",
        summary_text=f"{profile.customer_name} 付款截图只有客户和产品简称，待关联订单。",
        fields={
            "direction": "收款",
            "counterparty_name": profile.customer_name,
            "amount": profile.deposit_amount,
            "transaction_date": AS_OF_DATE,
            "purpose": "30%预付款，订单号缺失",
            "payment_method": "bank_transfer",
            "notes": case_key,
        },
    )
    payable_draft = harness.open_draft(
        inbox_item_id=persisted["main_factory_statement"]["inbox_item_id"],
        intent_type="payable_record",
        target_object_type="payable",
        summary_text=f"{main_payable['supplier_name']} {main_payable['payable_type']} 应付单先到，订单号未写清。",
        fields={
            "payable_no": f"{case_key}-{main_payable['suffix']}",
            "supplier_name": main_payable["supplier_name"],
            "payable_type": main_payable["payable_type"],
            "amount_due": main_payable["amount_due"],
            "due_date": DUE_SOON_DATE,
            "billing_mode": "per_order",
            "notes": case_key,
        },
    )
    shipment_draft = harness.open_draft(
        inbox_item_id=persisted["shipment"]["inbox_item_id"],
        intent_type="shipment",
        target_object_type="shipment",
        summary_text=f"{primary_shipment['shipment_type']} 物流信息缺订单号，待关联。",
        fields={
            "shipment_date": AS_OF_DATE,
            "shipment_type": primary_shipment["shipment_type"],
            "factory_name": primary_shipment["factory_name"],
            "finished_qty": primary_shipment["finished_qty"],
            "cut_qty": primary_shipment["cut_qty"],
            "shipment_status": primary_shipment["shipment_status"],
            "notes": case_key,
        },
    )
    return_draft = harness.open_draft(
        inbox_item_id=persisted["return"]["inbox_item_id"],
        intent_type="return_case",
        target_object_type="return_case",
        summary_text=f"{profile.issue_text} 引发退货/返修/退款/扣款，需要关联订单。",
        fields={
            "case_type": "repair_return",
            "opened_at": AS_OF_DATE,
            "customer_name": profile.customer_name,
            "reason_text": profile.issue_text,
            "refund_expected_amount": profile.refund_amount,
            "supplier_deduction_expected_amount": profile.supplier_deduction_amount,
            "notes": case_key,
        },
    )
    for draft in [payment_draft, payable_draft, shipment_draft, return_draft]:
        expect(draft["draft_status"] == "collecting", f"Pre-order draft should wait for association: {draft}")
    return {
        "payment": payment_draft,
        "payable": payable_draft,
        "shipment": shipment_draft,
        "return": return_draft,
    }


def run_case(harness: StressHarness, profile: CaseProfile, rng: random.Random) -> dict[str, Any]:
    case_key = f"{harness.actor_label}-CASE-{profile.index:02d}"
    proof_file = harness.workspace / f"{case_key}-payment-proof.txt"
    proof_file.write_text(
        f"OCR截图不完整: {profile.customer_name} {profile.product_name} 订金/预付 {profile.deposit_amount} 备注很糊",
        encoding="utf-8",
    )
    raw_events = [
        {
            "key": "payment",
            "text": messy_event_text(profile, "payment", rng),
            "raw_payload": {"kind": "payment_screenshot", "amount_hint": profile.deposit_amount},
            "attachments": [{"path": str(proof_file), "mime_type": "text/plain", "extracted_text": proof_file.read_text(encoding="utf-8")}],
        },
        {
            "key": "main_factory_statement",
            "text": messy_event_text(profile, "main_factory_statement", rng),
            "raw_payload": {"kind": "main_factory_statement", "amount_hint": primary_payable_spec(profile)["amount_due"]},
        },
        {
            "key": "cutting_statement",
            "text": messy_event_text(profile, "cutting_statement", rng),
            "raw_payload": {"kind": "cutting_or_piece_replenishment", "provider": profile.cutting_factory_name},
        },
        {
            "key": "process_plan",
            "text": messy_event_text(profile, "process_plan", rng),
            "raw_payload": {"kind": "process_plan", "steps": list(profile.process_steps), "flow_name": profile.flow_name},
        },
        {
            "key": "shipment",
            "text": messy_event_text(profile, "shipment", rng),
            "raw_payload": {"kind": "logistics_text", "qty_hint": profile.qty},
        },
        {
            "key": "exception_logistics",
            "text": messy_event_text(profile, "exception_logistics", rng),
            "raw_payload": {
                "kind": "logistics_or_production_exception",
                "special_event": profile.special_event,
                "lost_qty": profile.lost_qty,
                "replenish_qty": profile.replenish_qty,
                "rework_qty": profile.rework_qty,
            },
        },
        {
            "key": "warehouse",
            "text": messy_event_text(profile, "warehouse", rng),
            "raw_payload": {"kind": "warehouse_status", "warehouse": profile.warehouse_name},
        },
        {
            "key": "return",
            "text": messy_event_text(profile, "return", rng),
            "raw_payload": {"kind": "after_sales_issue", "refund_hint": profile.refund_amount},
        },
        {
            "key": "order",
            "text": messy_event_text(profile, "order", rng),
            "raw_payload": {"kind": "sales_order_confirmation"},
        },
        {
            "key": "work",
            "text": messy_event_text(profile, "work", rng),
            "raw_payload": {"kind": "production_arrangement"},
        },
    ]
    shuffled = list(raw_events)
    rng.shuffle(shuffled)
    persisted = {
        event["key"]: harness.persist(
            case_key=case_key,
            event_key=str(event["key"]),
            text=str(event["text"]),
            raw_payload=event.get("raw_payload"),  # type: ignore[arg-type]
            attachments=event.get("attachments"),  # type: ignore[arg-type]
        )
        for event in shuffled
    }
    pre_order_drafts = open_pre_order_drafts(harness, profile=profile, case_key=case_key, persisted=persisted)

    order_draft = harness.open_draft(
        inbox_item_id=persisted["order"]["inbox_item_id"],
        intent_type="sales_order",
        target_object_type="sales_order",
        summary_text=f"{case_key} 正式订单确认。",
        fields={
            "order_no": f"{case_key}-SO",
            "order_date": AS_OF_DATE,
            "order_type": profile.order_type,
            "customer_name": profile.customer_name,
            "product_name": profile.product_name,
            "spec_text": profile.spec_text,
            "qty": profile.qty,
            "unit": "个",
            "confirmed_unit_price": profile.unit_price,
            "confirmed_total_amount": profile.total_amount,
            "deposit_ratio": 0.3,
            "deposit_expected_amount": profile.deposit_amount,
            "promised_delivery_date": DUE_SOON_DATE,
            "current_factory": profile.sewing_factory_name,
            "current_step": profile.flow_name,
            "progress_text": f"{profile.scenario_name}，原始输入乱序补录。",
            "order_status": "in_production",
            "notes": case_key,
        },
    )
    order_commit = harness.prepare_and_commit(str(order_draft["workflow_draft_id"]))
    sales_order_id = int(order_commit["committed_object"]["object_id"])
    order_no = f"{case_key}-SO"

    for event_key in ["payment", "main_factory_statement", "shipment", "return"]:
        harness.resolve_to_order(
            inbox_item_id=persisted[event_key]["inbox_item_id"],
            target_order_id=sales_order_id,
            order_no=order_no,
            reason_text=f"{case_key} 补确认该乱序输入归属 {order_no}。",
        )

    payment_commit = harness.prepare_and_commit(str(pre_order_drafts["payment"]["workflow_draft_id"]))
    cash_receipt_id = int(payment_commit["committed_object"]["object_id"])
    payable_commit = harness.prepare_and_commit(str(pre_order_drafts["payable"]["workflow_draft_id"]))
    primary_payable_id = int(payable_commit["committed_object"]["object_id"])
    primary_payable = primary_payable_spec(profile)
    payable_ids: list[tuple[int, float]] = [(primary_payable_id, float(primary_payable["amount_due"]))]
    for item in payable_specs(profile):
        if item["suffix"] == primary_payable["suffix"]:
            continue
        payable_draft = harness.open_draft(
            inbox_item_id=persisted["cutting_statement" if item["step_code"] in {"laser_cut", "position_cut", "replenish_cut"} else "process_plan"]["inbox_item_id"],
            intent_type="payable_record",
            target_object_type="payable",
            summary_text=f"{case_key} {item['supplier_name']} {item['payable_type']} 应付。",
            fields={
                "payable_no": f"{case_key}-{item['suffix']}",
                "supplier_name": item["supplier_name"],
                "payable_type": item["payable_type"],
                "amount_due": item["amount_due"],
                "due_date": DUE_SOON_DATE,
                "billing_mode": "per_order",
                "notes": case_key,
            },
            candidate_links=[{"target_type": "sales_order", "target_key": str(sales_order_id), "confidence_score": 1.0}],
        )
        committed_payable = harness.prepare_and_commit(str(payable_draft["workflow_draft_id"]))
        payable_ids.append((int(committed_payable["committed_object"]["object_id"]), float(item["amount_due"])))
    shipment_commit = harness.prepare_and_commit(str(pre_order_drafts["shipment"]["workflow_draft_id"]))
    shipment_id = int(shipment_commit["committed_object"]["object_id"])
    shipment_ids = [shipment_id]
    for item in shipment_specs(profile)[1:]:
        shipment_draft = harness.open_draft(
            inbox_item_id=persisted["exception_logistics" if item["shipment_type"] in {"lost_goods_exception", "replacement_delivery", "rework_return_to_factory"} else "warehouse"]["inbox_item_id"],
            intent_type="shipment",
            target_object_type="shipment",
            summary_text=f"{case_key} {item['shipment_type']} 物流/到仓/补货记录。",
            fields={
                "shipment_date": AS_OF_DATE,
                "shipment_type": item["shipment_type"],
                "factory_name": item["factory_name"],
                "finished_qty": item["finished_qty"],
                "cut_qty": item["cut_qty"],
                "shipment_status": item["shipment_status"],
                "notes": case_key,
            },
            candidate_links=[{"target_type": "sales_order", "target_key": str(sales_order_id), "confidence_score": 1.0}],
        )
        committed_shipment = harness.prepare_and_commit(str(shipment_draft["workflow_draft_id"]))
        shipment_ids.append(int(committed_shipment["committed_object"]["object_id"]))
    return_commit = harness.prepare_and_commit(str(pre_order_drafts["return"]["workflow_draft_id"]))
    return_case_id = int(return_commit["committed_object"]["object_id"])

    deposit_draft = harness.open_draft(
        inbox_item_id=persisted["payment"]["inbox_item_id"],
        intent_type="receivable_record",
        target_object_type="receivable",
        summary_text=f"{case_key} 30%定金应收。",
        fields={
            "receivable_no": f"{case_key}-AR-DEP",
            "receivable_type": "deposit",
            "amount_due": profile.deposit_amount,
            "due_date": AS_OF_DATE,
            "collection_mode": "bank_transfer",
            "notes": case_key,
        },
        candidate_links=[{"target_type": "sales_order", "target_key": str(sales_order_id), "confidence_score": 1.0}],
    )
    deposit_commit = harness.prepare_and_commit(str(deposit_draft["workflow_draft_id"]))
    deposit_receivable_id = int(deposit_commit["committed_object"]["object_id"])
    receipt_allocation = harness.allocate(
        cash_transaction_id=cash_receipt_id,
        allocations=[
            {"target_type": "receivable", "target_id": deposit_receivable_id, "allocated_amount": profile.deposit_amount}
        ],
    )
    expect(receipt_allocation["targets"][0]["status"] == "received", f"Deposit allocation failed: {receipt_allocation}")

    tail_amount = round(profile.total_amount - profile.deposit_amount, 2)
    tail_draft = harness.open_draft(
        inbox_item_id=persisted["order"]["inbox_item_id"],
        intent_type="receivable_record",
        target_object_type="receivable",
        summary_text=f"{case_key} 尾款应收。",
        fields={
            "receivable_no": f"{case_key}-AR-TAIL",
            "receivable_type": "tail",
            "amount_due": tail_amount,
            "due_date": DUE_SOON_DATE,
            "collection_mode": "bank_transfer",
            "notes": case_key,
        },
        candidate_links=[{"target_type": "sales_order", "target_key": str(sales_order_id), "confidence_score": 1.0}],
    )
    tail_receivable_id = int(harness.prepare_and_commit(str(tail_draft["workflow_draft_id"]))["committed_object"]["object_id"])

    work_order_ids: list[int] = []
    for item in work_step_specs(profile):
        work_draft = harness.open_draft(
            inbox_item_id=persisted["work" if item["step_code"] not in {"replenish_cut", "replenish_accessory", "replacement_goods", "rework"} else "exception_logistics"]["inbox_item_id"],
            intent_type="work_order_record",
            target_object_type="work_order",
            summary_text=f"{case_key} {item['work_type']} 作业排期。",
            fields={
                "work_order_no": f"{case_key}-{item['suffix']}",
                "work_type": item["work_type"],
                "factory_name": item["provider_name"],
                "planned_qty": item["planned_qty"],
                "planned_due_at": item["planned_due_at"],
                "work_status": "planned",
                "notes": f"{case_key} / flow={profile.flow_name} / event={profile.special_event}",
            },
            candidate_links=[{"target_type": "sales_order", "target_key": str(sales_order_id), "confidence_score": 1.0}],
        )
        work_order_ids.append(int(harness.prepare_and_commit(str(work_draft["workflow_draft_id"]))["committed_object"]["object_id"]))
    work_order_id = work_order_ids[0]

    refund_draft = harness.open_draft(
        inbox_item_id=persisted["return"]["inbox_item_id"],
        intent_type="refund_record",
        target_object_type="refund",
        summary_text=f"{case_key} 客户退款。",
        fields={"refund_amount": profile.refund_amount, "refund_status": "pending", "notes": case_key},
        candidate_links=[
            {"target_type": "sales_order", "target_key": str(sales_order_id), "confidence_score": 1.0},
            {"target_type": "return_case", "target_key": str(return_case_id), "confidence_score": 1.0},
        ],
    )
    refund_id = int(harness.prepare_and_commit(str(refund_draft["workflow_draft_id"]))["committed_object"]["object_id"])
    refund_cash_draft = harness.open_draft(
        inbox_item_id=persisted["return"]["inbox_item_id"],
        intent_type="cash_transaction_record",
        target_object_type="cash_transaction",
        summary_text=f"{case_key} 退款付款。",
        fields={
            "direction": "付款",
            "counterparty_name": profile.customer_name,
            "amount": profile.refund_amount,
            "transaction_date": AS_OF_DATE,
            "purpose": "客户退款",
            "payment_method": "bank_transfer",
            "notes": case_key,
        },
        candidate_links=[{"target_type": "sales_order", "target_key": str(sales_order_id), "confidence_score": 1.0}],
    )
    refund_cash_id = int(harness.prepare_and_commit(str(refund_cash_draft["workflow_draft_id"]))["committed_object"]["object_id"])
    refund_allocation = harness.allocate(
        cash_transaction_id=refund_cash_id,
        allocations=[{"target_type": "refund", "target_id": refund_id, "allocated_amount": profile.refund_amount}],
    )
    expect(refund_allocation["targets"][0]["status"] == "paid", f"Refund allocation failed: {refund_allocation}")

    deduction_draft = harness.open_draft(
        inbox_item_id=persisted["return"]["inbox_item_id"],
        intent_type="supplier_deduction_record",
        target_object_type="supplier_deduction",
        summary_text=f"{case_key} 供应商扣款。",
        fields={
            "supplier_name": profile.sewing_factory_name,
            "deduction_amount": profile.supplier_deduction_amount,
            "deduction_reason": profile.issue_text,
            "deduction_status": "pending",
        },
        candidate_links=[
            {"target_type": "return_case", "target_key": str(return_case_id), "confidence_score": 1.0},
            {"target_type": "work_order", "target_key": str(work_order_id), "confidence_score": 1.0},
        ],
    )
    deduction_id = int(harness.prepare_and_commit(str(deduction_draft["workflow_draft_id"]))["committed_object"]["object_id"])

    supplier_cash_id = None
    supplier_payment_amount = supplier_paid_amount(profile)
    if supplier_payment_amount > EPSILON:
        supplier_cash_draft = harness.open_draft(
            inbox_item_id=persisted["main_factory_statement"]["inbox_item_id"],
            intent_type="cash_transaction_record",
            target_object_type="cash_transaction",
            summary_text=f"{case_key} 供应商付款。",
            fields={
                "direction": "付款",
                "counterparty_name": "多供应商合并付款",
                "amount": supplier_payment_amount,
                "transaction_date": AS_OF_DATE,
                "purpose": "供应商账单付款",
                "payment_method": "bank_transfer",
                "notes": case_key,
            },
            candidate_links=[{"target_type": "sales_order", "target_key": str(sales_order_id), "confidence_score": 1.0}],
        )
        supplier_cash_id = int(harness.prepare_and_commit(str(supplier_cash_draft["workflow_draft_id"]))["committed_object"]["object_id"])
        payable_allocation = harness.allocate(
            cash_transaction_id=supplier_cash_id,
            allocations=allocation_plan(supplier_payment_amount, payable_ids),
        )
        expect(abs(payable_allocation["allocation_total"] - supplier_payment_amount) < EPSILON, f"Payable allocation amount wrong: {payable_allocation}")

    return {
        "case_key": case_key,
        "scenario": profile.scenario_name,
        "raw_event_order": [event["key"] for event in shuffled],
        "sales_order_id": sales_order_id,
        "cash_receipt_id": cash_receipt_id,
        "payable_ids": [item[0] for item in payable_ids],
        "shipment_ids": shipment_ids,
        "return_case_id": return_case_id,
        "refund_id": refund_id,
        "deduction_id": deduction_id,
        "tail_receivable_id": tail_receivable_id,
        "work_order_ids": work_order_ids,
        "supplier_cash_id": supplier_cash_id,
        "expected": {
            "flow_name": profile.flow_name,
            "process_steps": list(profile.process_steps),
            "special_event": profile.special_event,
            "total_amount": profile.total_amount,
            "deposit_amount": profile.deposit_amount,
            "tail_amount": tail_amount,
            "payable_amount": total_payable_amount(profile),
            "refund_amount": profile.refund_amount,
            "supplier_paid_amount": supplier_payment_amount,
            "work_order_count": len(work_order_ids),
            "shipment_count": len(shipment_ids),
            "payable_count": len(payable_ids),
        },
    }


def validate_stress_state(db_path: Path, *, actor_label: str, profiles: list[CaseProfile], case_results: list[dict[str, Any]]) -> dict[str, Any]:
    connection = connect(db_path)
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        expect(integrity == "ok", f"Integrity check failed during stress validation: {integrity}")
        prefix_like = f"{actor_label}-CASE-%"
        counts = {
            "sales_orders": one(connection, "SELECT COUNT(*) AS n FROM sales_orders WHERE order_no LIKE ?", (f"{prefix_like}-SO",))["n"],
            "receivables": one(connection, "SELECT COUNT(*) AS n FROM receivables WHERE receivable_no LIKE ?", (f"{prefix_like}-AR-%",))["n"],
            "payables": one(connection, "SELECT COUNT(*) AS n FROM payables WHERE payable_no LIKE ?", (f"{prefix_like}-AP-%",))["n"],
            "cash_transactions": one(connection, "SELECT COUNT(*) AS n FROM cash_transactions WHERE notes LIKE ?", (f"{prefix_like}",))["n"],
            "work_orders": one(connection, "SELECT COUNT(*) AS n FROM work_orders WHERE work_order_no LIKE ?", (f"{prefix_like}-WO-%",))["n"],
            "shipments": one(connection, "SELECT COUNT(*) AS n FROM shipments WHERE notes LIKE ?", (prefix_like,))["n"],
            "return_cases": one(connection, "SELECT COUNT(*) AS n FROM return_cases WHERE notes LIKE ?", (prefix_like,))["n"],
            "refunds": one(connection, "SELECT COUNT(*) AS n FROM refunds WHERE notes LIKE ?", (prefix_like,))["n"],
            "supplier_deductions": one(
                connection,
                """
                SELECT COUNT(*) AS n
                FROM supplier_deductions d
                JOIN return_cases rc ON rc.return_case_id = d.return_case_id
                WHERE rc.notes LIKE ?
                """,
                (prefix_like,),
            )["n"],
            "new_products": one(connection, "SELECT COUNT(*) AS n FROM products WHERE product_name LIKE ?", (f"{actor_label}-%",))["n"],
            "new_parties": one(connection, "SELECT COUNT(*) AS n FROM parties WHERE party_name LIKE ?", (f"{actor_label}-%",))["n"],
            "new_cutting_factories": one(
                connection,
                """
                SELECT COUNT(DISTINCT party_name) AS n
                FROM parties
                WHERE party_name LIKE ?
                  AND (party_name LIKE '%激光%' OR party_name LIKE '%切割%' OR party_name LIKE '%补裁片%')
                """,
                (f"{actor_label}-%",),
            )["n"],
            "evidence_assets": one(
                connection,
                """
                SELECT COUNT(*) AS n
                FROM evidence_assets e
                JOIN inbox_items i ON i.inbox_item_id = e.inbox_item_id
                WHERE i.source_actor = ?
                """,
                (actor_label,),
            )["n"],
            "unresolved_pending": one(
                connection,
                """
                SELECT COUNT(*) AS n
                FROM pending_associations pa
                JOIN inbox_items i ON i.inbox_item_id = pa.inbox_item_id
                WHERE i.source_actor = ?
                  AND pa.association_status != 'confirmed'
                """,
                (actor_label,),
            )["n"],
            "open_test_drafts": one(
                connection,
                """
                SELECT COUNT(DISTINCT d.workflow_draft_id) AS n
                FROM workflow_drafts d
                JOIN draft_source_links link ON link.workflow_draft_id = d.workflow_draft_id
                JOIN inbox_items i ON i.inbox_item_id = link.inbox_item_id
                WHERE i.source_actor = ?
                  AND d.draft_status != 'committed'
                """,
                (actor_label,),
            )["n"],
        }
        expected_payable_count = sum(len(payable_specs(profile)) for profile in profiles)
        expected_work_order_count = sum(len(work_step_specs(profile)) for profile in profiles)
        expected_shipment_count = sum(len(shipment_specs(profile)) for profile in profiles)
        expected_supplier_payment_count = sum(1 for profile in profiles if supplier_paid_amount(profile) > EPSILON)
        expected_flow_count = min(len({profile.flow_name for profile in profiles}), len(profiles))
        expected_special_event_count = min(len({profile.special_event for profile in profiles}), len(profiles))
        expect(counts["sales_orders"] == len(profiles), f"Sales order count wrong: {counts}")
        expect(counts["receivables"] == len(profiles) * 2, f"Receivable count wrong: {counts}")
        expect(counts["payables"] == expected_payable_count, f"Payable count wrong: {counts}")
        expect(counts["cash_transactions"] == len(profiles) * 2 + expected_supplier_payment_count, f"Cash count wrong: {counts}")
        expect(counts["work_orders"] == expected_work_order_count, f"Work order count wrong: {counts}")
        expect(counts["shipments"] == expected_shipment_count, f"Shipment count wrong: {counts}")
        expect(counts["return_cases"] == len(profiles), f"Return case count wrong: {counts}")
        expect(counts["refunds"] == len(profiles), f"Refund count wrong: {counts}")
        expect(counts["supplier_deductions"] == len(profiles), f"Supplier deduction count wrong: {counts}")
        expect(counts["new_products"] == len(profiles), f"Product count wrong: {counts}")
        expect(counts["new_parties"] >= min(len(profiles), 10), f"New party coverage too low: {counts}")
        expect(counts["new_cutting_factories"] >= min(len(profiles), 5), f"Cutting factory coverage too low: {counts}")
        expect(counts["evidence_assets"] == len(profiles), f"Evidence count wrong: {counts}")
        expect(counts["unresolved_pending"] == 0, f"Unresolved pending associations remain: {counts}")
        expect(counts["open_test_drafts"] == 0, f"Open test drafts remain: {counts}")
        expect(expected_flow_count >= min(len(profiles), 10), "Process-flow variation is too low.")
        expect(expected_special_event_count >= min(len(profiles), 8), "Special-event variation is too low.")

        samples: list[dict[str, Any]] = []
        for profile, result in zip(profiles, case_results, strict=True):
            order_no = f"{result['case_key']}-SO"
            finance = one(
                connection,
                """
                SELECT payable_amount, cash_in_amount, cash_out_amount
                FROM v_order_finance_status
                WHERE order_no = ?
                """,
                (order_no,),
            )
            forecast = one(
                connection,
                """
                SELECT expected_cash_in, expected_cash_out
                FROM v_cash_forecast
                WHERE order_no = ?
                """,
                (order_no,),
            )
            placeholders = ",".join("?" for _ in result["payable_ids"])
            payable = one(
                connection,
                f"""
                SELECT
                  SUM(amount_due) AS amount_due,
                  SUM(amount_paid) AS amount_paid,
                  SUM(CASE WHEN payable_status = 'paid' THEN 1 ELSE 0 END) AS paid_count,
                  SUM(CASE WHEN payable_status = 'partial' THEN 1 ELSE 0 END) AS partial_count,
                  SUM(CASE WHEN payable_status = 'pending' THEN 1 ELSE 0 END) AS pending_count
                FROM payables
                WHERE payable_id IN ({placeholders})
                """,
                tuple(result["payable_ids"]),
            )
            refund = one(
                connection,
                "SELECT refund_amount, refund_status FROM refunds WHERE refund_id = ?",
                (result["refund_id"],),
            )
            profit = one(
                connection,
                "SELECT estimated_gross_profit FROM v_order_profit_snapshot WHERE order_no = ?",
                (order_no,),
            )
            payable_total = total_payable_amount(profile)
            supplier_payment = supplier_paid_amount(profile)
            expected_cash_out = round(payable_total + profile.refund_amount, 2)
            actual_cash_out = round(profile.refund_amount + supplier_payment, 2)
            expect(abs(float(finance["payable_amount"]) - expected_cash_out) < EPSILON, f"Finance payable wrong for {order_no}: {finance}")
            expect(abs(float(finance["cash_in_amount"]) - profile.deposit_amount) < EPSILON, f"Finance cash-in wrong for {order_no}: {finance}")
            expect(abs(float(finance["cash_out_amount"]) - actual_cash_out) < EPSILON, f"Finance cash-out wrong for {order_no}: {finance}")
            expect(abs(float(forecast["expected_cash_in"]) - (profile.total_amount - profile.deposit_amount)) < EPSILON, f"Forecast cash-in wrong for {order_no}: {forecast}")
            expect(abs(float(forecast["expected_cash_out"]) - (payable_total - supplier_payment)) < EPSILON, f"Forecast cash-out wrong for {order_no}: {forecast}")
            expect(abs(float(payable["amount_due"]) - payable_total) < EPSILON, f"Payable sum wrong for {order_no}: {payable}")
            expect(abs(float(payable["amount_paid"] or 0) - supplier_payment) < EPSILON, f"Payable paid sum wrong for {order_no}: {payable}")
            expect(refund["refund_status"] == "paid", f"Refund status wrong for {order_no}: {refund}")
            expect(float(profit["estimated_gross_profit"]) < profile.total_amount, f"Profit did not include costs/refunds for {order_no}: {profit}")
            if len(samples) < 5:
                samples.append(
                    {
                        "order_no": order_no,
                        "finance": finance,
                        "forecast": forecast,
                        "payable": payable,
                        "refund": refund,
                        "profit": profit,
                    }
                )

        bad_allocations = connection.execute(
            """
            SELECT cash_transaction_id, SUM(allocated_amount) AS allocated
            FROM settlement_allocations
            GROUP BY cash_transaction_id
            HAVING allocated - (
              SELECT COALESCE(amount, 0)
              FROM cash_transactions ct
              WHERE ct.cash_transaction_id = settlement_allocations.cash_transaction_id
            ) > ?
            """,
            (EPSILON,),
        ).fetchall()
        expect(not bad_allocations, f"Found over-allocated cash transactions: {[dict(row) for row in bad_allocations]}")
    finally:
        connection.close()

    return {
        "integrity": integrity,
        "counts": counts,
        "expected_counts": {
            "payables": expected_payable_count,
            "work_orders": expected_work_order_count,
            "shipments": expected_shipment_count,
            "supplier_payment_cash_transactions": expected_supplier_payment_count,
            "process_flow_variants": expected_flow_count,
            "special_event_variants": expected_special_event_count,
        },
        "expected_supplier_payment_count": expected_supplier_payment_count,
        "sample_cases": samples,
    }


def run_stress(harness: StressHarness, *, case_count: int, seed: int) -> dict[str, Any]:
    rng = random.Random(seed)
    profiles = make_profiles(harness.actor_label, case_count)
    case_results: list[dict[str, Any]] = []
    for profile in profiles:
        case_results.append(run_case(harness, profile, rng))

    control = refresh_control_tower(data_root=harness.data_root, as_of_date=AS_OF_DATE, actor_label=harness.actor_label)
    report = generate_daily_report(
        data_root=harness.data_root,
        report_date=AS_OF_DATE,
        actor_label=harness.actor_label,
        refresh_first=False,
    )
    state = validate_stress_state(harness.db_path, actor_label=harness.actor_label, profiles=profiles, case_results=case_results)
    return {
        "case_count": case_count,
        "seed": seed,
        "case_catalog": [
            {
                "case_key": result["case_key"],
                "scenario": result["scenario"],
                "raw_event_order": result["raw_event_order"],
                "expected": result["expected"],
            }
            for result in case_results
        ],
        "state": state,
        "control_tower": control,
        "daily_report": report["report_json"],
    }


def main() -> int:
    args = parse_args()
    data_root = Path(args.data_root).expanduser().resolve()
    initialize_runtime(data_root)
    db_path = data_root / "db" / "order.db"
    expect(db_path.exists(), f"DB missing after init: {db_path}")
    workspace = Path(tempfile.mkdtemp(prefix="order-chaos-stress-"))
    actor_label = "CHAOS-STRESS-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = data_root / "db" / f"order.db.{actor_label}.bak"
    test_files: list[str] = []
    cleanup: dict[str, Any] = {
        "db_restored": False,
        "files_removed_count": 0,
        "files_removed_sample": [],
        "workspace_removed": False,
    }

    with connect(db_path) as connection:
        before_counts = table_counts(connection)
        before_integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    expect(before_integrity == "ok", f"Formal DB integrity failed before stress: {before_integrity}")
    sqlite_backup(db_path, backup_path)
    before_hash = sha256_file(backup_path)

    harness = StressHarness(data_root=data_root, actor_label=actor_label, workspace=workspace)
    result: dict[str, Any] | None = None
    error: str | None = None
    try:
        result = run_stress(harness, case_count=args.case_count, seed=args.seed)
        test_files = collect_test_files(db_path, actor_label)
    except Exception as exc:  # noqa: BLE001 - cleanup must run before surfacing the failure.
        error = repr(exc)
        try:
            test_files = collect_test_files(db_path, actor_label)
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
            removed_files = remove_files(test_files)
            cleanup["files_removed_count"] = len(removed_files)
            cleanup["files_removed_sample"] = removed_files[:5]
        if not args.keep_files and workspace.exists():
            shutil.rmtree(workspace)
            cleanup["workspace_removed"] = True

    with connect(db_path) as connection:
        after_counts = table_counts(connection)
        after_integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        residual_rows = connection.execute(
            "SELECT COUNT(*) FROM inbox_items WHERE source_actor = ?",
            (actor_label,),
        ).fetchone()[0]
    expect(after_integrity == "ok", f"Formal DB integrity failed after cleanup: {after_integrity}")
    if cleanup["db_restored"]:
        expect(after_counts == before_counts, "Formal DB counts changed after backup restore.")
        expect(cleanup["restored_hash_matches_backup"], "Restored DB hash does not match backup.")
        expect(int(residual_rows) == 0, f"Stress rows remain after restore: {residual_rows}")

    output = {
        "status": "ok",
        "actor_label": actor_label,
        "data_root": str(data_root),
        "db_path": str(db_path),
        "before_counts": before_counts,
        "after_counts": after_counts,
        "test_result": result,
        "cleanup": cleanup,
        "error": error,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
