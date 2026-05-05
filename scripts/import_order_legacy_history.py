#!/usr/bin/env python3
"""Import legacy order history into the local-first raw-input layer."""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
ORDER_SCRIPTS = REPO_ROOT / "order" / "scripts"

import sys

sys.path.insert(0, str(ORDER_SCRIPTS))

from runtime_common import connect_db, initialize_runtime, persist_input, resolve_data_root, upsert_object_thread, utc_now


LEGACY_ROOT_DEFAULT = Path("/Users/redcreen/Documents/openclaw-order/legacy-imports")
TEXT_EXTENSIONS = {".md", ".txt", ".csv", ".json", ".jsonl", ".hmd"}
BINARY_EXTENSIONS = {".xlsx", ".xls", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf"}
EXCLUDED_DIR_NAMES = {".git", ".openclaw", "__pycache__", "scripts", "skills", "skills-repo"}
PRODUCT_STOPWORDS = {"狗"}
PARTY_STOPWORDS = {"-", "工厂"}
FILE_TEXT_LIMIT = 50000


@dataclass(frozen=True)
class ArtifactGroup:
    content_hash: str
    primary_path: Path
    all_paths: tuple[Path, ...]
    category: str
    source_bucket: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import legacy order history into the new local-first system.")
    parser.add_argument("--data-root", default=None, help="Override the order data root.")
    parser.add_argument("--legacy-root", default=str(LEGACY_ROOT_DEFAULT), help="Path to the archived legacy-imports root.")
    parser.add_argument("--skip-session-messages", action="store_true", help="Skip importing per-message legacy session user turns.")
    return parser.parse_args()


def load_known_entities(connection: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    orders = [
        {
            "sales_order_id": int(row[0]),
            "order_no": str(row[1] or "").strip(),
            "customer_name": str(row[2] or "").strip(),
            "product_name": str(row[3] or "").strip(),
        }
        for row in connection.execute(
            """
            SELECT sales_order_id, order_no, customer_name, product_name
            FROM sales_orders
            ORDER BY sales_order_id
            """
        )
    ]
    parties = [
        {
            "party_id": int(row[0]),
            "party_name": str(row[1] or "").strip(),
            "party_role": str(row[2] or "").strip(),
        }
        for row in connection.execute(
            """
            SELECT party_id, party_name, party_role
            FROM parties
            ORDER BY party_id
            """
        )
    ]
    products = [
        {
            "product_id": int(row[0]),
            "product_name": str(row[1] or "").strip(),
        }
        for row in connection.execute(
            """
            SELECT product_id, product_name
            FROM products
            ORDER BY product_id
            """
        )
    ]
    return {"orders": orders, "parties": parties, "products": products}


def read_binary_hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_text_like(path: Path) -> bool:
    if path.suffix.lower() in TEXT_EXTENSIONS:
        return True
    if path.suffix.lower() in BINARY_EXTENSIONS:
        return False
    try:
        path.read_text(encoding="utf-8")
        return True
    except UnicodeDecodeError:
        return False


def should_include_file(path: Path) -> bool:
    suffix = path.suffix.lower()
    if suffix in {".py", ".sh", ".pyc", ".skill"}:
        return False
    if suffix in TEXT_EXTENSIONS | BINARY_EXTENSIONS:
        return True
    if suffix:
        return False
    return is_text_like(path)


def extract_text(path: Path) -> tuple[str | None, int]:
    if not is_text_like(path):
        return None, 0
    text = path.read_text(encoding="utf-8", errors="replace")
    return text, len(text)


def category_from_path(root: Path, path: Path) -> tuple[str, str]:
    relative = path.relative_to(root)
    relative_text = relative.as_posix()
    if "recovered-agents-order/sessions/" in relative_text:
        return "legacy_session_transcript", "legacy-recovered-agent-sessions"
    if "recovered-workspace-agents-order/memory/" in relative_text or "/memory/" in relative_text:
        return "legacy_memory", "legacy-memory"
    if "沟通记录/" in relative_text:
        return "legacy_communication_log", "legacy-communication-log"
    if "products/" in relative_text:
        return "legacy_product_artifact", "legacy-product-artifact"
    if "/work/" in relative_text:
        return "legacy_work_artifact", "legacy-work-artifact"
    if "bitable-data/" in relative_text or path.suffix.lower() == ".csv":
        return "legacy_csv_snapshot", "legacy-csv-snapshot"
    if "财务" in path.name:
        return "legacy_finance_doc", "legacy-finance-doc"
    return "legacy_business_doc", "legacy-business-doc"


def preferred_path(paths: list[Path]) -> Path:
    def score(path: Path) -> tuple[int, int, str]:
        text = path.as_posix()
        bucket_score = 9
        if "recovered-workspace-agents-order" in text:
            bucket_score = 0
        elif "workspace-order/memory/" in text:
            bucket_score = 1
        elif "workspace-order/沟通记录/" in text:
            bucket_score = 2
        elif "workspace-order/products/" in text:
            bucket_score = 3
        elif "workspace-order/work/" in text:
            bucket_score = 4
        elif "workspace-order/bitable-data/" in text:
            bucket_score = 5
        elif "workspace-order/backup/" in text:
            bucket_score = 6
        elif "recovered-agents-order/sessions/" in text:
            bucket_score = 7
        return (bucket_score, len(text), text)

    return sorted(paths, key=score)[0]


def discover_legacy_artifact_files(legacy_root: Path) -> list[Path]:
    files: set[Path] = set()
    workspace_root = legacy_root / "workspace-order"
    recovered_workspace_root = legacy_root / "recovered-workspace-agents-order"
    recovered_agents_root = legacy_root / "recovered-agents-order"

    def add_recursive(base: Path) -> None:
        if not base.exists():
            return
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            if any(part in EXCLUDED_DIR_NAMES for part in path.relative_to(base).parts):
                continue
            if not should_include_file(path):
                continue
            files.add(path)

    for relative in [
        Path("memory"),
        Path("沟通记录"),
        Path("products"),
        Path("work"),
        Path("bitable-data"),
        Path("backup/bitable-data"),
        Path("backup/memory"),
        Path("backup/沟通记录"),
    ]:
        add_recursive(workspace_root / relative)

    for path in workspace_root.iterdir():
        if not path.is_file():
            continue
        if path.name.startswith("."):
            continue
        if not should_include_file(path):
            continue
        files.add(path)

    for path in recovered_workspace_root.iterdir():
        if path.is_file() and should_include_file(path):
            files.add(path)
    add_recursive(recovered_workspace_root / "memory")

    sessions_dir = recovered_agents_root / "sessions"
    if sessions_dir.exists():
        sessions_index = sessions_dir / "sessions.json"
        if sessions_index.exists():
            files.add(sessions_index)
        for path in sessions_dir.glob("*.jsonl"):
            files.add(path)

    return sorted(files)


def group_artifacts(legacy_root: Path, paths: list[Path]) -> list[ArtifactGroup]:
    grouped: dict[str, list[Path]] = defaultdict(list)
    for path in paths:
        grouped[read_binary_hash(path)].append(path)

    artifacts: list[ArtifactGroup] = []
    for content_hash, group_paths in grouped.items():
        primary = preferred_path(group_paths)
        category, source_bucket = category_from_path(legacy_root, primary)
        artifacts.append(
            ArtifactGroup(
                content_hash=content_hash,
                primary_path=primary,
                all_paths=tuple(sorted(group_paths)),
                category=category,
                source_bucket=source_bucket,
            )
        )
    artifacts.sort(key=lambda item: item.primary_path.as_posix())
    return artifacts


def short_text(value: str | None) -> str:
    return value.strip() if value else ""


def collect_entity_hints(text: str, refs: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    hints = {"sales_orders": [], "parties": [], "products": []}

    for row in refs["orders"]:
        matched_on: list[str] = []
        order_no = short_text(row["order_no"])
        customer_name = short_text(row["customer_name"])
        product_name = short_text(row["product_name"])
        if order_no and (len(order_no) >= 4 or not order_no.isdigit()) and order_no in text:
            matched_on.append(f"order_no:{order_no}")
        if customer_name and len(customer_name) >= 2 and customer_name in text:
            matched_on.append(f"customer_name:{customer_name}")
        if product_name and len(product_name) >= 2 and product_name not in PRODUCT_STOPWORDS and product_name in text:
            matched_on.append(f"product_name:{product_name}")
        if matched_on:
            hints["sales_orders"].append(
                {
                    "sales_order_id": row["sales_order_id"],
                    "order_no": order_no,
                    "customer_name": customer_name,
                    "product_name": product_name,
                    "matched_on": matched_on,
                }
            )

    for row in refs["parties"]:
        party_name = short_text(row["party_name"])
        if len(party_name) < 2 or party_name in PARTY_STOPWORDS:
            continue
        if party_name in text:
            hints["parties"].append(
                {
                    "party_id": row["party_id"],
                    "party_name": party_name,
                    "party_role": row["party_role"],
                }
            )

    for row in refs["products"]:
        product_name = short_text(row["product_name"])
        if len(product_name) < 2 or product_name in PRODUCT_STOPWORDS:
            continue
        if product_name in text:
            hints["products"].append(
                {
                    "product_id": row["product_id"],
                    "product_name": product_name,
                }
            )

    return hints


def candidate_links_from_hints(hints: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    links: list[dict[str, Any]] = []
    for item in hints["sales_orders"]:
        links.append(
            {
                "target_type": "sales_order",
                "target_key": str(item["sales_order_id"]),
                "confidence_score": 0.72 if any(token.startswith("order_no:") for token in item["matched_on"]) else 0.58,
                "reason": "legacy history text contains known sales-order reference",
            }
        )
    for item in hints["parties"]:
        links.append(
            {
                "target_type": "party",
                "target_key": str(item["party_id"]),
                "confidence_score": 0.52,
                "reason": "legacy history text contains known party name",
            }
        )
    for item in hints["products"]:
        links.append(
            {
                "target_type": "product",
                "target_key": str(item["product_id"]),
                "confidence_score": 0.52,
                "reason": "legacy history text contains known product name",
            }
        )
    return links


def is_session_bootstrap_prompt(text: str) -> bool:
    normalized = text.strip()
    return normalized.startswith("A new session was started via /new or /reset.")


def existing_source_message_ids(connection: sqlite3.Connection, channel_type: str) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            """
            SELECT source_message_id
            FROM inbox_items
            WHERE channel_type = ?
              AND source_message_id IS NOT NULL
              AND source_message_id != ''
            """,
            (channel_type,),
        )
    }


def ensure_thread_and_candidates(
    data_root: Path,
    *,
    inbox_item_id: str,
    category: str,
    category_title: str,
    session_key: str | None,
    summary_text: str | None,
    candidate_links: list[dict[str, Any]],
    extra_thread: dict[str, str] | None = None,
) -> None:
    connection = connect_db(data_root)
    now = utc_now()
    thread_id = upsert_object_thread(
        connection,
        object_type="legacy_history",
        object_key=category,
        title=category_title,
        last_summary=summary_text,
        now=now,
    )
    connection.execute(
        """
        INSERT OR IGNORE INTO object_thread_items (
            object_thread_id, inbox_item_id, link_role, linked_at
        ) VALUES (?, ?, ?, ?)
        """,
        (thread_id, inbox_item_id, "source", now),
    )
    if extra_thread and extra_thread.get("object_key"):
        extra_thread_id = upsert_object_thread(
            connection,
            object_type=str(extra_thread["object_type"]),
            object_key=str(extra_thread["object_key"]),
            title=extra_thread.get("title"),
            last_summary=summary_text,
            now=now,
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO object_thread_items (
                object_thread_id, inbox_item_id, link_role, linked_at
            ) VALUES (?, ?, ?, ?)
            """,
            (extra_thread_id, inbox_item_id, "source", now),
        )
    for candidate in candidate_links:
        connection.execute(
            """
            INSERT INTO link_candidates (
                link_candidate_id, inbox_item_id, target_type, target_key,
                confidence_score, candidate_reason, candidate_status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"lc_legacy_{sha256(f'{inbox_item_id}:{candidate['target_type']}:{candidate['target_key']}'.encode('utf-8')).hexdigest()[:24]}",
                inbox_item_id,
                candidate["target_type"],
                candidate["target_key"],
                candidate["confidence_score"],
                candidate["reason"],
                "provisional",
                now,
            ),
        )
    connection.execute(
        """
        INSERT INTO audit_log (
            object_type, object_id, action_type, actor_label, new_value_json, reason_text, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "inbox_item",
            inbox_item_id,
            "legacy_history_linked",
            "legacy-history-importer",
            json.dumps(
                {
                    "category": category,
                    "session_key": session_key,
                    "candidate_count": len(candidate_links),
                },
                ensure_ascii=False,
            ),
            "Linked legacy history inbox item into object threads and provisional candidates.",
            now,
        ),
    )
    connection.commit()
    connection.close()


def artifact_payload(
    *,
    legacy_root: Path,
    group: ArtifactGroup,
    text: str | None,
    full_text_chars: int,
    hints: dict[str, list[dict[str, Any]]],
    run_id: str,
) -> dict[str, Any]:
    primary_relative = group.primary_path.relative_to(legacy_root).as_posix()
    all_relatives = [path.relative_to(legacy_root).as_posix() for path in group.all_paths]
    return {
        "legacy_history": {
            "run_id": run_id,
            "import_kind": "artifact_file",
            "category": group.category,
            "source_bucket": group.source_bucket,
            "content_hash": group.content_hash,
            "primary_path": primary_relative,
            "source_paths": all_relatives,
            "source_count": len(all_relatives),
            "text_extracted": text is not None,
            "full_text_chars": full_text_chars,
        },
        "entity_hints": hints,
    }


def import_artifact_group(
    *,
    data_root: Path,
    legacy_root: Path,
    group: ArtifactGroup,
    refs: dict[str, list[dict[str, Any]]],
    existing_ids: set[str],
    run_id: str,
) -> tuple[bool, str]:
    source_message_id = f"legacy-file:{group.content_hash}"
    if source_message_id in existing_ids:
        return False, source_message_id

    extracted_text, full_text_chars = extract_text(group.primary_path)
    raw_text = None
    if extracted_text:
        raw_text = extracted_text[:FILE_TEXT_LIMIT]
        if len(extracted_text) > FILE_TEXT_LIMIT:
            raw_text += f"\n\n[legacy-history truncated, full_text_chars={len(extracted_text)}]"

    hints = collect_entity_hints(extracted_text or group.primary_path.stem, refs)
    candidate_links = [] if group.category == "legacy_session_transcript" else candidate_links_from_hints(hints)
    payload = artifact_payload(
        legacy_root=legacy_root,
        group=group,
        text=extracted_text,
        full_text_chars=full_text_chars,
        hints=hints,
        run_id=run_id,
    )
    result = persist_input(
        data_root=data_root,
        channel_type="legacy_history_artifact",
        channel_session_key=f"legacy-artifact:{group.category}",
        source_actor="legacy-history-importer",
        source_message_id=source_message_id,
        raw_text=raw_text,
        raw_payload=payload,
        attachments=[
            {
                "path": str(group.primary_path),
                "mime_type": None,
                "extracted_text": extracted_text,
            }
        ],
    )
    ensure_thread_and_candidates(
        data_root,
        inbox_item_id=str(result["inbox_item_id"]),
        category=group.category,
        category_title=group.category.replace("_", " "),
        session_key=f"legacy-artifact:{group.category}",
        summary_text=f"{group.primary_path.name} imported from legacy history",
        candidate_links=candidate_links,
    )
    existing_ids.add(source_message_id)
    return True, source_message_id


def iter_session_user_messages(session_path: Path) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for line in session_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        if event.get("type") != "message":
            continue
        message = event.get("message") or {}
        if message.get("role") != "user":
            continue
        content = message.get("content") or []
        text_parts = [str(item.get("text") or "") for item in content if item.get("type") == "text"]
        joined = "\n".join(part for part in text_parts if part).strip()
        if not joined:
            continue
        if is_session_bootstrap_prompt(joined):
            continue
        messages.append(
            {
                "event_id": str(event.get("id") or ""),
                "timestamp": event.get("timestamp"),
                "raw_text": joined,
            }
        )
    return messages


def import_session_messages(
    *,
    data_root: Path,
    legacy_root: Path,
    session_dir: Path,
    refs: dict[str, list[dict[str, Any]]],
    existing_ids: set[str],
    run_id: str,
) -> dict[str, int]:
    summary = {"imported_messages": 0, "skipped_messages": 0, "session_files": 0}
    for session_path in sorted(session_dir.glob("*.jsonl")):
        summary["session_files"] += 1
        session_id = session_path.stem
        user_messages = iter_session_user_messages(session_path)
        for item in user_messages:
            source_message_id = f"legacy-session-user:{session_id}:{item['event_id']}"
            if source_message_id in existing_ids:
                summary["skipped_messages"] += 1
                continue
            hints = collect_entity_hints(item["raw_text"], refs)
            candidate_links = candidate_links_from_hints(hints)
            payload = {
                "legacy_history": {
                    "run_id": run_id,
                    "import_kind": "session_user_message",
                    "session_id": session_id,
                    "session_path": session_path.relative_to(legacy_root).as_posix(),
                    "event_id": item["event_id"],
                    "event_timestamp": item["timestamp"],
                },
                "entity_hints": hints,
            }
            result = persist_input(
                data_root=data_root,
                channel_type="legacy_order_session_message",
                channel_session_key=f"legacy-session:{session_id}",
                source_actor="legacy-session-user",
                source_message_id=source_message_id,
                raw_text=item["raw_text"],
                raw_payload=payload,
                attachments=None,
            )
            ensure_thread_and_candidates(
                data_root,
                inbox_item_id=str(result["inbox_item_id"]),
                category="legacy_session_user_history",
                category_title="legacy session user history",
                session_key=f"legacy-session:{session_id}",
                summary_text=f"user message imported from legacy session {session_id}",
                candidate_links=candidate_links,
                extra_thread={
                    "object_type": "legacy_session",
                    "object_key": session_id,
                    "title": f"legacy session {session_id}",
                },
            )
            existing_ids.add(source_message_id)
            summary["imported_messages"] += 1
    return summary


def write_import_metadata(data_root: Path, summary: dict[str, Any]) -> None:
    connection = connect_db(data_root)
    connection.execute(
        "INSERT OR REPLACE INTO import_metadata (key, value) VALUES (?, ?)",
        ("legacy_history.last_run_summary", json.dumps(summary, ensure_ascii=False)),
    )
    connection.execute(
        "INSERT OR REPLACE INTO import_metadata (key, value) VALUES (?, ?)",
        ("legacy_history.last_run_at", utc_now()),
    )
    connection.commit()
    connection.close()


def write_report(data_root: Path, summary: dict[str, Any]) -> Path:
    report_path = data_root / "reports" / f"legacy-history-import-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
    lines = [
        f"# Legacy History Import {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        f"- Run ID: `{summary['run_id']}`",
        f"- Legacy root: `{summary['legacy_root']}`",
        f"- Imported artifact files: `{summary['artifact_imported']}`",
        f"- Skipped artifact files: `{summary['artifact_skipped']}`",
        f"- Unique artifact groups scanned: `{summary['artifact_groups']}`",
        f"- Imported session user messages: `{summary['session_imported_messages']}`",
        f"- Skipped session user messages: `{summary['session_skipped_messages']}`",
        f"- Session files scanned: `{summary['session_files_scanned']}`",
        "",
        "## Categories",
        "",
    ]
    for category, count in sorted(summary["category_counts"].items()):
        lines.append(f"- `{category}`: `{count}`")
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- Legacy files are preserved in raw inbox + evidence layers, not forced into formal order facts.",
            "- Legacy session user messages were imported as independent raw inputs to match the messy historical intake model.",
            "- Candidate links are provisional only; no automatic formal order reassignment was made during this import.",
            "",
        ]
    )
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


def main() -> int:
    args = parse_args()
    data_root = resolve_data_root(args.data_root)
    legacy_root = Path(args.legacy_root).expanduser().resolve()
    initialize_runtime(data_root)
    base_connection = connect_db(data_root)
    refs = load_known_entities(base_connection)
    existing_artifact_ids = existing_source_message_ids(base_connection, "legacy_history_artifact")
    existing_session_ids = existing_source_message_ids(base_connection, "legacy_order_session_message")
    base_connection.close()

    run_id = f"legacy-history-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    artifact_paths = discover_legacy_artifact_files(legacy_root)
    grouped_artifacts = group_artifacts(legacy_root, artifact_paths)

    artifact_imported = 0
    artifact_skipped = 0
    category_counts: dict[str, int] = defaultdict(int)
    for group in grouped_artifacts:
        imported, _ = import_artifact_group(
            data_root=data_root,
            legacy_root=legacy_root,
            group=group,
            refs=refs,
            existing_ids=existing_artifact_ids,
            run_id=run_id,
        )
        if imported:
            artifact_imported += 1
            category_counts[group.category] += 1
        else:
            artifact_skipped += 1

    session_summary = {"imported_messages": 0, "skipped_messages": 0, "session_files": 0}
    if not args.skip_session_messages:
        session_summary = import_session_messages(
            data_root=data_root,
            legacy_root=legacy_root,
            session_dir=legacy_root / "recovered-agents-order" / "sessions",
            refs=refs,
            existing_ids=existing_session_ids,
            run_id=run_id,
        )
        if session_summary["imported_messages"]:
            category_counts["legacy_session_user_history"] += session_summary["imported_messages"]

    summary = {
        "status": "ok",
        "run_id": run_id,
        "data_root": str(data_root),
        "legacy_root": str(legacy_root),
        "artifact_groups": len(grouped_artifacts),
        "artifact_imported": artifact_imported,
        "artifact_skipped": artifact_skipped,
        "session_imported_messages": session_summary["imported_messages"],
        "session_skipped_messages": session_summary["skipped_messages"],
        "session_files_scanned": session_summary["session_files"],
        "category_counts": dict(sorted(category_counts.items())),
    }
    write_import_metadata(data_root, summary)
    report_path = write_report(data_root, summary)
    summary["report_path"] = str(report_path)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
