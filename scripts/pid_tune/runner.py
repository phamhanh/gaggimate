"""Unattended five-scenario PID tuning ladder."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gaggimate_ws import (
    GaggimateWsClient,
    fetch_api_settings,
    parse_pid_csv,
    parse_status_tick,
    read_status_once,
    status_has_pid_live,
)
from pid_tune.config import LadderConfig, load_config
from pid_tune.metrics import RunMetrics, StatusSample, score_run
from pid_tune.scenarios import TuneScenario, in_start_band, waypoints_above_ct
from pid_tune.session import ensure_brew_mode, pid_tune_session
from pid_tune.state import LadderState, load_state, new_state, save_state
from pid_tune.suggest_gains import PidGains, suggest_gains
from pid_tune.thermal import cool_to_waypoints, soak_at_zero, wait_until_ct

DEFAULT_KP, DEFAULT_KI, DEFAULT_KD = 34.0, 0.27, 80.0


def load_gains_from_settings(host: str, port: int) -> PidGains:
    settings = fetch_api_settings(host, port)
    kp, ki, kd, _kff = parse_pid_csv(settings.get("pid", f"{DEFAULT_KP},{DEFAULT_KI},{DEFAULT_KD},1"))
    return PidGains(kp=kp, ki=ki, kd=kd)


def collect_samples(
    client: GaggimateWsClient,
    duration: float,
    scenario: str,
    *,
    max_safe_ct: float,
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
        if tick["ct"] > max_safe_ct:
            raise RuntimeError(f"abort: ct={tick['ct']:.1f} exceeds {max_safe_ct}C")
        if tick["frozen"]:
            raise RuntimeError("abort: pidLive.frozen during idle tune")

    first = client.wait_status(timeout=15.0)
    if not status_has_pid_live(first):
        raise RuntimeError("evt:status missing pidLive — deploy Plan 1 firmware first")
    on_status(first)
    client.stream_status(duration, on_status=on_status)
    return samples


def acceptance_ok(metrics: RunMetrics, config: LadderConfig) -> bool:
    acc = config.acceptance
    if metrics.target_temp <= 0:
        return False
    if metrics.max_overshoot_c > acc.max_overshoot_c:
        return False
    if metrics.time_to_band_s is None or metrics.time_to_band_s > acc.time_to_band_s:
        return False
    if metrics.frozen_samples > 0:
        return False
    return metrics.in_band_fraction >= 0.3 or (
        metrics.time_to_band_s is not None and metrics.time_to_band_s <= acc.time_to_band_s
    )


def run_ramp_test(
    client: GaggimateWsClient,
    scenario: TuneScenario,
    gains: PidGains,
    config: LadderConfig,
    *,
    dry_run: bool,
) -> tuple[list[StatusSample], RunMetrics]:
    """S1–S4: step to final_tt; S5: soak at 0 then ramp to final_tt."""
    record_s = config.record_max_s_for(scenario.id)
    target = scenario.final_tt

    if not dry_run:
        client.set_pid(gains.kp, gains.ki, gains.kd, persist=False)

    if scenario.id == "cold_soak":
        if not dry_run:
            soak_at_zero(client, scenario.pre_soak_duration_s, dry_run=False)
            ensure_brew_mode(client)
            client.set_target_temp(target)
        samples = collect_samples(client, record_s, scenario.id, max_safe_ct=config.max_safe_ct)
        metrics = score_run(samples, target=target, band_c=config.acceptance.band_c)
    else:
        if not dry_run:
            client.set_target_temp(target)
        samples = collect_samples(client, record_s, scenario.id, max_safe_ct=config.max_safe_ct)
        metrics = score_run(samples, target=target, band_c=config.acceptance.band_c)

    return samples, metrics


def ensure_start_band(
    client: GaggimateWsClient,
    scenario: TuneScenario,
    config: LadderConfig,
    *,
    dry_run: bool,
    log: Callable[[str], None],
) -> None:
    """Cool through waypoints until ct is in scenario start band."""
    ct = _read_ct(client)
    if in_start_band(ct, scenario):
        log(f"{scenario.id}: ct={ct:.1f} already in start band")
        return

    if scenario.id == "cold_soak":
        waypoints = list(config.cool_waypoints) + [0]
        if ct > config.cold_soak_max_ct:
            cool_to_waypoints(
                client,
                waypoints,
                ct_tolerance=config.ct_tolerance,
                timeout_per_step=lambda wp: config.cool_timeout_s(wp),
                dry_run=dry_run,
                log=log,
            )
            if not dry_run:
                wait_until_ct(
                    client,
                    "<=",
                    config.cold_soak_max_ct,
                    config.cool_timeout_s(0),
                    log=log,
                )
        return

    needed = waypoints_above_ct(ct, config.cool_waypoints)
    if not needed:
        log(f"{scenario.id}: ct={ct:.1f} below waypoints but outside band — waiting")
        if not dry_run:
            wait_until_ct(
                client,
                ">=",
                scenario.start_ct_min,
                config.record_max_s_default,
                log=log,
            )
        return

    cool_to_waypoints(
        client,
        needed,
        ct_tolerance=config.ct_tolerance,
        timeout_per_step=lambda wp: config.cool_timeout_s(wp),
        dry_run=dry_run,
        log=log,
    )

    ct = _read_ct(client)
    if not in_start_band(ct, scenario) and not dry_run:
        if ct > scenario.start_ct_max:
            wait_until_ct(
                client,
                "<=",
                scenario.start_ct_max,
                config.record_max_s_default,
                log=log,
            )
        elif ct < scenario.start_ct_min:
            wait_until_ct(
                client,
                ">=",
                scenario.start_ct_min,
                config.record_max_s_default,
                log=log,
            )


def tune_scenario_until_accept(
    client: GaggimateWsClient,
    scenario: TuneScenario,
    gains: PidGains,
    config: LadderConfig,
    state: LadderState,
    *,
    dry_run: bool,
    deadline: float | None,
    log: Callable[[str], None],
) -> PidGains:
    state.current_scenario = scenario.id
    save_state(state)

    for iteration in range(config.max_iter_per_scenario):
        if deadline is not None and time.time() >= deadline:
            raise TimeoutError(f"--max-hours exceeded during {scenario.id}")

        ensure_start_band(client, scenario, config, dry_run=dry_run, log=log)

        if dry_run:
            log(f"{scenario.id}: dry-run — would tune iteration {iteration + 1}")
            return gains

        _samples, metrics = run_ramp_test(client, scenario, gains, config, dry_run=False)
        state.last_metrics = metrics.to_dict()
        save_state(state)

        accepted = acceptance_ok(metrics, config)
        step_log = {
            "scenario": scenario.id,
            "iteration": iteration + 1,
            "metrics": metrics.to_dict(),
            "gains": gains.as_tuple(),
            "accept": accepted,
        }
        log(f"{scenario.id} iter {iteration + 1}: {step_log}")

        if accepted:
            state.mark_completed(scenario.id)
            save_state(state)
            return gains

        next_gains, reason = suggest_gains(
            metrics,
            gains.kp,
            gains.ki,
            gains.kd,
            band_max_s=config.acceptance.time_to_band_s,
        )
        log(f"{scenario.id}: suggest {next_gains.as_tuple()} — {reason}")
        gains = next_gains
        state.set_gains(gains)
        save_state(state)
        client.set_pid(gains.kp, gains.ki, gains.kd, persist=False)
        time.sleep(config.iter_pause_s)

    raise RuntimeError(f"{scenario.id}: max iterations ({config.max_iter_per_scenario}) without acceptance")


def _read_ct(client: GaggimateWsClient) -> float:
    message = client.wait_status(timeout=15.0)
    return float(parse_status_tick(message)["ct"])


def plan_ladder(ct: float, config: LadderConfig, state: LadderState | None) -> list[str]:
    """Human-readable plan from current ct and resume state."""
    lines: list[str] = []
    completed = set(state.completed if state else [])
    for scenario in config.scenarios:
        if scenario.id in completed:
            lines.append(f"  [done] {scenario.id}")
            continue
        band = f"{scenario.start_ct_min:.0f}–{scenario.start_ct_max:.0f}°C"
        if in_start_band(ct, scenario):
            lines.append(f"  [ready] {scenario.id} (ct={ct:.1f} in {band})")
        else:
            waypoints = waypoints_above_ct(ct, config.cool_waypoints)
            if scenario.id == "cold_soak":
                waypoints = waypoints + ([0] if ct > config.cold_soak_max_ct else [])
            cool = f" cool via {waypoints}" if waypoints else ""
            lines.append(f"  [cool{cool}] {scenario.id} need {band}, ct={ct:.1f}")
    return lines


def run_full_ladder(
    host: str,
    port: int,
    config: LadderConfig | None = None,
    *,
    resume: bool = False,
    dry_run: bool = False,
    max_hours: float | None = None,
    only: str | None = None,
    state_path: Path | None = None,
) -> dict[str, Any]:
    cfg = config or load_config()
    log = lambda message: print(message, file=sys.stderr)

    status = read_status_once(host, port)
    if not status_has_pid_live(status):
        raise RuntimeError("pidLive missing — deploy Plan 1 firmware first")

    tick = parse_status_tick(status)
    log(
        f"start: ct={tick['ct']:.1f} tt={tick['tt']:.1f} "
        f"kp={tick['kp']:.2f} ki={tick['ki']:.3f} kd={tick['kd']:.0f} frozen={tick['frozen']}"
    )

    gains = load_gains_from_settings(host, port)
    state = load_state(state_path) if resume else None
    if state is not None and state.host != host:
        log(f"resume: host mismatch ({state.host} vs {host}), starting fresh")
        state = None
    if state is None:
        state = new_state(host, gains)
    else:
        gains = state.gains_tuple()
        log(f"resume: completed={state.completed} gains={gains.as_tuple()}")

    deadline = time.time() + max_hours * 3600 if max_hours is not None else None

    for line in plan_ladder(tick["ct"], cfg, state):
        log(line)

    if dry_run:
        return {"host": host, "dry_run": True, "plan": plan_ladder(tick["ct"], cfg, state)}

    result: dict[str, Any] = {
        "host": host,
        "started_gains": gains.as_tuple(),
        "completed": [],
        "steps": [],
    }

    session_kw = {"apply_idle_prep": True, "target_water_c": int(cfg.final_tt)}
    with pid_tune_session(host, port, **session_kw) as original_standby_s:
        result["standby_timeout_restored_s"] = original_standby_s
        with GaggimateWsClient(host, port) as client:
            ensure_brew_mode(client)

            scenarios = list(cfg.scenarios)
            if only is not None:
                scenarios = [scenario for scenario in scenarios if scenario.id == only]

            for scenario in scenarios:
                if scenario.id in state.completed:
                    log(f"skip {scenario.id} (already completed)")
                    continue

                if deadline is not None and time.time() >= deadline:
                    raise TimeoutError("--max-hours exceeded before next scenario")

                gains = tune_scenario_until_accept(
                    client,
                    scenario,
                    gains,
                    cfg,
                    state,
                    dry_run=False,
                    deadline=deadline,
                    log=log,
                )
                result["completed"].append(scenario.id)

            if not only and all(s.id in state.completed for s in cfg.scenarios):
                client.set_pid(gains.kp, gains.ki, gains.kd, persist=True)
                result["persisted"] = gains.as_tuple()
                log(f"ladder complete — persisted {gains.as_tuple()}")

    result["final_gains"] = gains.as_tuple()
    result["state"] = state.to_dict()
    return result
