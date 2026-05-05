#!/usr/bin/env python3
"""Smoke test for the order runtime JSON API and wrapper adapter."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = REPO_ROOT / "order" / "tests" / "fixtures" / "runtime-api"
RUNTIME_API = REPO_ROOT / "order" / "scripts" / "order_runtime_api.py"
WRAPPER = REPO_ROOT / "plugins" / "openclaw-order" / "scripts" / "order_hard_execute.py"
BINDING_PATH = REPO_ROOT / "plugins" / "openclaw-order" / ".codex-plugin" / "agent-binding.json"
TEST_AGENT = "runtime-api-test-agent"


def load_fixture(name: str, **replacements: str) -> dict[str, object]:
    text = (FIXTURE_ROOT / name).read_text(encoding="utf-8")
    for key, value in replacements.items():
        text = text.replace(f"__{key}__", value)
    return json.loads(text)


def run_json(command: list[str], *, cwd: Path = REPO_ROOT) -> dict[str, object]:
    completed = subprocess.run(command, cwd=str(cwd), text=True, capture_output=True)
    if completed.returncode != 0:
        raise AssertionError(
            f"Command failed with {completed.returncode}: {' '.join(command)}\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}"
        )
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"Command did not return JSON: {' '.join(command)}\n{completed.stdout}") from exc


def run_api_request(request: dict[str, object], request_dir: Path, name: str) -> dict[str, object]:
    request_path = request_dir / f"{name}.json"
    request_path.write_text(json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8")
    return run_json([sys.executable, str(RUNTIME_API), "--request-file", str(request_path)])


def assert_ok_envelope(payload: dict[str, object], command: str) -> dict[str, object]:
    assert payload["status"] == "ok", payload
    assert payload["command"] == command, payload
    assert isinstance(payload.get("result"), dict), payload
    assert payload.get("error") is None, payload
    return payload["result"]  # type: ignore[return-value]


def main() -> int:
    temp_root = Path(tempfile.mkdtemp(prefix="order-runtime-api-smoke-"))
    data_root = temp_root / "openclaw-order"
    request_dir = temp_root / "requests"
    request_dir.mkdir(parents=True, exist_ok=True)

    persist_request = load_fixture("persist-input.json", DATA_ROOT=str(data_root))
    persist_envelope = run_api_request(persist_request, request_dir, "persist-input")
    persisted = assert_ok_envelope(persist_envelope, "persist-input")
    assert persisted["status"] == "persisted", persisted
    assert Path(str(persisted["raw_archive_path"])).exists(), persisted
    inbox_item_id = str(persisted["inbox_item_id"])

    open_request = load_fixture("open-draft.json", DATA_ROOT=str(data_root), INBOX_ITEM_ID=inbox_item_id)
    open_envelope = run_api_request(open_request, request_dir, "open-draft")
    opened = assert_ok_envelope(open_envelope, "open-draft")
    assert opened["status"] == "draft_opened", opened
    workflow_draft_id = str(opened["workflow_draft_id"])

    prepare_request = load_fixture(
        "prepare-confirmation.json",
        DATA_ROOT=str(data_root),
        WORKFLOW_DRAFT_ID=workflow_draft_id,
    )
    prepare_envelope = run_api_request(prepare_request, request_dir, "prepare-confirmation")
    prepared = assert_ok_envelope(prepare_envelope, "prepare-confirmation")
    confirmation = prepared["confirmation"]
    assert confirmation["commit_ready"] is True, prepared
    confirm_token = str(confirmation["confirm_token"])

    commit_request = load_fixture(
        "commit-draft.json",
        DATA_ROOT=str(data_root),
        WORKFLOW_DRAFT_ID=workflow_draft_id,
        CONFIRM_TOKEN=confirm_token,
    )
    commit_envelope = run_api_request(commit_request, request_dir, "commit-draft")
    committed = assert_ok_envelope(commit_envelope, "commit-draft")
    assert committed["status"] == "committed", committed
    assert committed["committed_object"]["object_type"] == "sales_order", committed

    original_binding = BINDING_PATH.read_text(encoding="utf-8") if BINDING_PATH.exists() else ""
    try:
        run_json([sys.executable, str(WRAPPER), "bind-agent", "--agent", TEST_AGENT])
        wrapper_persist = run_json(
            [
                sys.executable,
                str(WRAPPER),
                "persist-input",
                "--agent",
                TEST_AGENT,
                "--data-root",
                str(data_root),
                "--text",
                "包装层也走 runtime API。",
                "--source-actor",
                "runtime-api-smoke",
                "--channel-session-key",
                "wrapper-runtime-api",
            ]
        )
        wrapper_result = assert_ok_envelope(wrapper_persist, "persist-input")
        assert wrapper_result["status"] == "persisted", wrapper_result
    finally:
        if original_binding:
            BINDING_PATH.write_text(original_binding, encoding="utf-8")
        else:
            BINDING_PATH.unlink(missing_ok=True)

    result = {
        "status": "ok",
        "data_root": str(data_root),
        "checks": [
            "runtime-api-persist-input",
            "runtime-api-open-draft",
            "runtime-api-prepare-confirmation",
            "runtime-api-commit-draft",
            "wrapper-adapter-persist-input",
        ],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
