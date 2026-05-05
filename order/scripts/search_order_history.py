#!/usr/bin/env python3
"""Search historical order inputs stored in the local-first runtime."""

from __future__ import annotations

import argparse
import json

from history_common import search_history


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Search historical order inputs and legacy evidence.")
    parser.add_argument("--data-root", help="Override the order data root.")
    parser.add_argument(
        "--query",
        nargs="+",
        help="Search text. Supports multiple words without extra quoting. When omitted, returns recent items matching the filters.",
    )
    parser.add_argument("--limit", type=int, default=10, help="Maximum number of results to return.")
    parser.add_argument("--channel-type", action="append", dest="channel_types", help="Filter by channel type. Repeatable.")
    parser.add_argument("--category", action="append", dest="categories", help="Filter by legacy-history category. Repeatable.")
    parser.add_argument("--legacy-only", action="store_true", help="Restrict results to items with legacy-history metadata.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = search_history(
            data_root=args.data_root,
            query=" ".join(args.query) if args.query else None,
            limit=max(args.limit, 1),
            channel_types=args.channel_types,
            categories=args.categories,
            legacy_only=args.legacy_only,
        )
    except ValueError as exc:
        raise SystemExit(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
