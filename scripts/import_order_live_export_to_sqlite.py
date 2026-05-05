#!/usr/bin/env python3
"""Import the current live order export into a local SQLite review database.

This script is intentionally review-oriented:

- it creates a local-first staging database
- it preserves raw legacy fields as JSON
- it maps the current live export into a cleaner operational shape
- it does not mutate the legacy Feishu system
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_EXPORT_DIR = Path("/Users/redcreen/.openclaw/tmp/order-feishu-export-20260419/csv")
DEFAULT_OUTPUT = Path("/Users/redcreen/.openclaw/tmp/order-review-local/order_review.db")


def schema_path() -> Path:
    return Path(__file__).resolve().parents[1] / "order" / "runtime" / "schema_v1.sql"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--export-dir", default=str(DEFAULT_EXPORT_DIR))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    return parser.parse_args()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def as_float(value: Any) -> float | None:
    if value in ("", None):
        return None
    try:
        return float(str(value).strip())
    except ValueError:
        return None


def as_iso_date(value: Any) -> str | None:
    if value in ("", None):
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        ms = int(text)
        if ms > 10_000_000_000:
            return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date().isoformat()
    return text


def parse_relation(value: str | None) -> dict[str, list[str]]:
    if not value:
        return {"record_ids": [], "texts": []}
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return {"record_ids": [], "texts": []}
    record_ids: list[str] = []
    texts: list[str] = []
    if not isinstance(payload, list):
        return {"record_ids": [], "texts": []}
    for item in payload:
        if not isinstance(item, dict):
            continue
        for rid in item.get("record_ids") or []:
            if rid:
                record_ids.append(str(rid))
        for text in item.get("text_arr") or []:
            if text:
                texts.append(str(text))
        if item.get("text"):
            texts.append(str(item["text"]))
    dedup_record_ids = list(dict.fromkeys(record_ids))
    dedup_texts = list(dict.fromkeys(texts))
    return {"record_ids": dedup_record_ids, "texts": dedup_texts}


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(schema_path().read_text(encoding="utf-8"))


def upsert_party(conn: sqlite3.Connection, name: str | None, role: str) -> None:
    if not name:
        return
    conn.execute(
        """
        INSERT OR IGNORE INTO parties (party_name, party_role)
        VALUES (?, ?)
        """,
        (name, role),
    )


def upsert_product(conn: sqlite3.Connection, name: str | None, spec: str | None = None) -> None:
    if not name:
        return
    conn.execute(
        """
        INSERT OR IGNORE INTO products (product_name, spec_text)
        VALUES (?, ?)
        """,
        (name, spec or None),
    )


def import_orders(conn: sqlite3.Connection, rows: list[dict[str, str]]) -> dict[str, int]:
    imported = 0
    for row in rows:
        upsert_party(conn, row.get("客户名称"), "customer")
        upsert_product(conn, row.get("产品名称"), row.get("规格型号"))
        qty = as_float(row.get("数量"))
        unit = row.get("单位") or "个"
        confirmed_unit_price = as_float(row.get("成交单价"))
        confirmed_total = as_float(row.get("成交总价"))
        deposit_ratio = as_float(row.get("预付款比例"))
        deposit_expected = as_float(row.get("预付款金额"))
        deposit_received = as_float(row.get("已收金额"))
        outstanding = as_float(row.get("未收金额"))
        conn.execute(
            """
            INSERT INTO sales_orders (
                legacy_record_id, order_no, order_date, order_type, customer_name,
                product_name, spec_text, qty, unit, confirmed_unit_price,
                confirmed_total_amount, tax_unit_price, tax_total_amount,
                promised_delivery_date, order_status, deposit_ratio,
                deposit_expected_amount, deposit_received_amount, received_amount,
                outstanding_amount, receipt_status, invoice_type, invoice_status,
                invoice_amount, estimated_profit, notes, current_step,
                current_factory, progress_text, processing_cost, material_cost,
                delivered_qty, total_cost, cut_pieces_sent_qty,
                finished_goods_returned_qty, raw_fields_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row.get("record_id"),
                row.get("订单编号"),
                as_iso_date(row.get("订单日期")),
                row.get("订单类型"),
                row.get("客户名称"),
                row.get("产品名称"),
                row.get("规格型号"),
                qty,
                unit,
                confirmed_unit_price,
                confirmed_total,
                as_float(row.get("含税单价")),
                as_float(row.get("含税总价")),
                as_iso_date(row.get("交货日期")),
                row.get("订单状态"),
                deposit_ratio,
                deposit_expected,
                deposit_received,
                deposit_received,
                outstanding,
                row.get("收款状态"),
                row.get("发票类型"),
                row.get("开票状态"),
                as_float(row.get("开票金额")),
                as_float(row.get("预计利润")),
                row.get("备注"),
                row.get("当前工序"),
                row.get("当前工厂"),
                row.get("生产进度"),
                as_float(row.get("加工总成本")),
                as_float(row.get("材料成本")),
                as_float(row.get("已交货数量")),
                as_float(row.get("总成本")),
                as_float(row.get("裁片已发数量")),
                as_float(row.get("成品已回数量")),
                json.dumps(row, ensure_ascii=False),
            ),
        )
        imported += 1
    return {"sales_orders": imported}


def import_samples(conn: sqlite3.Connection, rows: list[dict[str, str]]) -> dict[str, int]:
    imported = 0
    for row in rows:
        upsert_party(conn, row.get("客户"), "customer")
        upsert_product(conn, row.get("产品名称"), row.get("规格型号"))
        conn.execute(
            """
            INSERT INTO samples (
                legacy_record_id, sample_no, sample_date, customer_name,
                product_name, spec_text, sample_status,
                estimated_unit_price, confirmed_unit_price, raw_fields_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row.get("record_id"),
                row.get("样品编号"),
                as_iso_date(row.get("样品日期")),
                row.get("客户"),
                row.get("产品名称"),
                row.get("规格型号"),
                row.get("样品状态"),
                as_float(row.get("估算价格")),
                as_float(row.get("客户确认价格")),
                json.dumps(row, ensure_ascii=False),
            ),
        )
        imported += 1
    return {"samples": imported}


def build_order_id_map(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        legacy_record_id: sales_order_id
        for legacy_record_id, sales_order_id in conn.execute(
            "SELECT legacy_record_id, sales_order_id FROM sales_orders"
        )
        if legacy_record_id
    }


def build_order_no_map(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        order_no: sales_order_id
        for order_no, sales_order_id in conn.execute(
            "SELECT order_no, sales_order_id FROM sales_orders"
        )
        if order_no
    }


def relation_order_ids(relation: dict[str, list[str]], order_id_map: dict[str, int], order_no_map: dict[str, int]) -> list[int]:
    found: list[int] = []
    for record_id in relation["record_ids"]:
        mapped = order_id_map.get(record_id)
        if mapped:
            found.append(mapped)
    for text in relation["texts"]:
        mapped = order_no_map.get(text)
        if mapped:
            found.append(mapped)
    return list(dict.fromkeys(found))


def import_production_lots(conn: sqlite3.Connection, rows: list[dict[str, str]]) -> dict[str, int]:
    imported = 0
    linked = 0
    order_id_map = build_order_id_map(conn)
    order_no_map = build_order_no_map(conn)
    for row in rows:
        upsert_party(conn, row.get("工厂"), "factory")
        upsert_product(conn, row.get("产品"))
        cursor = conn.execute(
            """
            INSERT INTO production_lots (
                legacy_record_id, lot_no, production_date, factory_name,
                product_name, qty_total, processing_cost, cost_detail,
                status, notes, raw_fields_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row.get("record_id"),
                row.get("批次编号"),
                as_iso_date(row.get("生产日期")),
                row.get("工厂"),
                row.get("产品"),
                as_float(row.get("总数量")),
                as_float(row.get("加工成本")),
                row.get("成本明细"),
                row.get("状态"),
                row.get("备注"),
                json.dumps(row, ensure_ascii=False),
            ),
        )
        lot_id = cursor.lastrowid
        relation = parse_relation(row.get("关联订单"))
        for sales_order_id in relation_order_ids(relation, order_id_map, order_no_map):
            conn.execute(
                """
                INSERT OR IGNORE INTO production_lot_order_links (
                    production_lot_id, sales_order_id, relation_text
                ) VALUES (?, ?, ?)
                """,
                (lot_id, sales_order_id, ",".join(relation["texts"]) or None),
            )
            linked += 1
        imported += 1
    return {"production_lots": imported, "production_lot_order_links": linked}


def import_shipments(conn: sqlite3.Connection, rows: list[dict[str, str]]) -> dict[str, int]:
    imported = 0
    linked = 0
    order_id_map = build_order_id_map(conn)
    order_no_map = build_order_no_map(conn)
    for row in rows:
        upsert_party(conn, row.get("加工厂"), "factory")
        cursor = conn.execute(
            """
            INSERT INTO shipments (
                legacy_record_id, shipment_date, shipment_type, factory_name,
                cut_detail, cut_qty, finished_qty, shipment_status, notes, raw_fields_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row.get("record_id"),
                as_iso_date(row.get("批次日期")),
                row.get("批次类型"),
                row.get("加工厂"),
                row.get("裁片明细"),
                as_float(row.get("裁片数量")),
                as_float(row.get("成品数量")),
                row.get("状态"),
                row.get("备注"),
                json.dumps(row, ensure_ascii=False),
            ),
        )
        shipment_id = cursor.lastrowid
        relation = parse_relation(row.get("关联订单"))
        for sales_order_id in relation_order_ids(relation, order_id_map, order_no_map):
            conn.execute(
                """
                INSERT OR IGNORE INTO shipment_order_links (
                    shipment_id, sales_order_id, relation_text
                ) VALUES (?, ?, ?)
                """,
                (shipment_id, sales_order_id, ",".join(relation["texts"]) or None),
            )
            linked += 1
        imported += 1
    return {"shipments": imported, "shipment_order_links": linked}


def import_supplier_settlements(conn: sqlite3.Connection, rows: list[dict[str, str]]) -> dict[str, int]:
    imported = 0
    linked = 0
    order_id_map = build_order_id_map(conn)
    order_no_map = build_order_no_map(conn)
    for row in rows:
        upsert_party(conn, row.get("供应商"), "supplier")
        cursor = conn.execute(
            """
            INSERT INTO supplier_settlements (
                legacy_record_id, settlement_date, supplier_name, lot_relation_text,
                order_relation_text, product_name, qty, unit_price, amount,
                payable_type, settlement_status, payment_status, process_name,
                notes, voucher_text, raw_fields_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row.get("record_id"),
                as_iso_date(row.get("结算日期")),
                row.get("供应商"),
                ",".join(parse_relation(row.get("关联生产批次"))["texts"]) or None,
                ",".join(parse_relation(row.get("关联订单"))["texts"]) or None,
                row.get("产品"),
                as_float(row.get("数量")),
                as_float(row.get("单价")),
                as_float(row.get("金额")),
                row.get("款项性质"),
                row.get("结算状态"),
                row.get("付款状态"),
                row.get("工序"),
                row.get("备注"),
                row.get("结算凭证"),
                json.dumps(row, ensure_ascii=False),
            ),
        )
        settlement_id = cursor.lastrowid
        relation = parse_relation(row.get("关联订单"))
        for sales_order_id in relation_order_ids(relation, order_id_map, order_no_map):
            conn.execute(
                """
                INSERT OR IGNORE INTO supplier_settlement_order_links (
                    supplier_settlement_id, sales_order_id, relation_text
                ) VALUES (?, ?, ?)
                """,
                (settlement_id, sales_order_id, ",".join(relation["texts"]) or None),
            )
            linked += 1
        imported += 1
    return {"supplier_settlements": imported, "supplier_settlement_order_links": linked}


def import_cash_transactions(conn: sqlite3.Connection, rows: list[dict[str, str]]) -> dict[str, int]:
    imported = 0
    linked = 0
    order_id_map = build_order_id_map(conn)
    order_no_map = build_order_no_map(conn)
    for row in rows:
        direction = row.get("类型")
        upsert_party(conn, row.get("对方名称"), "customer" if direction == "收款" else "supplier")
        cursor = conn.execute(
            """
            INSERT INTO cash_transactions (
                legacy_record_id, transaction_date, direction, counterparty_name,
                amount, purpose, payment_method, is_marked_paid,
                expected_payment_date, notes, raw_fields_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row.get("record_id"),
                as_iso_date(row.get("日期")),
                direction,
                row.get("对方名称"),
                as_float(row.get("金额")),
                row.get("款项性质"),
                row.get("支付方式"),
                row.get("是否已付"),
                as_iso_date(row.get("预计付款日期")),
                row.get("备注"),
                json.dumps(row, ensure_ascii=False),
            ),
        )
        cash_id = cursor.lastrowid
        relation = parse_relation(row.get("关联订单"))
        for sales_order_id in relation_order_ids(relation, order_id_map, order_no_map):
            conn.execute(
                """
                INSERT OR IGNORE INTO cash_transaction_order_links (
                    cash_transaction_id, sales_order_id, relation_text
                ) VALUES (?, ?, ?)
                """,
                (cash_id, sales_order_id, ",".join(relation["texts"]) or None),
            )
            linked += 1
        imported += 1
    return {"cash_transactions": imported, "cash_transaction_order_links": linked}


def import_bom_items(conn: sqlite3.Connection, rows: list[dict[str, str]]) -> dict[str, int]:
    imported = 0
    for row in rows:
        upsert_party(conn, row.get("供应商"), "supplier")
        upsert_product(conn, row.get("产品名称"))
        conn.execute(
            """
            INSERT INTO bom_items (
                legacy_record_id, product_name, planned_qty, part_name,
                material_name, supplier_name, color_code, effective_width,
                unit_consumption, approved_material_qty, unit_name, unit_price,
                line_amount, notes, raw_fields_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row.get("record_id"),
                row.get("产品名称"),
                as_float(row.get("数量")),
                row.get("部位"),
                row.get("布料"),
                row.get("供应商"),
                row.get("色号"),
                as_float(row.get("有效门幅")),
                as_float(row.get("单耗")),
                as_float(row.get("核定用料")),
                row.get("单位"),
                as_float(row.get("单价")),
                as_float(row.get("金额")),
                row.get("备注"),
                json.dumps(row, ensure_ascii=False),
            ),
        )
        imported += 1
    return {"bom_items": imported}


def write_metadata(conn: sqlite3.Connection, export_dir: Path) -> None:
    metadata = {
        "source": "legacy_feishu_live_export",
        "export_dir": str(export_dir),
        "imported_at": datetime.now().isoformat(),
    }
    for key, value in metadata.items():
        conn.execute(
            "INSERT OR REPLACE INTO import_metadata (key, value) VALUES (?, ?)",
            (key, value),
        )


def main() -> None:
    args = parse_args()
    export_dir = Path(args.export_dir)
    output_path = Path(args.output)
    ensure_parent(output_path)
    if output_path.exists():
        output_path.unlink()

    csv_files = {
        "orders": export_dir / "01-订单主表.csv",
        "shipments": export_dir / "02-物流批次表.csv",
        "lots": export_dir / "03-生产批次表.csv",
        "settlements": export_dir / "04-供应商结算表.csv",
        "cash": export_dir / "08-资金流水表.csv",
        "samples": export_dir / "11-样品管理表.csv",
        "bom": export_dir / "14-BOM表-产品用料清单（新）.csv",
    }

    conn = sqlite3.connect(output_path)
    conn.row_factory = sqlite3.Row
    create_schema(conn)

    summary: dict[str, int | str] = {}
    summary.update(import_orders(conn, read_csv(csv_files["orders"])))
    summary.update(import_samples(conn, read_csv(csv_files["samples"])))
    summary.update(import_production_lots(conn, read_csv(csv_files["lots"])))
    summary.update(import_shipments(conn, read_csv(csv_files["shipments"])))
    summary.update(import_supplier_settlements(conn, read_csv(csv_files["settlements"])))
    summary.update(import_cash_transactions(conn, read_csv(csv_files["cash"])))
    summary.update(import_bom_items(conn, read_csv(csv_files["bom"])))
    write_metadata(conn, export_dir)
    conn.commit()

    review_dir = output_path.parent
    summary_path = review_dir / "import_summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "database": str(output_path),
                "summary": summary,
                "views": {
                    "v_order_production_status": conn.execute("SELECT COUNT(*) FROM v_order_production_status").fetchone()[0],
                    "v_order_finance_status": conn.execute("SELECT COUNT(*) FROM v_order_finance_status").fetchone()[0],
                    "v_order_profit_snapshot": conn.execute("SELECT COUNT(*) FROM v_order_profit_snapshot").fetchone()[0],
                    "v_cash_forecast": conn.execute("SELECT COUNT(*) FROM v_cash_forecast").fetchone()[0],
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(summary_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
