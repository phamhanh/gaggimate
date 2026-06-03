"""Score idle/preheat PID runs from evt:status samples."""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field


@dataclass
class StatusSample:
    t: float
    ct: float
    tt: float
    p: float = 0.0
    i: float = 0.0
    d: float = 0.0
    kff: float = 0.0
    out: float = 0.0
    frozen: int = 0
    kp: float = 0.0
    ki: float = 0.0
    kd: float = 0.0
    scenario: str = ""


@dataclass
class HoldSummary:
    ct_min: float = 0.0
    ct_max: float = 0.0
    ct_mean: float = 0.0
    final_error_c: float = 0.0
    sample_count: int = 0
    duration_s: float = 0.0


def summarize_hold(samples: list[StatusSample], target_tt: float) -> HoldSummary:
    if not samples:
        return HoldSummary()
    cts = [sample.ct for sample in samples]
    return HoldSummary(
        ct_min=min(cts),
        ct_max=max(cts),
        ct_mean=statistics.mean(cts),
        final_error_c=samples[-1].ct - target_tt,
        sample_count=len(samples),
        duration_s=samples[-1].t - samples[0].t,
    )


@dataclass
class RunMetrics:
    target_temp: float
    band_c: float = 0.5
    time_to_band_s: float | None = None
    max_overshoot_c: float = 0.0
    max_undershoot_c: float = 0.0
    final_error_c: float = 0.0
    in_band_fraction: float = 0.0
    ct_variance_in_band: float | None = None
    max_abs_i: float = 0.0
    duration_s: float = 0.0
    sample_count: int = 0
    frozen_samples: int = 0

    def to_dict(self) -> dict[str, float | int | None]:
        return {
            "target_temp": self.target_temp,
            "band_c": self.band_c,
            "time_to_band_s": self.time_to_band_s,
            "max_overshoot_c": round(self.max_overshoot_c, 3),
            "max_undershoot_c": round(self.max_undershoot_c, 3),
            "final_error_c": round(self.final_error_c, 3),
            "in_band_fraction": round(self.in_band_fraction, 3),
            "ct_variance_in_band": (
                round(self.ct_variance_in_band, 4)
                if self.ct_variance_in_band is not None
                else None
            ),
            "max_abs_i": round(self.max_abs_i, 2),
            "duration_s": round(self.duration_s, 1),
            "sample_count": self.sample_count,
            "frozen_samples": self.frozen_samples,
        }


def _effective_target(samples: list[StatusSample]) -> float:
    for sample in reversed(samples):
        if sample.tt > 0:
            return sample.tt
    return samples[-1].tt if samples else 0.0


def time_to_band(
    samples: list[StatusSample],
    *,
    band_c: float = 0.5,
    target: float | None = None,
) -> float | None:
    if not samples:
        return None
    tt = target if target is not None else _effective_target(samples)
    if tt <= 0:
        return None
    t0 = samples[0].t
    for sample in samples:
        if abs(sample.ct - tt) <= band_c:
            return sample.t - t0
    return None


def max_overshoot(samples: list[StatusSample], *, target: float | None = None) -> float:
    if not samples:
        return 0.0
    tt = target if target is not None else _effective_target(samples)
    return max(0.0, max(sample.ct - tt for sample in samples))


def max_undershoot(
    samples: list[StatusSample], *, target: float | None = None
) -> float:
    if not samples:
        return 0.0
    tt = target if target is not None else _effective_target(samples)
    return max(0.0, max(tt - sample.ct for sample in samples))


def in_band_fraction(
    samples: list[StatusSample], *, band_c: float = 0.5, target: float | None = None
) -> float:
    if not samples:
        return 0.0
    tt = target if target is not None else _effective_target(samples)
    if tt <= 0:
        return 0.0
    in_band = sum(1 for sample in samples if abs(sample.ct - tt) <= band_c)
    return in_band / len(samples)


def variance_in_band(
    samples: list[StatusSample],
    *,
    band_c: float = 0.5,
    target: float | None = None,
    min_samples: int = 8,
    settling_grace_s: float = 0.0,
) -> float | None:
    """Calculates variance of in-band samples, optionally skipping the initial transition."""
    tt = target if target is not None else _effective_target(samples)
    if tt <= 0 or not samples:
        return None

    first_in_band_t = None
    for s in samples:
        if abs(s.ct - tt) <= band_c:
            first_in_band_t = s.t
            break

    if first_in_band_t is None:
        return None

    steady_ct = [
        sample.ct
        for sample in samples
        if abs(sample.ct - tt) <= band_c
        and sample.t >= (first_in_band_t + settling_grace_s)
    ]

    if len(steady_ct) < min_samples:
        return None
    return statistics.pvariance(steady_ct)


def is_stable(
    samples: list[StatusSample],
    *,
    band_c: float = 0.2,
    window: int = 16,
    target: float | None = None,
) -> bool:
    if len(samples) < window:
        return False
    tail = samples[-window:]
    var = variance_in_band(tail, band_c=band_c, target=target, min_samples=window)
    return var is not None and var < 0.02


def wait_stable(
    samples: list[StatusSample],
    *,
    band_c: float = 0.2,
    window: int = 16,
    target: float | None = None,
) -> float | None:
    """Return sample time t when tail window first becomes stable, else None."""
    for end in range(window, len(samples) + 1):
        if is_stable(samples[:end], band_c=band_c, window=window, target=target):
            return samples[end - 1].t
    return None


def score_run(
    samples: list[StatusSample],
    *,
    band_c: float = 0.5,
    target: float | None = None,
) -> RunMetrics:
    if not samples:
        return RunMetrics(target_temp=target or 0.0, band_c=band_c)

    tt = target if target is not None else _effective_target(samples)
    t0 = samples[0].t
    duration = samples[-1].t - t0
    metrics = RunMetrics(
        target_temp=tt,
        band_c=band_c,
        time_to_band_s=time_to_band(samples, band_c=band_c, target=tt),
        max_overshoot_c=max_overshoot(samples, target=tt),
        max_undershoot_c=max_undershoot(samples, target=tt),
        final_error_c=samples[-1].ct - tt,
        in_band_fraction=in_band_fraction(samples, band_c=band_c, target=tt),
        ct_variance_in_band=variance_in_band(
            samples, band_c=band_c, target=tt, settling_grace_s=15.0
        ),
        max_abs_i=max(abs(sample.i) for sample in samples),
        duration_s=duration,
        sample_count=len(samples),
        frozen_samples=sum(1 for sample in samples if sample.frozen),
    )
    return metrics
