#!/usr/bin/env python3
"""Validate the standalone order-skill repository shape."""

from __future__ import annotations

import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
REQUIRED_PATHS = [
    "README.md",
    "README.zh-CN.md",
    "openclaw.plugin.json",
    "package.json",
    "index.js",
    "src/plugin/index.js",
    "order/runtime/schema_v1.sql",
    "order/runtime/command_manifest.json",
    "order/scripts/order_runtime_api.py",
    "plugins/openclaw-order/scripts/order_hard_execute.py",
    "plugins/openclaw-order/skills/order-runtime/SKILL.md",
    "docs/README.md",
    "docs/README.zh-CN.md",
    "docs/architecture.md",
    "docs/architecture.zh-CN.md",
    "docs/roadmap.md",
    "docs/roadmap.zh-CN.md",
    "docs/test-plan.md",
    "docs/test-plan.zh-CN.md",
    "docs/reference/development-plan.md",
    "docs/reference/development-plan.zh-CN.md",
    ".codex/brief.md",
    ".codex/status.md",
    ".codex/plan.md",
]
FORBIDDEN_ACTIVE_STRINGS = [
    "openclaw-skills/order-host-plugin",
    "/Users/redcreen/Project/openclaw-skills/order-host-plugin",
    "order-host-plugin/",
    "order/docs/",
    ".agents/plugins/marketplace.json",
]
EXCLUDED_TEXT_PARTS = {
    ".codex/codex-app-loop.json",
    ".codex/message-ingress.json",
    ".codex/ptl-policy/",
    ".codex/task-pipeline.json",
    "docs/devlog/",
    "scripts/validate_order_skill_repo.py",
}


def fail(message: str) -> None:
    print(f"validate_order_skill_repo: {message}", file=sys.stderr)
    raise SystemExit(1)


def is_text_file(path: Path) -> bool:
    return path.suffix in {".md", ".json", ".js", ".py", ".sh", ".yaml", ".yml", ".txt"}


def main() -> None:
    missing = [item for item in REQUIRED_PATHS if not (REPO_ROOT / item).exists()]
    if missing:
        fail(f"missing required paths: {missing}")

    if (REPO_ROOT / "order-host-plugin").exists():
        fail("standalone project must not contain order-host-plugin/")
    if (REPO_ROOT / "order" / "docs").exists():
        fail("standalone project must not keep duplicate order/docs/")

    package = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))
    if package.get("name") != "@redcreen/order-skill":
        fail("package.json name must be @redcreen/order-skill")

    binding_path = REPO_ROOT / "plugins" / "openclaw-order" / ".codex-plugin" / "agent-binding.json"
    if binding_path.exists():
        binding = json.loads(binding_path.read_text(encoding="utf-8"))
        if binding.get("status") != "unbound" or binding.get("targetAgent"):
            fail("public plugin binding must default to unbound with empty targetAgent when the local binding file exists")

    offenders: list[str] = []
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file() or not is_text_file(path):
            continue
        relative = path.relative_to(REPO_ROOT).as_posix()
        if any(part in relative for part in EXCLUDED_TEXT_PARTS):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for forbidden in FORBIDDEN_ACTIVE_STRINGS:
            if forbidden in text:
                offenders.append(f"{relative}: {forbidden}")
    if offenders:
        fail("forbidden stale standalone references:\n" + "\n".join(offenders))

    print(json.dumps({"status": "ok", "checked_paths": len(REQUIRED_PATHS)}, indent=2))


if __name__ == "__main__":
    main()
