"""Load ladder tuning config from config.yaml (JSON-compatible YAML subset)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from pid_tune.scenarios import TuneScenario

CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"


@dataclass
class AcceptanceConfig:
    max_overshoot_c: float = 0.5
    time_to_band_s: float = 240.0
    band_c: float = 0.5


@dataclass
class LadderConfig:
    final_tt: float = 92.0
    ct_tolerance: float = 1.0
    cool_waypoints: list[int] = field(default_factory=lambda: [85, 60, 30])
    max_safe_ct: float = 160.0
    max_iter_per_scenario: int = 12
    iter_pause_s: float = 5.0
    record_max_s_default: float = 240.0
    scenario_record_max_s: dict[str, float] = field(default_factory=dict)
    acceptance: AcceptanceConfig = field(default_factory=AcceptanceConfig)
    cool_timeouts_s: dict[str, float] = field(default_factory=dict)
    cold_soak_max_ct: float = 2.0
    cold_soak_duration_s: float = 3600.0
    scenarios: tuple[TuneScenario, ...] = ()

    def record_max_s_for(self, scenario_id: str) -> float:
        return self.scenario_record_max_s.get(scenario_id, self.record_max_s_default)

    def cool_timeout_s(self, waypoint: int) -> float:
        return self.cool_timeouts_s.get(str(waypoint), self.cool_timeouts_s.get(str(float(waypoint)), 3600.0))


def _parse_scenarios(raw: list[dict[str, object]]) -> tuple[TuneScenario, ...]:
    scenarios: list[TuneScenario] = []
    for item in raw:
        scenarios.append(
            TuneScenario(
                id=str(item["id"]),
                name=str(item.get("name", item["id"])),
                start_ct_min=float(item["start_ct_min"]),
                start_ct_max=float(item["start_ct_max"]),
                final_tt=float(item.get("final_tt", 92.0)),
                pre_soak_tt=float(item.get("pre_soak_tt", 0.0)),
                pre_soak_duration_s=float(item.get("pre_soak_duration_s", 3600.0)),
            )
        )
    return tuple(scenarios)


def load_config(path: Path | None = None) -> LadderConfig:
    config_path = path or CONFIG_PATH
    raw: dict[str, object] = {}
    if config_path.is_file():
        raw = json.loads(config_path.read_text(encoding="utf-8"))

    acceptance_raw = raw.get("acceptance", {})
    if not isinstance(acceptance_raw, dict):
        acceptance_raw = {}

    scenarios_raw = raw.get("scenarios", [])
    if not scenarios_raw:
        from pid_tune.scenarios import SCENARIOS

        scenarios = SCENARIOS
    else:
        scenarios = _parse_scenarios(scenarios_raw)  # type: ignore[arg-type]

    cool_timeouts = raw.get("cool_timeouts_s", {})
    if not isinstance(cool_timeouts, dict):
        cool_timeouts = {}

    scenario_record = raw.get("scenario_record_max_s", {})
    if not isinstance(scenario_record, dict):
        scenario_record = {}

    return LadderConfig(
        final_tt=float(raw.get("final_tt", 92.0)),
        ct_tolerance=float(raw.get("ct_tolerance", 1.0)),
        cool_waypoints=[int(value) for value in raw.get("cool_waypoints", [85, 60, 30])],
        max_safe_ct=float(raw.get("max_safe_ct", 160.0)),
        max_iter_per_scenario=int(raw.get("max_iter_per_scenario", 12)),
        iter_pause_s=float(raw.get("iter_pause_s", 5.0)),
        record_max_s_default=float(raw.get("record_max_s_default", 240.0)),
        scenario_record_max_s={str(key): float(value) for key, value in scenario_record.items()},
        acceptance=AcceptanceConfig(
            max_overshoot_c=float(acceptance_raw.get("max_overshoot_c", 0.5)),
            time_to_band_s=float(acceptance_raw.get("time_to_band_s", 240.0)),
            band_c=float(acceptance_raw.get("band_c", 0.5)),
        ),
        cool_timeouts_s={str(key): float(value) for key, value in cool_timeouts.items()},
        cold_soak_max_ct=float(raw.get("cold_soak_max_ct", 2.0)),
        cold_soak_duration_s=float(raw.get("cold_soak_duration_s", 3600.0)),
        scenarios=scenarios,
    )
