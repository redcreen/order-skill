#!/usr/bin/env python3
"""Initialize the local-first order runtime."""

from __future__ import annotations

import argparse
import json

from runtime_common import initialize_runtime, resolve_data_root


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Initialize the local-first order runtime.")
    parser.add_argument("--data-root", help="Override the default order data root.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    data_root = resolve_data_root(args.data_root)
    result = initialize_runtime(data_root)
    print(json.dumps({"status": "initialized", "data_root": str(data_root), "paths": result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

