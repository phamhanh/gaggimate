#!/usr/bin/env python3
"""
Download shot(s) from a Gaggimate device and save as JSON under ./shots.

Default: all non-deleted shots from today (local calendar day).
Use --latest for a single most recent shot only.

Uses HTTP /api/history/index.bin and /api/history/{id}.slog (stdlib only).
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
from datetime import date, datetime, tzinfo
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))

from device_http import http_base_url, http_get_bytes, resolve_host
from gaggimate_ws import DEFAULT_HOST, GaggimateWsClient, parse_host
from parse_binary_shot import parse_binary_shot
from shot_index import ShotIndexEntry, load_active_shots_from_index

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "shots"


def pad_shot_id(shot_id: str | int) -> str:
    return str(shot_id).zfill(6)


def note_url_candidates(history_url: str, shot_id: str | int) -> list[str]:
    base = history_url.rstrip("/")
    padded = pad_shot_id(shot_id)
    raw = str(shot_id)
    urls = [f"{base}/{padded}.json"]
    if raw != padded:
        urls.append(f"{base}/{raw}.json")
    return urls


NOTES_DEFAULTS: dict[str, Any] = {
    "id": "",
    "rating": 0,
    "beanType": "",
    "doseIn": "",
    "doseOut": "",
    "ratio": "",
    "grindSetting": "",
    "balanceTaste": "balanced",
    "notes": "",
}


def round2(value: Any) -> Any:
    if value is None:
        return value
    try:
        number = float(value)
    except (TypeError, ValueError):
        return value
    if number != number:  # NaN
        return value
    return round(number + 1e-12, 2)


def normalize_sample(sample: dict[str, Any]) -> dict[str, Any]:
    return {
        "t": sample.get("t"),
        "tt": round2(sample.get("tt")),
        "ct": round2(sample.get("ct")),
        "tp": round2(sample.get("tp")),
        "cp": round2(sample.get("cp")),
        "fl": round2(sample.get("fl")),
        "tf": round2(sample.get("tf")),
        "pf": round2(sample.get("pf")),
        "vf": round2(sample.get("vf")),
        "v": round2(sample.get("v")),
        "ev": round2(sample.get("ev")),
        "pr": round2(sample.get("pr")),
        "systemInfo": sample.get("systemInfo"),
        "phaseNumber": sample.get("phaseNumber"),
        "phaseDisplayNumber": sample.get("phaseDisplayNumber"),
        "phaseName": sample.get("phaseName"),
    }


def normalize_notes(notes: dict[str, Any] | None, shot_id: str) -> dict[str, Any]:
    merged = {**NOTES_DEFAULTS, **(notes or {}), "id": str(shot_id)}
    return {
        "id": merged["id"],
        "rating": merged.get("rating") or 0,
        "beanType": merged.get("beanType") or "",
        "doseIn": merged.get("doseIn") or "",
        "doseOut": merged.get("doseOut") or "",
        "ratio": merged.get("ratio") or "",
        "grindSetting": merged.get("grindSetting") or "",
        "balanceTaste": merged.get("balanceTaste") or "balanced",
        "notes": merged.get("notes") or "",
    }


def shot_local_date(timestamp: int, tz: tzinfo) -> date | None:
    if timestamp <= 0:
        return None
    return datetime.fromtimestamp(timestamp, tz=tz).date()


def filter_shots_for_day(
    shots: list[ShotIndexEntry],
    day: date,
    tz: tzinfo,
) -> list[ShotIndexEntry]:
    matched = [
        shot
        for shot in shots
        if shot_local_date(shot.timestamp, tz) == day
    ]
    return sorted(matched, key=lambda shot: (shot.timestamp, shot.id))


def pick_latest_shot(shots: list[ShotIndexEntry]) -> ShotIndexEntry:
    if not shots:
        raise RuntimeError("No shots on device (index empty or all deleted)")
    return max(shots, key=lambda shot: (shot.timestamp, shot.id))


def _decode_notes_bytes(notes_bytes: bytes) -> dict[str, Any] | None:
    if notes_bytes[:2] == b"\x1f\x8b":
        try:
            notes_bytes = gzip.decompress(notes_bytes)
        except OSError:
            return None
    try:
        payload = json.loads(notes_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def load_notes_json(history_url: str, shot_id: str, *, http_timeout: float) -> dict[str, Any] | None:
    for notes_url in note_url_candidates(history_url, shot_id):
        notes_bytes = http_get_bytes(notes_url, http_timeout)
        if notes_bytes is None:
            continue
        payload = _decode_notes_bytes(notes_bytes)
        if payload is not None:
            return payload
    return None


def build_export(
    raw_shot: dict[str, Any],
    *,
    index_entry: ShotIndexEntry | None,
    notes: dict[str, Any] | None,
) -> dict[str, Any]:
    shot_id = str(raw_shot.get("id", ""))
    export = {
        **raw_shot,
        "id": shot_id,
        "samples": [normalize_sample(sample) for sample in raw_shot.get("samples") or []],
        "volume": round2(raw_shot.get("volume")),
        "notes": normalize_notes(notes, shot_id),
        "loaded": True,
    }
    if index_entry is not None and index_entry.duration:
        export.setdefault("duration", index_entry.duration)
    return export


def download_shot_json(
    history_url: str,
    shot_id: str,
    out_dir: Path,
    *,
    index_entry: ShotIndexEntry | None,
    http_timeout: float,
) -> Path:
    padded = pad_shot_id(shot_id)
    slog_url = f"{history_url.rstrip('/')}/{padded}.slog"
    slog_bytes = http_get_bytes(slog_url, http_timeout)
    if slog_bytes is None:
        raise RuntimeError(f"Shot file missing: {padded}.slog")

    raw_shot = parse_binary_shot(slog_bytes, shot_id)
    notes = load_notes_json(history_url, shot_id, http_timeout=http_timeout)
    export = build_export(raw_shot, index_entry=index_entry, notes=notes)

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"shot-{shot_id}.json"
    out_path.write_text(json.dumps(export, indent=2) + "\n", encoding="utf-8")
    return out_path


def list_index_shots(history_url: str, http_timeout: float) -> list[ShotIndexEntry] | None:
    return load_active_shots_from_index(history_url, http_timeout)


def ws_shots_for_day(
    client: GaggimateWsClient,
    day: date,
    tz: tzinfo,
) -> list[tuple[str, int]]:
    """Return (shot_id, timestamp) pairs from WebSocket list for the given day."""
    matched: list[tuple[str, int]] = []
    for item in client.list_history():
        timestamp = int(item.get("timestamp") or 0)
        if shot_local_date(timestamp, tz) != day:
            continue
        shot_id = str(item.get("id", ""))
        if shot_id:
            matched.append((shot_id, timestamp))
    return sorted(matched, key=lambda pair: (pair[1], int(pair[0]) if pair[0].isdigit() else 0))


def fetch_shots(
    host: str,
    port: int,
    out_dir: Path,
    *,
    day: date,
    tz: tzinfo,
    latest_only: bool,
    http_timeout: float,
    ws_timeout: float,
) -> list[Path]:
    history_url = f"{http_base_url(host, port)}/api/history"
    index_shots = list_index_shots(history_url, http_timeout)

    if latest_only:
        if index_shots is not None:
            latest = pick_latest_shot(index_shots)
            targets: list[tuple[str, ShotIndexEntry | None]] = [(str(latest.id), latest)]
        else:
            with GaggimateWsClient(host, port, timeout=ws_timeout) as client:
                ws_shots = client.list_history()
            if not ws_shots:
                raise RuntimeError("No shots on device")
            latest_ws = max(ws_shots, key=lambda item: int(item.get("timestamp") or 0))
            targets = [(str(latest_ws.get("id", "")), None)]
    elif index_shots is not None:
        day_shots = filter_shots_for_day(index_shots, day, tz)
        if not day_shots:
            raise RuntimeError(f"No shots on {day.isoformat()} (local time)")
        targets = [(str(shot.id), shot) for shot in day_shots]
    else:
        with GaggimateWsClient(host, port, timeout=ws_timeout) as client:
            ws_pairs = ws_shots_for_day(client, day, tz)
        if not ws_pairs:
            raise RuntimeError(f"No shots on {day.isoformat()} (local time)")
        targets = [(shot_id, None) for shot_id, _timestamp in ws_pairs]

    written: list[Path] = []
    for shot_id, index_entry in targets:
        if not shot_id:
            continue
        print(f"  shot-{shot_id}.json", flush=True)
        written.append(
            download_shot_json(
                history_url,
                shot_id,
                out_dir,
                index_entry=index_entry,
                http_timeout=http_timeout,
            )
        )
    return written


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download Gaggimate shot(s) as JSON into ./shots (default: today)",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("GAGGIMATE_HOST", DEFAULT_HOST),
        help=f"Hostname or IP (default: {DEFAULT_HOST} or $GAGGIMATE_HOST)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help=f"Output directory (default: {DEFAULT_OUT.relative_to(ROOT)}/)",
    )
    parser.add_argument(
        "--latest",
        action="store_true",
        help="Download only the most recent shot (ignore --date)",
    )
    parser.add_argument(
        "--date",
        type=str,
        metavar="YYYY-MM-DD",
        help="Calendar day to fetch (default: today, local timezone)",
    )
    parser.add_argument(
        "--tz",
        type=str,
        default=None,
        help="IANA timezone for --date / today (default: local system timezone)",
    )
    parser.add_argument("--timeout", type=float, default=15.0, help="WebSocket timeout (seconds)")
    parser.add_argument(
        "--http-timeout",
        type=float,
        default=30.0,
        help="HTTP timeout per request (seconds)",
    )
    return parser


def resolve_timezone(name: str | None) -> tzinfo:
    if name:
        return ZoneInfo(name)
    local = datetime.now().astimezone().tzinfo
    return local if local is not None else ZoneInfo("UTC")


def main() -> int:
    args = build_parser().parse_args()
    host, port = parse_host(args.host)

    try:
        tz = resolve_timezone(args.tz)
    except Exception as error:
        print(f"Error: invalid timezone: {error}", file=sys.stderr)
        return 1

    if args.date:
        try:
            day = date.fromisoformat(args.date)
        except ValueError:
            print("Error: --date must be YYYY-MM-DD", file=sys.stderr)
            return 1
    else:
        day = datetime.now(tz).date()

    try:
        ip = resolve_host(host, port)
    except OSError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2

    if args.latest:
        label = "latest shot"
    else:
        tz_label = getattr(tz, "key", str(tz))
        label = f"shot(s) on {day.isoformat()} ({tz_label})"
    print(f"Fetching {label} from {host} ({ip})...", flush=True)

    try:
        paths = fetch_shots(
            host,
            port,
            args.out.resolve(),
            day=day,
            tz=tz,
            latest_only=args.latest,
            http_timeout=args.http_timeout,
            ws_timeout=args.timeout,
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

    for path in paths:
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
