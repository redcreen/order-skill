#!/usr/bin/env python3
"""JSON command API for the local-first order runtime."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import traceback
import uuid
from pathlib import Path
from typing import Any, Callable

from history_common import replay_history, search_history, show_history_item
from runtime_common import initialize_runtime, open_guided_intake_draft, persist_input, resolve_data_root
from runtime_flow import (
    commit_workflow_draft,
    generate_daily_report,
    prepare_draft_confirmation,
    record_settlement_allocations,
    refresh_control_tower,
    resolve_pending_association_item,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
ORDER_SCRIPTS = REPO_ROOT / "order" / "scripts"

COMPAT_SCRIPT_MAP = {
    "history-backfill": ORDER_SCRIPTS / "open_history_backfill_draft.py",
    "association-candidates": ORDER_SCRIPTS / "list_pending_association_candidates.py",
    "resolve-pending": ORDER_SCRIPTS / "resolve_pending_association_direct.py",
    "backfill-queue": ORDER_SCRIPTS / "list_history_backfill_queue.py",
    "backfill-ready": ORDER_SCRIPTS / "list_history_backfill_ready.py",
    "backfill-finalize": ORDER_SCRIPTS / "finalize_history_backfill.py",
    "smoke-runtime": ORDER_SCRIPTS / "smoke_order_runtime.py",
    "smoke-stage89": ORDER_SCRIPTS / "smoke_order_stage89.py",
}
COMPAT_COMMANDS_WITH_DATA_ROOT = {
    "history-backfill",
    "association-candidates",
    "resolve-pending",
    "backfill-queue",
    "backfill-ready",
    "backfill-finalize",
}


class RuntimeApiError(Exception):
    """A handled runtime API error."""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = details or {}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Execute one order runtime JSON API request.")
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument("--request-file", help="Path to a JSON request envelope.")
    input_group.add_argument("--request-json", help="Inline JSON request envelope.")
    return parser.parse_args()


def json_dumps(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def load_request(args: argparse.Namespace) -> dict[str, Any]:
    if args.request_file:
        payload = json.loads(Path(args.request_file).read_text(encoding="utf-8"))
    else:
        payload = json.loads(args.request_json)
    if not isinstance(payload, dict):
        raise RuntimeApiError("Request envelope must be a JSON object.")
    return payload


def response_envelope(
    request: dict[str, Any],
    *,
    status: str,
    result: dict[str, Any] | None,
    error: dict[str, Any] | None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "request_id": request.get("request_id"),
        "command": request.get("command"),
        "status": status,
        "result": result,
        "error": error,
        "warnings": warnings or [],
    }


def actor_label(request: dict[str, Any], payload: dict[str, Any]) -> str | None:
    if payload.get("actor_label"):
        return str(payload["actor_label"])
    actor = request.get("actor")
    if isinstance(actor, dict):
        if actor.get("label"):
            return str(actor["label"])
        if actor.get("agent_id"):
            return str(actor["agent_id"])
    return None


def request_data_root(request: dict[str, Any], payload: dict[str, Any]) -> Path:
    raw = request.get("data_root") or payload.get("data_root")
    return resolve_data_root(str(raw) if raw else None)


def optional_str(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    text = str(value)
    return text if text else None


def require_str(payload: dict[str, Any], key: str) -> str:
    value = optional_str(payload, key)
    if not value:
        raise RuntimeApiError(f"Missing required payload field: {key}")
    return value


def require_int(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if value in (None, ""):
        raise RuntimeApiError(f"Missing required payload field: {key}")
    try:
        return int(str(value))
    except ValueError as exc:
        raise RuntimeApiError(f"Payload field must be an integer: {key}", details={key: value}) from exc


def list_or_none(value: Any) -> list[Any] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise RuntimeApiError("Expected a JSON list.", details={"value": value})
    return value


def dict_or_none(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise RuntimeApiError("Expected a JSON object.", details={"value": value})
    return value


def handle_init_runtime(request: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    return {"status": "initialized", **initialize_runtime(request_data_root(request, payload))}


def handle_persist_input(request: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    source = request.get("source") if isinstance(request.get("source"), dict) else {}
    return persist_input(
        data_root=request_data_root(request, payload),
        channel_type=optional_str(payload, "channel_type")
        or optional_str(source, "channel_type")  # type: ignore[arg-type]
        or "local",
        channel_session_key=optional_str(payload, "channel_session_key")
        or optional_str(source, "channel_session_key"),  # type: ignore[arg-type]
        source_actor=optional_str(payload, "source_actor") or actor_label(request, payload),
        source_message_id=optional_str(payload, "source_message_id"),
        raw_text=optional_str(payload, "text") or optional_str(payload, "raw_text"),
        raw_payload=payload.get("raw_payload"),
        attachments=list_or_none(payload.get("attachments")),  # type: ignore[arg-type]
    )


def handle_open_draft(request: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    return open_guided_intake_draft(
        data_root=request_data_root(request, payload),
        inbox_item_id=require_str(payload, "inbox_item_id"),
        intent_type=require_str(payload, "intent_type"),
        target_object_type=optional_str(payload, "target_object_type"),
        target_action=optional_str(payload, "target_action"),
        summary_text=optional_str(payload, "summary_text"),
        draft_fields=payload.get("draft_fields"),
        thread=dict_or_none(payload.get("thread")),  # type: ignore[arg-type]
        candidate_links=list_or_none(payload.get("candidate_links")),  # type: ignore[arg-type]
        pending_targets=list_or_none(payload.get("pending_associations") or payload.get("pending_targets")),  # type: ignore[arg-type]
        required_fields=list_or_none(payload.get("required_fields")),  # type: ignore[arg-type]
        actor_label=actor_label(request, payload),
    )


def handle_prepare_confirmation(request: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    return prepare_draft_confirmation(
        data_root=request_data_root(request, payload),
        workflow_draft_id=require_str(payload, "workflow_draft_id"),
        actor_label=actor_label(request, payload),
    )


def handle_commit_draft(request: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    return commit_workflow_draft(
        data_root=request_data_root(request, payload),
        workflow_draft_id=require_str(payload, "workflow_draft_id"),
        confirm_token=require_str(payload, "confirm_token"),
        actor_label=actor_label(request, payload),
    )


def handle_resolve_association(request: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    return resolve_pending_association_item(
        data_root=request_data_root(request, payload),
        pending_association_id=require_str(payload, "pending_association_id"),
        target_key=require_str(payload, "target_key"),
        reason_text=optional_str(payload, "reason_text"),
        actor_label=actor_label(request, payload),
        thread=dict_or_none(payload.get("thread")),  # type: ignore[arg-type]
    )


def handle_allocate(request: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    allocations = list_or_none(payload.get("allocations"))
    if allocations is None:
        raise RuntimeApiError("Missing required payload field: allocations")
    return record_settlement_allocations(
        data_root=request_data_root(request, payload),
        cash_transaction_id=require_int(payload, "cash_transaction_id"),
        allocations=allocations,  # type: ignore[arg-type]
        actor_label=actor_label(request, payload),
        replace_existing=bool(payload.get("replace_existing", False)),
        require_full_amount=bool(payload.get("require_full_amount", False)),
        dry_run=bool(payload.get("dry_run", False)),
        confirm_token=optional_str(payload, "confirm_token"),
    )


def handle_refresh_control_tower(request: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    return refresh_control_tower(
        data_root=request_data_root(request, payload),
        as_of_date=optional_str(payload, "as_of_date"),
        actor_label=actor_label(request, payload),
    )


def handle_daily_report(request: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    refresh_first = payload.get("refresh_first", True)
    if "skip_refresh" in payload:
        refresh_first = not bool(payload["skip_refresh"])
    return generate_daily_report(
        data_root=request_data_root(request, payload),
        report_date=optional_str(payload, "report_date"),
        actor_label=actor_label(request, payload),
        refresh_first=bool(refresh_first),
    )


def handle_history_search(request: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    data_root = str(request_data_root(request, payload))
    return search_history(
        data_root=data_root,
        query=optional_str(payload, "query"),
        limit=int(payload.get("limit", 20)),
        channel_types=list_or_none(payload.get("channel_types")),  # type: ignore[arg-type]
        categories=list_or_none(payload.get("categories")),  # type: ignore[arg-type]
        legacy_only=bool(payload.get("legacy_only", False)),
    )


def handle_history_show(request: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    data_root = str(request_data_root(request, payload))
    return show_history_item(
        data_root=data_root,
        inbox_item_id=optional_str(payload, "inbox_item_id"),
        source_message_id=optional_str(payload, "source_message_id"),
        max_text_chars=int(payload.get("max_text_chars", 4000)),
        include_evidence_text=bool(payload.get("include_evidence_text", False)),
    )


def handle_history_replay(request: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    data_root = str(request_data_root(request, payload))
    return replay_history(
        data_root=data_root,
        channel_session_key=optional_str(payload, "channel_session_key"),
        object_thread_id=optional_str(payload, "object_thread_id"),
        object_type=optional_str(payload, "object_type"),
        object_key=optional_str(payload, "object_key"),
        limit=int(payload.get("limit", 30)),
        max_text_chars=int(payload.get("max_text_chars", 2000)),
    )


def parse_stdout(stdout: str) -> dict[str, Any]:
    stripped = stdout.strip()
    if not stripped:
        return {"status": "ok"}
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return {"status": "ok", "stdout": stdout}
    if isinstance(parsed, dict):
        return parsed
    return {"status": "ok", "stdout_json": parsed}


def compat_argv(command: str, payload: dict[str, Any], data_root: Path) -> list[str]:
    raw = payload.get("argv", [])
    if raw is None:
        raw = []
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise RuntimeApiError("Compatibility payload field `argv` must be a list of strings.")
    argv = list(raw)
    if command in COMPAT_COMMANDS_WITH_DATA_ROOT and "--data-root" not in argv:
        argv = ["--data-root", str(data_root), *argv]
    return argv


def handle_compat_command(command: str, request: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    script_path = COMPAT_SCRIPT_MAP.get(command)
    if not script_path:
        raise RuntimeApiError(f"Unsupported order runtime command: {command}")
    if not script_path.exists():
        raise RuntimeApiError(f"Missing compatibility script for command: {command}", details={"script": str(script_path)})

    data_root = request_data_root(request, payload)
    completed = subprocess.run(
        [sys.executable, str(script_path), *compat_argv(command, payload, data_root)],
        cwd=str(REPO_ROOT),
        text=True,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise RuntimeApiError(
            f"Compatibility command failed: {command}",
            details={
                "returncode": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
            },
        )
    result = parse_stdout(completed.stdout)
    result.setdefault("status", "ok")
    result["_adapter"] = {
        "mode": "compat_subprocess",
        "script": str(script_path.relative_to(REPO_ROOT)),
    }
    if completed.stderr:
        result["_adapter"]["stderr"] = completed.stderr
    return result


DIRECT_HANDLERS: dict[str, Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]] = {
    "init-runtime": handle_init_runtime,
    "persist-input": handle_persist_input,
    "open-draft": handle_open_draft,
    "prepare-confirmation": handle_prepare_confirmation,
    "commit-draft": handle_commit_draft,
    "resolve-association": handle_resolve_association,
    "allocate": handle_allocate,
    "refresh-control-tower": handle_refresh_control_tower,
    "daily-report": handle_daily_report,
    "history-search": handle_history_search,
    "history-show": handle_history_show,
    "history-replay": handle_history_replay,
}


def execute_request(request: dict[str, Any]) -> dict[str, Any]:
    command = request.get("command")
    if not isinstance(command, str) or not command.strip():
        raise RuntimeApiError("Request envelope requires a non-empty `command` string.")
    normalized_command = command.strip()
    request["command"] = normalized_command

    payload = request.get("payload") or {}
    if not isinstance(payload, dict):
        raise RuntimeApiError("Request envelope `payload` must be a JSON object.")

    handler = DIRECT_HANDLERS.get(normalized_command)
    if handler:
        return handler(request, payload)
    return handle_compat_command(normalized_command, request, payload)


def main() -> int:
    request: dict[str, Any] = {"request_id": f"invalid-{uuid.uuid4().hex}", "command": None}
    try:
        request = load_request(parse_args())
        result = execute_request(request)
        print(json_dumps(response_envelope(request, status="ok", result=result, error=None)))
        return 0
    except RuntimeApiError as exc:
        print(
            json_dumps(
                response_envelope(
                    request,
                    status="error",
                    result=None,
                    error={"type": exc.__class__.__name__, "message": str(exc), "details": exc.details},
                )
            )
        )
        return 1
    except Exception as exc:
        print(
            json_dumps(
                response_envelope(
                    request,
                    status="error",
                    result=None,
                    error={
                        "type": exc.__class__.__name__,
                        "message": str(exc),
                        "traceback": traceback.format_exc(),
                    },
                )
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
