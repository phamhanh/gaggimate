"""Apply PID to device and verify via HTTP /api/status (NVS kp/ki/kd)."""

from __future__ import annotations

import time
from typing import Callable

from gaggimate_ws import GaggimateWsClient, parse_status_tick, read_status_once
from pid_tune.suggest_gains import PidGains

# Match tolerances for NVS float formatting (settings store %.3f in pid CSV).
KP_TOL = 0.05
KI_TOL = 0.001
KD_TOL = 1.0
VERIFY_TIMEOUT_S = 10.0
VERIFY_POLL_S = 0.5


def read_gains_from_device(host: str, port: int) -> PidGains:
    tick = parse_status_tick(read_status_once(host, port))
    return PidGains(kp=float(tick["kp"]), ki=float(tick["ki"]), kd=float(tick["kd"]))


def gains_close(expected: PidGains, actual: PidGains) -> bool:
    return (
        abs(expected.kp - actual.kp) <= KP_TOL
        and abs(expected.ki - actual.ki) <= KI_TOL
        and abs(expected.kd - actual.kd) <= KD_TOL
    )


def _wait_for_gains(
    host: str,
    port: int,
    expected: PidGains,
    log: Callable[[str], None],
    *,
    timeout_s: float,
    label: str,
) -> PidGains:
    deadline = time.time() + timeout_s
    last = read_gains_from_device(host, port)
    while time.time() < deadline:
        if gains_close(expected, last):
            return last
        time.sleep(VERIFY_POLL_S)
        last = read_gains_from_device(host, port)
        log(f"{label}: waiting — want {expected.as_tuple()} have {last.as_tuple()}")
    raise RuntimeError(
        f"{label}: expected {expected.as_tuple()}, "
        f"/api/status still shows {last.as_tuple()} after {timeout_s:.0f}s."
    )


def apply_and_verify_pid(
    client: GaggimateWsClient,
    gains: PidGains,
    log: Callable[[str], None],
    *,
    timeout_s: float = VERIFY_TIMEOUT_S,
    label: str = "pid",
) -> PidGains:
    """Send req:set-pid (persist=true), poll /api/status until kp/ki/kd match.

    A single apply is realistic: firmware Heater::setTunings calls
    simplePid->reset() whenever the gains differ, which zeros the integral
    accumulator. No Ki=0 pre-step is needed — and real machines never get one.
    """
    client.set_pid(gains.kp, gains.ki, gains.kd)
    log(f"{label}: set {gains.as_tuple()}, verifying on /api/status …")
    try:
        verified = _wait_for_gains(
            client.host,
            client.port,
            gains,
            log,
            timeout_s=timeout_s,
            label=label,
        )
    except RuntimeError as exc:
        raise RuntimeError(
            f"PID apply failed: {exc} "
            "Check firmware req:set-pid + persist, and that --host matches the machine you curl."
        ) from exc
    log(f"{label}: verified {verified.as_tuple()} on device")
    return verified
