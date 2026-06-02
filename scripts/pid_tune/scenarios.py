"""Five-scenario PID tuning ladder definitions."""

from __future__ import annotations

from dataclasses import dataclass

COOL_WAYPOINTS = [85, 60, 30]
CT_TOLERANCE = 1.0


@dataclass(frozen=True)
class TuneScenario:
    id: str
    name: str
    start_ct_min: float
    start_ct_max: float
    final_tt: float = 92.0
    pre_soak_tt: float = 0.0
    pre_soak_duration_s: float = 3600.0


SCENARIOS: tuple[TuneScenario, ...] = (
    TuneScenario("near_88", "near_88", 87.0, 89.0),
    TuneScenario("near_85", "near_85", 84.0, 86.0),
    TuneScenario("near_60", "near_60", 58.0, 62.0),
    TuneScenario("near_30", "near_30", 28.0, 32.0),
    TuneScenario("cold_soak", "cold_soak", 0.0, 2.0, pre_soak_tt=0.0, pre_soak_duration_s=3600.0),
)

_SCENARIO_BY_ID = {scenario.id: scenario for scenario in SCENARIOS}


def scenario_by_id(scenario_id: str) -> TuneScenario:
    try:
        return _SCENARIO_BY_ID[scenario_id]
    except KeyError as error:
        raise ValueError(f"unknown scenario: {scenario_id}") from error


def next_scenario(current_id: str | None) -> TuneScenario | None:
    if current_id is None:
        return SCENARIOS[0] if SCENARIOS else None
    for index, scenario in enumerate(SCENARIOS):
        if scenario.id == current_id and index + 1 < len(SCENARIOS):
            return SCENARIOS[index + 1]
    return None


def in_start_band(ct: float, scenario: TuneScenario) -> bool:
    return scenario.start_ct_min <= ct <= scenario.start_ct_max


def waypoints_above_ct(ct: float, waypoints: list[int] | None = None) -> list[int]:
    """Cooling ladder waypoints still above current ct (high → low order)."""
    ladder = waypoints if waypoints is not None else COOL_WAYPOINTS
    return [waypoint for waypoint in ladder if ct > waypoint + CT_TOLERANCE]
