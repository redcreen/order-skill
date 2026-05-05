#!/usr/bin/env python3
"""Open or update a first-pass guided-intake draft from persisted input."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from runtime_common import open_guided_intake_draft, resolve_data_root


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Open or update an order guided-intake draft.")
    parser.add_argument("--data-root", help="Override the default order data root.")
    parser.add_argument("--payload-file", required=True, help="Path to a JSON payload describing the draft input.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    data_root = resolve_data_root(args.data_root)
    payload = json.loads(Path(args.payload_file).read_text(encoding="utf-8"))
    result = open_guided_intake_draft(
        data_root=data_root,
        inbox_item_id=payload["inbox_item_id"],
        intent_type=payload["intent_type"],
        target_object_type=payload.get("target_object_type"),
        target_action=payload.get("target_action"),
        summary_text=payload.get("summary_text"),
        draft_fields=payload.get("draft_fields"),
        thread=payload.get("thread"),
        candidate_links=payload.get("candidate_links"),
        pending_targets=payload.get("pending_associations"),
        required_fields=payload.get("required_fields"),
        actor_label=payload.get("actor_label"),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
