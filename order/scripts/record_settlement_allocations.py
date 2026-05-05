#!/usr/bin/env python3
"""Record one or more settlement allocations for a cash transaction."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from runtime_common import resolve_data_root
from runtime_flow import record_settlement_allocations


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Allocate one cash transaction across receivable/payable targets.")
    parser.add_argument("--data-root", help="Override the default order data root.")
    parser.add_argument("--payload-file", required=True, help="JSON payload with cash_transaction_id and allocations.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = json.loads(Path(args.payload_file).read_text(encoding="utf-8"))
    result = record_settlement_allocations(
        data_root=resolve_data_root(args.data_root),
        cash_transaction_id=int(payload["cash_transaction_id"]),
        allocations=list(payload["allocations"]),
        actor_label=payload.get("actor_label"),
        replace_existing=bool(payload.get("replace_existing", True)),
        require_full_amount=bool(payload.get("require_full_amount", False)),
        dry_run=bool(payload.get("dry_run", False)),
        confirm_token=payload.get("confirm_token"),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
