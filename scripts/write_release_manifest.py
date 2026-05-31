#!/usr/bin/env python3
"""Write out/release-manifest.json after a firmware build."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


def latest_snapshot(root: Path) -> str | None:
    snapshots = root / "device-data" / "snapshots"
    if not snapshots.is_dir():
        return None
    candidates = sorted(snapshots.iterdir(), key=lambda path: path.stat().st_mtime, reverse=True)
    for candidate in candidates:
        if candidate.is_dir() and (candidate / "manifest.json").is_file():
            return str(candidate.relative_to(root))
    return None


def count_files(path: Path, pattern: str) -> int:
    if not path.is_dir():
        return 0
    return sum(1 for _ in path.glob(pattern))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()

    out = args.root / "out"
    manifest = {
        "version": args.version,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "spiffs": {
            "profiles": count_files(args.root / "data" / "p", "*.json"),
            "shots": count_files(args.root / "data" / "h", "*.slog"),
            "snapshot": latest_snapshot(args.root),
        },
        "artifacts": {
            "display-firmware": (out / "display-firmware.bin").is_file(),
            "display-filesystem": (out / "display-filesystem.bin").is_file(),
            "board-firmware": (out / "board-firmware.bin").is_file(),
        },
    }

    out.mkdir(parents=True, exist_ok=True)
    path = out / "release-manifest.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"  {path.relative_to(args.root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
