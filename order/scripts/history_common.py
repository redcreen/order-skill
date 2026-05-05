#!/usr/bin/env python3
"""Shared helpers for order history retrieval."""

from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict
from typing import Any

from runtime_common import connect_db, initialize_runtime, resolve_data_root


WHITESPACE_RE = re.compile(r"\s+")


def parse_json(text: str | None) -> dict[str, Any] | list[Any] | None:
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def normalized_text(value: str | None) -> str:
    return WHITESPACE_RE.sub(" ", (value or "")).strip()


def tokenize_query(query: str | None) -> list[str]:
    if not query:
        return []
    return [token.lower() for token in normalized_text(query).split(" ") if token]


def snippet(text: str | None, tokens: list[str], *, width: int = 180) -> str:
    normalized = normalized_text(text)
    if not normalized:
        return ""
    if not tokens:
        return normalized[:width]
    lowered = normalized.lower()
    positions = [lowered.find(token) for token in tokens if token in lowered]
    if not positions:
        return normalized[:width]
    start = max(min(positions) - width // 3, 0)
    end = min(start + width, len(normalized))
    if end - start < width and start > 0:
        start = max(end - width, 0)
    excerpt = normalized[start:end]
    if start > 0:
        excerpt = "..." + excerpt
    if end < len(normalized):
        excerpt = excerpt + "..."
    return excerpt


def inbox_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    payload = parse_json(row["raw_payload_json"])
    legacy_history = payload.get("legacy_history") if isinstance(payload, dict) else None
    return {
        "inbox_item_id": str(row["inbox_item_id"]),
        "channel_type": str(row["channel_type"]),
        "channel_session_key": row["channel_session_key"],
        "source_actor": row["source_actor"],
        "source_message_id": row["source_message_id"],
        "content_type": str(row["content_type"]),
        "raw_text": row["raw_text"],
        "raw_payload": payload,
        "raw_archive_path": row["raw_archive_path"],
        "received_at": str(row["received_at"]),
        "created_at": str(row["created_at"]),
        "legacy_history": legacy_history if isinstance(legacy_history, dict) else None,
    }


def load_support_maps(connection: sqlite3.Connection) -> tuple[dict[str, list[dict[str, Any]]], dict[str, int], dict[str, list[dict[str, Any]]]]:
    evidence_map: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in connection.execute(
        """
        SELECT inbox_item_id, evidence_asset_id, file_name, mime_type, local_path, file_hash, source_path, extracted_text, created_at
        FROM evidence_assets
        ORDER BY rowid
        """
    ):
        evidence_map[str(row["inbox_item_id"])].append(
            {
                "evidence_asset_id": str(row["evidence_asset_id"]),
                "file_name": row["file_name"],
                "mime_type": row["mime_type"],
                "local_path": row["local_path"],
                "file_hash": row["file_hash"],
                "source_path": row["source_path"],
                "extracted_text": row["extracted_text"],
                "created_at": row["created_at"],
            }
        )

    candidate_count_map: dict[str, int] = defaultdict(int)
    for row in connection.execute(
        """
        SELECT inbox_item_id, COUNT(*) AS candidate_count
        FROM link_candidates
        GROUP BY inbox_item_id
        """
    ):
        candidate_count_map[str(row["inbox_item_id"])] = int(row["candidate_count"])

    thread_map: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in connection.execute(
        """
        SELECT oti.inbox_item_id, ot.object_thread_id, ot.object_type, ot.object_key, ot.title, ot.last_summary
        FROM object_thread_items oti
        JOIN object_threads ot ON ot.object_thread_id = oti.object_thread_id
        ORDER BY ot.object_type, ot.object_key
        """
    ):
        thread_map[str(row["inbox_item_id"])].append(
            {
                "object_thread_id": str(row["object_thread_id"]),
                "object_type": str(row["object_type"]),
                "object_key": str(row["object_key"]),
                "title": row["title"],
                "last_summary": row["last_summary"],
            }
        )

    return evidence_map, candidate_count_map, thread_map


def load_inbox_record(connection: sqlite3.Connection, *, inbox_item_id: str | None = None, source_message_id: str | None = None) -> dict[str, Any]:
    if bool(inbox_item_id) == bool(source_message_id):
        raise ValueError("Provide exactly one of inbox_item_id or source_message_id.")
    if inbox_item_id:
        row = connection.execute("SELECT * FROM inbox_items WHERE inbox_item_id = ?", (inbox_item_id,)).fetchone()
    else:
        row = connection.execute("SELECT * FROM inbox_items WHERE source_message_id = ?", (source_message_id,)).fetchone()
    if not row:
        raise ValueError("History item not found.")
    base = inbox_row_to_dict(row)
    evidence_map, _, thread_map = load_support_maps(connection)
    base["evidence_assets"] = evidence_map.get(base["inbox_item_id"], [])
    base["object_threads"] = thread_map.get(base["inbox_item_id"], [])
    base["candidate_links"] = [
        {
            "target_type": str(item["target_type"]),
            "target_key": str(item["target_key"]),
            "confidence_score": item["confidence_score"],
            "candidate_reason": item["candidate_reason"],
            "candidate_status": item["candidate_status"],
            "created_at": item["created_at"],
        }
        for item in connection.execute(
            """
            SELECT target_type, target_key, confidence_score, candidate_reason, candidate_status, created_at
            FROM link_candidates
            WHERE inbox_item_id = ?
            ORDER BY confidence_score DESC, rowid ASC
            """,
            (base["inbox_item_id"],),
        )
    ]
    return base


def build_search_document(item: dict[str, Any], evidence_assets: list[dict[str, Any]]) -> str:
    parts: list[str] = [
        str(item.get("source_message_id") or ""),
        str(item.get("channel_type") or ""),
        str(item.get("channel_session_key") or ""),
        str(item.get("source_actor") or ""),
        normalized_text(item.get("raw_text")),
        json.dumps(item.get("legacy_history") or {}, ensure_ascii=False),
        json.dumps(item.get("raw_payload") or {}, ensure_ascii=False),
    ]
    for asset in evidence_assets:
        parts.extend(
            [
                str(asset.get("file_name") or ""),
                str(asset.get("source_path") or ""),
                normalized_text(asset.get("extracted_text")),
            ]
        )
    return "\n".join(part for part in parts if part).lower()


def search_history(
    *,
    data_root: str | None,
    query: str | None,
    limit: int,
    channel_types: list[str] | None,
    categories: list[str] | None,
    legacy_only: bool,
) -> dict[str, Any]:
    resolved_root = resolve_data_root(data_root)
    initialize_runtime(resolved_root)
    connection = connect_db(resolved_root)
    evidence_map, candidate_count_map, thread_map = load_support_maps(connection)
    rows = connection.execute("SELECT * FROM inbox_items ORDER BY received_at DESC, rowid DESC").fetchall()
    tokens = tokenize_query(query)
    category_filter = {item for item in (categories or []) if item}
    channel_filter = {item for item in (channel_types or []) if item}
    results: list[dict[str, Any]] = []

    for row in rows:
        item = inbox_row_to_dict(row)
        category = (item.get("legacy_history") or {}).get("category") if item.get("legacy_history") else None
        if legacy_only and not category:
            continue
        if category_filter and category not in category_filter:
            continue
        if channel_filter and item["channel_type"] not in channel_filter:
            continue

        evidence_assets = evidence_map.get(item["inbox_item_id"], [])
        haystack = build_search_document(item, evidence_assets)
        if tokens and not all(token in haystack for token in tokens):
            continue

        raw_text = normalized_text(item.get("raw_text"))
        evidence_text = next(
            (normalized_text(asset.get("extracted_text")) for asset in evidence_assets if normalized_text(asset.get("extracted_text"))),
            "",
        )
        preview_text = raw_text or evidence_text or json.dumps(item.get("legacy_history") or {}, ensure_ascii=False)
        score = 0.0
        if tokens:
            score = float(sum(haystack.count(token) for token in tokens))
            if raw_text:
                score += 0.2 * sum(raw_text.lower().count(token) for token in tokens)
        results.append(
            {
                "inbox_item_id": item["inbox_item_id"],
                "channel_type": item["channel_type"],
                "channel_session_key": item["channel_session_key"],
                "source_actor": item["source_actor"],
                "source_message_id": item["source_message_id"],
                "received_at": item["received_at"],
                "legacy_history": item.get("legacy_history"),
                "preview": snippet(preview_text, tokens),
                "evidence_count": len(evidence_assets),
                "candidate_link_count": candidate_count_map.get(item["inbox_item_id"], 0),
                "object_threads": thread_map.get(item["inbox_item_id"], []),
                "score": round(score, 3),
            }
        )

    results.sort(key=lambda item: (item["score"], item["received_at"]), reverse=True)
    connection.close()
    return {
        "status": "ok",
        "data_root": str(resolved_root),
        "query": query,
        "limit": limit,
        "result_count": min(len(results), limit),
        "results": results[:limit],
    }


def show_history_item(
    *,
    data_root: str | None,
    inbox_item_id: str | None,
    source_message_id: str | None,
    max_text_chars: int,
    include_evidence_text: bool,
) -> dict[str, Any]:
    resolved_root = resolve_data_root(data_root)
    initialize_runtime(resolved_root)
    connection = connect_db(resolved_root)
    item = load_inbox_record(connection, inbox_item_id=inbox_item_id, source_message_id=source_message_id)
    raw_text = normalized_text(item.get("raw_text"))
    item["raw_text_preview"] = raw_text[:max_text_chars] if raw_text else ""
    if raw_text and len(raw_text) > max_text_chars:
        item["raw_text_preview"] += f"... [truncated at {max_text_chars} chars]"
    item.pop("raw_text", None)
    if not include_evidence_text:
        for asset in item["evidence_assets"]:
            extracted = normalized_text(asset.get("extracted_text"))
            asset["extracted_text_preview"] = extracted[:max_text_chars] if extracted else ""
            if extracted and len(extracted) > max_text_chars:
                asset["extracted_text_preview"] += f"... [truncated at {max_text_chars} chars]"
            asset.pop("extracted_text", None)
    connection.close()
    return {"status": "ok", "item": item}


def resolve_thread_id(connection: sqlite3.Connection, *, object_thread_id: str | None, object_type: str | None, object_key: str | None) -> str:
    if object_thread_id:
        return object_thread_id
    if not object_type or not object_key:
        raise ValueError("Provide object_thread_id or object_type + object_key.")
    row = connection.execute(
        """
        SELECT object_thread_id
        FROM object_threads
        WHERE object_type = ? AND object_key = ?
        """,
        (object_type, object_key),
    ).fetchone()
    if not row:
        raise ValueError("History thread not found.")
    return str(row["object_thread_id"])


def replay_history(
    *,
    data_root: str | None,
    channel_session_key: str | None,
    object_thread_id: str | None,
    object_type: str | None,
    object_key: str | None,
    limit: int,
    max_text_chars: int,
) -> dict[str, Any]:
    resolved_root = resolve_data_root(data_root)
    initialize_runtime(resolved_root)
    connection = connect_db(resolved_root)
    if bool(channel_session_key) == bool(object_thread_id or object_type or object_key):
        if not channel_session_key and not (object_thread_id or object_type or object_key):
            raise ValueError("Provide channel_session_key or object thread selectors.")
        if channel_session_key and (object_thread_id or object_type or object_key):
            raise ValueError("Choose either channel_session_key or object thread selectors.")

    rows: list[sqlite3.Row]
    replay_target: dict[str, Any]
    if channel_session_key:
        replay_target = {"mode": "channel_session_key", "channel_session_key": channel_session_key}
        rows = connection.execute(
            """
            SELECT *
            FROM inbox_items
            WHERE channel_session_key = ?
            ORDER BY received_at ASC, rowid ASC
            LIMIT ?
            """,
            (channel_session_key, limit),
        ).fetchall()
    else:
        resolved_thread_id = resolve_thread_id(
            connection,
            object_thread_id=object_thread_id,
            object_type=object_type,
            object_key=object_key,
        )
        thread_row = connection.execute(
            """
            SELECT object_thread_id, object_type, object_key, title, last_summary
            FROM object_threads
            WHERE object_thread_id = ?
            """,
            (resolved_thread_id,),
        ).fetchone()
        replay_target = {
            "mode": "object_thread",
            "object_thread_id": resolved_thread_id,
            "object_type": thread_row["object_type"] if thread_row else None,
            "object_key": thread_row["object_key"] if thread_row else None,
            "title": thread_row["title"] if thread_row else None,
            "last_summary": thread_row["last_summary"] if thread_row else None,
        }
        rows = connection.execute(
            """
            SELECT i.*
            FROM object_thread_items oti
            JOIN inbox_items i ON i.inbox_item_id = oti.inbox_item_id
            WHERE oti.object_thread_id = ?
            ORDER BY i.received_at ASC, oti.rowid ASC
            LIMIT ?
            """,
            (resolved_thread_id, limit),
        ).fetchall()

    results: list[dict[str, Any]] = []
    for row in rows:
        item = inbox_row_to_dict(row)
        raw_text = normalized_text(item.get("raw_text"))
        results.append(
            {
                "inbox_item_id": item["inbox_item_id"],
                "received_at": item["received_at"],
                "channel_type": item["channel_type"],
                "channel_session_key": item["channel_session_key"],
                "source_actor": item["source_actor"],
                "source_message_id": item["source_message_id"],
                "legacy_history": item.get("legacy_history"),
                "preview": raw_text[:max_text_chars] if raw_text else "",
            }
        )
    connection.close()
    return {
        "status": "ok",
        "data_root": str(resolved_root),
        "replay_target": replay_target,
        "item_count": len(results),
        "items": results,
    }
