"""Persist ladder progress for --resume (device-data/pid-tune/state.json)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pid_tune.suggest_gains import PidGains

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STATE_PATH = ROOT / "device-data" / "pid-tune" / "state.json"


@dataclass
class LadderState:
    host: str
    started_at: str
    current_scenario: str | None = None
    completed: list[str] = field(default_factory=list)
    gains: dict[str, float] = field(default_factory=dict)
    last_metrics: dict[str, Any] = field(default_factory=dict)

    def gains_tuple(self) -> PidGains:
        return PidGains(
            kp=float(self.gains.get("kp", 34.0)),
            ki=float(self.gains.get("ki", 0.27)),
            kd=float(self.gains.get("kd", 80.0)),
        )

    def set_gains(self, gains: PidGains) -> None:
        self.gains = {"kp": gains.kp, "ki": gains.ki, "kd": gains.kd}

    def mark_completed(self, scenario_id: str) -> None:
        if scenario_id not in self.completed:
            self.completed.append(scenario_id)
        self.current_scenario = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LadderState:
        return cls(
            host=str(data.get("host", "")),
            started_at=str(data.get("started_at", "")),
            current_scenario=data.get("current_scenario"),
            completed=list(data.get("completed", [])),
            gains=dict(data.get("gains", {})),
            last_metrics=dict(data.get("last_metrics", {})),
        )


def load_state(path: Path = DEFAULT_STATE_PATH) -> LadderState | None:
    if not path.is_file():
        return None
    return LadderState.from_dict(json.loads(path.read_text(encoding="utf-8")))


def save_state(state: LadderState, path: Path = DEFAULT_STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state.to_dict(), indent=2) + "\n", encoding="utf-8")


def new_state(host: str, gains: PidGains) -> LadderState:
    state = LadderState(
        host=host,
        started_at=datetime.now(timezone.utc).isoformat(),
    )
    state.set_gains(gains)
    return state
