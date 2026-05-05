#!/usr/bin/env python3
"""Resolve one pending association and refresh related drafts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from runtime_common import resolve_data_root
from runtime_flow import resolve_pending_association_item


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Resolve one pending association.")
    parser.add_argument("--data-root", help="Override the default order data root.")
    parser.add_argument("--payload-file", required=True, help="JSON payload with pending_association_id and target resolution.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = json.loads(Path(args.payload_file).read_text(encoding="utf-8"))
    result = resolve_pending_association_item(
        data_root=resolve_data_root(args.data_root),
        pending_association_id=payload["pending_association_id"],
        target_key=str(payload["target_key"]),
        reason_text=payload.get("reason_text"),
        actor_label=payload.get("actor_label"),
        thread=payload.get("thread"),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
