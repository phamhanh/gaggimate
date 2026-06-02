#!/usr/bin/env python3
"""Stream evt:status PID telemetry to CSV (Plan 2). See docs/pid-tuning-agent.md."""

from __future__ import annotations

import argparse
import csv
import sys
import time
from typing import Any
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gaggimate_ws import (
    DEFAULT_HOST,
    GaggimateWsClient,
    parse_host,
    parse_status_tick,
    status_has_pid_live,
)
from pid_tune.metrics import StatusSample
from pid_tune.session import ensure_brew_mode, pid_tune_session

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT_DIR = ROOT / "device-data" / "pid-tune"

CSV_COLUMNS = [
    "t",
    "scenario",
    "ct",
    "tt",
    "p",
    "i",
    "d",
    "kff",
    "out",
    "frozen",
    "kp",
    "ki",
    "kd",
]


def sample_from_tick(tick: dict[str, Any], scenario: str) -> StatusSample:
    return StatusSample(
        t=tick["t"],
        ct=tick["ct"],
        tt=tick["tt"],
        p=tick["p"],
        i=tick["i"],
        d=tick["d"],
        kff=tick["kff"],
        out=tick["out"],
        frozen=tick["frozen"],
        kp=tick["kp"],
        ki=tick["ki"],
        kd=tick["kd"],
        scenario=scenario,
    )


def default_csv_path(scenario: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return DEFAULT_OUT_DIR / f"{stamp}-{scenario}.csv"


def stream_to_csv(
    host: str,
    port: int,
    duration: float,
    scenario: str,
    out_path: Path,
    *,
    print_lines: bool = False,
    client: GaggimateWsClient | None = None,
) -> list[StatusSample]:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    samples: list[StatusSample] = []
    t0 = time.time()

    def on_status(message: dict[str, object]) -> None:
        tick = parse_status_tick(message, t=time.time() - t0)
        tick["scenario"] = scenario
        sample = sample_from_tick(tick, scenario)
        samples.append(sample)
        if print_lines:
            print(
                f"{sample.t:7.1f} {sample.ct:6.2f} {sample.tt:6.2f} "
                f"{sample.p:7.1f} {sample.i:7.1f} {sample.d:7.1f} {sample.out:6.0f} "
                f"{sample.frozen} {sample.kp:5.1f} {sample.ki:6.3f} {sample.kd:5.0f}"
            )

    def stream_with(client: GaggimateWsClient) -> None:
        nonlocal samples
        first = client.wait_status(timeout=15.0)
        if not status_has_pid_live(first):
            raise RuntimeError(
                "evt:status missing pidLive — deploy Plan 1 firmware or see docs/pid-telemetry-api.md"
            )
        on_status(first)
        client.stream_status(max(0.0, duration - (time.time() - t0)), on_status=on_status)

    if client is not None:
        stream_with(client)
    else:
        with GaggimateWsClient(host, port) as owned:
            stream_with(owned)

    with out_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for sample in samples:
            writer.writerow(
                {
                    "t": f"{sample.t:.3f}",
                    "scenario": sample.scenario,
                    "ct": f"{sample.ct:.3f}",
                    "tt": f"{sample.tt:.3f}",
                    "p": f"{sample.p:.3f}",
                    "i": f"{sample.i:.3f}",
                    "d": f"{sample.d:.3f}",
                    "kff": f"{sample.kff:.3f}",
                    "out": f"{sample.out:.3f}",
                    "frozen": sample.frozen,
                    "kp": f"{sample.kp:.3f}",
                    "ki": f"{sample.ki:.4f}",
                    "kd": f"{sample.kd:.3f}",
                }
            )

    return samples


def main() -> int:
    parser = argparse.ArgumentParser(description="Stream Gaggimate PID telemetry to CSV")
    parser.add_argument("--host", default=DEFAULT_HOST, help="Device hostname or IP")
    parser.add_argument("--duration", type=float, default=120.0, help="Seconds to record")
    parser.add_argument(
        "--scenario",
        default="cold_start",
        choices=["cold_start", "preheat_stable", "post_shot_grace", "post_standby", "manual"],
    )
    parser.add_argument("--out", type=Path, default=None, help="CSV path (default: device-data/pid-tune/...)")
    parser.add_argument("--print", action="store_true", dest="print_lines", help="Print columns to stdout")
    parser.add_argument(
        "--no-standby-guard",
        action="store_true",
        help="Do not POST standbyTimeout=0 for the run (not recommended)",
    )
    args = parser.parse_args()

    host, port = parse_host(args.host)
    out_path = args.out or default_csv_path(args.scenario)

    if args.print_lines:
        print("      t     ct     tt       p       i       d    out fr    kp     ki    kd")

    def run_stream() -> list[StatusSample]:
        with GaggimateWsClient(host, port) as client:
            if not args.no_standby_guard:
                ensure_brew_mode(client)
            return stream_to_csv(
                host,
                port,
                args.duration,
                args.scenario,
                out_path,
                print_lines=args.print_lines,
                client=client,
            )

    if args.no_standby_guard:
        samples = run_stream()
    else:
        with pid_tune_session(host, port, apply_idle_prep=True, target_water_c=93) as _original_s:
            samples = run_stream()
    print(f"Wrote {len(samples)} samples to {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (TimeoutError, ConnectionError, RuntimeError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1) from error
