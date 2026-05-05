#!/usr/bin/env python3
"""Show one historical order input item in detail."""

from __future__ import annotations

import argparse
import json

from history_common import show_history_item


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Show one historical order input item in detail.")
    parser.add_argument("--data-root", help="Override the order data root.")
    parser.add_argument("--inbox-item-id", help="Lookup by inbox_item_id.")
    parser.add_argument("--source-message-id", help="Lookup by source_message_id.")
    parser.add_argument("--max-text-chars", type=int, default=1200, help="Maximum raw/evidence text preview length.")
    parser.add_argument(
        "--include-evidence-text",
        action="store_true",
        help="Include full evidence extracted_text instead of only preview fields.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = show_history_item(
            data_root=args.data_root,
            inbox_item_id=args.inbox_item_id,
            source_message_id=args.source_message_id,
            max_text_chars=max(args.max_text_chars, 80),
            include_evidence_text=args.include_evidence_text,
        )
    except ValueError as exc:
        raise SystemExit(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
