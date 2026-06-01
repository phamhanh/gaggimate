#!/usr/bin/env python3
"""Replay disturbance Kff from exported shot JSON (v6+ thermalSettings or CLI overrides).

Mirrors Heater::calculateDisturbanceFeedforwardGain in
lib/GaggiMateController/src/peripherals/Heater.cpp.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from statistics import mean, median
from typing import Any

WATER_DENSITY = 1.0
WATER_SPECIFIC_HEAT = 4.18
HEATER_EFFICIENCY = 0.95
HEAT_LOSS_WATTS = 5.0


def gain_per_flow_ml(
    setpoint_c: float,
    inlet_c: float,
    combined_kff: float,
    flow_ml_s: float,
) -> float:
    if combined_kff <= 0.0 or flow_ml_s <= 0.01:
        return 0.0
    temp_delta = setpoint_c - inlet_c
    if temp_delta <= 0.0:
        return 0.0
    power_per_flow = (
        WATER_DENSITY * WATER_SPECIFIC_HEAT * temp_delta + (HEAT_LOSS_WATTS / flow_ml_s)
    ) / HEATER_EFFICIENCY
    return power_per_flow * combined_kff


def kff_output(
    setpoint_c: float,
    inlet_c: float,
    combined_kff: float,
    flow_ml_s: float,
) -> float:
    return gain_per_flow_ml(setpoint_c, inlet_c, combined_kff, flow_ml_s) * flow_ml_s


def resolve_thermal_settings(
    shot: dict[str, Any],
    args: argparse.Namespace,
) -> dict[str, float | bool]:
    ts = shot.get("thermalSettings") or {}
    inlet = args.inlet
    if inlet is None:
        if "inletTempC" not in ts:
            raise ValueError("No thermalSettings.inletTempC; pass --inlet")
        inlet = float(ts["inletTempC"])

    combined_kff = args.kff
    if combined_kff is None:
        if "combinedKff" not in ts:
            raise ValueError("No thermalSettings.combinedKff; pass --kff")
        combined_kff = float(ts["combinedKff"])

    kff_enabled = ts.get("kffEnabled", True) if args.kff is None else combined_kff > 0
    if not kff_enabled or combined_kff <= 0:
        combined_kff = 0.0

    pump_1 = args.flow_1bar if args.flow_1bar is not None else float(ts.get("pumpFlow1Bar", 0))
    pump_9 = args.flow_9bar if args.flow_9bar is not None else float(ts.get("pumpFlow9Bar", 0))

    return {
        "inletTempC": inlet,
        "combinedKff": combined_kff,
        "kffEnabled": bool(kff_enabled and combined_kff > 0),
        "pumpFlow1Bar": pump_1,
        "pumpFlow9Bar": pump_9,
    }


def phase_for_sample(
    sample_index: int,
    transitions: list[dict[str, Any]],
) -> tuple[int, str]:
    current = 0
    name = "Phase 1"
    for tr in transitions:
        if sample_index >= tr.get("sampleIndex", 0):
            current = int(tr.get("phaseNumber", 0))
            name = str(tr.get("phaseName", name))
        else:
            break
    return current, name


def summarize(values: list[float]) -> dict[str, float | int] | None:
    if not values:
        return None
    return {
        "n": len(values),
        "mean": round(mean(values), 3),
        "median": round(median(values), 3),
        "min": round(min(values), 3),
        "max": round(max(values), 3),
    }


def analyze_shot(shot: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    thermal = resolve_thermal_settings(shot, args)
    inlet = float(thermal["inletTempC"])
    combined_kff = float(thermal["combinedKff"])

    samples = shot.get("samples") or []
    transitions = shot.get("phaseTransitions") or []

    by_phase: dict[str, dict[str, Any]] = {}
    settle_ct: list[float] = []

    for i, sample in enumerate(samples):
        tt = float(sample.get("tt", 0))
        flow = float(sample.get("fl", 0))
        ct = float(sample.get("ct", 0))
        kff = kff_output(tt, inlet, combined_kff, flow) if thermal["kffEnabled"] else 0.0

        _, phase_name = phase_for_sample(i, transitions)
        if not phase_name:
            phase_name = f"phase_{i}"
        bucket = by_phase.setdefault(
            phase_name,
            {"kffOut": [], "ct": [], "flow": [], "tt": []},
        )
        bucket["kffOut"].append(kff)
        bucket["ct"].append(ct)
        bucket["flow"].append(flow)
        bucket["tt"].append(tt)

        if "settle" in phase_name.lower():
            settle_ct.append(ct)

    phases_out = {}
    for name, data in by_phase.items():
        phases_out[name] = {
            "kffOut": summarize(data["kffOut"]),
            "ct": summarize(data["ct"]),
            "flow": summarize(data["flow"]),
            "tt": summarize(data["tt"]),
        }

    result: dict[str, Any] = {
        "id": shot.get("id"),
        "profile": shot.get("profile"),
        "thermalSettings": thermal,
        "phases": phases_out,
    }
    if settle_ct:
        result["settlePhaseCt"] = summarize(settle_ct)
    return result


def load_shot(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Replay Kff from shot JSON exports (uses thermalSettings when present).",
    )
    parser.add_argument("shots", nargs="+", type=Path, help="Exported shot JSON file(s)")
    parser.add_argument(
        "--compare",
        action="store_true",
        help="With two shots, print side-by-side phase Kff means",
    )
    parser.add_argument("--inlet", type=float, help="Inlet temp °C (overrides thermalSettings)")
    parser.add_argument("--kff", type=float, help="combinedKff (overrides thermalSettings)")
    parser.add_argument("--flow-1bar", type=float, dest="flow_1bar", help="Pump 1 bar ml/s (metadata)")
    parser.add_argument("--flow-9bar", type=float, dest="flow_9bar", help="Pump 9 bar ml/s (metadata)")
    parser.add_argument("--json", action="store_true", help="Print JSON to stdout")
    args = parser.parse_args()

    results = []
    for path in args.shots:
        try:
            shot = load_shot(path)
            results.append({"file": str(path), **analyze_shot(shot, args)})
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            print(f"{path}: {exc}", file=sys.stderr)
            return 1

    if args.json:
        print(json.dumps(results if len(results) > 1 else results[0], indent=2))
        return 0

    for entry in results:
        print(f"\n=== {entry.get('file', entry.get('id'))} ===")
        thermal = entry["thermalSettings"]
        print(
            f"inlet={thermal['inletTempC']}°C  combinedKff={thermal['combinedKff']}  "
            f"kffEnabled={thermal['kffEnabled']}  "
            f"pump1bar={thermal['pumpFlow1Bar']}  pump9bar={thermal['pumpFlow9Bar']}"
        )
        for phase_name, stats in entry.get("phases", {}).items():
            kff = stats.get("kffOut")
            ct = stats.get("ct")
            kff_s = f"mean kffOut={kff['mean']}" if kff else "no flow"
            ct_s = f" mean ct={ct['mean']}" if ct else ""
            print(f"  {phase_name}: {kff_s}{ct_s}")
        settle = entry.get("settlePhaseCt")
        if settle:
            print(f"  (settle ct: mean={settle['mean']} min={settle['min']} max={settle['max']})")

    if args.compare and len(results) == 2:
        print("\n--- compare (mean kffOut per phase) ---")
        names = sorted(
            set(results[0].get("phases", {})) | set(results[1].get("phases", {})),
        )
        for name in names:
            a = results[0]["phases"].get(name, {}).get("kffOut")
            b = results[1]["phases"].get(name, {}).get("kffOut")
            am = a["mean"] if a else None
            bm = b["mean"] if b else None
            delta = None
            if am is not None and bm is not None:
                delta = round(bm - am, 3)
            print(f"  {name}: {am} -> {bm}  (Δ {delta})")

    return 0


if __name__ == "__main__":
    sys.exit(main())
