#!/usr/bin/env python3
"""Cut over legacy order state into the new local-first system."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tarfile
from datetime import datetime
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
OPENCLAW_ROOT = Path("/Users/redcreen/.openclaw")
DEFAULT_DATA_ROOT = Path("/Users/redcreen/Documents/openclaw-order")
DEFAULT_EXPORT_DIR = OPENCLAW_ROOT / "tmp" / "order-feishu-export-20260419" / "csv"

LEGACY_PATHS = {
    "workspace_order": OPENCLAW_ROOT / "workspace-order",
    "agents_order": OPENCLAW_ROOT / "agents" / "order",
    "workspace_agents_order": OPENCLAW_ROOT / "workspace" / "agents" / "order",
}


def run(cmd: list[str]) -> None:
    completed = subprocess.run(cmd, cwd=str(REPO_ROOT), check=False)
    if completed.returncode != 0:
        raise SystemExit(f"Command failed ({completed.returncode}): {' '.join(cmd)}")


def copytree_if_exists(src: Path, dst: Path) -> bool:
    if not src.exists():
        return False
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    return True


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def ensure_data_root(data_root: Path) -> None:
    data_root.mkdir(parents=True, exist_ok=True)
    run(["python3", str(REPO_ROOT / "order" / "scripts" / "init_order_runtime.py"), "--data-root", str(data_root)])


def import_live_export(data_root: Path, export_dir: Path) -> Path:
    output_path = data_root / "db" / "order.db"
    run(
        [
            "python3",
            str(REPO_ROOT / "scripts" / "import_order_live_export_to_sqlite.py"),
            "--export-dir",
            str(export_dir),
            "--output",
            str(output_path),
        ]
    )
    return output_path


def archive_legacy_material(data_root: Path) -> dict[str, str]:
    archive_root = data_root / "legacy-imports"
    archive_root.mkdir(parents=True, exist_ok=True)
    copied: dict[str, str] = {}
    for name, src in LEGACY_PATHS.items():
        target = archive_root / name.replace("_", "-")
        if copytree_if_exists(src, target):
            copied[name] = str(target)
    return copied


def backup_legacy_order_state() -> dict[str, str]:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_root = OPENCLAW_ROOT / "backups" / f"order-cutover-{timestamp}"
    backup_root.mkdir(parents=True, exist_ok=True)
    copied: dict[str, str] = {}
    for name, src in LEGACY_PATHS.items():
        target = backup_root / name.replace("_", "-")
        if copytree_if_exists(src, target):
            copied[name] = str(target)
    archive_path = backup_root.with_suffix(".tar.gz")
    with tarfile.open(archive_path, "w:gz") as tar:
        tar.add(backup_root, arcname=backup_root.name)
    write_json(
        backup_root / "backup-manifest.json",
        {
            "created_at": datetime.now().astimezone().isoformat(),
            "backup_root": str(backup_root),
            "archive_path": str(archive_path),
            "copied_paths": copied,
        },
    )
    return {
        "backup_root": str(backup_root),
        "archive_path": str(archive_path),
        **{f"path_{name}": path for name, path in copied.items()},
    }


def seed_order_workspace(data_root: Path) -> dict[str, str]:
    workspace_root = OPENCLAW_ROOT / "workspace-order"
    workspace_root.mkdir(parents=True, exist_ok=True)
    files = {
        "AGENTS.md": "\n".join(
            [
                "# AGENTS.md",
                "",
                "This workspace belongs to the order agent.",
                "",
                "Startup sequence:",
                "1. Treat the local-first order system at ~/Documents/openclaw-order as the source of truth.",
                "2. Load MEMORY.md for order role and long-term operating rules.",
                "3. Do not ask who you are. You are already the order operations agent.",
                "4. For order writes, use the installed order runtime plugin and its hard-execution scripts.",
            ]
        ),
        "SOUL.md": "\n".join(
            [
                "# SOUL.md",
                "",
                "You are the order operations agent.",
                "",
                "Core rules:",
                "- local-first truth: ~/Documents/openclaw-order",
                "- persist input first",
                "- draft -> confirmation -> commit",
                "- keep delayed links explicit",
                "- supervise delivery, payables, receivables, and daily reporting",
            ]
        ),
        "USER.md": "\n".join(
            [
                "# USER.md",
                "",
                "- Name: 刘超",
                "- What to call them: 超哥",
                "- Timezone: Asia/Shanghai",
                "- Role: self-operated plush-toy order and production operator",
            ]
        ),
        "MEMORY.md": "\n".join(
            [
                "# MEMORY.md",
                "",
                "## Role",
                "- Order and supply-chain execution agent",
                "- Local-first source of truth lives in ~/Documents/openclaw-order",
                "",
                "## Core rules",
                "- All order-related input persists first",
                "- Formal writes require draft -> confirmation -> commit",
                "- Queries must prefer the local SQLite truth, not chat memory",
                "- Follow-up and daily reports come from the control tower, not freeform recall",
                "- Legacy docs, CSV snapshots, and old chats are available through the imported history layer",
                "- When one legacy clue should become a formal record, use history-backfill to open a guided backfill draft",
                "- When a backfill draft has unresolved order or lot links, use association-candidates and resolve-pending",
                "- When many imported legacy sources need review, use backfill-queue as the batch work surface",
                "- When a backfill draft becomes ready, use backfill-ready and backfill-finalize for explicit confirmation and commit",
                "",
                "## Cutover",
                f"- Legacy order data was imported into {data_root}",
                "- Legacy Feishu export and workspace-order files were archived before reset",
            ]
        ),
        "HEARTBEAT.md": "# Keep empty unless periodic order follow-up tasks are added.\n",
        "BOOTSTRAP.md": "\n".join(
            [
                "# BOOTSTRAP.md",
                "",
                "This agent is already configured as the order operations agent.",
                "Do not ask identity/bootstrap questions.",
                "Load AGENTS.md, SOUL.md, USER.md, MEMORY.md, then start working on order operations immediately.",
            ]
        ),
        "IDENTITY.md": "\n".join(
            [
                "# IDENTITY.md",
                "",
                "- Name: order",
                "- Role: local-first order operations agent",
                "- Vibe: direct, execution-first, data-accurate",
            ]
        ),
        "TOOLS.md": "\n".join(
            [
                "# TOOLS.md",
                "",
                "- Primary data root: ~/Documents/openclaw-order",
                "- Runtime wrapper comes from the installed order plugin",
                "- Use local SQLite and archived evidence as order truth",
                "- For old docs / old chats / old CSV clues, use history-search / history-show / history-replay",
                "- To turn one old clue into a formal backfill flow, prefer history-backfill before generic open-draft",
                "- If the backfill draft still has unresolved links, use association-candidates then resolve-pending",
                "- For many imported sources, start with backfill-queue",
                "- If a history backfill draft is already ready, use backfill-ready and backfill-finalize",
            ]
        ),
    }
    written: dict[str, str] = {}
    for relative, content in files.items():
        path = workspace_root / relative
        path.write_text(content + "\n", encoding="utf-8")
        written[relative] = str(path)
    return written


def reset_order_agent() -> dict[str, object]:
    if any(path.exists() for path in LEGACY_PATHS.values()):
        run(["openclaw", "agents", "delete", "order", "--force", "--json"])
    run(
        [
            "openclaw",
            "agents",
            "add",
            "order",
            "--non-interactive",
            "--workspace",
            str(OPENCLAW_ROOT / "workspace-order"),
            "--agent-dir",
            str(OPENCLAW_ROOT / "agents" / "order" / "agent"),
            "--json",
        ]
    )
    return {
        "workspace": str(OPENCLAW_ROOT / "workspace-order"),
        "agent_dir": str(OPENCLAW_ROOT / "agents" / "order" / "agent"),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Cut over legacy order state into the new local-first system.")
    parser.add_argument("--data-root", default=str(DEFAULT_DATA_ROOT))
    parser.add_argument("--export-dir", default=str(DEFAULT_EXPORT_DIR))
    parser.add_argument("--skip-reset", action="store_true", help="Do not delete and recreate the order agent.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    data_root = Path(args.data_root).expanduser().resolve()
    export_dir = Path(args.export_dir).expanduser().resolve()

    ensure_data_root(data_root)
    imported_db = import_live_export(data_root, export_dir)
    legacy_archives = archive_legacy_material(data_root)
    backup_info = backup_legacy_order_state()
    reset_info = None
    if not args.skip_reset:
        reset_info = reset_order_agent()
    workspace_seed = seed_order_workspace(data_root)

    result = {
        "status": "ok",
        "data_root": str(data_root),
        "database": str(imported_db),
        "legacy_archives": legacy_archives,
        "backup": backup_info,
        "reset": reset_info,
        "workspace_seed": workspace_seed,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
