#!/usr/bin/env python3
"""Bound OpenClaw adapter for the order runtime JSON API."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
ORDER_SCRIPTS = REPO_ROOT / "order" / "scripts"
RUNTIME_API = ORDER_SCRIPTS / "order_runtime_api.py"
PLUGIN_ROOT = Path(__file__).resolve().parents[1]
BINDING_PATH = PLUGIN_ROOT / ".codex-plugin" / "agent-binding.json"

RUNTIME_COMMANDS = {
    "init-runtime",
    "persist-input",
    "open-draft",
    "prepare-confirmation",
    "commit-draft",
    "resolve-association",
    "allocate",
    "refresh-control-tower",
    "daily-report",
    "history-search",
    "history-show",
    "history-replay",
    "history-backfill",
    "association-candidates",
    "resolve-pending",
    "backfill-queue",
    "backfill-ready",
    "backfill-finalize",
    "smoke-runtime",
    "smoke-stage89",
}
LOCAL_ONLY_COMMANDS = {"bind-agent", "show-binding"}
COMPAT_COMMANDS = {
    "history-backfill",
    "association-candidates",
    "resolve-pending",
    "backfill-queue",
    "backfill-ready",
    "backfill-finalize",
    "smoke-runtime",
    "smoke-stage89",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Hard-execution adapter for order runtime API actions.",
        allow_abbrev=False,
    )
    parser.add_argument(
        "subcommand",
        choices=sorted(list(RUNTIME_COMMANDS) + sorted(LOCAL_ONLY_COMMANDS)),
        help="Order hard-execution subcommand.",
    )
    parser.add_argument("passthrough", nargs=argparse.REMAINDER, help="Arguments passed to the runtime API adapter.")
    return parser.parse_args()


def read_binding() -> dict[str, Any]:
    if not BINDING_PATH.exists():
        return {
            "installationScope": "explicit_agent_only",
            "autoInstall": False,
            "status": "unbound",
            "targetAgent": "",
            "notes": "Bind this plugin to one specific agent before running order execution commands.",
        }
    return json.loads(BINDING_PATH.read_text(encoding="utf-8"))


def write_binding(state: dict[str, Any]) -> None:
    BINDING_PATH.parent.mkdir(parents=True, exist_ok=True)
    BINDING_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_bind_agent(passthrough: list[str]) -> str:
    if len(passthrough) >= 2 and passthrough[0] == "--agent" and passthrough[1].strip():
        return passthrough[1].strip()
    raise SystemExit("bind-agent requires `--agent <agent-name>`.")


def extract_agent_arg(passthrough: list[str]) -> tuple[str, list[str]]:
    stripped: list[str] = []
    agent_name = ""
    index = 0
    while index < len(passthrough):
        item = passthrough[index]
        if item == "--agent":
            if index + 1 >= len(passthrough) or not passthrough[index + 1].strip():
                raise SystemExit("runtime subcommands require `--agent <agent-name>`.")
            agent_name = passthrough[index + 1].strip()
            index += 2
            continue
        stripped.append(item)
        index += 1
    if not agent_name:
        raise SystemExit("runtime subcommands require `--agent <agent-name>`.")
    return agent_name, stripped


def ensure_bound(agent_name: str) -> dict[str, Any]:
    state = read_binding()
    if state.get("status") != "bound" or not str(state.get("targetAgent") or "").strip():
        raise SystemExit(
            "openclaw-order plugin is not bound to a target agent. Run "
            "`python3 plugins/openclaw-order/scripts/order_hard_execute.py bind-agent --agent <agent-name>` first."
        )
    if str(state.get("targetAgent")).strip() != agent_name:
        raise SystemExit(
            f"openclaw-order plugin is bound to agent `{state.get('targetAgent')}`, "
            f"but this call came from `{agent_name}`."
        )
    return state


def print_runtime_subcommand_help(subcommand: str) -> None:
    if subcommand == "persist-input":
        print(
            "\n".join(
                [
                    "persist-input usage:",
                    "  python3 plugins/openclaw-order/scripts/order_hard_execute.py persist-input --agent <agent-name> --payload-file <payload.json>",
                    "  python3 plugins/openclaw-order/scripts/order_hard_execute.py persist-input --agent <agent-name> --text 'raw inbound text' [--source-actor <name>] [--channel-session-key <key>] [--source-message-id <id>] [--channel-type <type>] [--raw-payload-file <json-file>]",
                    "",
                    "This adapter always calls order/scripts/order_runtime_api.py and returns a JSON response envelope.",
                ]
            )
        )
        return
    if subcommand == "open-draft":
        print(
            "\n".join(
                [
                    "open-draft usage:",
                    "  python3 plugins/openclaw-order/scripts/order_hard_execute.py open-draft --agent <agent-name> --payload-file <payload.json>",
                    "  python3 plugins/openclaw-order/scripts/order_hard_execute.py open-draft --agent <agent-name> --inbox-item-id <inbox_item_id> --intent-type <intent> [--target-object-type <type>] [--target-action <action>] [--summary-text <text>] [--field key=value ...] [--required-field <name> ...] [--thread-object-type <type> --thread-object-key <key> [--thread-title <title>]] [--actor-label <label>]",
                    "",
                    "This adapter always calls order/scripts/order_runtime_api.py and returns a JSON response envelope.",
                ]
            )
        )
        return
    print(
        f"{subcommand} usage: python3 plugins/openclaw-order/scripts/order_hard_execute.py "
        f"{subcommand} --agent <agent-name> ...\n"
        "The adapter calls order/scripts/order_runtime_api.py and returns a JSON response envelope."
    )


def extract_data_root_arg(passthrough: list[str]) -> tuple[str | None, list[str]]:
    stripped: list[str] = []
    data_root: str | None = None
    index = 0
    while index < len(passthrough):
        item = passthrough[index]
        if item == "--data-root":
            if index + 1 >= len(passthrough) or not passthrough[index + 1].strip():
                raise SystemExit("`--data-root` requires a value.")
            data_root = passthrough[index + 1].strip()
            index += 2
            continue
        stripped.append(item)
        index += 1
    return data_root, stripped


def read_payload_file_arg(passthrough: list[str]) -> tuple[dict[str, Any] | None, list[str]]:
    stripped: list[str] = []
    payload: dict[str, Any] | None = None
    index = 0
    while index < len(passthrough):
        item = passthrough[index]
        if item == "--payload-file":
            if index + 1 >= len(passthrough) or not passthrough[index + 1].strip():
                raise SystemExit("`--payload-file` requires a value.")
            if payload is not None:
                raise SystemExit("Only one `--payload-file` is supported.")
            raw_payload = json.loads(Path(passthrough[index + 1]).read_text(encoding="utf-8"))
            if not isinstance(raw_payload, dict):
                raise SystemExit("`--payload-file` must contain a JSON object.")
            payload = raw_payload
            index += 2
            continue
        stripped.append(item)
        index += 1
    return payload, stripped


def parse_field_assignments(entries: list[str]) -> dict[str, str]:
    field_map: dict[str, str] = {}
    for item in entries:
        if "=" not in item:
            raise SystemExit("friendly open-draft fields must use `--field key=value`.")
        key, value = item.split("=", 1)
        normalized_key = key.strip()
        if not normalized_key:
            raise SystemExit("friendly open-draft fields require a non-empty key before `=`.")
        field_map[normalized_key] = value
    return field_map


def parse_persist_payload(passthrough: list[str]) -> tuple[dict[str, Any], str | None]:
    data_root, stripped = extract_data_root_arg(passthrough)
    file_payload, stripped = read_payload_file_arg(stripped)
    if file_payload is not None:
        return file_payload, data_root

    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    parser.add_argument("--text")
    parser.add_argument("--channel-type", default="local")
    parser.add_argument("--channel-session-key")
    parser.add_argument("--source-actor")
    parser.add_argument("--source-message-id")
    parser.add_argument("--raw-payload-file")
    parsed, extras = parser.parse_known_args(stripped)
    if extras:
        raise SystemExit(
            "persist-input friendly mode only supports `--text`, `--channel-type`, `--channel-session-key`, "
            "`--source-actor`, `--source-message-id`, `--raw-payload-file`, and `--data-root`. "
            "Use `--payload-file` for any richer input."
        )
    if not parsed.text or not str(parsed.text).strip():
        raise SystemExit("persist-input requires either `--payload-file <payload.json>` or `--text <raw inbound text>`.")

    raw_payload = None
    if parsed.raw_payload_file:
        raw_payload = json.loads(Path(parsed.raw_payload_file).read_text(encoding="utf-8"))

    return (
        {
            "text": str(parsed.text).strip(),
            "channel_type": parsed.channel_type,
            "channel_session_key": parsed.channel_session_key,
            "source_actor": parsed.source_actor,
            "source_message_id": parsed.source_message_id,
            "raw_payload": raw_payload,
        },
        data_root,
    )


def parse_open_draft_payload(passthrough: list[str]) -> tuple[dict[str, Any], str | None]:
    data_root, stripped = extract_data_root_arg(passthrough)
    file_payload, stripped = read_payload_file_arg(stripped)
    if file_payload is not None:
        return file_payload, data_root

    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    parser.add_argument("--inbox-item-id")
    parser.add_argument("--intent-type")
    parser.add_argument("--target-object-type")
    parser.add_argument("--target-action")
    parser.add_argument("--summary-text")
    parser.add_argument("--actor-label")
    parser.add_argument("--field", action="append", default=[])
    parser.add_argument("--required-field", action="append", default=[])
    parser.add_argument("--thread-object-type")
    parser.add_argument("--thread-object-key")
    parser.add_argument("--thread-title")
    parsed, extras = parser.parse_known_args(stripped)
    if extras:
        raise SystemExit(
            "open-draft friendly mode only supports `--inbox-item-id`, `--intent-type`, `--target-object-type`, "
            "`--target-action`, `--summary-text`, `--actor-label`, repeated `--field key=value`, repeated "
            "`--required-field <name>`, `--thread-object-type`, `--thread-object-key`, `--thread-title`, and `--data-root`. "
            "Use `--payload-file` for any richer input."
        )
    if not parsed.inbox_item_id or not str(parsed.inbox_item_id).strip():
        raise SystemExit("open-draft requires either `--payload-file <payload.json>` or `--inbox-item-id <id>`.")
    if not parsed.intent_type or not str(parsed.intent_type).strip():
        raise SystemExit("open-draft friendly mode requires `--intent-type <intent>`.")

    payload: dict[str, Any] = {
        "inbox_item_id": str(parsed.inbox_item_id).strip(),
        "intent_type": str(parsed.intent_type).strip(),
        "target_object_type": parsed.target_object_type,
        "target_action": parsed.target_action,
        "summary_text": parsed.summary_text,
        "draft_fields": parse_field_assignments(parsed.field),
        "required_fields": parsed.required_field or None,
        "actor_label": parsed.actor_label,
    }
    if parsed.thread_object_type or parsed.thread_object_key or parsed.thread_title:
        if not parsed.thread_object_type or not parsed.thread_object_key:
            raise SystemExit("thread-friendly open-draft mode requires both `--thread-object-type` and `--thread-object-key`.")
        payload["thread"] = {
            "object_type": parsed.thread_object_type,
            "object_key": parsed.thread_object_key,
            "title": parsed.thread_title,
        }
    return payload, data_root


def parse_simple_payload(passthrough: list[str], parser: argparse.ArgumentParser) -> tuple[argparse.Namespace, str | None]:
    data_root, stripped = extract_data_root_arg(passthrough)
    file_payload, stripped = read_payload_file_arg(stripped)
    if file_payload is not None:
        namespace = argparse.Namespace(payload=file_payload)
        return namespace, data_root
    parsed, extras = parser.parse_known_args(stripped)
    if extras:
        raise SystemExit(f"Unsupported arguments: {' '.join(extras)}")
    parsed.payload = None
    return parsed, data_root


def parse_runtime_payload(subcommand: str, passthrough: list[str]) -> tuple[dict[str, Any], str | None]:
    if subcommand == "persist-input":
        return parse_persist_payload(passthrough)
    if subcommand == "open-draft":
        return parse_open_draft_payload(passthrough)
    if subcommand in COMPAT_COMMANDS:
        data_root, stripped = extract_data_root_arg(passthrough)
        return {"argv": stripped}, data_root

    if subcommand == "init-runtime":
        data_root, stripped = extract_data_root_arg(passthrough)
        if stripped:
            raise SystemExit(f"Unsupported arguments: {' '.join(stripped)}")
        return {}, data_root
    if subcommand == "prepare-confirmation":
        parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
        parser.add_argument("--workflow-draft-id")
        parser.add_argument("--actor-label")
        parsed, data_root = parse_simple_payload(passthrough, parser)
        payload = parsed.payload or {"workflow_draft_id": parsed.workflow_draft_id, "actor_label": parsed.actor_label}
        return payload, data_root
    if subcommand == "commit-draft":
        parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
        parser.add_argument("--workflow-draft-id")
        parser.add_argument("--confirm-token")
        parser.add_argument("--actor-label")
        parsed, data_root = parse_simple_payload(passthrough, parser)
        payload = parsed.payload or {
            "workflow_draft_id": parsed.workflow_draft_id,
            "confirm_token": parsed.confirm_token,
            "actor_label": parsed.actor_label,
        }
        return payload, data_root
    if subcommand == "resolve-association":
        parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
        parser.add_argument("--pending-association-id")
        parser.add_argument("--target-key")
        parser.add_argument("--reason-text")
        parser.add_argument("--actor-label")
        parsed, data_root = parse_simple_payload(passthrough, parser)
        payload = parsed.payload or {
            "pending_association_id": parsed.pending_association_id,
            "target_key": parsed.target_key,
            "reason_text": parsed.reason_text,
            "actor_label": parsed.actor_label,
        }
        return payload, data_root
    if subcommand == "allocate":
        data_root, stripped = extract_data_root_arg(passthrough)
        file_payload, stripped = read_payload_file_arg(stripped)
        if stripped:
            raise SystemExit("allocate only supports `--payload-file` plus optional `--data-root`.")
        if file_payload is None:
            raise SystemExit("allocate requires `--payload-file <payload.json>`.")
        return file_payload, data_root
    if subcommand == "refresh-control-tower":
        parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
        parser.add_argument("--as-of-date")
        parser.add_argument("--actor-label")
        parsed, data_root = parse_simple_payload(passthrough, parser)
        payload = parsed.payload or {"as_of_date": parsed.as_of_date, "actor_label": parsed.actor_label}
        return payload, data_root
    if subcommand == "daily-report":
        parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
        parser.add_argument("--report-date")
        parser.add_argument("--actor-label")
        parser.add_argument("--skip-refresh", action="store_true")
        parsed, data_root = parse_simple_payload(passthrough, parser)
        payload = parsed.payload or {
            "report_date": parsed.report_date,
            "actor_label": parsed.actor_label,
            "skip_refresh": parsed.skip_refresh,
        }
        return payload, data_root
    if subcommand == "history-search":
        parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
        parser.add_argument("--query", nargs="+")
        parser.add_argument("--limit", type=int, default=10)
        parser.add_argument("--channel-type", action="append", dest="channel_types")
        parser.add_argument("--category", action="append", dest="categories")
        parser.add_argument("--legacy-only", action="store_true")
        parsed, data_root = parse_simple_payload(passthrough, parser)
        payload = parsed.payload or {
            "query": " ".join(parsed.query) if parsed.query else None,
            "limit": parsed.limit,
            "channel_types": parsed.channel_types,
            "categories": parsed.categories,
            "legacy_only": parsed.legacy_only,
        }
        return payload, data_root
    if subcommand == "history-show":
        parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
        parser.add_argument("--inbox-item-id")
        parser.add_argument("--source-message-id")
        parser.add_argument("--max-text-chars", type=int, default=1200)
        parser.add_argument("--include-evidence-text", action="store_true")
        parsed, data_root = parse_simple_payload(passthrough, parser)
        payload = parsed.payload or {
            "inbox_item_id": parsed.inbox_item_id,
            "source_message_id": parsed.source_message_id,
            "max_text_chars": parsed.max_text_chars,
            "include_evidence_text": parsed.include_evidence_text,
        }
        return payload, data_root
    if subcommand == "history-replay":
        parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
        parser.add_argument("--channel-session-key")
        parser.add_argument("--object-thread-id")
        parser.add_argument("--object-type")
        parser.add_argument("--object-key")
        parser.add_argument("--limit", type=int, default=50)
        parser.add_argument("--max-text-chars", type=int, default=180)
        parsed, data_root = parse_simple_payload(passthrough, parser)
        payload = parsed.payload or {
            "channel_session_key": parsed.channel_session_key,
            "object_thread_id": parsed.object_thread_id,
            "object_type": parsed.object_type,
            "object_key": parsed.object_key,
            "limit": parsed.limit,
            "max_text_chars": parsed.max_text_chars,
        }
        return payload, data_root

    raise SystemExit(f"Unsupported runtime subcommand: {subcommand}")


def build_runtime_request(subcommand: str, agent_name: str, payload: dict[str, Any], data_root: str | None) -> dict[str, Any]:
    request: dict[str, Any] = {
        "request_id": f"openclaw-wrapper-{uuid.uuid4().hex}",
        "command": subcommand,
        "actor": {"agent_id": agent_name, "label": agent_name},
        "source": {"adapter": "openclaw-order-plugin-wrapper"},
        "payload": payload,
    }
    if data_root:
        request["data_root"] = data_root
    return request


def run_runtime_api(request: dict[str, Any]) -> int:
    if not RUNTIME_API.exists():
        raise SystemExit(f"Missing order runtime API: {RUNTIME_API}")
    handle = tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        prefix="order-runtime-api-request-",
        suffix=".json",
        delete=False,
    )
    request_path = Path(handle.name)
    with handle:
        json.dump(request, handle, ensure_ascii=False, indent=2)
    try:
        completed = subprocess.run(
            [sys.executable, str(RUNTIME_API), "--request-file", str(request_path)],
            cwd=str(REPO_ROOT),
            text=True,
            capture_output=True,
        )
        if completed.stdout:
            print(completed.stdout, end="")
        if completed.stderr:
            print(completed.stderr, end="", file=sys.stderr)
        return completed.returncode
    finally:
        request_path.unlink(missing_ok=True)


def main() -> int:
    args = parse_args()
    if args.subcommand == "show-binding":
        print(json.dumps(read_binding(), ensure_ascii=False, indent=2))
        return 0
    if args.subcommand == "bind-agent":
        agent_name = parse_bind_agent(args.passthrough)
        state = read_binding()
        state["status"] = "bound"
        state["targetAgent"] = agent_name
        write_binding(state)
        print(json.dumps(state, ensure_ascii=False, indent=2))
        return 0

    agent_name, stripped_passthrough = extract_agent_arg(args.passthrough)
    if stripped_passthrough in (["--help"], ["-h"]):
        print_runtime_subcommand_help(args.subcommand)
        return 0
    ensure_bound(agent_name)
    payload, data_root = parse_runtime_payload(args.subcommand, stripped_passthrough)
    return run_runtime_api(build_runtime_request(args.subcommand, agent_name, payload, data_root))


if __name__ == "__main__":
    raise SystemExit(main())
