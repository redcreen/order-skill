#!/usr/bin/env python3
"""Generate one concise daily order report."""

from __future__ import annotations

import argparse
import json

from runtime_common import resolve_data_root
from runtime_flow import generate_daily_report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate the order daily report.")
    parser.add_argument("--data-root", help="Override the default order data root.")
    parser.add_argument("--report-date", help="ISO date for the report.")
    parser.add_argument("--actor-label", help="Actor label for audit log.")
    parser.add_argument("--skip-refresh", action="store_true", help="Skip control-tower refresh before report generation.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = generate_daily_report(
        data_root=resolve_data_root(args.data_root),
        report_date=args.report_date,
        actor_label=args.actor_label,
        refresh_first=not args.skip_refresh,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
