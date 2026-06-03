"""Human-readable evt:status lines for PID ladder debugging."""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from gaggimate_ws import GaggimateWsClient, parse_status_tick


def wall_clock() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def format_status(tick: dict[str, float | int]) -> str:
    """Single-line ct/tt/NVS gains snapshot from parse_status_tick output."""
    return (
        f"{wall_clock()} ct={tick['ct']:.1f}°C tt={tick['tt']:.1f}°C "
        f"Kp={tick['kp']:.2f} Ki={tick['ki']:.3f} Kd={tick['kd']:.0f}"
    )


def read_tick(client: GaggimateWsClient, timeout: float = 15.0) -> dict[str, float | int]:
    message = client.wait_status(timeout=timeout)
    return parse_status_tick(message)


def log_status(
    client: GaggimateWsClient,
    log: Callable[[str], None],
    prefix: str,
    *,
    timeout: float = 15.0,
) -> dict[str, float | int]:
    tick = read_tick(client, timeout=timeout)
    log(f"{prefix} {format_status(tick)}")
    return tick
