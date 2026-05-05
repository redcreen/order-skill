#!/usr/bin/env python3
"""GPT-5.5 short-input extraction plus lazy guided-intake runtime test.

This is the end-to-end layer above test_order_lazy_guided_intake_50.py:

1. GPT-5.5 extracts field updates from short, incomplete Chinese user turns.
2. The guided-intake runtime stores every source turn and keeps incomplete work
   in draft/checkpoint state.
3. Formal rows are created only after the extracted confirmation turn is
   validated and confirmed.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import test_order_lazy_guided_intake_50 as lazy  # noqa: E402


DEFAULT_MODEL = "openai-codex/gpt-5.5"
LLM_FIELD_SCHEMA = [
    "customer_name",
    "product_name",
    "spec",
    "qty",
    "unit_price",
    "promised_delivery_date",
    "factory_name",
    "warehouse_name",
    "flow_name",
    "process_flow_confirmed",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run GPT-5.5 extraction in front of lazy guided-intake runtime tests.")
    parser.add_argument("--case-count", type=int, default=10)
    parser.add_argument("--seed", type=int, default=20260505)
    parser.add_argument("--batch-size", type=int, default=5)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--keep-data-root", action="store_true")
    parser.add_argument("--actor-label")
    parser.add_argument("--output-file")
    return parser.parse_args()


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


def normalize_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"true", "yes", "y", "1", "确认", "已确认", "confirm", "confirmed"}


def normalize_number(value: Any) -> float:
    if isinstance(value, str):
        value = value.replace(",", "").strip()
    return float(value)


def normalize_update(raw: dict[str, Any]) -> dict[str, Any]:
    updates: dict[str, Any] = {}
    for field in LLM_FIELD_SCHEMA:
        if field not in raw or raw[field] in (None, ""):
            continue
        if field == "qty":
            updates[field] = int(normalize_number(raw[field]))
        elif field == "unit_price":
            updates[field] = round(float(normalize_number(raw[field])), 2)
        elif field == "process_flow_confirmed":
            updates[field] = "yes" if normalize_bool(raw[field]) else "no"
        else:
            updates[field] = str(raw[field]).strip()
    return updates


def flow_catalog(cases: list[lazy.LazyCase]) -> list[dict[str, Any]]:
    return [
        {
            "product_name": case.product_name,
            "spec": case.spec_text,
            "default_flow_name": case.flow_name,
            "default_process_steps": list(case.process_steps),
            "default_process_labels": [lazy.work_type_for_step(step) for step in case.process_steps],
        }
        for case in cases
    ]


def candidate_catalog(cases: list[lazy.LazyCase]) -> dict[str, list[str]]:
    return {
        "customers": sorted({case.customer_name for case in cases}),
        "products": sorted({case.product_name for case in cases}),
        "specs": sorted({case.spec_text for case in cases}),
        "factories": sorted({case.factory_name for case in cases}),
        "warehouses": sorted({case.warehouse_name for case in cases}),
        "flows": sorted({case.flow_name for case in cases}),
    }


def turn_payloads(cases: list[lazy.LazyCase]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for case in cases:
        payloads.extend(
            [
                {
                    "turn_key": f"{case.order_no}-T1",
                    "case_index": case.index,
                    "turn_number": 1,
                    "text": case.lazy_turns[0],
                    "previous_turns": [],
                },
                {
                    "turn_key": f"{case.order_no}-T2",
                    "case_index": case.index,
                    "turn_number": 2,
                    "text": case.lazy_turns[1],
                    "previous_turns": [case.lazy_turns[0]],
                },
                {
                    "turn_key": f"{case.order_no}-T3",
                    "case_index": case.index,
                    "turn_number": 3,
                    "text": case.lazy_turns[2],
                    "previous_turns": [case.lazy_turns[0], case.lazy_turns[1]],
                },
            ]
        )
    return payloads


def build_prompt(batch_cases: list[lazy.LazyCase]) -> str:
    schema = {
        "turns": [
            {
                "turn_key": "<same as input>",
                "field_updates": {field: None for field in LLM_FIELD_SCHEMA},
                "needs_more_info": True,
                "confidence": 0.0,
            }
        ]
    }
    return "\n\n".join(
        [
            "你是 order 懒人输入抽取器。不要调用工具，不要写数据库，只输出合法 JSON。",
            "用户输入非常短、不完整、口语化。你的任务是从本轮文本、previous_turns 和候选主数据中抽取字段增量。",
            "previous_turns 只是同一个会话的前序原始短句，不是标准答案；不能凭空补字段。",
            "如果本轮是在确认系统建议流程，可以结合 previous_turns 和 product_flow_templates 输出 flow_name 与 process_flow_confirmed。",
            "如果文本出现“仓库X”“发X”“到X仓”“入X”，且 X 在候选仓库里，必须输出 warehouse_name；例如“仓库客户仓”必须输出 {\"warehouse_name\":\"客户仓\"}。",
            "第三轮经常同时包含交期、流程确认、仓库/发货去向，这三个都要抽取，漏掉任何一个都算失败。",
            "不要补全 order_no、deposit_amount、total_amount、process_steps、order_status 这类系统派生字段。",
            "字段名只能使用：",
            json.dumps(LLM_FIELD_SCHEMA, ensure_ascii=False),
            "候选主数据如下，用于解决无分隔文本和简称匹配，例如“王总01小兔子0187个”：",
            json.dumps(candidate_catalog(batch_cases), ensure_ascii=False, indent=2),
            "产品流程模板如下；当用户说“按老模板”“按系统带的流程确认”时，用同一 product_name/spec 的模板名称作为 flow_name：",
            json.dumps({"product_flow_templates": flow_catalog(batch_cases)}, ensure_ascii=False, indent=2),
            "输出格式必须严格是：",
            json.dumps(schema, ensure_ascii=False, indent=2),
            "输入 turns 如下：",
            json.dumps({"turns": turn_payloads(batch_cases)}, ensure_ascii=False, indent=2),
        ]
    )


def run_openclaw_extract(
    *,
    prompt: str,
    batch_index: int,
    actor_label: str,
    model: str,
    timeout: int,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    session_id = f"{actor_label.lower()}-lazy-batch-{batch_index:02d}"
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
            f"OpenClaw lazy batch {batch_index} failed with code {completed.returncode}.\n"
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
    turns = parsed.get("turns")
    if not isinstance(turns, list):
        raise AssertionError(f"Batch {batch_index} output missing turns list: {parsed}")
    extracted: dict[str, dict[str, Any]] = {}
    for item in turns:
        if not isinstance(item, dict) or not item.get("turn_key"):
            raise AssertionError(f"Invalid extracted turn in batch {batch_index}: {item!r}")
        extracted[str(item["turn_key"])] = {
            "field_updates": normalize_update(dict(item.get("field_updates") or {})),
            "needs_more_info": bool(item.get("needs_more_info", True)),
            "confidence": item.get("confidence"),
        }
    return extracted, route


def expected_final_fields(case: lazy.LazyCase) -> dict[str, Any]:
    expected = {
        "customer_name": case.customer_name,
        "product_name": case.product_name,
        "spec": case.spec_text,
        "qty": case.qty,
        "unit_price": case.unit_price,
        "promised_delivery_date": lazy.DUE_DATE,
        "factory_name": case.factory_name,
        "flow_name": case.flow_name,
        "process_flow_confirmed": "yes",
    }
    if any(case.warehouse_name in turn for turn in case.lazy_turns):
        expected["warehouse_name"] = case.warehouse_name
    return expected


def compare_final_fields(case: lazy.LazyCase, collected: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for field, expected in expected_final_fields(case).items():
        if field not in collected:
            errors.append(f"missing {field}")
            continue
        actual = collected[field]
        if field in {"qty"}:
            if int(normalize_number(actual)) != int(expected):
                errors.append(f"{field} expected {expected!r}, got {actual!r}")
        elif field == "unit_price":
            if abs(float(normalize_number(actual)) - float(expected)) > lazy.EPSILON:
                errors.append(f"{field} expected {expected!r}, got {actual!r}")
        elif field == "process_flow_confirmed":
            if normalize_update({field: actual}).get(field) != "yes":
                errors.append(f"{field} expected yes, got {actual!r}")
        elif str(actual) != str(expected):
            errors.append(f"{field} expected {expected!r}, got {actual!r}")
    return errors


def apply_system_derivations(case: lazy.LazyCase, collected: dict[str, Any]) -> None:
    if normalize_update({"process_flow_confirmed": collected.get("process_flow_confirmed")}).get("process_flow_confirmed") != "yes":
        return
    collected["process_flow_confirmed"] = "yes"
    collected.setdefault("order_no", case.order_no)
    collected.setdefault("flow_name", case.flow_name)
    collected.setdefault("process_steps", " > ".join(case.process_steps))
    collected.setdefault("warehouse_name", case.warehouse_name)
    collected.setdefault("deposit_amount", case.deposit_amount)
    collected.setdefault("confirmed_total_amount", case.total_amount)
    collected.setdefault("order_status", "in_production")
    collected.setdefault("current_step", collected.get("flow_name", case.flow_name))
    collected.setdefault("current_factory", collected.get("factory_name", case.factory_name))
    collected.setdefault("notes", "llm lazy guided intake confirmed by user")


def run_case_with_extraction(
    *,
    harness: lazy.LazyHarness,
    case: lazy.LazyCase,
    extracted_turns: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    session_key = f"llm-lazy-case-{case.index:03d}"
    collected: dict[str, Any] = {}

    first = harness.persist(session_key=session_key, msg_id=f"case-{case.index:03d}-turn-1", text=case.lazy_turns[0])
    collected.update(extracted_turns[f"{case.order_no}-T1"]["field_updates"])
    first_draft = harness.open_draft(
        inbox_item_id=first["inbox_item_id"],
        summary_text=f"{case.product_name} GPT-5.5 短句抽取后进入懒人引导。",
        fields=collected,
    )
    lazy.expect(first_draft["draft_status"] == "collecting", f"First draft should collect: {first_draft}")
    lazy.expect(first_draft["missing_required_fields"], f"First turn should have missing fields: {first_draft}")
    not_ready = harness.prepare(str(first_draft["workflow_draft_id"]))
    lazy.expect(not_ready["commit_ready"] is False, f"Incomplete draft should not be ready: {not_ready}")
    blocked = harness.commit(str(first_draft["workflow_draft_id"]), "confirm-fake", expect_ok=False)
    lazy.expect(blocked["status"] == "blocked", f"Incomplete commit should be blocked: {blocked}")

    second = harness.persist(session_key=session_key, msg_id=f"case-{case.index:03d}-turn-2", text=case.lazy_turns[1])
    collected.update(extracted_turns[f"{case.order_no}-T2"]["field_updates"])
    second_draft = harness.open_draft(
        inbox_item_id=second["inbox_item_id"],
        summary_text=f"{case.product_name} GPT-5.5 已补充字段，仍需流程确认。",
        fields=collected,
    )
    lazy.expect(second_draft["workflow_draft_id"] == first_draft["workflow_draft_id"], "Second turn should update same draft.")
    lazy.expect(
        "process_flow_confirmed" in second_draft["missing_required_fields"],
        f"Second turn should still require process confirmation: {second_draft}",
    )

    final = harness.persist(session_key=session_key, msg_id=f"case-{case.index:03d}-turn-3", text=case.lazy_turns[2])
    collected.update(extracted_turns[f"{case.order_no}-T3"]["field_updates"])
    extraction_errors = compare_final_fields(case, collected)
    if extraction_errors:
        raise AssertionError({case.order_no: extraction_errors, "collected": collected, "turns": extracted_turns})
    apply_system_derivations(case, collected)
    final_draft = harness.open_draft(
        inbox_item_id=final["inbox_item_id"],
        summary_text=f"{case.product_name} GPT-5.5 抽到用户确认流程：{case.flow_name}。",
        fields=collected,
    )
    lazy.expect(final_draft["workflow_draft_id"] == first_draft["workflow_draft_id"], "Final turn should update same draft.")
    lazy.expect(final_draft["draft_status"] == "needs_confirmation", f"Final draft should need confirmation: {final_draft}")
    lazy.expect(final_draft["missing_required_fields"] == [], f"Final draft should have no missing fields: {final_draft}")
    committed_order = lazy.prepare_and_commit(harness, str(final_draft["workflow_draft_id"]))
    sales_order_id = int(committed_order["committed_object"]["object_id"])
    work_order_ids = lazy.commit_work_orders(harness, case, sales_order_id, session_key)
    row_state = lazy.validate_case_rows(harness.db_path, case)
    return {
        "case_index": case.index,
        "lazy_turn_lengths": [len(text) for text in case.lazy_turns],
        "extracted_final_fields": {field: collected.get(field) for field in expected_final_fields(case)},
        "first_missing_required_fields": first_draft["missing_required_fields"],
        "second_missing_required_fields": second_draft["missing_required_fields"],
        "sales_order_id": sales_order_id,
        "work_order_ids": work_order_ids,
        "row_state": row_state,
    }


def main() -> int:
    args = parse_args()
    if args.case_count <= 0:
        raise SystemExit("--case-count must be positive.")
    if args.batch_size <= 0:
        raise SystemExit("--batch-size must be positive.")

    actor_label = args.actor_label or f"LLM-LAZY-{utc_label()}"
    cases = lazy.make_cases(args.case_count, args.seed)
    input_shape = lazy.validate_lazy_input_shape(cases)
    extracted_turns: dict[str, dict[str, Any]] = {}
    routes: list[dict[str, Any]] = []
    for batch_index, start in enumerate(range(0, len(cases), args.batch_size), start=1):
        batch_cases = cases[start : start + args.batch_size]
        batch_extracted, route = run_openclaw_extract(
            prompt=build_prompt(batch_cases),
            batch_index=batch_index,
            actor_label=actor_label,
            model=args.model,
            timeout=args.timeout,
        )
        expected_keys = {payload["turn_key"] for payload in turn_payloads(batch_cases)}
        missing = sorted(expected_keys - set(batch_extracted))
        extra = sorted(set(batch_extracted) - expected_keys)
        if missing or extra:
            raise AssertionError({"batch_index": batch_index, "missing_turns": missing, "extra_turns": extra})
        extracted_turns.update(batch_extracted)
        routes.append(route)

    data_root = Path(tempfile.mkdtemp(prefix="order-llm-lazy-guided-data-"))
    cleanup = {"data_root": str(data_root), "data_root_removed": False}
    output: dict[str, Any] | None = None
    try:
        lazy.initialize_runtime(data_root)
        harness = lazy.LazyHarness(data_root=data_root, actor_label=actor_label)
        case_results = [run_case_with_extraction(harness=harness, case=case, extracted_turns=extracted_turns) for case in cases]
        counts = lazy.table_counts(harness.db_path, actor_label)
        expected_work_orders = sum(len(case.process_steps) for case in cases)
        lazy.expect(counts["sales_orders"] == args.case_count, f"Sales order count wrong: {counts}")
        lazy.expect(counts["work_orders"] == expected_work_orders, f"Work order count wrong: {counts}")
        lazy.expect(counts["open_drafts"] == 0, f"Open drafts remain: {counts}")
        if not args.keep_data_root and data_root.exists():
            shutil.rmtree(data_root)
            cleanup["data_root_removed"] = True
        output = {
            "status": "ok",
            "actor_label": actor_label,
            "model_required": args.model,
            "case_count": args.case_count,
            "batch_size": args.batch_size,
            "input_shape": input_shape,
            "llm": {
                "turn_count": len(extracted_turns),
                "routes": routes,
                "all_batches_gpt55": all(
                    route["provider"] == "openai-codex" and route["model"] == "gpt-5.5" and route["fallback_used"] is False
                    for route in routes
                ),
                "exact_final_field_case_count": len(case_results),
                "validated_required_fields_per_case": [
                    "customer_name",
                    "factory_name",
                    "flow_name",
                    "process_flow_confirmed",
                    "product_name",
                    "promised_delivery_date",
                    "qty",
                    "spec",
                    "unit_price",
                ],
                "validated_optional_fields_when_present": ["warehouse_name"],
            },
            "counts": counts,
            "expected": {
                "sales_orders": args.case_count,
                "work_orders": expected_work_orders,
                "turns_per_case": 3,
            },
            "coverage": {
                "gpt55_short_turn_extraction": len(extracted_turns),
                "draft_blocks_before_required_fields": args.case_count,
                "process_confirmation_required": args.case_count,
                "confirmed_then_committed": args.case_count,
                "work_order_sequence_checked": expected_work_orders,
            },
            "case_results": case_results,
            "cleanup": cleanup,
        }
        if args.output_file:
            Path(args.output_file).parent.mkdir(parents=True, exist_ok=True)
            Path(args.output_file).write_text(json.dumps(output, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
        print(json.dumps(output, ensure_ascii=False, indent=2, default=str))
        return 0
    finally:
        if not args.keep_data_root and data_root.exists():
            shutil.rmtree(data_root)
            cleanup["data_root_removed"] = True
            if output is not None and args.output_file:
                Path(args.output_file).write_text(json.dumps(output, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
