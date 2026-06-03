"""Five-scenario PID tuning ladder definitions."""

from __future__ import annotations

from dataclasses import dataclass

COOL_WAYPOINTS = [88, 85, 60, 30]
CT_TOLERANCE = 0.0

# Lowest cool tt for each scenario's prep (wait until ct <= tt + ct_tolerance).
PREP_COOL_TT: dict[str, int] = {
    "near_88": 88,
    "near_85": 85,
    "near_60": 60,
    "near_30": 30,
}


@dataclass(frozen=True)
class TuneScenario:
    id: str
    name: str
    start_ct_min: float
    start_ct_max: float
    final_tt: float = 92.0
    pre_soak_tt: float = 0.0
    pre_soak_duration_s: float = 3600.0


def scenario_by_id(scenario_id: str) -> TuneScenario:
    for scenario in SCENARIOS:
        if scenario.id == scenario_id:
            return scenario
    raise KeyError(scenario_id)


SCENARIOS: tuple[TuneScenario, ...] = (
    TuneScenario("near_88", "near_88", 88.0, 88.0),
    TuneScenario("near_85", "near_85", 85.0, 85.0),
    TuneScenario("near_60", "near_60", 60.0, 60.0),
    TuneScenario("near_30", "near_30", 30.0, 30.0),
    TuneScenario(
        "cold_soak",
        "cold_soak",
        0.0,
        2.0,
        final_tt=92.0,
        pre_soak_tt=0.0,
        pre_soak_duration_s=1500.0,
    ),
)


def prep_cool_tt(scenario: TuneScenario) -> int | None:
    """Return the lowest cooling target temperature for a scenario's prep."""
    return PREP_COOL_TT.get(scenario.id)


def prep_done(
    ct: float,
    scenario: TuneScenario,
    *,
    ct_tolerance: float = CT_TOLERANCE,
    cold_soak_max_ct: float = 2.0,
) -> bool:
    if scenario.id == "cold_soak":
        return ct <= cold_soak_max_ct
    cool_tt = prep_cool_tt(scenario)
    if cool_tt is None:
        return False
    return ct <= cool_tt + ct_tolerance


def waypoints_above_ct(ct: float, waypoints: list[int] | None = None) -> list[int]:
    """Cooling ladder waypoints still above current ct (high → low order)."""
    ladder = waypoints if waypoints is not None else COOL_WAYPOINTS
    return [waypoint for waypoint in ladder if ct > waypoint + CT_TOLERANCE]


def waypoints_for_scenario(
    scenario: TuneScenario,
    ct: float,
    cool_waypoints: list[int] | None = None,
    *,
    ct_tolerance: float = CT_TOLERANCE,
    cold_soak_max_ct: float = 2.0,
) -> list[int]:
    """Cool waypoints still needed before prep_done (high → low)."""
    if prep_done(
        ct, scenario, ct_tolerance=ct_tolerance, cold_soak_max_ct=cold_soak_max_ct
    ):
        return []

    ladder = cool_waypoints if cool_waypoints is not None else COOL_WAYPOINTS

    if scenario.id == "near_88":
        return [88] if 88 in ladder and ct > 88 + ct_tolerance else []

    if scenario.id == "near_85":
        return [85] if 85 in ladder and ct > 85 + ct_tolerance else []

    if scenario.id == "near_60":
        return [wp for wp in ladder if wp >= 60 and ct > wp + ct_tolerance]

    if scenario.id == "near_30":
        return [wp for wp in ladder if wp >= 30 and ct > wp + ct_tolerance]

    if scenario.id == "cold_soak":
        return [wp for wp in ladder if ct > wp + ct_tolerance]

    return []
