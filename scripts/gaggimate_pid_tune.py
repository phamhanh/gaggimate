#!/usr/bin/env python3
"""Orchestrate idle PID tuning via WebSocket (Plan 2). See docs/pid-tuning-agent.md."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gaggimate_ws import (
    DEFAULT_HOST,
    GaggimateWsClient,
    fetch_api_settings,
    parse_host,
    parse_pid_csv,
    parse_status_tick,
    status_has_pid_live,
)
from pid_tune.metrics import RunMetrics, StatusSample, score_run
from pid_tune.session import ensure_brew_mode, pid_tune_session
from pid_tune.suggest_gains import PidGains, suggest_gains

# User NVS baseline on plumvillage machine (overshoots); not flash DEFAULT_PID.
DEFAULT_KP, DEFAULT_KI, DEFAULT_KD = 34.0, 0.27, 80.0
MAX_SAFE_CT = 160.0
SOAK_BEFORE_STEP_S = 30.0
STEP_HIGH_C = 93.0
STEP_LOW_C = 85.0


def load_gains_from_settings(host: str, port: int) -> PidGains:
    settings = fetch_api_settings(host, port)
    kp, ki, kd, _kff = parse_pid_csv(settings.get("pid", f"{DEFAULT_KP},{DEFAULT_KI},{DEFAULT_KD},1"))
    return PidGains(kp=kp, ki=ki, kd=kd)


def collect_samples(
    client: GaggimateWsClient,
    duration: float,
    scenario: str,
) -> list[StatusSample]:
    samples: list[StatusSample] = []
    t0 = time.time()

    def on_status(message: dict[str, object]) -> None:
        tick = parse_status_tick(message, t=time.time() - t0)
        samples.append(
            StatusSample(
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
        )
        if tick["ct"] > MAX_SAFE_CT:
            raise RuntimeError(f"abort: ct={tick['ct']:.1f} exceeds {MAX_SAFE_CT}C")

    first = client.wait_status(timeout=15.0)
    if not status_has_pid_live(first):
        raise RuntimeError("evt:status missing pidLive — deploy Plan 1 firmware first")
    on_status(first)
    client.stream_status(duration, on_status=on_status)
    return samples


def run_preheat_stable_step(
    client: GaggimateWsClient,
    gains: PidGains,
    *,
    dry_run: bool,
    settle_s: float = 240.0,
) -> tuple[list[StatusSample], RunMetrics]:
    if not dry_run:
        client.set_pid(gains.kp, gains.ki, gains.kd, persist=False)
        client.set_target_temp(STEP_LOW_C)
        time.sleep(SOAK_BEFORE_STEP_S)
        client.set_target_temp(STEP_HIGH_C)

    samples = collect_samples(client, SOAK_BEFORE_STEP_S + settle_s, "preheat_stable")
    metrics = score_run(samples, target=STEP_HIGH_C)
    return samples, metrics


def run_cold_start(
    client: GaggimateWsClient,
    gains: PidGains,
    *,
    target: float,
    dry_run: bool,
    duration: float = 240.0,
) -> tuple[list[StatusSample], RunMetrics]:
    if not dry_run:
        client.set_pid(gains.kp, gains.ki, gains.kd, persist=False)
        client.set_target_temp(target)
    samples = collect_samples(client, duration, "cold_start")
    return samples, score_run(samples, target=target)


def acceptance_ok(metrics: RunMetrics, *, band_c: float = 0.5, max_overshoot_c: float = 0.5) -> bool:
    if metrics.target_temp <= 0:
        return False
    if metrics.max_overshoot_c > max_overshoot_c:
        return False
    if metrics.time_to_band_s is None or metrics.time_to_band_s > 240.0:
        return False
    return metrics.in_band_fraction >= 0.3 or (
        metrics.time_to_band_s is not None and metrics.time_to_band_s <= 240.0
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Tune idle Gaggimate PID over WebSocket")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument(
        "--scenario",
        default="preheat_stable",
        choices=["cold_start", "preheat_stable", "post_shot_grace"],
    )
    parser.add_argument("--iterations", type=int, default=1)
    parser.add_argument("--target", type=float, default=STEP_HIGH_C)
    parser.add_argument("--dry-run", action="store_true", help="Score stream only; do not send PID/temp")
    parser.add_argument("--persist", action="store_true", help="On success, persist final gains to NVS")
    parser.add_argument("--kp", type=float, default=None)
    parser.add_argument("--ki", type=float, default=None)
    parser.add_argument("--kd", type=float, default=None)
    args = parser.parse_args()

    host, port = parse_host(args.host)

    gains = PidGains(
        kp=args.kp if args.kp is not None else DEFAULT_KP,
        ki=args.ki if args.ki is not None else DEFAULT_KI,
        kd=args.kd if args.kd is not None else DEFAULT_KD,
    )
    if args.kp is None:
        try:
            gains = load_gains_from_settings(host, port)
        except OSError:
            pass

    result: dict[str, object] = {"host": host, "scenario": args.scenario, "start_gains": gains.as_tuple()}

    session_kw = {
        "apply_idle_prep": not args.dry_run,
        "target_water_c": int(args.target),
    }
    with pid_tune_session(host, port, **session_kw) as original_standby_s:
        result["standby_timeout_restored_s"] = original_standby_s
        with GaggimateWsClient(host, port) as client:
            if not args.dry_run:
                ensure_brew_mode(client)
            for iteration in range(args.iterations):
                if args.scenario == "preheat_stable":
                    _samples, metrics = run_preheat_stable_step(client, gains, dry_run=args.dry_run)
                elif args.scenario == "cold_start":
                    _samples, metrics = run_cold_start(
                        client, gains, target=args.target, dry_run=args.dry_run
                    )
                else:
                    print(
                        "post_shot_grace: run a shot manually, then stream with gaggimate_pid_stream.py",
                        file=sys.stderr,
                    )
                    metrics = RunMetrics(target_temp=args.target)
                    break

                next_gains, reason = suggest_gains(metrics, gains.kp, gains.ki, gains.kd)
                step = {
                    "iteration": iteration + 1,
                    "metrics": metrics.to_dict(),
                    "gains": gains.as_tuple(),
                    "suggest": {
                        "kp": next_gains.kp,
                        "ki": next_gains.ki,
                        "kd": next_gains.kd,
                        "reason": reason,
                    },
                    "accept": acceptance_ok(metrics),
                }
                result.setdefault("steps", []).append(step)

                if acceptance_ok(metrics) and args.persist and not args.dry_run:
                    client.set_pid(gains.kp, gains.ki, gains.kd, persist=True)
                    step["persisted"] = gains.as_tuple()
                    break

                gains = next_gains
                if iteration + 1 < args.iterations and not args.dry_run:
                    client.set_pid(gains.kp, gains.ki, gains.kd, persist=False)
                    time.sleep(5.0)

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (TimeoutError, ConnectionError, RuntimeError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1) from error
