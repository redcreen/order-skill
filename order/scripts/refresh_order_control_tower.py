#!/usr/bin/env python3
"""Refresh derived commitments, followups, alerts, and exceptions."""

from __future__ import annotations

import argparse
import json

from runtime_common import resolve_data_root
from runtime_flow import refresh_control_tower


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh the order control tower.")
    parser.add_argument("--data-root", help="Override the default order data root.")
    parser.add_argument("--as-of-date", help="ISO date for the refresh snapshot.")
    parser.add_argument("--actor-label", help="Actor label for audit log.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = refresh_control_tower(
        data_root=resolve_data_root(args.data_root),
        as_of_date=args.as_of_date,
        actor_label=args.actor_label,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
