#!/usr/bin/env python3
"""Persist inbound order-related input before interpretation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from runtime_common import persist_input, resolve_data_root


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Persist inbound order-related input into the raw-input layer.")
    parser.add_argument("--data-root", help="Override the default order data root.")
    parser.add_argument("--payload-file", required=True, help="Path to a JSON payload describing the inbound input.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    data_root = resolve_data_root(args.data_root)
    payload = json.loads(Path(args.payload_file).read_text(encoding="utf-8"))
    result = persist_input(
        data_root=data_root,
        channel_type=payload.get("channel_type", "local"),
        channel_session_key=payload.get("channel_session_key"),
        source_actor=payload.get("source_actor"),
        source_message_id=payload.get("source_message_id"),
        raw_text=payload.get("text"),
        raw_payload=payload.get("raw_payload"),
        attachments=payload.get("attachments"),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

