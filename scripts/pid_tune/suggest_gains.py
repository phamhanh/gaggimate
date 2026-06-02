"""Deterministic next-gain suggestions from run metrics (no LLM in hot path)."""

from __future__ import annotations

from dataclasses import dataclass

from pid_tune.metrics import RunMetrics

# Heater scales autotune pre-gains ×1000 → physical Kp ∈ [20, 300], Kd ∈ [50, 800].
KP_MIN, KP_MAX = 20.0, 300.0
KI_MIN, KI_MAX = 0.05, 2.0
KD_MIN, KD_MAX = 50.0, 800.0

BAND_IDEAL_S = 60.0
BAND_MAX_S = 240.0
OVERSHOOT_HIGH_C = 0.5
OVERSHOOT_MED_C = 0.2


@dataclass
class PidGains:
    kp: float
    ki: float
    kd: float

    def as_tuple(self) -> tuple[float, float, float]:
        return (self.kp, self.ki, self.kd)


def clamp_gains(kp: float, ki: float, kd: float) -> PidGains:
    return PidGains(
        kp=min(max(kp, KP_MIN), KP_MAX),
        ki=min(max(ki, KI_MIN), KI_MAX),
        kd=min(max(kd, KD_MIN), KD_MAX),
    )


def suggest_gains(
    metrics: RunMetrics,
    kp: float,
    ki: float,
    kd: float,
    *,
    band_max_s: float = BAND_MAX_S,
) -> tuple[PidGains, str]:
    """Return clamped next gains and a short reason string."""
    reason_parts: list[str] = []
    new_kp, new_ki, new_kd = kp, ki, kd

    if metrics.max_overshoot_c > OVERSHOOT_HIGH_C:
        new_kp *= 0.92
        new_kd *= 1.05
        reason_parts.append(f"overshoot>{OVERSHOOT_HIGH_C}C:↓Kp↑Kd")
    elif metrics.max_overshoot_c > OVERSHOOT_MED_C:
        new_kp *= 0.96
        new_kd *= 1.03
        reason_parts.append(f"overshoot>{OVERSHOOT_MED_C}C:small↓Kp↑Kd")

    slow = metrics.time_to_band_s is None or metrics.time_to_band_s > band_max_s
    if slow and metrics.max_overshoot_c <= OVERSHOOT_MED_C:
        new_kp *= 1.10
        new_ki *= 1.10
        reason_parts.append("slow/no-overshoot:↑Kp↑Ki")
    elif (
        metrics.time_to_band_s is not None
        and metrics.time_to_band_s > BAND_IDEAL_S
        and metrics.max_overshoot_c <= OVERSHOOT_MED_C
    ):
        new_kp *= 1.05
        reason_parts.append("moderately slow:↑Kp")

    if (
        metrics.in_band_fraction > 0.5
        and metrics.final_error_c < -0.15
        and metrics.max_overshoot_c <= OVERSHOOT_MED_C
    ):
        new_ki *= 1.08
        reason_parts.append("drift low in band:↑Ki")

    if (
        metrics.ct_variance_in_band is not None
        and metrics.ct_variance_in_band > 0.15
        and metrics.in_band_fraction > 0.4
    ):
        new_kp *= 0.94
        new_kd *= 1.06
        reason_parts.append("ringing in band:↓Kp↑Kd")

    if not reason_parts:
        reason_parts.append("within targets; hold gains")

    gains = clamp_gains(new_kp, new_ki, new_kd)
    return gains, "; ".join(reason_parts)
