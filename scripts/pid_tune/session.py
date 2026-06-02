"""Auto-standby guard for PID tuning (POST /api/settings only)."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from gaggimate_ws import (
    GaggimateWsClient,
    fetch_api_settings,
    post_api_settings,
)

MODE_STANDBY = 0
MODE_BREW = 1


def read_standby_timeout_s(host: str, port: int) -> int:
    """standbyTimeout from GET /api/settings (seconds; 0 = disabled)."""
    settings = fetch_api_settings(host, port)
    return int(settings.get("standbyTimeout", 0))


def disable_auto_standby(host: str, port: int) -> None:
    """
    Prevent activateStandby() while idle (Controller.cpp: standbyTimeout > 0 required).

    Equivalent to:
      curl -X POST "http://HOST/api/settings" --data "standbyTimeout=0"
    """
    post_api_settings(host, {"standbyTimeout": "0"}, port=port)


def restore_auto_standby(host: str, port: int, standby_timeout_s: int) -> None:
    post_api_settings(host, {"standbyTimeout": str(standby_timeout_s)}, port=port)


def prepare_idle_tune(host: str, port: int, *, target_water_c: int = 93) -> None:
    """Kff off, brew startup, target temp (separate from standby guard)."""
    settings = fetch_api_settings(host, port)
    post_api_settings(
        host,
        {
            "pid": str(settings.get("pid", "")),
            "targetWaterTemp": str(target_water_c),
            "startupMode": "brew",
        },
        port=port,
    )


def ensure_brew_mode(client: GaggimateWsClient, timeout: float = 10.0) -> None:
    """Exit MODE_STANDBY (tt=0) via req:change-mode → MODE_BREW."""
    message = client.wait_status(timeout=timeout)
    mode = message.get("m", message.get("mode", MODE_BREW))
    if int(mode) == MODE_STANDBY:
        client.set_mode(MODE_BREW)


@contextmanager
def pid_tune_session(
    host: str,
    port: int,
    *,
    apply_idle_prep: bool = True,
    target_water_c: int = 93,
) -> Iterator[int]:
    """
    On enter: POST standbyTimeout=0 (and optional idle tune prep).
    On exit: restore standbyTimeout to the value read at start.
    Yields the original standby timeout in seconds.
    """
    original_s = read_standby_timeout_s(host, port)
    disable_auto_standby(host, port)
    if apply_idle_prep:
        prepare_idle_tune(host, port, target_water_c=target_water_c)
    try:
        yield original_s
    finally:
        restore_auto_standby(host, port, original_s)
