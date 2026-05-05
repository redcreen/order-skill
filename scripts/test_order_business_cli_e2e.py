#!/usr/bin/env python3
"""Business-scenario CLI E2E test for the installed OpenClaw order plugin.

This test intentionally drives the hard-execution CLI wrapper instead of
importing runtime functions, then verifies the SQLite state after each critical
business transition.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WRAPPER = (
    REPO_ROOT / "plugins" / "openclaw-order" / "scripts" / "order_hard_execute.py"
)
AS_OF_DATE = "2026-05-04"
SESSION_MAIN = "cli-business-e2e:main"
SESSION_INCOMPLETE = "cli-business-e2e:incomplete"
EPSILON = 1e-6


class CheckRecorder:
    def __init__(self) -> None:
        self.items: list[dict[str, str]] = []

    def ok(self, name: str, detail: str = "") -> None:
        self.items.append({"status": "ok", "name": name, "detail": detail})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a comprehensive order business CLI E2E scenario.")
    parser.add_argument("--agent", default="order", help="Bound OpenClaw agent id.")
    parser.add_argument("--wrapper", default=str(DEFAULT_WRAPPER), help="Path to order_hard_execute.py.")
    parser.add_argument("--data-root", help="Temporary order data root. Defaults to a new /tmp directory.")
    parser.add_argument("--keep-data-root", action="store_true", help="Document that the data root should be kept.")
    return parser.parse_args()


def fail(message: str) -> None:
    raise AssertionError(message)


def expect(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def load_json(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"Command did not return JSON.\n{text}") from exc
    expect(isinstance(value, dict), "Expected JSON object output.")
    return value


class CliHarness:
    def __init__(self, *, wrapper: Path, agent: str, data_root: Path, workspace: Path) -> None:
        self.wrapper = wrapper
        self.agent = agent
        self.data_root = data_root
        self.workspace = workspace
        self.payload_index = 0

    @property
    def db_path(self) -> Path:
        return self.data_root / "db" / "order.db"

    def run(self, *args: str, expect_ok: bool = True, parse_json: bool = True) -> dict[str, Any]:
        command = [sys.executable, str(self.wrapper), *args]
        completed = subprocess.run(
            command,
            cwd=str(REPO_ROOT),
            text=True,
            capture_output=True,
        )
        if expect_ok and completed.returncode != 0:
            raise AssertionError(
                "CLI command failed unexpectedly:\n"
                f"COMMAND: {' '.join(command)}\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}"
            )
        if not expect_ok and completed.returncode == 0:
            raise AssertionError(
                "CLI command unexpectedly succeeded:\n"
                f"COMMAND: {' '.join(command)}\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}"
            )
        if not parse_json:
            return {
                "returncode": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
                "command": command,
            }
        payload = load_json(completed.stdout)
        if expect_ok and "command" in payload:
            expect(payload.get("status") == "ok", f"Expected status=ok, got {payload}")
        elif not expect_ok and "command" in payload:
            expect(payload.get("status") == "error", f"Expected status=error, got {payload}")
        return payload

    def runtime(self, subcommand: str, *args: str, expect_ok: bool = True) -> dict[str, Any]:
        return self.run(
            subcommand,
            "--agent",
            self.agent,
            "--data-root",
            str(self.data_root),
            *args,
            expect_ok=expect_ok,
        )

    def payload_path(self, name: str, payload: dict[str, Any]) -> Path:
        self.payload_index += 1
        path = self.workspace / f"{self.payload_index:03d}-{name}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def payload_runtime(self, subcommand: str, name: str, payload: dict[str, Any], *, expect_ok: bool = True) -> dict[str, Any]:
        path = self.payload_path(name, payload)
        return self.runtime(subcommand, "--payload-file", str(path), expect_ok=expect_ok)

    def persist(
        self,
        *,
        msg_id: str,
        text: str,
        session_key: str = SESSION_MAIN,
        raw_payload: dict[str, Any] | None = None,
        attachments: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "text": text,
            "channel_type": "cli-business-test",
            "channel_session_key": session_key,
            "source_actor": "order-business-cli-e2e",
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
            "actor_label": "order-business-cli-e2e",
        }
        return self.payload_runtime("open-draft", name, payload)["result"]

    def prepare(self, workflow_draft_id: str) -> dict[str, Any]:
        return self.runtime("prepare-confirmation", "--workflow-draft-id", workflow_draft_id)["result"]

    def commit(self, workflow_draft_id: str, confirm_token: str, *, expect_ok: bool = True) -> dict[str, Any]:
        payload = self.runtime(
            "commit-draft",
            "--workflow-draft-id",
            workflow_draft_id,
            "--confirm-token",
            confirm_token,
            expect_ok=expect_ok,
        )
        return payload["result"] if expect_ok else payload

    def prepare_and_commit(self, workflow_draft_id: str, *, wrong_token_check: bool = False) -> dict[str, Any]:
        prepared = self.prepare(workflow_draft_id)
        expect(prepared["commit_ready"] is True, f"Draft should be commit-ready: {prepared}")
        token = prepared["confirmation"]["confirm_token"]
        wrong_token_result = None
        if wrong_token_check:
            wrong_token_result = self.commit(workflow_draft_id, "confirm-wrong-token", expect_ok=False)
            expect(
                "Confirmation token mismatch" in wrong_token_result["error"]["message"],
                f"Wrong token should be rejected: {wrong_token_result}",
            )
        committed = self.commit(workflow_draft_id, token)
        return {"confirmation": prepared, "committed": committed, "wrong_token_result": wrong_token_result}

    def resolve_association(self, *, pending_association_id: str, target_key: str, reason_text: str) -> dict[str, Any]:
        payload = {
            "pending_association_id": pending_association_id,
            "target_key": target_key,
            "reason_text": reason_text,
            "actor_label": "order-business-cli-e2e",
            "thread": {
                "object_type": "sales_order",
                "object_key": target_key,
                "title": f"sales_order:{target_key}",
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
            "actor_label": "order-business-cli-e2e",
        }
        if not expect_ok:
            dry_run_payload = dict(payload)
            dry_run_payload["dry_run"] = True
            return self.payload_runtime("allocate", "allocate-dry-run-negative", dry_run_payload, expect_ok=False)
        direct = self.payload_runtime("allocate", "allocate-direct-blocked", payload, expect_ok=False)
        expect("confirmation token" in direct["error"]["message"], f"Direct allocation should be blocked: {direct}")
        dry_run_payload = dict(payload)
        dry_run_payload["dry_run"] = True
        preview = self.payload_runtime("allocate", "allocate-dry-run", dry_run_payload)["result"]
        confirmed_payload = dict(payload)
        confirmed_payload["confirm_token"] = preview["confirmation"]["confirm_token"]
        result = self.payload_runtime("allocate", "allocate-confirmed", confirmed_payload)
        return result["result"]

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection


def binding_path_for_wrapper(wrapper: Path) -> Path:
    return wrapper.parents[1] / ".codex-plugin" / "agent-binding.json"


def read_binding_state(wrapper: Path) -> dict[str, Any]:
    binding_path = binding_path_for_wrapper(wrapper)
    if not binding_path.exists():
        return {
            "installationScope": "explicit_agent_only",
            "autoInstall": False,
            "status": "unbound",
            "targetAgent": "",
            "notes": "Bind this plugin to one specific agent before running order execution commands.",
        }
    return json.loads(binding_path.read_text(encoding="utf-8"))


def write_binding_state(wrapper: Path, state: dict[str, Any]) -> None:
    binding_path = binding_path_for_wrapper(wrapper)
    binding_path.parent.mkdir(parents=True, exist_ok=True)
    binding_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any]:
    return dict(row) if row is not None else {}


def one(connection: sqlite3.Connection, query: str, params: tuple[Any, ...] = ()) -> dict[str, Any]:
    return row_to_dict(connection.execute(query, params).fetchone())


def all_rows(connection: sqlite3.Connection, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in connection.execute(query, params).fetchall()]


def count_rows(connection: sqlite3.Connection, table: str) -> int:
    return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def pending_association_for(connection: sqlite3.Connection, inbox_item_id: str, target_type: str = "sales_order") -> str:
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
    expect(row is not None, f"Expected pending association for {inbox_item_id}/{target_type}.")
    return str(row["pending_association_id"])


def commit_sales_order(harness: CliHarness, *, order: dict[str, Any], msg_id: str, text: str) -> int:
    persisted = harness.persist(msg_id=msg_id, text=text)
    draft = harness.open_draft(
        inbox_item_id=persisted["inbox_item_id"],
        intent_type="sales_order",
        target_object_type="sales_order",
        target_action=order.pop("target_action", "create"),
        summary_text=text,
        fields=order,
        candidate_links=order.pop("candidate_links", None) if "candidate_links" in order else None,
        name=f"sales-order-{msg_id}",
    )
    committed = harness.prepare_and_commit(str(draft["workflow_draft_id"]), wrong_token_check=(msg_id == "msg-so-001"))
    return int(committed["committed"]["committed_object"]["object_id"])


def commit_linked_object(
    harness: CliHarness,
    *,
    msg_id: str,
    text: str,
    intent_type: str,
    target_object_type: str,
    fields: dict[str, Any],
    sales_order_id: int,
    name: str,
) -> int:
    persisted = harness.persist(msg_id=msg_id, text=text)
    draft = harness.open_draft(
        inbox_item_id=persisted["inbox_item_id"],
        intent_type=intent_type,
        target_object_type=target_object_type,
        summary_text=text,
        fields=fields,
        candidate_links=[{"target_type": "sales_order", "target_key": str(sales_order_id), "confidence_score": 1.0}],
        name=name,
    )
    committed = harness.prepare_and_commit(str(draft["workflow_draft_id"]))
    return int(committed["committed"]["committed_object"]["object_id"])


def run_scenario(harness: CliHarness, checks: CheckRecorder) -> dict[str, Any]:
    binding = harness.run("show-binding")
    if binding["status"] == "unbound":
        binding = harness.run("bind-agent", "--agent", harness.agent)
    expect(binding["status"] == "bound", f"Installed wrapper must be bound before testing: {binding}")
    expect(binding["targetAgent"] == harness.agent, f"Wrapper bound to unexpected agent: {binding}")
    checks.ok("wrapper is bound for this test run", json.dumps(binding, ensure_ascii=False))

    wrong_agent = harness.run("smoke-runtime", "--agent", "not-order", expect_ok=False, parse_json=False)
    expect("bound to agent" in wrong_agent["stderr"], f"Wrong-agent call should be rejected: {wrong_agent}")
    checks.ok("wrong agent is rejected", "runtime commands cannot run through another agent id")

    init_result = harness.runtime("init-runtime")["result"]
    expect(Path(init_result["db_path"]).exists(), "Runtime init should create SQLite db.")
    checks.ok("runtime initialized", init_result["db_path"])

    incomplete = harness.persist(
        msg_id="msg-incomplete-001",
        session_key=SESSION_INCOMPLETE,
        text="邓总那边小兔子先排 300 个，交期按 5 月 8 日，但是客户、工厂和对应订单还没说清楚。",
    )
    incomplete_draft = harness.open_draft(
        inbox_item_id=incomplete["inbox_item_id"],
        intent_type="production_arrangement",
        target_object_type="work_order",
        summary_text="小兔子 300 个平车排期信息不完整，需要补客户、工厂和订单关联。",
        fields={
            "product_name": "小兔子",
            "qty": 300,
            "work_type": "平车",
            "planned_qty": 300,
            "planned_due_at": "2026-05-08",
        },
        name="incomplete-production-arrangement",
    )
    expect(incomplete_draft["draft_status"] == "collecting", f"Incomplete draft should collect more info: {incomplete_draft}")
    expect(
        {"customer_name", "factory_name"}.issubset(set(incomplete_draft["missing_required_fields"])),
        f"Incomplete draft should identify missing fields: {incomplete_draft}",
    )
    incomplete_confirmation = harness.prepare(str(incomplete_draft["workflow_draft_id"]))
    expect(incomplete_confirmation["commit_ready"] is False, "Incomplete draft must not be commit-ready.")
    blocked_commit = harness.commit(str(incomplete_draft["workflow_draft_id"]), "confirm-fake", expect_ok=False)
    expect("open blockers" in blocked_commit["error"]["message"], f"Incomplete commit should be blocked: {blocked_commit}")
    checks.ok("incomplete natural-language input stays draft-only", "missing fields and association block commit")

    proof_file = harness.workspace / "wang-payment-proof.txt"
    proof_file.write_text("付款截图 OCR：王总 预付款 3750 小兔子 2026-05-04", encoding="utf-8")
    pre_order_payment = harness.persist(
        msg_id="msg-payment-before-order",
        text="王总发来一张付款截图，金额 3750，备注小兔子预付款，但暂时不知道具体订单号。",
        raw_payload={"evidence_kind": "payment_screenshot", "amount_hint": 3750, "counterparty_hint": "王总"},
        attachments=[
            {
                "path": str(proof_file),
                "mime_type": "text/plain",
                "extracted_text": "付款截图 OCR：王总 预付款 3750 小兔子 2026-05-04",
            }
        ],
    )
    expect(pre_order_payment["attachment_count"] == 1, "Payment screenshot evidence should be archived.")
    payment_draft = harness.open_draft(
        inbox_item_id=pre_order_payment["inbox_item_id"],
        intent_type="payment_receipt",
        target_object_type="cash_transaction",
        summary_text="王总预付款 3750，先记录为未关联订单的收款草稿。",
        fields={
            "direction": "收款",
            "counterparty_name": "王总",
            "amount": 3750,
            "transaction_date": AS_OF_DATE,
            "purpose": "预付款",
            "payment_method": "bank_transfer",
        },
        name="payment-before-order",
    )
    expect(payment_draft["draft_status"] == "collecting", "Payment before order should wait for sales_order association.")
    checks.ok("out-of-order payment is held pending association", payment_draft["workflow_draft_id"])

    sales_order_id = commit_sales_order(
        harness,
        msg_id="msg-so-001",
        text="王总确认小兔子 1000 个，18cm 粉色，单价 12.5，30% 定金，交期 5 月 3 日，定远乡冯杰开始平车。",
        order={
            "order_no": "SO-CLI-001",
            "order_date": "2026-05-04",
            "order_type": "customer_order",
            "customer_name": "王总",
            "product_name": "小兔子",
            "spec": "18cm 粉色",
            "qty": 1000,
            "unit": "个",
            "unit_price": 12.5,
            "promised_delivery_date": "2026-05-03",
            "deposit_rate": "30%",
            "deposit_amount": 3750,
            "factory_name": "定远乡工厂/冯杰",
            "current_step": "平车",
            "remark": "定金已收待关联，开始平车。",
            "processing_cost": 2500,
            "material_cost": 1800,
            "total_cost": 5300,
            "order_status": "in_production",
            "invoice_status": "pending",
            "invoice_amount": 0,
        },
    )
    checks.ok("customer sales order committed with strict token guard", f"sales_order_id={sales_order_id}")

    with harness.connect() as connection:
        order_row = one(connection, "SELECT * FROM sales_orders WHERE sales_order_id = ?", (sales_order_id,))
    expect(order_row["spec_text"] == "18cm 粉色", f"Alias spec should commit to spec_text: {order_row}")
    expect(abs(float(order_row["confirmed_unit_price"]) - 12.5) <= EPSILON, f"Alias unit_price should commit: {order_row}")
    expect(abs(float(order_row["confirmed_total_amount"]) - 12500) <= EPSILON, f"Total should derive from qty*unit_price: {order_row}")
    expect(abs(float(order_row["deposit_ratio"]) - 0.3) <= EPSILON, f"Percent deposit_rate should normalize: {order_row}")
    expect(abs(float(order_row["deposit_expected_amount"]) - 3750) <= EPSILON, f"Deposit expected should derive: {order_row}")
    expect(abs(float(order_row["deposit_received_amount"]) - 3750) <= EPSILON, f"Alias deposit_amount should commit: {order_row}")
    expect(order_row["current_factory"] == "定远乡工厂/冯杰", f"Alias factory_name should commit: {order_row}")
    checks.ok("LLM-style sales-order aliases normalize to canonical DB columns", f"sales_order_id={sales_order_id}")

    with harness.connect() as connection:
        pending_id = pending_association_for(connection, pre_order_payment["inbox_item_id"])
    resolve_result = harness.resolve_association(
        pending_association_id=pending_id,
        target_key=str(sales_order_id),
        reason_text="确认王总 3750 预付款对应 SO-CLI-001 小兔子。",
    )
    expect(resolve_result["status"] == "resolved", f"Pending association should resolve: {resolve_result}")
    payment_commit = harness.prepare_and_commit(str(payment_draft["workflow_draft_id"]))
    pre_order_cash_id = int(payment_commit["committed"]["committed_object"]["object_id"])
    checks.ok("pending payment resolved and committed", f"cash_transaction_id={pre_order_cash_id}")

    deposit_receivable_id = commit_linked_object(
        harness,
        msg_id="msg-ar-deposit",
        text="SO-CLI-001 的 30% 定金应收 3750，今天到期。",
        intent_type="receivable_record",
        target_object_type="receivable",
        fields={
            "receivable_no": "AR-CLI-001",
            "receivable_type": "deposit",
            "amount_due": 3750,
            "due_date": AS_OF_DATE,
            "collection_mode": "bank_transfer",
        },
        sales_order_id=sales_order_id,
        name="deposit-receivable",
    )
    deposit_allocation = harness.allocate(
        cash_transaction_id=pre_order_cash_id,
        allocations=[{"target_type": "receivable", "target_id": deposit_receivable_id, "allocated_amount": 3750}],
        require_full_amount=True,
    )
    expect(deposit_allocation["targets"][0]["status"] == "received", "Deposit receivable should be received.")
    checks.ok("deposit receipt allocated to receivable", json.dumps(deposit_allocation, ensure_ascii=False))

    tail_receivable_id = commit_linked_object(
        harness,
        msg_id="msg-ar-tail",
        text="SO-CLI-001 尾款应收 8750，客户发货后 5 月 10 日前付。",
        intent_type="receivable_record",
        target_object_type="receivable",
        fields={
            "receivable_no": "AR-CLI-002",
            "receivable_type": "tail",
            "amount_due": 8750,
            "due_date": "2026-05-10",
            "collection_mode": "bank_transfer",
        },
        sales_order_id=sales_order_id,
        name="tail-receivable",
    )

    payables: dict[str, int] = {}
    payable_specs = [
        ("AP-CLI-001", "弘辉复合", "composite", 420, AS_OF_DATE, "弘辉复合做小兔子布料复合，应付 420，今天到期。"),
        ("AP-CLI-002", "刘旭", "laser_cut", 680, "2026-05-05", "刘旭激光下料小兔子裁片，应付 680，明天到期。"),
        ("AP-CLI-003", "朱昌良", "embroidery", 300, "2026-05-07", "朱师傅刺绣眼睛鼻子，应付 300。"),
        ("AP-CLI-004", "冯杰", "processing", 2500, AS_OF_DATE, "冯杰平车加工费先记 2500，今天需要跟进付款。"),
        ("AP-CLI-005", "冯杰", "repair", 150, "2026-05-06", "客户退回 20 个需要返修，先记冯杰返修费用 150。"),
    ]
    for payable_no, supplier_name, payable_type, amount, due_date, text in payable_specs:
        payables[payable_no] = commit_linked_object(
            harness,
            msg_id=f"msg-{payable_no.lower()}",
            text=text,
            intent_type="payable_record",
            target_object_type="payable",
            fields={
                "payable_no": payable_no,
                "supplier_name": supplier_name,
                "payable_type": payable_type,
                "amount_due": amount,
                "due_date": due_date,
                "billing_mode": "per_order",
            },
            sales_order_id=sales_order_id,
            name=payable_no,
        )
    checks.ok("multi-supplier payables committed", json.dumps(payables, ensure_ascii=False))

    payout_id = commit_linked_object(
        harness,
        msg_id="msg-payout",
        text="今天付给供应商组 1100，其中弘辉 420，刘旭 680。",
        intent_type="cash_transaction_record",
        target_object_type="cash_transaction",
        fields={
            "direction": "付款",
            "counterparty_name": "供应商组",
            "amount": 1100,
            "transaction_date": AS_OF_DATE,
            "purpose": "复合和激光下料",
            "payment_method": "bank_transfer",
        },
        sales_order_id=sales_order_id,
        name="supplier-payout",
    )
    payout_allocation = harness.allocate(
        cash_transaction_id=payout_id,
        allocations=[
            {"target_type": "payable", "target_id": payables["AP-CLI-001"], "allocated_amount": 420},
            {"target_type": "payable", "target_id": payables["AP-CLI-002"], "allocated_amount": 680},
        ],
        require_full_amount=True,
    )
    expect({item["status"] for item in payout_allocation["targets"]} == {"paid"}, "Allocated payables should be paid.")
    checks.ok("one payout allocated across multiple suppliers", json.dumps(payout_allocation, ensure_ascii=False))

    invalid_allocation = harness.allocate(
        cash_transaction_id=pre_order_cash_id,
        allocations=[{"target_type": "payable", "target_id": payables["AP-CLI-003"], "allocated_amount": 10}],
        expect_ok=False,
    )
    expect("cannot allocate to payable" in invalid_allocation["error"]["message"], "Receipt must not allocate to payable.")
    checks.ok("settlement direction guard works", invalid_allocation["error"]["message"])

    partial_tail_receipt_id = commit_linked_object(
        harness,
        msg_id="msg-tail-receipt",
        text="王总今天又转 2000 尾款，先冲 SO-CLI-001 尾款。",
        intent_type="cash_transaction_record",
        target_object_type="cash_transaction",
        fields={
            "direction": "收款",
            "counterparty_name": "王总",
            "amount": 2000,
            "transaction_date": AS_OF_DATE,
            "purpose": "尾款部分收款",
            "payment_method": "bank_transfer",
        },
        sales_order_id=sales_order_id,
        name="partial-tail-receipt",
    )
    tail_allocation = harness.allocate(
        cash_transaction_id=partial_tail_receipt_id,
        allocations=[{"target_type": "receivable", "target_id": tail_receivable_id, "allocated_amount": 2000}],
        require_full_amount=True,
    )
    expect(tail_allocation["targets"][0]["status"] == "partial", "Tail receivable should be partial.")

    work_specs = [
        ("WO-CLI-001", "打样", "跟单内部", 1, "2026-05-01", "done", "小兔子打样已完成。"),
        ("WO-CLI-002", "平车", "冯杰", 1000, AS_OF_DATE, "planned", "冯杰今天要完成小兔子平车 1000 个。"),
        ("WO-CLI-003", "充棉", "张时库", 1000, "2026-05-05", "planned", "潢川张时库明天开始充棉。"),
        ("WO-CLI-004", "手工封口", "定远乡工厂", 1000, "2026-05-06", "planned", "手工封口 5 月 6 日要跟进。"),
    ]
    work_orders: dict[str, int] = {}
    for work_no, work_type, provider_name, qty, due_at, status, text in work_specs:
        work_orders[work_no] = commit_linked_object(
            harness,
            msg_id=f"msg-{work_no.lower()}",
            text=text,
            intent_type="work_order_record",
            target_object_type="work_order",
            fields={
                "work_order_no": work_no,
                "work_type": work_type,
                "provider_name": provider_name,
                "planned_qty": qty,
                "planned_due_at": due_at,
                "work_status": status,
            },
            sales_order_id=sales_order_id,
            name=work_no,
        )
    checks.ok("sample and production work orders committed", json.dumps(work_orders, ensure_ascii=False))

    shipment_ids: dict[str, int] = {}
    shipment_ids["cut_pieces"] = commit_linked_object(
        harness,
        msg_id="msg-ship-cut",
        text="裁片和眼睛鼻子配件已叫货拉拉送到物流点，通知冯杰去取，物流单 HL-001。",
        intent_type="shipment",
        target_object_type="shipment",
        fields={
            "shipment_date": AS_OF_DATE,
            "shipment_type": "cut_pieces_to_factory",
            "factory_name": "定远乡工厂/冯杰",
            "cut_detail": "小兔子裁片+眼睛鼻子配件，物流单 HL-001",
            "cut_qty": 1000,
            "shipment_status": "sent",
        },
        sales_order_id=sales_order_id,
        name="cut-pieces-shipment",
    )
    shipment_ids["customer_delivery"] = commit_linked_object(
        harness,
        msg_id="msg-ship-customer",
        text="今天先从工厂直接发 400 个小兔子给王总，剩余继续做。",
        intent_type="shipment",
        target_object_type="shipment",
        fields={
            "shipment_date": AS_OF_DATE,
            "shipment_type": "customer_delivery",
            "factory_name": "定远乡工厂/冯杰",
            "finished_qty": 400,
            "shipment_status": "shipped",
        },
        sales_order_id=sales_order_id,
        name="customer-delivery-shipment",
    )
    checks.ok("cut-piece logistics and customer delivery shipments committed", json.dumps(shipment_ids, ensure_ascii=False))

    ecommerce_order_id = commit_sales_order(
        harness,
        msg_id="msg-so-002",
        text="自营电商白熊 300 个，义乌赵总车缝，徐凯充棉手工，回义乌云仓入库。",
        order={
            "order_no": "SO-CLI-002",
            "order_date": "2026-05-04",
            "order_type": "ecommerce_self_sale",
            "customer_name": "自营电商",
            "product_name": "白熊",
            "spec_text": "20cm 白色",
            "qty": 300,
            "unit": "个",
            "confirmed_unit_price": 18,
            "confirmed_total_amount": 5400,
            "promised_delivery_date": "2026-05-06",
            "current_factory": "义乌赵总/徐凯",
            "current_step": "待回义乌云仓",
            "progress_text": "完成后发义乌云仓入库。",
            "order_status": "in_production",
        },
    )
    commit_linked_object(
        harness,
        msg_id="msg-ship-warehouse",
        text="白熊 300 个发回义乌物流点，货到后叫货拉拉送云仓入库。",
        intent_type="shipment",
        target_object_type="shipment",
        fields={
            "shipment_date": "2026-05-05",
            "shipment_type": "warehouse_receipt",
            "factory_name": "义乌赵总/徐凯",
            "finished_qty": 300,
            "shipment_status": "in_transit",
            "notes": "ERP 入库单待仓库确认。",
        },
        sales_order_id=ecommerce_order_id,
        name="warehouse-shipment",
    )
    checks.ok("ecommerce self-sale and warehouse inbound flow committed", f"sales_order_id={ecommerce_order_id}")

    return_case_id = commit_linked_object(
        harness,
        msg_id="msg-return-case",
        text="王总退回 20 个小兔子，要退款 240；冯杰这批可能扣款 120，同时要安排返修。",
        intent_type="return_case",
        target_object_type="return_case",
        fields={
            "case_type": "repair_return",
            "opened_at": AS_OF_DATE,
            "customer_name": "王总",
            "refund_expected_amount": 240,
            "supplier_deduction_expected_amount": 120,
            "reason_text": "客户退回 20 个，需要返修。",
            "notes": "退货、退款、扣款、返修需要串联。",
        },
        sales_order_id=sales_order_id,
        name="return-case",
    )
    refund_inbox = harness.persist(
        msg_id="msg-refund-record",
        text="王总退回的小兔子确认退款 240，先建退款账，等财务付款后平账。",
    )
    refund_draft = harness.open_draft(
        inbox_item_id=refund_inbox["inbox_item_id"],
        intent_type="refund_record",
        target_object_type="refund",
        summary_text="王总退货退款 240，关联小兔子订单和退货 case。",
        fields={"refund_amount": 240, "refund_status": "pending", "notes": "客户退货退款。"},
        candidate_links=[
            {"target_type": "sales_order", "target_key": str(sales_order_id), "confidence_score": 1.0},
            {"target_type": "return_case", "target_key": str(return_case_id), "confidence_score": 1.0},
        ],
        name="refund-record",
    )
    refund_commit = harness.prepare_and_commit(str(refund_draft["workflow_draft_id"]))
    refund_id = int(refund_commit["committed"]["committed_object"]["object_id"])
    refund_payout_id = commit_linked_object(
        harness,
        msg_id="msg-refund-payout",
        text="今天退给王总 240，冲小兔子退货退款。",
        intent_type="cash_transaction_record",
        target_object_type="cash_transaction",
        fields={
            "direction": "付款",
            "counterparty_name": "王总",
            "amount": 240,
            "transaction_date": AS_OF_DATE,
            "purpose": "客户退货退款",
            "payment_method": "bank_transfer",
        },
        sales_order_id=sales_order_id,
        name="refund-payout",
    )
    refund_allocation = harness.allocate(
        cash_transaction_id=refund_payout_id,
        allocations=[{"target_type": "refund", "target_id": refund_id, "allocated_amount": 240}],
        require_full_amount=True,
    )
    expect(refund_allocation["targets"][0]["status"] == "paid", f"Refund should be paid: {refund_allocation}")
    deduction_inbox = harness.persist(
        msg_id="msg-supplier-deduction",
        text="小兔子退货问题确认扣冯杰 120，关联平车作业和退货 case。",
    )
    deduction_draft = harness.open_draft(
        inbox_item_id=deduction_inbox["inbox_item_id"],
        intent_type="supplier_deduction_record",
        target_object_type="supplier_deduction",
        summary_text="冯杰因退货返修问题扣款 120。",
        fields={
            "supplier_name": "冯杰",
            "deduction_amount": 120,
            "deduction_reason": "小兔子退货返修质量问题扣款。",
            "deduction_status": "pending",
        },
        candidate_links=[
            {"target_type": "return_case", "target_key": str(return_case_id), "confidence_score": 1.0},
            {"target_type": "work_order", "target_key": str(work_orders["WO-CLI-002"]), "confidence_score": 1.0},
        ],
        name="supplier-deduction",
    )
    deduction_commit = harness.prepare_and_commit(str(deduction_draft["workflow_draft_id"]))
    deduction_id = int(deduction_commit["committed"]["committed_object"]["object_id"])
    checks.ok(
        "return, refund payout, and supplier deduction committed",
        json.dumps({"return_case_id": return_case_id, "refund_id": refund_id, "deduction_id": deduction_id}, ensure_ascii=False),
    )

    history_search = harness.runtime("history-search", "--query", "小兔子", "--limit", "20")["result"]
    expect(len(history_search["results"]) >= 8, "History search should find persisted 小兔子 inputs.")
    history_show = harness.runtime(
        "history-show",
        "--source-message-id",
        "msg-payment-before-order",
        "--include-evidence-text",
    )["result"]
    expect(len(history_show["item"]["evidence_assets"]) == 1, "History show should include payment evidence.")
    history_replay = harness.runtime("history-replay", "--channel-session-key", SESSION_MAIN, "--limit", "100")["result"]
    expect(len(history_replay["items"]) >= 10, "History replay should preserve conversational continuity.")
    checks.ok("history search/show/replay works with evidence", f"history_items={len(history_replay['items'])}")

    control_tower = harness.runtime("refresh-control-tower", "--as-of-date", AS_OF_DATE)["result"]
    expect(control_tower["exception_count"] >= 1, "Overdue order should produce exception.")
    expect(control_tower["alert_count"] >= 2, "Due/overdue commitments should produce alerts.")
    report = harness.runtime("daily-report", "--report-date", AS_OF_DATE, "--skip-refresh")["result"]
    expect(report["report_json"]["orders_in_production"] >= 2, "Daily report should include production orders.")
    expect(report["report_json"]["receivable_open_amount"] == 6750.0, f"Unexpected open receivable: {report}")
    expect(report["report_json"]["payable_open_amount"] == 2950.0, f"Unexpected open payable: {report}")
    checks.ok("control tower and daily report generated with action suggestions", report["report_body"].splitlines()[0])

    with harness.connect() as connection:
        row_counts = {
            table: count_rows(connection, table)
            for table in [
                "inbox_items",
                "evidence_assets",
                "workflow_drafts",
                "sales_orders",
                "receivables",
                "payables",
                "cash_transactions",
                "settlement_allocations",
                "work_orders",
                "shipments",
                "return_cases",
                "refunds",
                "supplier_deductions",
                "daily_reports",
                "audit_log",
            ]
        }
        key_state = {
            "sales_order_so_cli_001": one(
                connection,
                """
                SELECT sales_order_id, order_no, customer_name, product_name, qty,
                       promised_delivery_date, current_factory, current_step, order_status
                FROM sales_orders WHERE order_no = 'SO-CLI-001'
                """,
            ),
            "receivables": all_rows(
                connection,
                "SELECT receivable_no, receivable_type, amount_due, amount_received, receivable_status FROM receivables ORDER BY receivable_no",
            ),
            "payables": all_rows(
                connection,
                """
                SELECT payable_no, payable_type, amount_due, amount_paid, payable_status
                FROM payables ORDER BY payable_no
                """,
            ),
            "production_status": all_rows(
                connection,
                """
                SELECT order_no, customer_name, product_name, qty, promised_delivery_date,
                       current_factory, current_step
                FROM v_order_production_status ORDER BY order_no
                """,
            ),
            "finance_status": all_rows(
                connection,
                """
                SELECT order_no, confirmed_total_amount, payable_amount, cash_in_amount, cash_out_amount
                FROM v_order_finance_status ORDER BY order_no
                """,
            ),
            "profit_snapshot": all_rows(
                connection,
                """
                SELECT order_no, confirmed_total_amount, total_cost, payable_amount, estimated_gross_profit
                FROM v_order_profit_snapshot ORDER BY order_no
                """,
            ),
            "cash_forecast": all_rows(
                connection,
                """
                SELECT order_no, expected_cash_in, expected_cash_out
                FROM v_cash_forecast ORDER BY order_no
                """,
            ),
            "return_cases": all_rows(
                connection,
                """
                SELECT return_case_id, sales_order_id, case_type, refund_expected_amount,
                       supplier_deduction_expected_amount, case_status
                FROM return_cases ORDER BY return_case_id
                """,
            ),
            "refunds": all_rows(
                connection,
                "SELECT refund_id, return_case_id, sales_order_id, refund_amount, refund_status FROM refunds ORDER BY refund_id",
            ),
            "supplier_deductions": all_rows(
                connection,
                """
                SELECT supplier_deduction_id, return_case_id, work_order_id, deduction_amount, deduction_status
                FROM supplier_deductions ORDER BY supplier_deduction_id
                """,
            ),
            "open_followups": all_rows(
                connection,
                "SELECT followup_type, due_at, priority, notes FROM v_open_followups ORDER BY due_at, followup_type",
            ),
            "open_alerts": all_rows(
                connection,
                "SELECT alert_type, alert_text FROM v_open_alerts ORDER BY alert_id",
            ),
        }

    expect(row_counts["sales_orders"] == 2, f"Expected 2 sales orders: {row_counts}")
    expect(row_counts["receivables"] == 2, f"Expected 2 receivables: {row_counts}")
    expect(row_counts["payables"] == 5, f"Expected 5 payables: {row_counts}")
    expect(row_counts["cash_transactions"] == 4, f"Expected 4 cash transactions: {row_counts}")
    expect(row_counts["settlement_allocations"] == 5, f"Expected 5 settlement allocations: {row_counts}")
    expect(row_counts["work_orders"] == 4, f"Expected 4 work orders: {row_counts}")
    expect(row_counts["shipments"] == 3, f"Expected 3 shipments: {row_counts}")
    expect(row_counts["return_cases"] == 1, f"Expected 1 return case: {row_counts}")
    expect(row_counts["refunds"] == 1, f"Expected 1 refund: {row_counts}")
    expect(row_counts["supplier_deductions"] == 1, f"Expected 1 supplier deduction: {row_counts}")

    receivable_status = {row["receivable_no"]: row for row in key_state["receivables"]}
    expect(receivable_status["AR-CLI-001"]["receivable_status"] == "received", f"Deposit state wrong: {receivable_status}")
    expect(receivable_status["AR-CLI-002"]["receivable_status"] == "partial", f"Tail state wrong: {receivable_status}")
    expect(receivable_status["AR-CLI-002"]["amount_received"] == 2000.0, f"Tail allocation wrong: {receivable_status}")

    payable_status = {row["payable_no"]: row for row in key_state["payables"]}
    expect(payable_status["AP-CLI-001"]["payable_status"] == "paid", f"Payable AP-CLI-001 wrong: {payable_status}")
    expect(payable_status["AP-CLI-002"]["payable_status"] == "paid", f"Payable AP-CLI-002 wrong: {payable_status}")
    expect(payable_status["AP-CLI-004"]["payable_status"] == "pending", f"Payable AP-CLI-004 wrong: {payable_status}")

    finance_by_order = {row["order_no"]: row for row in key_state["finance_status"]}
    expect(finance_by_order["SO-CLI-001"]["cash_in_amount"] == 5750.0, f"Cash-in rollup wrong: {finance_by_order}")
    expect(finance_by_order["SO-CLI-001"]["cash_out_amount"] == 1340.0, f"Cash-out rollup wrong: {finance_by_order}")
    expect(finance_by_order["SO-CLI-001"]["payable_amount"] == 4290.0, f"Payable rollup wrong: {finance_by_order}")
    profit_by_order = {row["order_no"]: row for row in key_state["profit_snapshot"]}
    expect(
        profit_by_order["SO-CLI-001"]["estimated_gross_profit"] == 6960.0,
        f"Refund-adjusted profit wrong: {profit_by_order}",
    )

    forecast_by_order = {row["order_no"]: row for row in key_state["cash_forecast"]}
    expect(forecast_by_order["SO-CLI-001"]["expected_cash_in"] == 6750.0, f"Cash-in forecast wrong: {forecast_by_order}")
    expect(forecast_by_order["SO-CLI-001"]["expected_cash_out"] == 2950.0, f"Cash forecast wrong: {forecast_by_order}")
    expect(key_state["refunds"][0]["refund_status"] == "paid", f"Refund state wrong: {key_state['refunds']}")
    expect(key_state["supplier_deductions"][0]["deduction_status"] == "pending", f"Deduction state wrong: {key_state['supplier_deductions']}")
    expect(len(key_state["open_followups"]) >= 5, f"Expected actionable followups: {key_state['open_followups']}")
    expect(len(key_state["open_alerts"]) >= 2, f"Expected actionable alerts: {key_state['open_alerts']}")
    checks.ok("SQLite state and views verified", json.dumps(row_counts, ensure_ascii=False))

    return {
        "status": "ok",
        "data_root": str(harness.data_root),
        "checks": checks.items,
        "control_tower": control_tower,
        "daily_report_summary": report["report_json"],
        "row_counts": row_counts,
        "key_state": key_state,
        "implemented_extension": "return_case/refund/supplier_deduction commit targets and refund cash-out rollups are verified.",
    }


def main() -> int:
    args = parse_args()
    wrapper = Path(args.wrapper).expanduser().resolve()
    expect(wrapper.exists(), f"Missing wrapper: {wrapper}")
    data_root = Path(args.data_root).expanduser().resolve() if args.data_root else Path(tempfile.mkdtemp(prefix="order-business-cli-e2e-")) / "openclaw-order"
    workspace = Path(tempfile.mkdtemp(prefix="order-business-cli-payloads-"))
    checks = CheckRecorder()
    harness = CliHarness(wrapper=wrapper, agent=args.agent, data_root=data_root, workspace=workspace)
    original_binding = read_binding_state(wrapper)
    try:
        result = run_scenario(harness, checks)
        result["payload_workspace"] = str(workspace)
        result["keep_data_root"] = bool(args.keep_data_root or args.data_root)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        write_binding_state(wrapper, original_binding)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
