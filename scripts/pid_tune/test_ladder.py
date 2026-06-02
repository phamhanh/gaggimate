"""Minimal unit tests for PID ladder (no device)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pid_tune.config import load_config
from pid_tune.metrics import RunMetrics, score_run, time_to_band
from pid_tune.metrics import StatusSample
from pid_tune.runner import acceptance_ok, plan_ladder
from pid_tune.scenarios import in_start_band, scenario_by_id, waypoints_above_ct
from pid_tune.state import LadderState, new_state
from pid_tune.suggest_gains import PidGains, suggest_gains


class ScenariosTest(unittest.TestCase):
    def test_in_start_band(self) -> None:
        scenario = scenario_by_id("near_88")
        self.assertTrue(in_start_band(88.0, scenario))
        self.assertFalse(in_start_band(92.0, scenario))

    def test_waypoints_above_ct(self) -> None:
        self.assertEqual(waypoints_above_ct(95.0), [85, 60, 30])
        self.assertEqual(waypoints_above_ct(82.0), [60, 30])
        self.assertEqual(waypoints_above_ct(25.0), [])


class SuggestGainsTest(unittest.TestCase):
    def test_overshoot_reduces_kp(self) -> None:
        metrics = RunMetrics(
            target_temp=92.0,
            max_overshoot_c=0.8,
            time_to_band_s=90.0,
            in_band_fraction=0.5,
        )
        gains, reason = suggest_gains(metrics, 34.0, 0.27, 80.0)
        self.assertLess(gains.kp, 34.0)
        self.assertIn("overshoot", reason)


class AcceptanceTest(unittest.TestCase):
    def test_acceptance_ok(self) -> None:
        config = load_config()
        samples = [
            StatusSample(t=0, ct=88, tt=92),
            StatusSample(t=30, ct=91.8, tt=92),
            StatusSample(t=60, ct=92.0, tt=92),
        ]
        metrics = score_run(samples, target=92.0, band_c=0.5)
        metrics.time_to_band_s = time_to_band(samples, band_c=0.5, target=92.0)
        self.assertTrue(acceptance_ok(metrics, config))


class StateTest(unittest.TestCase):
    def test_resume_completed(self) -> None:
        state = new_state("host", PidGains(30, 0.2, 70))
        state.mark_completed("near_88")
        self.assertIn("near_88", state.completed)
        self.assertIsNone(state.current_scenario)


class PlanLadderTest(unittest.TestCase):
    def test_plan_marks_done(self) -> None:
        config = load_config()
        state = LadderState(host="h", started_at="t", completed=["near_88"])
        lines = plan_ladder(88.0, config, state)
        self.assertTrue(any("[done] near_88" in line for line in lines))


if __name__ == "__main__":
    unittest.main()
