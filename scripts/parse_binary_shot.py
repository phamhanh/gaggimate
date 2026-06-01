#!/usr/bin/env python3
"""Parse .slog binary shot files (mirrors web/src/pages/ShotHistory/parseBinaryShot.js)."""

from __future__ import annotations

import struct
from typing import Any

HEADER_SIZE_V4 = 128
HEADER_SIZE_V5 = 512
MAGIC = 0x544F4853  # 'SHOT'

TEMP_SCALE = 10
PRESSURE_SCALE = 10
FLOW_SCALE = 100
WEIGHT_SCALE = 10
RESISTANCE_SCALE = 100

FIELD_BITS = {
    "t": 0,
    "tt": 1,
    "ct": 2,
    "tp": 3,
    "cp": 4,
    "fl": 5,
    "tf": 6,
    "pf": 7,
    "vf": 8,
    "v": 9,
    "ev": 10,
    "pr": 11,
    "si": 12,
}

FIELD_DEFS: dict[int, dict[str, Any]] = {
    FIELD_BITS["t"]: {"name": "t", "signed": False, "scale": None, "time": True},
    FIELD_BITS["tt"]: {"name": "tt", "signed": False, "scale": TEMP_SCALE},
    FIELD_BITS["ct"]: {"name": "ct", "signed": False, "scale": TEMP_SCALE},
    FIELD_BITS["tp"]: {"name": "tp", "signed": False, "scale": PRESSURE_SCALE},
    FIELD_BITS["cp"]: {"name": "cp", "signed": False, "scale": PRESSURE_SCALE},
    FIELD_BITS["fl"]: {"name": "fl", "signed": True, "scale": FLOW_SCALE},
    FIELD_BITS["tf"]: {"name": "tf", "signed": True, "scale": FLOW_SCALE},
    FIELD_BITS["pf"]: {"name": "pf", "signed": True, "scale": FLOW_SCALE},
    FIELD_BITS["vf"]: {"name": "vf", "signed": True, "scale": FLOW_SCALE},
    FIELD_BITS["v"]: {"name": "v", "signed": False, "scale": WEIGHT_SCALE},
    FIELD_BITS["ev"]: {"name": "ev", "signed": False, "scale": WEIGHT_SCALE},
    FIELD_BITS["pr"]: {"name": "pr", "signed": False, "scale": RESISTANCE_SCALE},
    FIELD_BITS["si"]: {"name": "systemInfo", "signed": False, "scale": None, "system_info": True},
}

PHASE_TRANSITION_BASE = 110
PHASE_TRANSITION_SIZE = 29
MAX_PHASE_TRANSITIONS = 12
THERMAL_SNAPSHOT_OFFSET = PHASE_TRANSITION_BASE + MAX_PHASE_TRANSITIONS * PHASE_TRANSITION_SIZE + 1


def _decode_cstring(data: bytes) -> str:
    end = data.find(b"\x00")
    if end < 0:
        end = len(data)
    return data[:end].decode("utf-8", errors="replace")


def _count_set_bits(value: int) -> int:
    return value.bit_count()


def _read_uint16(data: bytes, offset: int, signed: bool) -> int:
    fmt = "<h" if signed else "<H"
    return struct.unpack_from(fmt, data, offset)[0]


def _parse_system_info(raw: int) -> dict[str, Any]:
    return {
        "raw": raw,
        "shotStartedVolumetric": bool(raw & 0x0001),
        "currentlyVolumetric": bool(raw & 0x0002),
        "bluetoothScaleConnected": bool(raw & 0x0004),
        "volumetricAvailable": bool(raw & 0x0008),
        "extendedRecording": bool(raw & 0x0010),
    }


def _parse_phase_transitions(data: bytes, transition_count: int) -> list[dict[str, Any]]:
    transitions: list[dict[str, Any]] = []
    for index in range(min(transition_count, MAX_PHASE_TRANSITIONS)):
        offset = PHASE_TRANSITION_BASE + index * PHASE_TRANSITION_SIZE
        sample_index = struct.unpack_from("<H", data, offset)[0]
        phase_number = data[offset + 2]
        phase_name = _decode_cstring(data[offset + 4 : offset + 29])
        transitions.append(
            {
                "sampleIndex": sample_index,
                "phaseNumber": phase_number,
                "phaseName": phase_name,
            }
        )
    return transitions


def _parse_thermal_snapshot(data: bytes, version: int) -> dict[str, Any] | None:
    if version < 6 or len(data) < THERMAL_SNAPSHOT_OFFSET + 8:
        return None
    offset = THERMAL_SNAPSHOT_OFFSET
    inlet_temp_c = data[offset]
    kff_enabled = data[offset + 1] != 0
    pump_flow_1bar, pump_flow_9bar, combined_kff = struct.unpack_from("<HHH", data, offset + 2)
    return {
        "inletTempC": inlet_temp_c,
        "kffEnabled": kff_enabled,
        "pumpFlow1Bar": pump_flow_1bar / 1000,
        "pumpFlow9Bar": pump_flow_9bar / 1000,
        "combinedKff": combined_kff / 1000,
    }


def _current_phase(sample_index: int, transitions: list[dict[str, Any]]) -> tuple[int, str]:
    current_phase = 0
    phase_name = "Phase 1"
    for transition in transitions:
        if sample_index >= transition["sampleIndex"]:
            current_phase = transition["phaseNumber"]
            phase_name = transition["phaseName"]
        else:
            break
    return current_phase, phase_name


def parse_binary_shot(data: bytes, shot_id: str | int) -> dict[str, Any]:
    """Parse a .slog file into the same JSON shape as the web UI export."""
    if len(data) < 16:
        raise ValueError("File too small for header")

    magic = struct.unpack_from("<I", data, 0)[0]
    if magic != MAGIC:
        raise ValueError(f"Bad magic: expected 0x{MAGIC:08x}, got 0x{magic:08x}")

    version = data[4]
    device_sample_size = data[5]
    header_size = struct.unpack_from("<H", data, 6)[0]
    expected_header_size = HEADER_SIZE_V4 if version <= 4 else HEADER_SIZE_V5

    if len(data) < expected_header_size:
        raise ValueError(
            f"File too small for v{version} header: need {expected_header_size}, got {len(data)}"
        )
    if header_size != expected_header_size:
        raise ValueError(
            f"Header size mismatch for v{version}: expected {expected_header_size}, got {header_size}"
        )

    sample_interval = struct.unpack_from("<H", data, 8)[0]
    fields_mask = struct.unpack_from("<I", data, 12)[0]
    sample_count_header = struct.unpack_from("<I", data, 16)[0]
    duration_header = struct.unpack_from("<I", data, 20)[0]
    start_epoch = struct.unpack_from("<I", data, 24)[0]
    profile_id = _decode_cstring(data[28:60])
    profile_name = _decode_cstring(data[60:108])
    final_weight_header = struct.unpack_from("<H", data, 108)[0]

    phase_transitions: list[dict[str, Any]] = []
    if version >= 5:
        transition_count = data[PHASE_TRANSITION_BASE + MAX_PHASE_TRANSITIONS * PHASE_TRANSITION_SIZE]
        phase_transitions = _parse_phase_transitions(data, transition_count)

    thermal_settings = _parse_thermal_snapshot(data, version)

    field_count = _count_set_bits(fields_mask)
    expected_sample_size = field_count * 2
    if device_sample_size != expected_sample_size:
        raise ValueError(
            f"Field mask indicates {field_count} fields ({expected_sample_size} bytes), "
            f"but device reports {device_sample_size} bytes"
        )

    field_layout: list[dict[str, Any]] = []
    for bit_pos in range(32):
        if fields_mask & (1 << bit_pos):
            field_def = FIELD_DEFS.get(bit_pos)
            if field_def:
                field_layout.append({**field_def, "bitPos": bit_pos})
            else:
                field_layout.append(
                    {
                        "name": f"unknown_{bit_pos}",
                        "signed": False,
                        "scale": None,
                        "bitPos": bit_pos,
                    }
                )

    samples: list[dict[str, Any]] = []
    data_bytes = len(data) - header_size
    if data_bytes < 0:
        raise ValueError("Data size misaligned")

    sample_size = device_sample_size
    full_sample_bytes = (data_bytes // sample_size) * sample_size
    trailing_bytes = data_bytes - full_sample_bytes
    inferred_samples = full_sample_bytes // sample_size if sample_size else 0
    max_samples = min(sample_count_header, inferred_samples) if sample_count_header else inferred_samples

    for sample_index in range(max_samples):
        base = header_size + sample_index * sample_size
        sample: dict[str, Any] = {}
        for field_idx, field in enumerate(field_layout):
            offset = base + field_idx * 2
            raw_value = _read_uint16(data, offset, field["signed"])
            if field.get("time"):
                final_value = raw_value * sample_interval
            elif field.get("system_info"):
                final_value = _parse_system_info(raw_value)
            elif field.get("scale"):
                final_value = raw_value / field["scale"]
            else:
                final_value = raw_value
            sample[field["name"]] = final_value

        if version >= 5:
            phase_number, phase_name = _current_phase(sample_index, phase_transitions)
            sample["phaseNumber"] = phase_number
            sample["phaseDisplayNumber"] = phase_number + 1
            sample["phaseName"] = phase_name

        samples.append(sample)

    last_t = samples[-1]["t"] if samples else 0
    header_incomplete = sample_count_header == 0
    inferred_incomplete = trailing_bytes != 0 or (
        sample_count_header > 0 and sample_count_header > inferred_samples
    )
    incomplete = header_incomplete or inferred_incomplete
    effective_duration = duration_header if not incomplete and duration_header else last_t

    header_volume = final_weight_header / WEIGHT_SCALE if final_weight_header else 0
    sample_volume = samples[-1].get("v", 0) if samples else 0
    volume = header_volume if header_volume > 0 else (sample_volume if sample_volume > 0 else None)

    result: dict[str, Any] = {
        "id": str(shot_id),
        "version": version,
        "profile": profile_name,
        "profileId": profile_id,
        "timestamp": start_epoch,
        "duration": effective_duration,
        "samples": samples,
        "volume": volume,
        "incomplete": incomplete,
        "sampleInterval": sample_interval,
        "fieldsMask": fields_mask,
        "trailingBytes": trailing_bytes,
        "samplesExpected": sample_count_header,
        "phaseTransitions": phase_transitions,
    }
    if thermal_settings is not None:
        result["thermalSettings"] = thermal_settings
    return result
