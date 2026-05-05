#!/usr/bin/env python3
"""LLM-backed 50-case unordered stress test for the order runtime.

This test is intentionally separate from test_order_chaos_stress_50.py:

1. OpenClaw order agent with openai-codex/gpt-5.5 extracts structured data from
   messy natural-language cases.
2. The extracted data is validated against the generated scenario truth.
3. The extracted data is used to exercise the formal runtime/data model in an
   isolated SQLite data root.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import test_order_chaos_stress_50 as base  # noqa: E402


DEFAULT_MODEL = "openai-codex/gpt-5.5"
CASE_FIELDS = [
    "case_key",
    "scenario_name",
    "customer_name",
    "product_name",
    "spec_text",
    "flow_name",
    "process_steps",
    "sewing_factory_name",
    "cotton_factory_name",
    "handwork_factory_name",
    "cutting_factory_name",
    "embroidery_factory_name",
    "composite_factory_name",
    "material_supplier_name",
    "accessory_supplier_name",
    "warehouse_name",
    "primary_shipment_type",
    "special_event",
    "order_type",
    "issue_text",
    "qty",
    "lost_qty",
    "replenish_qty",
    "rework_qty",
    "unit_price",
    "total_amount",
    "deposit_amount",
    "refund_amount",
    "supplier_deduction_amount",
    "supplier_paid_amount",
]
NUMERIC_FIELDS = {
    "qty",
    "lost_qty",
    "replenish_qty",
    "rework_qty",
    "unit_price",
    "total_amount",
    "deposit_amount",
    "refund_amount",
    "supplier_deduction_amount",
    "supplier_paid_amount",
}
BANNED_NATURAL_LANGUAGE_PHRASES = ("实际环节是", "代码顺序口径")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run 50 messy order scenarios through OpenClaw gpt-5.5 extraction.")
    parser.add_argument("--case-count", type=int, default=50)
    parser.add_argument("--seed", type=int, default=20260504)
    parser.add_argument("--batch-size", type=int, default=5)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--keep-data-root", action="store_true")
    parser.add_argument("--actor-label", help="Optional stable actor label. Required for deterministic resume across runs.")
    parser.add_argument(
        "--checkpoint-dir",
        help="Directory for per-batch checkpoints. Defaults to /tmp/order_llm_chaos_checkpoints/<actor-label>.",
    )
    parser.add_argument("--resume", action="store_true", help="Reuse completed batch checkpoints from --checkpoint-dir.")
    parser.add_argument("--output-file", help="Optional path to write the JSON result.")
    return parser.parse_args()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json_file(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def read_json_file(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def default_checkpoint_dir(actor_label: str) -> Path:
    return Path(tempfile.gettempdir()) / "order_llm_chaos_checkpoints" / actor_label


def profile_case_key(actor_label: str, profile: base.CaseProfile) -> str:
    return f"{actor_label}-CASE-{profile.index:02d}"


def profile_expected(actor_label: str, profile: base.CaseProfile) -> dict[str, Any]:
    payload = asdict(profile)
    payload["case_key"] = profile_case_key(actor_label, profile)
    payload["process_steps"] = list(profile.process_steps)
    payload["supplier_paid_amount"] = base.supplier_paid_amount(profile)
    return {field: payload[field] for field in CASE_FIELDS}


def validate_natural_language_inputs(case_texts: list[tuple[base.CaseProfile, str]]) -> dict[str, Any]:
    violations: list[dict[str, str]] = []
    for profile, text in case_texts:
        for phrase in BANNED_NATURAL_LANGUAGE_PHRASES:
            if phrase in text:
                violations.append({"case_index": str(profile.index), "phrase": phrase})
    if violations:
        raise AssertionError({"natural_language_input_banned_phrase_violations": violations})
    return {
        "case_count": len(case_texts),
        "banned_phrases": list(BANNED_NATURAL_LANGUAGE_PHRASES),
        "banned_phrase_violations": 0,
    }


def product_flow_catalog(batch_cases: list[tuple[base.CaseProfile, str]]) -> list[dict[str, Any]]:
    return [
        {
            "product_name": profile.product_name,
            "spec_text": profile.spec_text,
            "default_flow_name": profile.flow_name,
            "default_process_steps": list(profile.process_steps),
            "default_process_labels": [base.work_type_for_step(step) for step in profile.process_steps],
            "confirmation_policy": "suggest_from_product_template_then_require_human_confirm_or_edit",
        }
        for profile, _ in batch_cases
    ]


def messy_process_plan_text(profile: base.CaseProfile, rng: random.Random) -> str:
    templates = [
        f"{profile.product_name} 这个款我说不清完整流程，系统里如果有老模板先带出来，我确认有没有要改",
        f"这批先按 {profile.product_name} 之前那套做法提示我，不要直接落正式流程，等我确认",
        f"{profile.product_name} 流程大概照老款，但是这次可能因为 {profile.issue_text} 要插一个补充动作，系统先帮我核一下",
        f"我只记得 {profile.product_name} 不是简单平车，具体复合/绣花/切割有没有你按产品模板问我确认",
    ]
    return base.roughen_text(rng.choice(templates), rng)


def rough_case_text(actor_label: str, profile: base.CaseProfile, rng: random.Random) -> str:
    case_key = profile_case_key(actor_label, profile)
    event_keys = [
        "payment",
        "main_factory_statement",
        "cutting_statement",
        "process_plan",
        "shipment",
        "exception_logistics",
        "warehouse",
        "return",
        "order",
        "work",
    ]
    rng.shuffle(event_keys)
    event_lines = []
    for key in event_keys:
        if key == "process_plan":
            event_lines.append(f"- {messy_process_plan_text(profile, rng)}")
        else:
            event_lines.append(f"- {base.messy_event_text(profile, key, rng)}")
    return "\n".join(
        [
            f"测试编号/乱序线索: {case_key}",
            f"内部场景标签先写着，别当客户原话：{profile.scenario_name}",
            f"这段不是表格录入，是跟单嘴上说的一堆散信息，可能有错别字，也可能前后顺序不对。",
            (
                f"客户/产品大概是 {profile.customer_name} 做 {profile.product_name}，规格 {profile.spec_text}，"
                f"数量 {profile.qty} 个，单价 {profile.unit_price}，总金额 {profile.total_amount}，"
                f"先收 30% 定金 {profile.deposit_amount}。订单类型先按 {profile.order_type}。"
            ),
            (
                f"流程我这里不完整说了，先按这个产品以前的默认做法帮我带出来；"
                f"如果这批因为 {profile.issue_text} 或 {profile.special_event} 要改流程，你再问我确认。"
            ),
            (
                f"相关厂商别混：车缝/主加工 {profile.sewing_factory_name}；充棉 {profile.cotton_factory_name}；"
                f"手工 {profile.handwork_factory_name}；切割/补裁片 {profile.cutting_factory_name}；"
                f"绣花 {profile.embroidery_factory_name}；复合 {profile.composite_factory_name}；"
                f"布料 {profile.material_supplier_name}；配件 {profile.accessory_supplier_name}；"
                f"仓库 {profile.warehouse_name}。"
            ),
            (
                f"物流/异常：主发货类型 {profile.primary_shipment_type}，特殊事件 {profile.special_event}，"
                f"丢货 {profile.lost_qty}，补货或补裁/补配件 {profile.replenish_qty}，返工 {profile.rework_qty}。"
            ),
            (
                f"售后/财务也先挂上：问题 {profile.issue_text}，客户可能退款 {profile.refund_amount}，"
                f"供应商扣款 {profile.supplier_deduction_amount}，已付供应商 {base.supplier_paid_amount(profile)}。"
            ),
            "散消息如下，顺序故意打乱：",
            *event_lines,
        ]
    )


def build_prompt(batch_cases: list[tuple[base.CaseProfile, str]]) -> str:
    schema = {field: "<value>" for field in CASE_FIELDS}
    schema["process_steps"] = ["sample", "material"]
    catalog = product_flow_catalog(batch_cases)
    return "\n\n".join(
        [
            "你是 order 自然语言抽取测试器。不要调用任何工具，不要写入数据库，不要创建草稿。",
            "必须只输出合法 JSON，不要 markdown，不要解释。",
            "真实用户通常不会完整输入加工流程；系统已根据产品查到候选默认流程模板：",
            json.dumps({"product_flow_templates": catalog}, ensure_ascii=False, indent=2),
            "从下面每个乱序中文现场记录中抽取字段，输出格式：",
            json.dumps({"cases": [schema]}, ensure_ascii=False, indent=2),
            "要求：",
            "- case_key 必须原样保留。",
            "- 如果现场记录没有完整流程，必须按 product_flow_templates 中同一 product_name/spec_text 的默认模板输出 flow_name 和 process_steps。",
            "- 该流程在真实系统中仍需人类确认或修复；本测试只验证系统能先基于产品模板提出规范流程。",
            "- 金额和数量输出数字，不要字符串。",
            "- 工厂、供应商、仓库、客户、产品名称必须原样保留。",
            "- 不要凭空猜测产品模板之外的信息；但现场记录和产品模板已经给出的字段必须抽取。",
            "可用 process_steps 代码：sample, material, composite, laser_cut, position_cut, embroidery, accessory, replenish_accessory, replenish_cut, sewing, sewing_full, cotton, handwork, replacement_goods, rework, qc。",
            "开始抽取：",
            "\n\n---\n\n".join(text for _, text in batch_cases),
        ]
    )


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
    parsed = json.loads(stripped)
    if not isinstance(parsed, dict):
        raise AssertionError("LLM output root must be a JSON object.")
    return parsed


def run_openclaw_extract(
    *,
    prompt: str,
    batch_index: int,
    actor_label: str,
    model: str,
    timeout: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    session_id = f"{actor_label.lower()}-batch-{batch_index:02d}"
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
            f"OpenClaw batch {batch_index} failed with code {completed.returncode}.\n"
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
    reply = outer["payloads"][0]["text"]
    parsed = parse_json_text(reply)
    cases = parsed.get("cases")
    if not isinstance(cases, list):
        raise AssertionError(f"Batch {batch_index} output missing cases list: {parsed}")
    return cases, route


def validate_route(route: dict[str, Any], batch_index: int) -> None:
    if route.get("provider") != "openai-codex" or route.get("model") != "gpt-5.5" or route.get("fallback_used") is not False:
        raise AssertionError(f"Batch {batch_index} did not use openai-codex/gpt-5.5 cleanly: {route}")


def summarize_batch_validation(
    *,
    expected_by_key: dict[str, dict[str, Any]],
    cases: list[dict[str, Any]],
) -> dict[str, Any]:
    unknown_case_keys: list[str] = []
    missing_case_key_count = 0
    field_errors: dict[str, list[str]] = {}
    case_keys: list[str] = []
    for item in cases:
        if not isinstance(item, dict):
            missing_case_key_count += 1
            continue
        case_key = str(item.get("case_key") or "")
        if not case_key:
            missing_case_key_count += 1
            continue
        case_keys.append(case_key)
        expected = expected_by_key.get(case_key)
        if expected is None:
            unknown_case_keys.append(case_key)
            continue
        errors = validate_extracted_case(expected, item)
        if errors:
            field_errors[case_key] = errors
    return {
        "case_count": len(cases),
        "case_keys": case_keys,
        "missing_case_key_count": missing_case_key_count,
        "unknown_case_keys": unknown_case_keys,
        "field_error_case_count": len(field_errors),
        "field_errors": field_errors,
        "exact_match_case_count": len(case_keys) - len(unknown_case_keys) - len(field_errors),
    }


def write_progress_checkpoint(
    *,
    checkpoint_dir: Path,
    actor_label: str,
    case_count: int,
    batch_size: int,
    routes: list[dict[str, Any]],
    extracted_by_key: dict[str, dict[str, Any]],
    completed_batches: list[int],
    stage: str,
) -> None:
    write_json_file(
        checkpoint_dir / "progress.json",
        {
            "stage": stage,
            "actor_label": actor_label,
            "case_count": case_count,
            "batch_size": batch_size,
            "completed_batches": completed_batches,
            "completed_batch_count": len(completed_batches),
            "extracted_case_count": len(extracted_by_key),
            "all_completed_batches_gpt55": all(
                route.get("provider") == "openai-codex"
                and route.get("model") == "gpt-5.5"
                and route.get("fallback_used") is False
                for route in routes
            ),
            "updated_at": utc_now_iso(),
        },
    )


def normalize_number(value: Any) -> float:
    if isinstance(value, str):
        value = value.replace(",", "").strip()
    return float(value)


def validate_extracted_case(expected: dict[str, Any], actual: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for field in CASE_FIELDS:
        if field not in actual:
            errors.append(f"missing field {field}")
            continue
        if field == "process_steps":
            if list(actual[field]) != list(expected[field]):
                errors.append(f"process_steps expected {expected[field]!r}, got {actual[field]!r}")
            continue
        if field in NUMERIC_FIELDS:
            if abs(normalize_number(actual[field]) - normalize_number(expected[field])) > base.EPSILON:
                errors.append(f"{field} expected {expected[field]!r}, got {actual[field]!r}")
            continue
        if str(actual[field]) != str(expected[field]):
            errors.append(f"{field} expected {expected[field]!r}, got {actual[field]!r}")
    return errors


def extracted_to_profile(actual: dict[str, Any], index: int) -> base.CaseProfile:
    return base.CaseProfile(
        index=index,
        scenario_name=str(actual["scenario_name"]),
        customer_name=str(actual["customer_name"]),
        product_name=str(actual["product_name"]),
        spec_text=str(actual["spec_text"]),
        flow_name=str(actual["flow_name"]),
        process_steps=tuple(str(item) for item in actual["process_steps"]),
        sewing_factory_name=str(actual["sewing_factory_name"]),
        cotton_factory_name=str(actual["cotton_factory_name"]),
        handwork_factory_name=str(actual["handwork_factory_name"]),
        cutting_factory_name=str(actual["cutting_factory_name"]),
        embroidery_factory_name=str(actual["embroidery_factory_name"]),
        composite_factory_name=str(actual["composite_factory_name"]),
        material_supplier_name=str(actual["material_supplier_name"]),
        accessory_supplier_name=str(actual["accessory_supplier_name"]),
        warehouse_name=str(actual["warehouse_name"]),
        primary_shipment_type=str(actual["primary_shipment_type"]),
        special_event=str(actual["special_event"]),
        order_type=str(actual["order_type"]),
        issue_text=str(actual["issue_text"]),
        qty=int(normalize_number(actual["qty"])),
        lost_qty=int(normalize_number(actual["lost_qty"])),
        replenish_qty=int(normalize_number(actual["replenish_qty"])),
        rework_qty=int(normalize_number(actual["rework_qty"])),
        unit_price=normalize_number(actual["unit_price"]),
        total_amount=normalize_number(actual["total_amount"]),
        deposit_amount=normalize_number(actual["deposit_amount"]),
        refund_amount=normalize_number(actual["refund_amount"]),
        supplier_deduction_amount=normalize_number(actual["supplier_deduction_amount"]),
        supplier_paid_amount=normalize_number(actual["supplier_paid_amount"]),
    )


def validate_runtime_process_sequences(
    db_path: Path,
    *,
    actor_label: str,
    profiles: list[base.CaseProfile],
) -> dict[str, Any]:
    checked_cases = 0
    checked_work_orders = 0
    failures: dict[str, Any] = {}
    connection = base.connect(db_path)
    try:
        for profile in profiles:
            case_key = profile_case_key(actor_label, profile)
            expected_specs = base.work_step_specs(profile)
            rows = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT
                      wo.work_order_no,
                      wo.work_type,
                      wo.planned_qty,
                      wo.planned_due_at,
                      p.party_name AS provider_name
                    FROM work_orders wo
                    LEFT JOIN parties p ON p.party_id = wo.provider_party_id
                    WHERE wo.work_order_no LIKE ?
                    ORDER BY wo.work_order_no
                    """,
                    (f"{case_key}-WO-%",),
                ).fetchall()
            ]
            case_errors: list[str] = []
            if len(rows) != len(expected_specs):
                case_errors.append(f"expected {len(expected_specs)} work orders, got {len(rows)}")
            for row, expected in zip(rows, expected_specs, strict=False):
                expected_work_order_no = f"{case_key}-{expected['suffix']}"
                if row["work_order_no"] != expected_work_order_no:
                    case_errors.append(f"work_order_no expected {expected_work_order_no}, got {row['work_order_no']}")
                if row["work_type"] != expected["work_type"]:
                    case_errors.append(f"{row['work_order_no']} work_type expected {expected['work_type']}, got {row['work_type']}")
                if abs(float(row["planned_qty"] or 0) - float(expected["planned_qty"])) > base.EPSILON:
                    case_errors.append(
                        f"{row['work_order_no']} planned_qty expected {expected['planned_qty']}, got {row['planned_qty']}"
                    )
                if row["planned_due_at"] != expected["planned_due_at"]:
                    case_errors.append(
                        f"{row['work_order_no']} planned_due_at expected {expected['planned_due_at']}, got {row['planned_due_at']}"
                    )
                if row["provider_name"] != expected["provider_name"]:
                    case_errors.append(
                        f"{row['work_order_no']} provider expected {expected['provider_name']}, got {row['provider_name']}"
                    )
            if case_errors:
                failures[case_key] = {
                    "expected_process_steps": list(profile.process_steps),
                    "expected_work_types": [item["work_type"] for item in expected_specs],
                    "actual_work_types": [row["work_type"] for row in rows],
                    "errors": case_errors,
                }
            checked_cases += 1
            checked_work_orders += len(rows)
    finally:
        connection.close()
    if failures:
        raise AssertionError(json.dumps({"runtime_process_sequence_failures": failures}, ensure_ascii=False, indent=2))
    return {
        "checked_cases": checked_cases,
        "checked_work_orders": checked_work_orders,
        "status": "ok",
        "order_basis": "work_order_no lexical order with WO-<step_no>-<step_code> suffix",
    }


def run_runtime_with_profiles(
    *,
    profiles: list[base.CaseProfile],
    actor_label: str,
    seed: int,
    keep_data_root: bool,
) -> dict[str, Any]:
    data_root = Path(tempfile.mkdtemp(prefix="order-llm-chaos-data-"))
    workspace = Path(tempfile.mkdtemp(prefix="order-llm-chaos-work-"))
    cleanup: dict[str, Any] = {
        "data_root": str(data_root),
        "workspace": str(workspace),
        "data_root_removed": False,
        "workspace_removed": False,
    }
    try:
        base.initialize_runtime(data_root)
        harness = base.StressHarness(data_root=data_root, actor_label=actor_label, workspace=workspace)
        rng = random.Random(seed + 7919)
        case_results = [base.run_case(harness, profile, rng) for profile in profiles]
        control = base.refresh_control_tower(data_root=data_root, as_of_date=base.AS_OF_DATE, actor_label=actor_label)
        report = base.generate_daily_report(
            data_root=data_root,
            report_date=base.AS_OF_DATE,
            actor_label=actor_label,
            refresh_first=False,
        )
        state = base.validate_stress_state(harness.db_path, actor_label=actor_label, profiles=profiles, case_results=case_results)
        process_sequence_validation = validate_runtime_process_sequences(
            harness.db_path,
            actor_label=actor_label,
            profiles=profiles,
        )
        return {
            "data_root": str(data_root),
            "db_path": str(harness.db_path),
            "state": state,
            "process_sequence_validation": process_sequence_validation,
            "case_catalog": [
                {
                    "case_key": result["case_key"],
                    "scenario": result["scenario"],
                    "raw_event_order": result["raw_event_order"],
                    "expected": result["expected"],
                }
                for result in case_results
            ],
            "control_tower": control,
            "daily_report": report["report_json"],
            "cleanup": cleanup,
        }
    finally:
        if not keep_data_root and data_root.exists():
            shutil.rmtree(data_root)
            cleanup["data_root_removed"] = True
        if workspace.exists():
            shutil.rmtree(workspace)
            cleanup["workspace_removed"] = True


def main() -> int:
    args = parse_args()
    if args.case_count <= 0:
        raise SystemExit("--case-count must be positive.")
    if args.batch_size <= 0:
        raise SystemExit("--batch-size must be positive.")

    manifest_path = Path(args.checkpoint_dir) / "manifest.json" if args.checkpoint_dir else None
    actor_label = args.actor_label
    if actor_label is None and args.resume and manifest_path and manifest_path.exists():
        existing_manifest = read_json_file(manifest_path)
        actor_label = str(existing_manifest.get("actor_label") or "")
    if not actor_label:
        actor_label = "LLM-CHAOS-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    checkpoint_dir = Path(args.checkpoint_dir) if args.checkpoint_dir else default_checkpoint_dir(actor_label)
    checkpoint_dir.mkdir(parents=True, exist_ok=True)

    rng = random.Random(args.seed)
    generated_profiles = base.make_profiles(actor_label, args.case_count)
    expected_by_key = {profile_case_key(actor_label, profile): profile_expected(actor_label, profile) for profile in generated_profiles}
    case_texts = [(profile, rough_case_text(actor_label, profile, rng)) for profile in generated_profiles]
    input_validation = validate_natural_language_inputs(case_texts)

    extracted_by_key: dict[str, dict[str, Any]] = {}
    routes: list[dict[str, Any]] = []
    extraction_errors: dict[str, list[str]] = {}
    completed_batches: list[int] = []

    write_json_file(
        checkpoint_dir / "manifest.json",
        {
            "stage": "started",
            "actor_label": actor_label,
            "case_count": args.case_count,
            "batch_size": args.batch_size,
            "seed": args.seed,
            "model_required": args.model,
            "checkpoint_dir": str(checkpoint_dir),
            "output_file": args.output_file,
            "resume": args.resume,
            "natural_language_input_validation": input_validation,
            "started_at": utc_now_iso(),
        },
    )

    for batch_index, start in enumerate(range(0, len(case_texts), args.batch_size), start=1):
        batch = case_texts[start : start + args.batch_size]
        batch_checkpoint_path = checkpoint_dir / f"batch-{batch_index:02d}.json"
        if args.resume and batch_checkpoint_path.exists():
            batch_checkpoint = read_json_file(batch_checkpoint_path)
            cases = batch_checkpoint["cases"]
            route = batch_checkpoint["route"]
            validate_route(route, batch_index)
        else:
            cases, route = run_openclaw_extract(
                prompt=build_prompt(batch),
                batch_index=batch_index,
                actor_label=actor_label,
                model=args.model,
                timeout=args.timeout,
            )
            validate_route(route, batch_index)
        batch_validation = summarize_batch_validation(expected_by_key=expected_by_key, cases=cases)
        write_json_file(
            batch_checkpoint_path,
            {
                "stage": "batch_extracted",
                "actor_label": actor_label,
                "batch_index": batch_index,
                "source": "checkpoint" if args.resume and batch_checkpoint_path.exists() else "openclaw",
                "expected_case_keys": [profile_case_key(actor_label, profile) for profile, _ in batch],
                "route": route,
                "validation": batch_validation,
                "cases": cases,
                "updated_at": utc_now_iso(),
            },
        )
        routes.append(route)
        for item in cases:
            if not isinstance(item, dict):
                raise AssertionError(f"Batch {batch_index} produced a non-object case: {item!r}")
            case_key = str(item.get("case_key") or "")
            if not case_key:
                raise AssertionError(f"Batch {batch_index} produced a case without case_key: {item!r}")
            extracted_by_key[case_key] = item
        completed_batches.append(batch_index)
        write_progress_checkpoint(
            checkpoint_dir=checkpoint_dir,
            actor_label=actor_label,
            case_count=args.case_count,
            batch_size=args.batch_size,
            routes=routes,
            extracted_by_key=extracted_by_key,
            completed_batches=completed_batches,
            stage="extracting",
        )

    missing = sorted(set(expected_by_key) - set(extracted_by_key))
    extra = sorted(set(extracted_by_key) - set(expected_by_key))
    if missing or extra:
        write_json_file(
            checkpoint_dir / "extraction-summary.json",
            {
                "stage": "extraction_failed",
                "actor_label": actor_label,
                "missing_cases": missing,
                "extra_cases": extra,
                "extracted_case_count": len(extracted_by_key),
                "updated_at": utc_now_iso(),
            },
        )
        raise AssertionError({"missing_cases": missing, "extra_cases": extra})

    extracted_profiles: list[base.CaseProfile] = []
    for profile in generated_profiles:
        key = profile_case_key(actor_label, profile)
        expected = expected_by_key[key]
        actual = extracted_by_key[key]
        errors = validate_extracted_case(expected, actual)
        if errors:
            extraction_errors[key] = errors
        else:
            extracted_profiles.append(extracted_to_profile(actual, profile.index))

    if extraction_errors:
        write_json_file(
            checkpoint_dir / "extraction-summary.json",
            {
                "stage": "extraction_failed",
                "actor_label": actor_label,
                "extracted_case_count": len(extracted_by_key),
                "exact_match_case_count": len(extracted_profiles),
                "field_error_case_count": len(extraction_errors),
                "field_errors": extraction_errors,
                "updated_at": utc_now_iso(),
            },
        )
        raise AssertionError(json.dumps(extraction_errors, ensure_ascii=False, indent=2))

    write_json_file(
        checkpoint_dir / "extraction-summary.json",
        {
            "stage": "extraction_ok",
            "actor_label": actor_label,
            "batch_count": len(routes),
            "extracted_case_count": len(extracted_by_key),
            "exact_match_case_count": len(extracted_profiles),
            "all_batches_gpt55": all(
                route["provider"] == "openai-codex" and route["model"] == "gpt-5.5" and route["fallback_used"] is False
                for route in routes
            ),
            "updated_at": utc_now_iso(),
        },
    )
    write_progress_checkpoint(
        checkpoint_dir=checkpoint_dir,
        actor_label=actor_label,
        case_count=args.case_count,
        batch_size=args.batch_size,
        routes=routes,
        extracted_by_key=extracted_by_key,
        completed_batches=completed_batches,
        stage="runtime_starting",
    )
    runtime_result = run_runtime_with_profiles(
        profiles=extracted_profiles,
        actor_label=actor_label,
        seed=args.seed,
        keep_data_root=args.keep_data_root,
    )
    write_json_file(
        checkpoint_dir / "runtime-result.json",
        {
            "stage": "runtime_ok",
            "actor_label": actor_label,
            "runtime": runtime_result,
            "updated_at": utc_now_iso(),
        },
    )
    output = {
        "status": "ok",
        "actor_label": actor_label,
        "model_required": args.model,
        "case_count": args.case_count,
        "batch_size": args.batch_size,
        "checkpoint_dir": str(checkpoint_dir),
        "natural_language_input_validation": input_validation,
        "llm": {
            "batch_count": len(routes),
            "routes": routes,
            "all_batches_gpt55": all(
                route["provider"] == "openai-codex" and route["model"] == "gpt-5.5" and route["fallback_used"] is False
                for route in routes
            ),
            "extracted_case_count": len(extracted_by_key),
            "exact_match_case_count": len(extracted_profiles),
        },
        "runtime": runtime_result,
    }
    write_progress_checkpoint(
        checkpoint_dir=checkpoint_dir,
        actor_label=actor_label,
        case_count=args.case_count,
        batch_size=args.batch_size,
        routes=routes,
        extracted_by_key=extracted_by_key,
        completed_batches=completed_batches,
        stage="complete",
    )
    write_json_file(
        checkpoint_dir / "manifest.json",
        {
            "stage": "complete",
            "actor_label": actor_label,
            "case_count": args.case_count,
            "batch_size": args.batch_size,
            "seed": args.seed,
            "model_required": args.model,
            "checkpoint_dir": str(checkpoint_dir),
            "output_file": args.output_file,
            "resume": args.resume,
            "natural_language_input_validation": input_validation,
            "completed_at": utc_now_iso(),
        },
    )
    text = json.dumps(output, ensure_ascii=False, indent=2, default=str)
    if args.output_file:
        Path(args.output_file).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
