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
from pid_tune.config import LadderConfig, fresh_config, load_config
from pid_tune.metrics import RunMetrics, StatusSample, score_run, summarize_hold
from pid_tune.pid_apply import apply_and_verify_pid, read_gains_from_device
from pid_tune.scenarios import (
    TuneScenario,
    prep_cool_tt,
    prep_done,
    waypoints_for_scenario,
)
from pid_tune.session import ensure_brew_mode, pid_tune_session
from pid_tune.state import LadderState, load_state, new_state, save_state
from pid_tune.status_log import format_status, log_status, read_tick
from pid_tune.suggest_gains import PidGains, suggest_gains
from pid_tune.thermal import cold_equilibrate_at_zero, cool_to_waypoints, wait_until_ct

DEFAULT_KP, DEFAULT_KI, DEFAULT_KD = 34.0, 0.27, 80.0

# A single frozen telemetry tick (e.g. a transient BLE dropout while idle) must
# not abort a multi-hour ladder run. Only sustained loss is fatal.
MAX_FROZEN_CONSECUTIVE = 3


def load_gains_from_settings(host: str, port: int) -> PidGains:
    settings = fetch_api_settings(host, port)
    kp, ki, kd, _kff = parse_pid_csv(
        settings.get("pid", f"{DEFAULT_KP},{DEFAULT_KI},{DEFAULT_KD},1")
    )
    return PidGains(kp=kp, ki=ki, kd=kd)


_LOG_INTERVAL_S = 15.0


def collect_samples(
    client: GaggimateWsClient,
    duration: float,
    scenario: str,
    *,
    max_safe_ct: float,
    log: Callable[[str], None] | None = None,
    log_label: str | None = None,
) -> list[StatusSample]:
    samples: list[StatusSample] = []
    t0 = time.time()
    last_log = 0.0
    consecutive_frozen = 0
    emit = log or (lambda message: print(message, file=sys.stderr))
    label = log_label or f"record {scenario}"

    def on_status(message: dict[str, object]) -> None:
        nonlocal last_log, consecutive_frozen
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
            consecutive_frozen += 1
            if consecutive_frozen >= MAX_FROZEN_CONSECUTIVE:
                raise RuntimeError(
                    f"abort: pidLive.frozen for {MAX_FROZEN_CONSECUTIVE} "
                    "consecutive ticks during idle tune"
                )
            emit(
                f"{label}: frozen tick {consecutive_frozen}/"
                f"{MAX_FROZEN_CONSECUTIVE} — tolerating transient dropout"
            )
        else:
            consecutive_frozen = 0
        now = time.time()
        if now - last_log >= _LOG_INTERVAL_S:
            remaining = int(duration - tick["t"])
            emit(
                f"{label}: t={tick['t']:.0f}s ({max(0, remaining)}s left) — {format_status(tick)}"
            )
            last_log = now

    first = client.wait_status(timeout=15.0)
    if not status_has_pid_live(first):
        raise RuntimeError("evt:status missing pidLive — deploy Plan 1 firmware first")
    on_status(first)
    emit(f"{label}: started — {format_status(parse_status_tick(first, t=0.0))}")
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
    # Reject runs that only graze the band: require ct to actually dwell inside it.
    if metrics.in_band_fraction < acc.min_in_band_fraction:
        return False
    # Tolerate a few transient frozen ticks; reject only sustained telemetry loss.
    if metrics.frozen_samples > acc.max_frozen_samples:
        return False
    return abs(metrics.final_error_c) <= acc.max_final_error_c


def run_ramp_test(
    client: GaggimateWsClient,
    scenario: TuneScenario,
    gains: PidGains,
    config: LadderConfig,
    *,
    dry_run: bool,
    needs_prep_hold: bool = False,
    log: Callable[[str], None] | None = None,
) -> tuple[list[StatusSample], RunMetrics]:
    """Step to final_tt and record ramp (cold_soak prep is in ensure_prep)."""
    emit = log or (lambda message: print(message, file=sys.stderr))
    config = fresh_config()

    if needs_prep_hold:
        cool_tt = prep_cool_tt(scenario)
        if cool_tt is not None:
            record_prep_hold(
                client,
                scenario,
                float(cool_tt),
                config,
                dry_run=dry_run,
                log=emit,
            )

    record_s = config.record_max_s_for(scenario.id)
    target = scenario.final_tt

    if not dry_run:
        apply_and_verify_pid(client, gains, emit, label=f"{scenario.id} ramp")
        client.set_target_temp(target)
        log_status(client, emit, f"{scenario.id}: tt={target:.0f}°C →")

    emit(f"{scenario.id}: recording ramp {record_s:.0f}s — peak + integral tail")
    if dry_run:
        return [], score_run([], target=target, band_c=config.acceptance.band_c)

    samples = collect_samples(
        client,
        record_s,
        scenario.id,
        max_safe_ct=config.max_safe_ct,
        log=emit,
    )
    metrics = score_run(samples, target=target, band_c=config.acceptance.band_c)
    if samples:
        last = samples[-1]
        done_tick = {
            "ct": last.ct,
            "tt": last.tt,
            "kp": last.kp,
            "ki": last.ki,
            "kd": last.kd,
        }
        emit(
            f"{scenario.id}: record done — {format_status(done_tick)} "
            f"overshoot={metrics.max_overshoot_c:.2f}°C "
            f"time_to_band={metrics.time_to_band_s} final_err={metrics.final_error_c:+.2f}°C"
        )
    return samples, metrics


def record_prep_hold(
    client: GaggimateWsClient,
    scenario: TuneScenario,
    cool_tt: float,
    config: LadderConfig,
    *,
    dry_run: bool,
    log: Callable[[str], None],
) -> list[StatusSample]:
    """Record telemetry at cool tt for prep_hold_s immediately before the ramp test."""
    hold_s = config.prep_hold_s
    if hold_s <= 0:
        return []

    if dry_run:
        log(
            f"{scenario.id}: dry-run — would record hold {hold_s:.0f}s at tt={cool_tt:.0f}"
        )
        return []

    client.set_target_temp(cool_tt)
    tick = read_tick(client)
    log(
        f"{scenario.id}: hold started at tt={cool_tt:.0f} — {format_status(tick)}"
    )
    samples = collect_samples(
        client,
        hold_s,
        scenario.id,
        max_safe_ct=config.max_safe_ct,
        log=log,
        log_label=f"hold {scenario.id}",
    )
    summary = summarize_hold(samples, cool_tt)
    log(
        f"{scenario.id}: hold done — ct mean={summary.ct_mean:.1f} "
        f"min={summary.ct_min:.1f} max={summary.ct_max:.1f} "
        f"err={summary.final_error_c:+.1f}°C samples={summary.sample_count}"
    )
    return samples


def ensure_prep(
    client: GaggimateWsClient,
    scenario: TuneScenario,
    config: LadderConfig,
    *,
    dry_run: bool,
    log: Callable[[str], None],
) -> bool:
    """Cool via scenario waypoints until ct <= cool_tt + tolerance.

    Returns True when active cooling ran (prep hold runs just before ramp test).
    """
    log_status(client, log, f"{scenario.id}: prep start —")
    tick = read_tick(client)
    ct = float(tick["ct"])

    if scenario.id == "cold_soak":
        if not prep_done(
            ct,
            scenario,
            ct_tolerance=config.ct_tolerance,
            cold_soak_max_ct=config.cold_soak_max_ct,
        ):
            waypoints = waypoints_for_scenario(
                scenario,
                ct,
                config.cool_waypoints,
                ct_tolerance=config.ct_tolerance,
                cold_soak_max_ct=config.cold_soak_max_ct,
            )
            if waypoints:
                cool_to_waypoints(
                    client,
                    waypoints,
                    ct_tolerance=config.ct_tolerance,
                    timeout_per_step=lambda wp: config.cool_timeout_s(wp),
                    dry_run=dry_run,
                    log=log,
                    stop_when=lambda current_ct: prep_done(
                        current_ct,
                        scenario,
                        ct_tolerance=config.ct_tolerance,
                        cold_soak_max_ct=config.cold_soak_max_ct,
                    ),
                )
            if not dry_run:
                ct = _read_ct(client)
                if ct > config.cold_soak_max_ct:
                    wait_until_ct(
                        client,
                        "<=",
                        config.cold_soak_max_ct,
                        config.cool_timeout_s(0),
                        log=log,
                    )
        if not dry_run:
            cold_equilibrate_at_zero(
                client,
                plateau_s=config.cold_soak_plateau_s,
                soak_s=config.cold_soak_duration_s,
                dry_run=False,
                log=log,
            )
        log_status(client, log, f"{scenario.id}: prep done —")
        return False

    if prep_done(ct, scenario, ct_tolerance=config.ct_tolerance):
        log(f"{scenario.id}: prep done (already cool) — {format_status(tick)}")
        return False

    waypoints = waypoints_for_scenario(
        scenario,
        ct,
        config.cool_waypoints,
        ct_tolerance=config.ct_tolerance,
        cold_soak_max_ct=config.cold_soak_max_ct,
    )
    cool_to_waypoints(
        client,
        waypoints,
        ct_tolerance=config.ct_tolerance,
        timeout_per_step=lambda wp: config.cool_timeout_s(wp),
        dry_run=dry_run,
        log=log,
        stop_when=lambda current_ct: prep_done(
            current_ct,
            scenario,
            ct_tolerance=config.ct_tolerance,
        ),
    )
    log_status(client, log, f"{scenario.id}: prep done —")
    return True


def run_scenario_once(
    client: GaggimateWsClient,
    scenario: TuneScenario,
    gains: PidGains,
    config: LadderConfig,
    state: LadderState,
    *,
    dry_run: bool,
    log: Callable[[str], None],
    label: str = "",
) -> tuple[RunMetrics | None, bool]:
    """Prep to start band, run tt=92 test, return (metrics, accepted)."""
    prefix = f"{label} " if label else ""
    if not dry_run:
        apply_and_verify_pid(
            client, gains, log, label=f"{prefix}{scenario.id} before prep"
        )
    needs_prep_hold = ensure_prep(client, scenario, config, dry_run=dry_run, log=log)

    if dry_run:
        run_ramp_test(
            client,
            scenario,
            gains,
            config,
            dry_run=True,
            needs_prep_hold=needs_prep_hold,
            log=log,
        )
        return None, True

    _samples, metrics = run_ramp_test(
        client,
        scenario,
        gains,
        config,
        dry_run=False,
        needs_prep_hold=needs_prep_hold,
        log=log,
    )
    state.last_metrics = metrics.to_dict()
    save_state(state)
    accepted = acceptance_ok(metrics, config)
    log(f"{prefix}{scenario.id}: metrics={metrics.to_dict()} accept={accepted}")
    return metrics, accepted


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

    iteration = 0
    while True:
        config = fresh_config()
        iteration += 1
        max_iter = config.max_iter_per_scenario
        if iteration > max_iter:
            raise RuntimeError(
                f"{scenario.id}: max iterations ({max_iter}) without acceptance"
            )

        if deadline is not None and time.time() >= deadline:
            raise TimeoutError(f"--max-hours exceeded during {scenario.id}")

        log(
            f"{scenario.id}: === iteration {iteration}/{max_iter} "
            f"gains={gains.as_tuple()} ==="
        )
        log_status(client, log, f"{scenario.id}: device before apply —")

        if dry_run:
            run_scenario_once(
                client,
                scenario,
                gains,
                config,
                state,
                dry_run=True,
                log=log,
            )
            log(f"{scenario.id}: dry-run — would tune iteration {iteration}")
            return gains

        _metrics, accepted = run_scenario_once(
            client,
            scenario,
            gains,
            config,
            state,
            dry_run=False,
            log=log,
        )
        metrics = _metrics
        assert metrics is not None
        step_log = {
            "scenario": scenario.id,
            "iteration": iteration,
            "metrics": metrics.to_dict(),
            "gains": gains.as_tuple(),
            "accept": accepted,
        }
        log(f"{scenario.id} iter {iteration}: {step_log}")

        if accepted:
            log_status(client, log, f"{scenario.id}: accepted —")
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
        apply_and_verify_pid(client, gains, log, label=f"{scenario.id} after suggest")
        log(f"{scenario.id}: pause {config.iter_pause_s:.0f}s before next iteration")
        time.sleep(config.iter_pause_s)


def _run_verification(
    client: GaggimateWsClient,
    gains: PidGains,
    config: LadderConfig,
    state: LadderState,
    *,
    deadline: float | None,
    log: Callable[[str], None],
) -> PidGains:
    """One full S1–S5 verify lap; safely halts early on scenario thrashing."""
    state.phase = "verification"
    state.verified = []
    save_state(state)

    max_attempts = config.verification_laps + 1
    thrashing_guard: set[str] = set()

    for attempt in range(max_attempts):
        log(f"verification attempt {attempt + 1}/{max_attempts}")
        failed_scenario: TuneScenario | None = None

        for scenario in config.scenarios:
            if scenario.id in state.verified:
                log(f"verify skip {scenario.id} (already verified)")
                continue

            if deadline is not None and time.time() >= deadline:
                raise TimeoutError("--max-hours exceeded during verification")

            state.current_scenario = scenario.id
            save_state(state)

            _metrics, ok = run_scenario_once(
                client,
                scenario,
                gains,
                config,
                state,
                dry_run=False,
                log=log,
                label="verify",
            )
            if ok:
                state.mark_verified(scenario.id)
                save_state(state)
                continue

            failed_scenario = scenario
            break

        if failed_scenario is None:
            return gains

        if failed_scenario.id in thrashing_guard:
            log(
                f"thrashing detected on {failed_scenario.id}! Halting verification to accept global compromise."
            )
            return gains

        thrashing_guard.add(failed_scenario.id)

        log(f"verify failed on {failed_scenario.id}, re-tuning")
        gains = tune_scenario_until_accept(
            client,
            failed_scenario,
            gains,
            config,
            state,
            dry_run=False,
            deadline=deadline,
            log=log,
        )
        state.verified = []
        save_state(state)

    raise RuntimeError("verification failed after max re-tune retries")


def _read_ct(client: GaggimateWsClient) -> float:
    message = client.wait_status(timeout=15.0)
    return float(parse_status_tick(message)["ct"])


def plan_ladder(
    ct: float, config: LadderConfig, state: LadderState | None
) -> list[str]:
    """Human-readable plan from current ct and resume state."""
    lines: list[str] = []
    completed = set(state.completed if state else [])
    for scenario in config.scenarios:
        if scenario.id in completed:
            lines.append(f"  [done] {scenario.id}")
            continue
        if prep_done(
            ct,
            scenario,
            ct_tolerance=config.ct_tolerance,
            cold_soak_max_ct=config.cold_soak_max_ct,
        ):
            cool_tt = prep_cool_tt(scenario)
            label = f"cool tt={cool_tt}°C" if cool_tt is not None else "cold soak"
            lines.append(f"  [ready] {scenario.id} (ct={ct:.1f}, {label})")
        else:
            waypoints = waypoints_for_scenario(
                scenario,
                ct,
                config.cool_waypoints,
                ct_tolerance=config.ct_tolerance,
                cold_soak_max_ct=config.cold_soak_max_ct,
            )
            cool = f" via {waypoints}" if waypoints else ""
            cool_tt = prep_cool_tt(scenario)
            hold_s = config.prep_hold_s
            if hold_s > 0 and cool_tt is not None:
                lines.append(
                    f"  [cool{cool}] {scenario.id} ct={ct:.1f} "
                    f"→ record hold {hold_s:.0f}s at tt={cool_tt} "
                    f"→ ramp tt={config.final_tt:.0f}"
                )
            else:
                lines.append(f"  [cool{cool}] {scenario.id} ct={ct:.1f}")
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
    log(f"start: {format_status(tick)}")

    gains = load_gains_from_settings(host, port)
    state = load_state(state_path) if resume else None
    if state is not None and state.host != host:
        log(f"resume: host mismatch ({state.host} vs {host}), starting fresh")
        state = None
    if state is None:
        state = new_state(host, gains)
    else:
        gains = state.gains_tuple()
        log(
            f"resume: phase={state.phase} completed={state.completed} verified={state.verified} gains={gains.as_tuple()}"
        )

    deadline = time.time() + max_hours * 3600 if max_hours is not None else None

    for line in plan_ladder(tick["ct"], cfg, state):
        log(line)

    if dry_run:
        return {
            "host": host,
            "dry_run": True,
            "plan": plan_ladder(tick["ct"], cfg, state),
        }

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
            cfg = fresh_config()
            apply_and_verify_pid(client, gains, log, label="ladder preflight")

            scenarios = list(cfg.scenarios)
            if only is not None:
                scenarios = [scenario for scenario in scenarios if scenario.id == only]

            tuning_done = all(s.id in state.completed for s in cfg.scenarios)
            skip_tuning = state.phase == "verification" and tuning_done and only is None

            if not skip_tuning:
                for scenario in scenarios:
                    if scenario.id in state.completed and state.phase == "tuning":
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
                tuning_done = all(s.id in state.completed for s in cfg.scenarios)

            if not only and tuning_done:
                cfg = fresh_config()
                gains = _run_verification(
                    client,
                    gains,
                    cfg,
                    state,
                    deadline=deadline,
                    log=log,
                )
                apply_and_verify_pid(client, gains, log, label="ladder final")
                result["persisted"] = gains.as_tuple()
                result["verified"] = list(state.verified)
                log(f"ladder complete — verified gains in NVS {gains.as_tuple()}")
                log_status(client, log, "verified gains in NVS —")
            elif tuning_done and only:
                log(
                    f"single scenario {only} accepted — gains in NVS {gains.as_tuple()}"
                )

    result["final_gains"] = gains.as_tuple()
    result["state"] = state.to_dict()
    return result
