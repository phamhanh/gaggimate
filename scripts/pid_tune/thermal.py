"""Blocking thermal waits over evt:status (passive cooling, soak)."""

from __future__ import annotations

import sys
import time
from typing import Callable, Literal

from gaggimate_ws import GaggimateWsClient, parse_status_tick

CompareOp = Literal["<=", ">="]
_LOG_INTERVAL_S = 60.0


def _read_ct(client: GaggimateWsClient, timeout: float = 15.0) -> float:
    message = client.wait_status(timeout=timeout)
    return float(parse_status_tick(message)["ct"])


def _compare(ct: float, threshold: float, op: CompareOp) -> bool:
    if op == "<=":
        return ct <= threshold
    return ct >= threshold


def wait_until_ct(
    client: GaggimateWsClient,
    op: CompareOp,
    threshold: float,
    timeout_s: float,
    *,
    log: Callable[[str], None] | None = None,
) -> float:
    """Block until ct satisfies op threshold or timeout. Returns final ct."""
    emit = log or (lambda message: print(message, file=sys.stderr))
    deadline = time.time() + timeout_s
    last_log = 0.0
    ct = _read_ct(client)
    while time.time() < deadline:
        if _compare(ct, threshold, op):
            emit(f"thermal: ct={ct:.1f} reached {op} {threshold:.1f}")
            return ct
        now = time.time()
        if now - last_log >= _LOG_INTERVAL_S:
            remaining = int(deadline - now)
            emit(f"thermal: ct={ct:.1f} waiting {op} {threshold:.1f} ({remaining}s left)")
            last_log = now
        time.sleep(0.5)
        ct = _read_ct(client, timeout=5.0)
    raise TimeoutError(f"ct={ct:.1f} did not reach {op} {threshold:.1f} within {timeout_s:.0f}s")


def cool_to_waypoints(
    client: GaggimateWsClient,
    waypoints: list[int],
    *,
    ct_tolerance: float,
    timeout_per_step: Callable[[int], float],
    dry_run: bool = False,
    log: Callable[[str], None] | None = None,
) -> None:
    """Set tt to each waypoint (high→low) and wait until ct <= waypoint + tolerance."""
    emit = log or (lambda message: print(message, file=sys.stderr))
    for waypoint in waypoints:
        threshold = waypoint + ct_tolerance
        timeout_s = timeout_per_step(waypoint)
        emit(f"cool: target tt={waypoint}°C, wait ct<={threshold:.1f} (timeout {timeout_s / 60:.0f} min)")
        if not dry_run:
            client.set_target_temp(float(waypoint))
        wait_until_ct(client, "<=", threshold, timeout_s, log=emit)


def soak_at_zero(
    client: GaggimateWsClient,
    duration_s: float,
    *,
    dry_run: bool = False,
    log: Callable[[str], None] | None = None,
) -> None:
    """Heater off at tt=0; block for duration_s."""
    emit = log or (lambda message: print(message, file=sys.stderr))
    emit(f"soak: tt=0 for {duration_s / 60:.0f} min")
    if not dry_run:
        client.set_target_temp(0.0)
    deadline = time.time() + duration_s
    last_log = 0.0
    while time.time() < deadline:
        now = time.time()
        if now - last_log >= _LOG_INTERVAL_S:
            remaining = int(deadline - now)
            ct = _read_ct(client, timeout=5.0)
            emit(f"soak: {remaining}s left, ct={ct:.1f}")
            last_log = now
        time.sleep(2.0)


def stabilize_at_tt(
    client: GaggimateWsClient,
    tt: float,
    band: float,
    min_stable_s: float,
    *,
    max_wait_s: float = 300.0,
    dry_run: bool = False,
    log: Callable[[str], None] | None = None,
) -> None:
    """Wait until ct stays within band of tt for min_stable_s (optional pre-step)."""
    emit = log or (lambda message: print(message, file=sys.stderr))
    if not dry_run:
        client.set_target_temp(tt)
    emit(f"stabilize: tt={tt:.0f}, band±{band:.1f} for {min_stable_s:.0f}s")
    stable_since: float | None = None
    deadline = time.time() + max_wait_s
    while time.time() < deadline:
        ct = _read_ct(client, timeout=5.0)
        if abs(ct - tt) <= band:
            if stable_since is None:
                stable_since = time.time()
            elif time.time() - stable_since >= min_stable_s:
                emit(f"stabilize: ct={ct:.1f} stable at tt={tt:.0f}")
                return
        else:
            stable_since = None
        time.sleep(0.5)
    raise TimeoutError(f"did not stabilize at tt={tt:.0f} within {max_wait_s:.0f}s")
