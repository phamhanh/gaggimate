#!/usr/bin/env python3
"""Parse /h/index.bin (mirrors src/display/models/shot_log_format.h)."""

from __future__ import annotations

import struct
from dataclasses import dataclass

INDEX_HEADER_SIZE = 32
INDEX_ENTRY_SIZE = 128
INDEX_MAGIC = 0x58444953  # 'SIDX'

SHOT_FLAG_DELETED = 0x02
SHOT_FLAG_HAS_NOTES = 0x04


@dataclass
class ShotIndexEntry:
    id: int
    timestamp: int
    duration: int
    deleted: bool
    has_notes: bool


def parse_index_bytes(data: bytes) -> list[ShotIndexEntry]:
    if len(data) < INDEX_HEADER_SIZE:
        raise ValueError("index.bin too small")

    magic, version, entry_size, entry_count, _next_id = struct.unpack_from("<IHHII", data, 0)
    if magic != INDEX_MAGIC:
        raise ValueError(f"invalid index magic: 0x{magic:08x}")
    if entry_size != INDEX_ENTRY_SIZE:
        raise ValueError(f"unsupported entry size {entry_size}")

    expected = INDEX_HEADER_SIZE + entry_count * INDEX_ENTRY_SIZE
    if len(data) < expected:
        raise ValueError(f"index.bin truncated ({len(data)} < {expected})")

    entries: list[ShotIndexEntry] = []
    offset = INDEX_HEADER_SIZE
    for _ in range(entry_count):
        shot_id, timestamp, duration, _volume, _rating, flags = struct.unpack_from("<IIIHBB", data, offset)
        entries.append(
            ShotIndexEntry(
                id=shot_id,
                timestamp=timestamp,
                duration=duration,
                deleted=bool(flags & SHOT_FLAG_DELETED),
                has_notes=bool(flags & SHOT_FLAG_HAS_NOTES),
            )
        )
        offset += INDEX_ENTRY_SIZE

    return entries


def active_shots(data: bytes) -> list[ShotIndexEntry]:
    return [entry for entry in parse_index_bytes(data) if not entry.deleted]


def fetch_active_shots(base_url: str, timeout: float) -> list[ShotIndexEntry] | None:
    """Download index.bin from device; return entries or None if missing."""
    from device_http import http_get_bytes

    index_bytes = http_get_bytes(f"{base_url.rstrip('/')}/index.bin", timeout)
    if index_bytes is None:
        return None
    return active_shots(index_bytes)
