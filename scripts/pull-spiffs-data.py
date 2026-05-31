#!/usr/bin/env python3
"""
Pull profiles and shot history from a Gaggimate device into data/p and data/h
for SPIFFS build, with an optional device-data snapshot archive.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gaggimate_ws import DEFAULT_HOST, GaggimateWsClient, parse_host

ROOT = Path(__file__).resolve().parent.parent
DATA_P = ROOT / "data" / "p"
DATA_H = ROOT / "data" / "h"
SNAPSHOT_ROOT = ROOT / "device-data" / "snapshots"


def pad_shot_id(shot_id: str) -> str:
    return str(shot_id).zfill(6)


def http_get_bytes(url: str, timeout: float) -> bytes | None:
    try:
        with urlopen(url, timeout=timeout) as response:
            if response.status != 200:
                return None
            return response.read()
    except HTTPError as error:
        if error.code == 404:
            return None
        raise
    except URLError:
        raise


def wipe_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)


def copy_tree(src: Path, dest: Path) -> None:
    if not src.exists():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def pull_profiles(
    client: GaggimateWsClient,
    dest: Path,
    *,
    dry_run: bool,
) -> tuple[int, str | None]:
    profiles = client.list_profiles()
    selected_id = next((profile.id for profile in profiles if profile.selected), None)

    if dry_run:
        print(f"Would pull {len(profiles)} profile(s) to {dest}")
        for profile in profiles:
            print(f"  {profile.id}\t{profile.label!r}")
        return len(profiles), selected_id

    wipe_dir(dest)
    for profile in profiles:
        body = client.load_profile(profile.id)
        write_json(dest / f"{profile.id}.json", body)
        print(f"  profile {profile.id} ({profile.label!r})")

    return len(profiles), selected_id


def pull_history(
    host: str,
    client: GaggimateWsClient,
    dest: Path,
    *,
    dry_run: bool,
    http_timeout: float,
) -> int:
    shots = client.list_history()
    base_url = f"http://{host}/api/history"

    if dry_run:
        print(f"Would pull {len(shots)} shot(s) + index.bin to {dest}")
        for shot in shots[:20]:
            shot_id = str(shot.get("id", ""))
            print(f"  {pad_shot_id(shot_id)}.slog")
        if len(shots) > 20:
            print(f"  ... and {len(shots) - 20} more")
        print(f"  GET {base_url}/index.bin")
        return len(shots)

    wipe_dir(dest)

    for index, shot in enumerate(shots, start=1):
        shot_id = str(shot.get("id", ""))
        padded = pad_shot_id(shot_id)
        slog_url = f"{base_url}/{padded}.slog"
        slog_bytes = http_get_bytes(slog_url, http_timeout)
        if slog_bytes is None:
            print(f"  WARN: missing {padded}.slog", file=sys.stderr)
        else:
            (dest / f"{padded}.slog").write_bytes(slog_bytes)

        notes_url = f"{base_url}/{shot_id}.json"
        notes_bytes = http_get_bytes(notes_url, http_timeout)
        if notes_bytes is None:
            notes_url = f"{base_url}/{padded}.json"
            notes_bytes = http_get_bytes(notes_url, http_timeout)
        if notes_bytes is not None:
            (dest / f"{padded}.json").write_bytes(notes_bytes)

        if index % 10 == 0 or index == len(shots):
            print(f"  shots {index}/{len(shots)}")

    index_bytes = http_get_bytes(f"{base_url}/index.bin", http_timeout)
    if index_bytes is None:
        print("  WARN: index.bin not found on device", file=sys.stderr)
    else:
        (dest / "index.bin").write_bytes(index_bytes)
        print(f"  index.bin ({len(index_bytes)} bytes)")

    return len(shots)


def snapshot_pull(
    *,
    device_version: str,
    host: str,
    profile_count: int,
    shot_count: int,
    selected_profile_id: str | None,
) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe_version = device_version.replace("/", "-") or "unknown"
    snapshot_dir = SNAPSHOT_ROOT / f"{safe_version}-{timestamp}"
    copy_tree(DATA_P, snapshot_dir / "p")
    copy_tree(DATA_H, snapshot_dir / "h")
    manifest = {
        "host": host,
        "pulledAt": datetime.now(timezone.utc).isoformat(),
        "deviceVersion": device_version,
        "selectedProfileId": selected_profile_id,
        "profileCount": profile_count,
        "shotCount": shot_count,
        "snapshot": str(snapshot_dir.relative_to(ROOT)),
    }
    write_json(snapshot_dir / "manifest.json", manifest)
    return snapshot_dir


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Pull device profiles and shot history into data/p and data/h.",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("GAGGIMATE_HOST", DEFAULT_HOST),
        help=f"Device hostname (default: {DEFAULT_HOST})",
    )
    parser.add_argument("--dry-run", action="store_true", help="List counts only; do not write files")
    parser.add_argument("--no-snapshot", action="store_true", help="Skip device-data/snapshots archive")
    parser.add_argument("--timeout", type=float, default=15.0, help="WebSocket connect timeout (seconds)")
    parser.add_argument("--http-timeout", type=float, default=60.0, help="HTTP download timeout (seconds)")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    host, port = parse_host(args.host)
    http_host = host if port == 80 else f"{host}:{port}"

    print(f"Pulling SPIFFS user data from {http_host}...")

    try:
        with GaggimateWsClient(host, port, timeout=args.timeout) as client:
            ota = client.fetch_ota_settings()
            device_version = str(ota.get("displayVersion") or "unknown")

            print("Profiles:")
            profile_count, selected_id = pull_profiles(client, DATA_P, dry_run=args.dry_run)

            print("Shot history:")
            shot_count = pull_history(
                http_host,
                client,
                DATA_H,
                dry_run=args.dry_run,
                http_timeout=args.http_timeout,
            )
    except (TimeoutError, ConnectionError, OSError, URLError) as error:
        print(f"Error: {error}", file=sys.stderr)
        print(
            f"Could not reach Gaggimate at {host}:{port}. "
            "Check Wi‑Fi or pass --host with the device IP.",
            file=sys.stderr,
        )
        return 2
    except RuntimeError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    print(f"\nSummary: {profile_count} profile(s), {shot_count} shot(s)")

    if args.dry_run:
        return 0

    if not args.no_snapshot:
        snapshot_dir = snapshot_pull(
            device_version=device_version,
            host=http_host,
            profile_count=profile_count,
            shot_count=shot_count,
            selected_profile_id=selected_id,
        )
        print(f"Snapshot: {snapshot_dir.relative_to(ROOT)}")

    print(f"Wrote {DATA_P.relative_to(ROOT)}/ and {DATA_H.relative_to(ROOT)}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
