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

sys.path.insert(0, str(Path(__file__).resolve().parent))

from device_http import http_base_url, http_get_bytes, resolve_host
from gaggimate_ws import DEFAULT_HOST, GaggimateWsClient, parse_host
from shot_index import ShotIndexEntry, fetch_active_shots

ROOT = Path(__file__).resolve().parent.parent
DATA_P = ROOT / "data" / "p"
DATA_H = ROOT / "data" / "h"
SNAPSHOT_ROOT = ROOT / "device-data" / "snapshots"


def pad_shot_id(shot_id: str) -> str:
    return str(shot_id).zfill(6)


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
        print(f"  profile {profile.id} ({profile.label!r})", flush=True)

    return len(profiles), selected_id


def list_shots(
    history_url: str,
    client: GaggimateWsClient | None,
    http_timeout: float,
) -> tuple[list[ShotIndexEntry], str]:
    """Prefer HTTP index.bin (fast); fall back to WebSocket directory scan."""
    shots = fetch_active_shots(history_url, http_timeout)
    if shots is not None:
        return shots, "index.bin"

    if client is None:
        raise RuntimeError("index.bin missing and no WebSocket client for fallback")

    print("  (no index.bin — falling back to req:history:list, may be slow)", file=sys.stderr)
    ws_shots = client.list_history()
    entries = [
        ShotIndexEntry(
            id=int(shot.get("id", 0)),
            timestamp=int(shot.get("timestamp", 0)),
            duration=int(shot.get("duration", 0)),
            deleted=False,
            has_notes=False,
        )
        for shot in ws_shots
    ]
    return entries, "websocket"


def pull_history(
    history_url: str,
    shots: list[ShotIndexEntry],
    source: str,
    dest: Path,
    *,
    dry_run: bool,
    http_timeout: float,
) -> int:
    print(f"  {len(shots)} shot(s) from {source}", flush=True)

    if dry_run:
        print(f"Would pull {len(shots)} shot(s) + index.bin to {dest}")
        for shot in shots[:20]:
            print(f"  {pad_shot_id(shot.id)}.slog")
        if len(shots) > 20:
            print(f"  ... and {len(shots) - 20} more")
        return len(shots)

    wipe_dir(dest)

    index_bytes = http_get_bytes(f"{history_url.rstrip('/')}/index.bin", http_timeout)
    if index_bytes is not None:
        (dest / "index.bin").write_bytes(index_bytes)
        print(f"  index.bin ({len(index_bytes)} bytes)", flush=True)

    for index, shot in enumerate(shots, start=1):
        shot_id = str(shot.id)
        padded = pad_shot_id(shot_id)
        print(f"  downloading {padded}.slog ({index}/{len(shots)})...", flush=True)
        slog_bytes = http_get_bytes(f"{history_url.rstrip('/')}/{padded}.slog", http_timeout)
        if slog_bytes is None:
            print(f"  WARN: missing {padded}.slog", file=sys.stderr)
        else:
            (dest / f"{padded}.slog").write_bytes(slog_bytes)

        if shot.has_notes:
            notes_url = f"{history_url.rstrip('/')}/{shot_id}.json"
            notes_bytes = http_get_bytes(notes_url, http_timeout)
            if notes_bytes is None:
                notes_bytes = http_get_bytes(f"{history_url.rstrip('/')}/{padded}.json", http_timeout)
            if notes_bytes is not None:
                (dest / f"{padded}.json").write_bytes(notes_bytes)

    if index_bytes is None:
        print("  WARN: index.bin not found on device", file=sys.stderr)

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
        help=f"Hostname or IP (default: {DEFAULT_HOST} or $GAGGIMATE_HOST)",
    )
    parser.add_argument("--dry-run", action="store_true", help="List counts only; do not write files")
    parser.add_argument("--no-snapshot", action="store_true", help="Skip device-data/snapshots archive")
    parser.add_argument("--timeout", type=float, default=15.0, help="WebSocket connect timeout (seconds)")
    parser.add_argument("--http-timeout", type=float, default=30.0, help="HTTP download timeout per file (seconds)")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    host, port = parse_host(args.host)

    try:
        ip = resolve_host(host, port)
    except OSError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2

    history_url = f"{http_base_url(host, port)}/api/history"
    print(f"Pulling SPIFFS user data from {host} ({ip})...")

    device_version = "unknown"
    profile_count = 0
    shot_count = 0
    selected_id: str | None = None

    try:
        with GaggimateWsClient(host, port, timeout=args.timeout) as client:
            print("Profiles:", flush=True)
            profile_count, selected_id = pull_profiles(client, DATA_P, dry_run=args.dry_run)
            try:
                ota = client.fetch_ota_settings()
                device_version = str(ota.get("displayVersion") or "unknown")
            except TimeoutError:
                print("  WARN: could not read device version", file=sys.stderr)
        # WebSocket closed before HTTP bulk transfer (reduces ESP32 contention)

        print("Shot history:", flush=True)
        print("  reading shot list from index.bin...", flush=True)
        shots = fetch_active_shots(history_url, args.http_timeout)
        if shots is None:
            with GaggimateWsClient(host, port, timeout=args.timeout) as fallback_client:
                shots, source = list_shots(history_url, fallback_client, args.http_timeout)
        else:
            source = "index.bin"
        shot_count = pull_history(
            history_url,
            shots,
            source,
            DATA_H,
            dry_run=args.dry_run,
            http_timeout=args.http_timeout,
        )
    except (TimeoutError, ConnectionError, OSError) as error:
        print(f"Error: {error}", file=sys.stderr)
        print(
            f"Could not reach Gaggimate at {host} ({ip}). "
            "Try --host with the device IP if mDNS is slow.",
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
            host=f"{host} ({ip})",
            profile_count=profile_count,
            shot_count=shot_count,
            selected_profile_id=selected_id,
        )
        print(f"Snapshot: {snapshot_dir.relative_to(ROOT)}")

    print(f"Wrote {DATA_P.relative_to(ROOT)}/ and {DATA_H.relative_to(ROOT)}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
