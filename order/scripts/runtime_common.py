#!/usr/bin/env python3
"""Shared helpers for the order runtime foundation."""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_DATA_ROOT = Path.home() / "Documents" / "openclaw-order"
INTENT_REQUIREMENTS = {
    "sales_order": ["customer_name", "product_name", "qty"],
    "payment_receipt": ["direction", "amount"],
    "shipment": ["shipment_type"],
    "supplier_payable": ["supplier_name", "amount", "payable_type"],
    "production_arrangement": ["customer_name", "product_name", "qty", "factory_name"],
    "receivable_record": ["receivable_type", "amount_due", "due_date"],
    "payable_record": ["supplier_name", "payable_type", "amount_due", "due_date"],
    "cash_transaction_record": ["direction", "amount", "transaction_date"],
    "work_order_record": ["work_type", "planned_qty", "planned_due_at"],
    "return_case": ["case_type", "reason_text"],
    "refund_record": ["refund_amount"],
    "supplier_deduction_record": ["supplier_name", "deduction_amount", "deduction_reason"],
}
INTENT_ASSOCIATION_REQUIREMENTS = {
    "payment_receipt": ["sales_order"],
    "shipment": ["sales_order"],
    "supplier_payable": ["sales_order", "production_lot"],
    "production_arrangement": ["sales_order"],
    "receivable_record": ["sales_order"],
    "payable_record": ["sales_order"],
    "work_order_record": ["sales_order"],
    "return_case": ["sales_order"],
    "refund_record": ["sales_order"],
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def schema_path() -> Path:
    return repo_root() / "order" / "runtime" / "schema_v1.sql"


def resolve_data_root(raw: str | None = None) -> Path:
    return Path(raw).expanduser().resolve() if raw else DEFAULT_DATA_ROOT.resolve()


def ensure_runtime_dirs(data_root: Path) -> dict[str, Path]:
    dirs = {
        "root": data_root,
        "db": data_root / "db",
        "raw": data_root / "raw",
        "attachments": data_root / "attachments",
        "exports": data_root / "exports",
        "reports": data_root / "reports",
        "logs": data_root / "logs",
    }
    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)
    return dirs


def db_path(data_root: Path) -> Path:
    return data_root / "db" / "order.db"


def connect_db(data_root: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path(data_root))
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON;")
    return connection


def table_columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    return {str(row["name"]) for row in connection.execute(f"PRAGMA table_info({table_name})")}


def apply_runtime_migrations(connection: sqlite3.Connection) -> None:
    inbox_columns = table_columns(connection, "inbox_items")
    if "raw_archive_path" not in inbox_columns:
        connection.execute("ALTER TABLE inbox_items ADD COLUMN raw_archive_path TEXT")


def initialize_runtime(data_root: Path) -> dict[str, str]:
    dirs = ensure_runtime_dirs(data_root)
    schema_sql = schema_path().read_text(encoding="utf-8")
    connection = connect_db(data_root)
    connection.executescript(schema_sql)
    apply_runtime_migrations(connection)
    connection.execute(
        "INSERT OR REPLACE INTO runtime_metadata (key, value) VALUES (?, ?)",
        ("initialized_at", utc_now()),
    )
    connection.execute(
        "INSERT OR REPLACE INTO runtime_metadata (key, value) VALUES (?, ?)",
        ("data_root", str(data_root)),
    )
    connection.commit()
    connection.close()
    return {key: str(value) for key, value in dirs.items()} | {"db_path": str(db_path(data_root))}


def sha256_for_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def json_dumps(value: object, *, indent: int | None = None) -> str:
    return json.dumps(value, ensure_ascii=False, indent=indent, default=str)


def upsert_object_thread(
    connection: sqlite3.Connection,
    *,
    object_type: str,
    object_key: str,
    title: str | None,
    last_summary: str | None,
    now: str,
) -> str:
    row = connection.execute(
        """
        SELECT object_thread_id
        FROM object_threads
        WHERE object_type = ? AND object_key = ?
        """,
        (object_type, object_key),
    ).fetchone()
    if row:
        object_thread_id = row["object_thread_id"]
        connection.execute(
            """
            UPDATE object_threads
            SET title = COALESCE(?, title),
                last_summary = COALESCE(?, last_summary),
                last_active_at = ?
            WHERE object_thread_id = ?
            """,
            (title, last_summary, now, object_thread_id),
        )
        return str(object_thread_id)

    object_thread_id = f"thread_{uuid.uuid4().hex}"
    connection.execute(
        """
        INSERT INTO object_threads (
            object_thread_id, object_type, object_key, title, last_summary, last_active_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (object_thread_id, object_type, object_key, title, last_summary, now),
    )
    return object_thread_id


def normalize_field_entries(
    raw_fields: dict[str, object] | list[dict[str, object]] | None,
    *,
    source_turn_ref: str | None,
) -> list[dict[str, object]]:
    if not raw_fields:
        return []

    normalized: list[dict[str, object]] = []
    if isinstance(raw_fields, dict):
        iterable = []
        for field_name, value in raw_fields.items():
            if isinstance(value, dict):
                item = {"field_name": field_name} | value
            else:
                item = {"field_name": field_name, "value": value}
            iterable.append(item)
    else:
        iterable = raw_fields

    for item in iterable:
        field_name = item.get("field_name")
        if not field_name:
            continue
        field_value = item.get("value")
        if field_value is None:
            field_value = item.get("field_value")
        normalized.append(
            {
                "field_name": field_name,
                "field_value": None if field_value is None else str(field_value),
                "value_source_type": item.get("value_source_type", "user_input"),
                "source_turn_ref": item.get("source_turn_ref", source_turn_ref),
                "confidence_score": item.get("confidence_score"),
                "is_required": 1 if item.get("is_required") else 0,
                "is_confirmed": 1 if item.get("is_confirmed") else 0,
            }
        )
    return normalized


def persist_input(
    *,
    data_root: Path,
    channel_type: str,
    channel_session_key: str | None,
    source_actor: str | None,
    source_message_id: str | None,
    raw_text: str | None,
    raw_payload: dict | list | None,
    attachments: list[dict[str, str]] | None,
) -> dict[str, object]:
    initialize_runtime(data_root)
    connection = connect_db(data_root)
    inbox_item_id = f"inbox_{uuid.uuid4().hex}"
    received_at = utc_now()
    payload_json = json_dumps(raw_payload) if raw_payload is not None else None
    content_type = "multipart" if attachments else "text"
    raw_day_dir = data_root / "raw" / datetime.now().strftime("%Y%m%d")
    raw_day_dir.mkdir(parents=True, exist_ok=True)
    raw_archive_path = raw_day_dir / f"{inbox_item_id}.json"
    connection.execute(
        """
        INSERT INTO inbox_items (
            inbox_item_id, channel_type, channel_session_key, source_actor,
            source_message_id, content_type, raw_text, raw_payload_json,
            raw_archive_path, received_at, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            inbox_item_id,
            channel_type,
            channel_session_key,
            source_actor,
            source_message_id,
            content_type,
            raw_text,
            payload_json,
            str(raw_archive_path),
            received_at,
            received_at,
        ),
    )

    saved_assets: list[dict[str, str]] = []
    attachments_dir = data_root / "attachments" / datetime.now().strftime("%Y%m%d")
    attachments_dir.mkdir(parents=True, exist_ok=True)

    for item in attachments or []:
        src = Path(item["path"]).expanduser().resolve()
        evidence_asset_id = f"asset_{uuid.uuid4().hex}"
        destination = attachments_dir / f"{evidence_asset_id}-{src.name}"
        shutil.copy2(src, destination)
        file_hash = sha256_for_file(destination)
        connection.execute(
            """
            INSERT INTO evidence_assets (
                evidence_asset_id, inbox_item_id, file_name, mime_type,
                local_path, file_hash, source_path, extracted_text, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                evidence_asset_id,
                inbox_item_id,
                src.name,
                item.get("mime_type"),
                str(destination),
                file_hash,
                str(src),
                item.get("extracted_text"),
                received_at,
            ),
        )
        saved_assets.append(
            {
                "evidence_asset_id": evidence_asset_id,
                "local_path": str(destination),
                "file_hash": file_hash,
            }
        )

    raw_archive_path.write_text(
        json_dumps(
            {
                "inbox_item_id": inbox_item_id,
                "channel_type": channel_type,
                "channel_session_key": channel_session_key,
                "source_actor": source_actor,
                "source_message_id": source_message_id,
                "content_type": content_type,
                "raw_text": raw_text,
                "raw_payload": raw_payload,
                "attachments": saved_assets,
                "received_at": received_at,
            },
            indent=2,
        ),
        encoding="utf-8",
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
            "persisted",
            source_actor,
            json_dumps({"raw_archive_path": str(raw_archive_path), "attachment_count": len(saved_assets)}),
            "Persist inbound order-related input before interpretation.",
            received_at,
        ),
    )
    connection.commit()
    connection.close()

    return {
        "status": "persisted",
        "inbox_item_id": inbox_item_id,
        "raw_archive_path": str(raw_archive_path),
        "attachment_count": len(saved_assets),
        "attachments": saved_assets,
    }


def open_guided_intake_draft(
    *,
    data_root: Path,
    inbox_item_id: str,
    intent_type: str,
    target_object_type: str | None,
    target_action: str | None,
    summary_text: str | None,
    draft_fields: dict[str, object] | list[dict[str, object]] | None,
    thread: dict[str, str] | None,
    candidate_links: list[dict[str, object]] | None,
    pending_targets: list[dict[str, object]] | None,
    required_fields: list[str] | None,
    actor_label: str | None,
) -> dict[str, object]:
    initialize_runtime(data_root)
    connection = connect_db(data_root)
    now = utc_now()
    inbox_row = connection.execute(
        """
        SELECT channel_type, channel_session_key
        FROM inbox_items
        WHERE inbox_item_id = ?
        """,
        (inbox_item_id,),
    ).fetchone()
    if not inbox_row:
        connection.close()
        raise ValueError(f"Unknown inbox_item_id: {inbox_item_id}")

    session_row = connection.execute(
        """
        SELECT intake_session_id
        FROM intake_sessions
        WHERE channel_type = ?
          AND COALESCE(channel_session_key, '') = COALESCE(?, '')
          AND COALESCE(intent_type, '') = COALESCE(?, '')
          AND session_status IN ('collecting', 'needs_confirmation')
        ORDER BY last_active_at DESC
        LIMIT 1
        """,
        (inbox_row["channel_type"], inbox_row["channel_session_key"], intent_type),
    ).fetchone()
    if session_row:
        intake_session_id = str(session_row["intake_session_id"])
        connection.execute(
            """
            UPDATE intake_sessions
            SET summary_text = COALESCE(?, summary_text),
                last_active_at = ?
            WHERE intake_session_id = ?
            """,
            (summary_text, now, intake_session_id),
        )
    else:
        intake_session_id = f"intake_{uuid.uuid4().hex}"
        connection.execute(
            """
            INSERT INTO intake_sessions (
                intake_session_id, channel_type, channel_session_key, intent_type,
                session_status, summary_text, started_at, last_active_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                intake_session_id,
                str(inbox_row["channel_type"]),
                inbox_row["channel_session_key"],
                intent_type,
                "collecting",
                summary_text,
                now,
                now,
            ),
        )

    connection.execute(
        """
        INSERT OR IGNORE INTO intake_session_items (
            intake_session_id, inbox_item_id, link_role, linked_at
        ) VALUES (?, ?, ?, ?)
        """,
        (intake_session_id, inbox_item_id, "source", now),
    )

    draft_row = connection.execute(
        """
        SELECT workflow_draft_id
        FROM workflow_drafts
        WHERE intake_session_id = ?
          AND COALESCE(target_object_type, '') = COALESCE(?, '')
          AND COALESCE(target_action, '') = COALESCE(?, '')
          AND draft_status IN ('collecting', 'needs_confirmation')
        ORDER BY updated_at DESC
        LIMIT 1
        """,
        (intake_session_id, target_object_type, target_action),
    ).fetchone()
    if draft_row:
        workflow_draft_id = str(draft_row["workflow_draft_id"])
    else:
        workflow_draft_id = f"draft_{uuid.uuid4().hex}"
        connection.execute(
            """
            INSERT INTO workflow_drafts (
                workflow_draft_id, intake_session_id, target_object_type,
                target_action, draft_status, confidence_score, preview_json,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                workflow_draft_id,
                intake_session_id,
                target_object_type,
                target_action,
                "collecting",
                None,
                None,
                now,
                now,
            ),
        )

    connection.execute(
        """
        INSERT OR IGNORE INTO draft_source_links (
            workflow_draft_id, inbox_item_id, link_role, linked_at
        ) VALUES (?, ?, ?, ?)
        """,
        (workflow_draft_id, inbox_item_id, "source", now),
    )

    object_thread_id = None
    if thread and thread.get("object_type") and thread.get("object_key"):
        object_thread_id = upsert_object_thread(
            connection,
            object_type=str(thread["object_type"]),
            object_key=str(thread["object_key"]),
            title=thread.get("title"),
            last_summary=summary_text,
            now=now,
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO object_thread_items (
                object_thread_id, inbox_item_id, link_role, linked_at
            ) VALUES (?, ?, ?, ?)
            """,
            (object_thread_id, inbox_item_id, "source", now),
        )

    field_entries = normalize_field_entries(draft_fields, source_turn_ref=inbox_item_id)
    for field in field_entries:
        existing = connection.execute(
            """
            SELECT draft_field_value_id
            FROM draft_field_values
            WHERE workflow_draft_id = ?
              AND field_name = ?
              AND COALESCE(source_turn_ref, '') = COALESCE(?, '')
            LIMIT 1
            """,
            (workflow_draft_id, field["field_name"], field["source_turn_ref"]),
        ).fetchone()
        if existing:
            connection.execute(
                """
                UPDATE draft_field_values
                SET field_value = ?, value_source_type = ?, confidence_score = ?,
                    is_required = ?, is_confirmed = ?
                WHERE draft_field_value_id = ?
                """,
                (
                    field["field_value"],
                    field["value_source_type"],
                    field["confidence_score"],
                    field["is_required"],
                    field["is_confirmed"],
                    existing["draft_field_value_id"],
                ),
            )
        else:
            connection.execute(
                """
                INSERT INTO draft_field_values (
                    draft_field_value_id, workflow_draft_id, field_name, field_value,
                    value_source_type, source_turn_ref, confidence_score,
                    is_required, is_confirmed
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"dfv_{uuid.uuid4().hex}",
                    workflow_draft_id,
                    field["field_name"],
                    field["field_value"],
                    field["value_source_type"],
                    field["source_turn_ref"],
                    field["confidence_score"],
                    field["is_required"],
                    field["is_confirmed"],
                ),
            )

    required_names = set(INTENT_REQUIREMENTS.get(intent_type, []))
    if required_fields:
        required_names.update(required_fields)
    for field in field_entries:
        if field["is_required"]:
            required_names.add(str(field["field_name"]))

    captured_names = {
        str(field["field_name"])
        for field in field_entries
        if field.get("field_value") not in (None, "", "None")
    }
    missing_required_fields = sorted(required_names - captured_names)

    required_associations = set(INTENT_ASSOCIATION_REQUIREMENTS.get(intent_type, []))
    explicit_targets = {str(item.get("target_type")) for item in (candidate_links or []) if item.get("target_type")}
    explicit_targets.update(
        {str(item.get("target_type")) for item in (pending_targets or []) if item.get("target_type")}
    )
    if thread and thread.get("object_type"):
        explicit_targets.add(str(thread["object_type"]))
    unresolved_targets: list[dict[str, object]] = list(pending_targets or [])
    for target_type in sorted(required_associations - explicit_targets):
        unresolved_targets.append(
            {
                "target_type": target_type,
                "target_key": None,
                "reason_text": f"{intent_type} still needs an explicit {target_type} association.",
            }
        )

    for item in candidate_links or []:
        target_type = item.get("target_type")
        target_key = item.get("target_key")
        if not target_type or not target_key:
            continue
        existing_candidate = connection.execute(
            """
            SELECT link_candidate_id, confidence_score
            FROM link_candidates
            WHERE inbox_item_id = ? AND target_type = ? AND target_key = ?
            LIMIT 1
            """,
            (inbox_item_id, str(target_type), str(target_key)),
        ).fetchone()
        if existing_candidate:
            connection.execute(
                """
                UPDATE link_candidates
                SET confidence_score = COALESCE(?, confidence_score),
                    candidate_reason = COALESCE(?, candidate_reason),
                    candidate_status = ?
                WHERE link_candidate_id = ?
                """,
                (
                    item.get("confidence_score"),
                    item.get("reason"),
                    item.get("candidate_status", "provisional"),
                    existing_candidate["link_candidate_id"],
                ),
            )
        else:
            connection.execute(
                """
                INSERT INTO link_candidates (
                    link_candidate_id, inbox_item_id, target_type, target_key,
                    confidence_score, candidate_reason, candidate_status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"lc_{uuid.uuid4().hex}",
                    inbox_item_id,
                    str(target_type),
                    str(target_key),
                    item.get("confidence_score"),
                    item.get("reason"),
                    item.get("candidate_status", "provisional"),
                    now,
                ),
            )

    unresolved_pairs = {
        (str(item.get("target_type")), str(item.get("target_key") or ""))
        for item in unresolved_targets
        if item.get("target_type")
    }

    for item in unresolved_targets:
        target_type = item.get("target_type")
        if not target_type:
            continue
        normalized_target_key = str(item.get("target_key") or "")
        existing_pending = connection.execute(
            """
            SELECT pending_association_id
            FROM pending_associations
            WHERE inbox_item_id = ?
              AND target_type = ?
              AND COALESCE(target_key, '') = ?
              AND association_status != 'confirmed'
            LIMIT 1
            """,
            (inbox_item_id, str(target_type), normalized_target_key),
        ).fetchone()
        if existing_pending:
            connection.execute(
                """
                UPDATE pending_associations
                SET reason_text = COALESCE(?, reason_text)
                WHERE pending_association_id = ?
                """,
                (item.get("reason_text"), existing_pending["pending_association_id"]),
            )
        else:
            connection.execute(
                """
                INSERT INTO pending_associations (
                    pending_association_id, inbox_item_id, target_type, target_key,
                    association_status, reason_text, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"pa_{uuid.uuid4().hex}",
                    inbox_item_id,
                    str(target_type),
                    item.get("target_key"),
                    item.get("association_status", "unresolved"),
                    item.get("reason_text"),
                    now,
                ),
            )

    for target_type, normalized_target_key in sorted(unresolved_pairs):
        rows = connection.execute(
            """
            SELECT pending_association_id
            FROM pending_associations
            WHERE inbox_item_id = ?
              AND target_type = ?
              AND COALESCE(target_key, '') = ?
              AND association_status != 'confirmed'
            ORDER BY created_at ASC, rowid ASC
            """,
            (inbox_item_id, target_type, normalized_target_key),
        ).fetchall()
        for duplicate_row in rows[1:]:
            connection.execute(
                """
                UPDATE pending_associations
                SET association_status = 'confirmed',
                    reason_text = 'merged duplicate pending association'
                WHERE pending_association_id = ?
                """,
                (duplicate_row["pending_association_id"],),
            )

    for row in connection.execute(
        """
        SELECT pending_association_id, target_type, COALESCE(target_key, '') AS normalized_target_key
        FROM pending_associations
        WHERE inbox_item_id = ?
          AND association_status != 'confirmed'
        """,
        (inbox_item_id,),
    ).fetchall():
        pair = (str(row["target_type"]), str(row["normalized_target_key"]))
        if pair in unresolved_pairs:
            continue
        connection.execute(
            """
            UPDATE pending_associations
            SET association_status = 'confirmed',
                reason_text = COALESCE(reason_text, 'superseded by refreshed draft state')
            WHERE pending_association_id = ?
            """,
            (row["pending_association_id"],),
        )

    for field_name in missing_required_fields:
        checkpoint_type = f"missing_field:{field_name}"
        checkpoint_row = connection.execute(
            """
            SELECT draft_checkpoint_id
            FROM draft_checkpoints
            WHERE workflow_draft_id = ? AND checkpoint_type = ? AND checkpoint_status = 'open'
            """,
            (workflow_draft_id, checkpoint_type),
        ).fetchone()
        if not checkpoint_row:
            connection.execute(
                """
                INSERT INTO draft_checkpoints (
                    draft_checkpoint_id, workflow_draft_id, checkpoint_type,
                    prompt_text, checkpoint_status, created_at, resolved_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"dcp_{uuid.uuid4().hex}",
                    workflow_draft_id,
                    checkpoint_type,
                    f"请补充 {field_name}，系统才能继续确认这条 {intent_type} 记录。",
                    "open",
                    now,
                    None,
                ),
            )

    if missing_required_fields:
        placeholders = ",".join("?" for _ in missing_required_fields)
        connection.execute(
            f"""
            UPDATE draft_checkpoints
            SET checkpoint_status = 'resolved', resolved_at = ?
            WHERE workflow_draft_id = ?
              AND checkpoint_status = 'open'
              AND checkpoint_type LIKE 'missing_field:%'
              AND REPLACE(checkpoint_type, 'missing_field:', '') NOT IN ({placeholders})
            """,
            (now, workflow_draft_id, *missing_required_fields),
        )
    else:
        connection.execute(
            """
            UPDATE draft_checkpoints
            SET checkpoint_status = 'resolved', resolved_at = ?
            WHERE workflow_draft_id = ?
              AND checkpoint_status = 'open'
              AND checkpoint_type LIKE 'missing_field:%'
            """,
            (now, workflow_draft_id),
        )

    unresolved_checkpoint_types: list[str] = []
    for item in unresolved_targets:
        target_type = str(item.get("target_type"))
        target_key = item.get("target_key") or "__unknown__"
        checkpoint_type = f"pending_association:{target_type}:{target_key}"
        unresolved_checkpoint_types.append(checkpoint_type)
        checkpoint_row = connection.execute(
            """
            SELECT draft_checkpoint_id
            FROM draft_checkpoints
            WHERE workflow_draft_id = ? AND checkpoint_type = ? AND checkpoint_status = 'open'
            """,
            (workflow_draft_id, checkpoint_type),
        ).fetchone()
        if not checkpoint_row:
            prompt = item.get("reason_text") or f"请确认这条输入对应的 {target_type}。"
            connection.execute(
                """
                INSERT INTO draft_checkpoints (
                    draft_checkpoint_id, workflow_draft_id, checkpoint_type,
                    prompt_text, checkpoint_status, created_at, resolved_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"dcp_{uuid.uuid4().hex}",
                    workflow_draft_id,
                    checkpoint_type,
                    prompt,
                    "open",
                    now,
                    None,
                ),
            )

    if unresolved_checkpoint_types:
        placeholders = ",".join("?" for _ in unresolved_checkpoint_types)
        connection.execute(
            f"""
            UPDATE draft_checkpoints
            SET checkpoint_status = 'resolved', resolved_at = ?
            WHERE workflow_draft_id = ?
              AND checkpoint_status = 'open'
              AND checkpoint_type LIKE 'pending_association:%'
              AND checkpoint_type NOT IN ({placeholders})
            """,
            (now, workflow_draft_id, *unresolved_checkpoint_types),
        )
    else:
        connection.execute(
            """
            UPDATE draft_checkpoints
            SET checkpoint_status = 'resolved', resolved_at = ?
            WHERE workflow_draft_id = ?
              AND checkpoint_status = 'open'
              AND checkpoint_type LIKE 'pending_association:%'
            """,
            (now, workflow_draft_id),
        )

    preview = {
        "intent_type": intent_type,
        "target_object_type": target_object_type,
        "target_action": target_action,
        "summary_text": summary_text,
        "captured_fields": {field["field_name"]: field["field_value"] for field in field_entries},
        "required_fields": sorted(required_names),
        "missing_required_fields": missing_required_fields,
        "candidate_links": candidate_links or [],
        "pending_associations": unresolved_targets,
        "thread": (
            {
                "object_thread_id": object_thread_id,
                "object_type": thread.get("object_type"),
                "object_key": thread.get("object_key"),
                "title": thread.get("title"),
            }
            if object_thread_id and thread
            else None
        ),
    }
    draft_status = "collecting" if (missing_required_fields or unresolved_targets) else "needs_confirmation"
    connection.execute(
        """
        UPDATE workflow_drafts
        SET draft_status = ?, preview_json = ?, updated_at = ?
        WHERE workflow_draft_id = ?
        """,
        (draft_status, json_dumps(preview, indent=2), now, workflow_draft_id),
    )
    connection.execute(
        """
        UPDATE intake_sessions
        SET session_status = ?, last_active_at = ?
        WHERE intake_session_id = ?
        """,
        (draft_status, now, intake_session_id),
    )
    connection.execute(
        """
        INSERT INTO audit_log (
            object_type, object_id, action_type, actor_label,
            new_value_json, reason_text, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "workflow_draft",
            workflow_draft_id,
            "guided_intake_opened",
            actor_label,
            json_dumps(preview),
            "Opened or updated a guided-intake draft from persisted raw input.",
            now,
        ),
    )
    connection.commit()
    open_checkpoint_count = connection.execute(
        """
        SELECT COUNT(*)
        FROM draft_checkpoints
        WHERE workflow_draft_id = ? AND checkpoint_status = 'open'
        """,
        (workflow_draft_id,),
    ).fetchone()[0]
    connection.close()

    return {
        "status": "draft_opened",
        "intake_session_id": intake_session_id,
        "workflow_draft_id": workflow_draft_id,
        "object_thread_id": object_thread_id,
        "draft_status": draft_status,
        "missing_required_fields": missing_required_fields,
        "pending_association_count": len(unresolved_targets),
        "open_checkpoint_count": open_checkpoint_count,
        "preview": preview,
    }
