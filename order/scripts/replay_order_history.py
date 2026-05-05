#!/usr/bin/env python3
"""Replay historical order inputs by session key or object thread."""

from __future__ import annotations

import argparse
import json

from history_common import replay_history


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay historical order inputs by session key or object thread.")
    parser.add_argument("--data-root", help="Override the order data root.")
    parser.add_argument("--channel-session-key", help="Replay all items for one channel_session_key.")
    parser.add_argument("--object-thread-id", help="Replay all items linked to one object_thread_id.")
    parser.add_argument("--object-type", help="Resolve thread by object_type together with --object-key.")
    parser.add_argument("--object-key", help="Resolve thread by object_key together with --object-type.")
    parser.add_argument("--limit", type=int, default=50, help="Maximum number of replay items to return.")
    parser.add_argument("--max-text-chars", type=int, default=180, help="Maximum preview length per replay item.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = replay_history(
            data_root=args.data_root,
            channel_session_key=args.channel_session_key,
            object_thread_id=args.object_thread_id,
            object_type=args.object_type,
            object_key=args.object_key,
            limit=max(args.limit, 1),
            max_text_chars=max(args.max_text_chars, 80),
        )
    except ValueError as exc:
        raise SystemExit(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
