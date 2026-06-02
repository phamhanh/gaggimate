#!/usr/bin/env python3
"""Unattended five-scenario PID tuning ladder (Plan 2). See docs/pid-tuning-agent.md."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gaggimate_ws import DEFAULT_HOST, parse_host
from pid_tune.config import load_config
from pid_tune.runner import run_full_ladder


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run unattended S1→S5 PID tuning ladder over WebSocket"
    )
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--resume", action="store_true", help="Continue from device-data/pid-tune/state.json")
    parser.add_argument("--dry-run", action="store_true", help="Print ladder plan only; no device changes")
    parser.add_argument("--max-hours", type=float, default=None, help="Abort after N hours (safety cap)")
    parser.add_argument(
        "--only",
        default=None,
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args()

    host, port = parse_host(args.host)
    config = load_config()

    result = run_full_ladder(
        host,
        port,
        config,
        resume=args.resume,
        dry_run=args.dry_run,
        max_hours=args.max_hours,
        only=args.only,
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (TimeoutError, ConnectionError, RuntimeError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1) from error
