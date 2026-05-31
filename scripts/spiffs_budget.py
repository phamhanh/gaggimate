#!/usr/bin/env python3
"""Check SPIFFS data directory size against display partition budget."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# LilyGo-T-RGB default_16MB.csv spiffs partition
SPIFFS_PARTITION_BYTES = 0x360000
# Reserve headroom for SPIFFS metadata and file headers
SPIFFS_METADATA_RESERVE = 128 * 1024


def dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for entry in path.rglob("*"):
        if entry.is_file():
            total += entry.stat().st_size
    return total


def check_data_tree(root: Path) -> tuple[int, int, dict[str, int]]:
    parts = {
        "w": dir_size(root / "data" / "w"),
        "p": dir_size(root / "data" / "p"),
        "h": dir_size(root / "data" / "h"),
    }
    total = sum(parts.values())
    budget = SPIFFS_PARTITION_BYTES - SPIFFS_METADATA_RESERVE
    return total, budget, parts


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate SPIFFS data size before buildfs")
    parser.add_argument(
        "command",
        nargs="?",
        choices=("check",),
        default="check",
        help="Only 'check' is supported",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="Repository root (default: parent of scripts/)",
    )
    args = parser.parse_args()

    total, budget, parts = check_data_tree(args.root)
    print(f"SPIFFS data: {total:,} bytes (budget {budget:,} bytes)")
    print(f"  data/w: {parts['w']:,} bytes")
    print(f"  data/p: {parts['p']:,} bytes")
    print(f"  data/h: {parts['h']:,} bytes")

    if total > budget:
        over = total - budget
        print(
            f"\nERROR: SPIFFS data exceeds partition budget by {over:,} bytes.",
            file=sys.stderr,
        )
        print(
            "Prune old shots on the device, use --display-only release, or deploy with a smaller history.",
            file=sys.stderr,
        )
        return 1

    print("SPIFFS data fits partition budget.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
