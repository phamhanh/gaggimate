"""Deterministic next-gain suggestions from run metrics (no LLM in hot path)."""

from __future__ import annotations

from dataclasses import dataclass

from pid_tune.metrics import RunMetrics

# Heater scales autotune pre-gains ×1000 → physical Kp ∈ [20, 300], Kd ∈ [50, 800].
# Updated physical limits tailored to high-lag, small-volume espresso boilers
KP_MIN, KP_MAX = (
    2.0,
    150.0,
)  # Expanded floor; dropped cap to prevent aggressive oscillations
KI_MIN, KI_MAX = 0.001, 0.50  # Significantly crushed cap to eliminate windup saturation
KD_MIN, KD_MAX = (
    20.0,
    1500.0,
)  # Massively expanded ceiling for strong derivative braking

BAND_IDEAL_S = 60.0
BAND_MAX_S = 240.0
OVERSHOOT_HIGH_C = 0.5
OVERSHOOT_MED_C = 0.2

# Strict steady-state performance requirements
STRICT_STABILITY_BAND_C = 0.15  # Must hold within +/- 0.15°C of target temp
MIN_STABLE_DURATION_S = 60.0  # Must maintain flatline stability for 60 seconds minimum


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
    """Return clamped next gains, enforcing a strict 60-second target-hold threshold."""
    reason_parts: list[str] = []
    new_kp, new_ki, new_kd = kp, ki, kd

    slow = metrics.time_to_band_s is None or metrics.time_to_band_s > band_max_s

    # =========================================================================
    # PATH 1: TRANSIENT PERFORMANCE & STABILITY (TUNES KP & KD)
    # =========================================================================
    # Mutually exclusive overshoot tiers — a severe overshoot must NOT also fire
    # the high-overshoot branch (that compounded Kp×0.675/Kd×1.725 and lurched
    # the search into undershoot, causing gain oscillation instead of convergence).
    if metrics.max_overshoot_c > 1.0:  # Radical adjustment for massive overshoots
        new_kp *= 0.75
        new_kd *= 1.50
        reason_parts.append("severe-overshoot:↓↓Kp↑↑Kd")

    elif metrics.max_overshoot_c > OVERSHOOT_HIGH_C:
        new_kp *= 0.90
        new_kd *= 1.15
        reason_parts.append(f"overshoot>{OVERSHOOT_HIGH_C}C:↓Kp↑Kd")

    elif metrics.max_overshoot_c > OVERSHOOT_MED_C:
        new_kp *= 0.96
        new_kd = max(new_kd * 1.03, new_kd + 3.0)
        reason_parts.append(f"overshoot>{OVERSHOOT_MED_C}C:small↓Kp↑Kd")

    elif (
        metrics.ct_variance_in_band is not None
        and metrics.ct_variance_in_band > 0.15
        and metrics.in_band_fraction > 0.4
    ):
        new_kp *= 0.94
        new_kd = max(new_kd * 1.06, new_kd + 4.0)
        reason_parts.append("ringing in band:↓Kp↑Kd")

    elif slow:
        new_kp *= 1.10
        reason_parts.append("slow/no-overshoot:↑Kp")

    elif metrics.time_to_band_s is not None and metrics.time_to_band_s > BAND_IDEAL_S:
        new_kp *= 1.05
        reason_parts.append("moderately slow:↑Kp")

    # =========================================================================
    # PATH 2: STEADY-STATE ACCURACY & 60s HOLD (TUNES KI SIMULTANEOUSLY)
    # =========================================================================
    # Derive the absolute number of seconds spent inside the target band.
    if metrics.duration_s >= 120.0:
        actual_stable_seconds = metrics.in_band_fraction * metrics.duration_s

        # Condition A: Check if the tail is cleanly welded to target center
        is_welded_to_target = (
            abs(metrics.final_error_c) <= STRICT_STABILITY_BAND_C
            and metrics.ct_variance_in_band is not None
            and metrics.ct_variance_in_band < 0.05
        )

        # Condition B: Enforce the ultimate hold duration requirement
        held_target_for_60s = (
            actual_stable_seconds >= MIN_STABLE_DURATION_S and is_welded_to_target
        )

        if not held_target_for_60s:
            # System failed hold criteria. If it's drooping/sagging cold below target:
            if metrics.final_error_c < -STRICT_STABILITY_BAND_C:
                if new_ki <= 0.001:
                    new_ki = (
                        0.015  # Instantly bypass the multiplication-from-zero dead end
                    )
                    reason_parts.append("failed-60s-hold:kickstart-Ki-bias")
                else:
                    new_ki *= (
                        1.20  # Aggressively scale up to eliminate steady-state droop
                    )
                    reason_parts.append("failed-60s-hold:↑Ki")

            # If it's floating too warm above the target ceiling:
            elif metrics.final_error_c > STRICT_STABILITY_BAND_C:
                new_ki *= 0.80  # Drop I term to dump systemic accumulation
                reason_parts.append("failed-60s-hold:↓Ki")

            # Variance or noise issue at the tail rather than directional drift
            else:
                reason_parts.append("unstable-tail-variance:holding-gains")

        else:
            # Truly successful run: Passed both transient limits and flatlined for 60+ seconds
            if not any("Kp" in part or "Kd" in part for part in reason_parts):
                reason_parts.append(
                    f"TARGET MET: Perfectly flatlined for {actual_stable_seconds:.1f}s; locked"
                )

    # Fallback catch-all if absolutely zero conditions met
    if not reason_parts:
        reason_parts.append("no changes triggered; hold gains")

    gains = clamp_gains(new_kp, new_ki, new_kd)
    return gains, "; ".join(reason_parts)
