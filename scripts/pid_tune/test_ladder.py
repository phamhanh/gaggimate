"""Minimal unit tests for PID ladder (no device)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pid_tune.config import load_config
from pid_tune.metrics import RunMetrics, score_run, summarize_hold, time_to_band
from pid_tune.metrics import StatusSample
from pid_tune.pid_apply import apply_and_verify_pid, gains_close
from pid_tune.suggest_gains import PidGains
from pid_tune.runner import acceptance_ok, plan_ladder, record_prep_hold, run_ramp_test
from pid_tune.status_log import format_status
from pid_tune.scenarios import (
    prep_cool_tt,
    prep_done,
    scenario_by_id,
    waypoints_above_ct,
    waypoints_for_scenario,
)
from pid_tune.state import LadderState, load_state, new_state
from pid_tune.suggest_gains import suggest_gains


class ScenariosTest(unittest.TestCase):
    def test_waypoints_above_ct(self) -> None:
        self.assertEqual(waypoints_above_ct(95.0), [88, 85, 60, 30])
        self.assertEqual(waypoints_above_ct(82.0), [60, 30])
        self.assertEqual(waypoints_above_ct(25.0), [])

    def test_waypoints_for_scenario_stops_at_prep(self) -> None:
        config = load_config()
        near_88 = scenario_by_id("near_88")
        self.assertEqual(
            waypoints_for_scenario(
                near_88, 95.0, config.cool_waypoints, ct_tolerance=config.ct_tolerance
            ),
            [88],
        )
        self.assertEqual(
            waypoints_for_scenario(
                near_88, 89.0, config.cool_waypoints, ct_tolerance=config.ct_tolerance
            ),
            [88],
        )
        self.assertEqual(
            waypoints_for_scenario(
                near_88, 88.0, config.cool_waypoints, ct_tolerance=config.ct_tolerance
            ),
            [],
        )
        near_85 = scenario_by_id("near_85")
        self.assertEqual(
            waypoints_for_scenario(
                near_85, 95.0, config.cool_waypoints, ct_tolerance=config.ct_tolerance
            ),
            [85],
        )
        self.assertEqual(
            waypoints_for_scenario(
                near_85, 86.0, config.cool_waypoints, ct_tolerance=config.ct_tolerance
            ),
            [85],
        )
        self.assertEqual(
            waypoints_for_scenario(
                near_85, 85.0, config.cool_waypoints, ct_tolerance=config.ct_tolerance
            ),
            [],
        )
        near_60 = scenario_by_id("near_60")
        self.assertEqual(
            waypoints_for_scenario(
                near_60, 95.0, config.cool_waypoints, ct_tolerance=config.ct_tolerance
            ),
            [88, 85, 60],
        )
        self.assertEqual(
            waypoints_for_scenario(
                near_60, 61.0, config.cool_waypoints, ct_tolerance=config.ct_tolerance
            ),
            [60],
        )
        self.assertEqual(
            waypoints_for_scenario(
                near_60, 60.0, config.cool_waypoints, ct_tolerance=config.ct_tolerance
            ),
            [],
        )

    def test_prep_done_after_cool(self) -> None:
        config = load_config()
        near_88 = scenario_by_id("near_88")
        near_60 = scenario_by_id("near_60")
        self.assertEqual(prep_cool_tt(near_88), 88)
        self.assertTrue(prep_done(88.0, near_88, ct_tolerance=config.ct_tolerance))
        self.assertFalse(prep_done(88.9, near_88, ct_tolerance=config.ct_tolerance))
        self.assertFalse(prep_done(89.0, near_88, ct_tolerance=config.ct_tolerance))
        self.assertFalse(prep_done(95.0, near_88, ct_tolerance=config.ct_tolerance))
        self.assertTrue(prep_done(60.0, near_60, ct_tolerance=config.ct_tolerance))
        self.assertFalse(prep_done(61.0, near_60, ct_tolerance=config.ct_tolerance))


class PidApplyTest(unittest.TestCase):
    def test_gains_close(self) -> None:
        expected = PidGains(28.778, 0.27, 88.2)
        self.assertTrue(gains_close(expected, PidGains(28.78, 0.27, 88.0)))
        self.assertFalse(gains_close(expected, PidGains(34.0, 0.27, 80.0)))

    def test_apply_and_verify_sends_single_set_pid(self) -> None:
        class FakeClient:
            host = "h"
            port = 80

            def __init__(self) -> None:
                self.calls: list[tuple[float, float, float]] = []

            def set_pid(self, kp: float, ki: float, kd: float, **_: object) -> None:
                self.calls.append((kp, ki, kd))

        client = FakeClient()
        gains = PidGains(28.778, 0.27, 88.2)
        with patch("pid_tune.pid_apply._wait_for_gains", return_value=gains):
            apply_and_verify_pid(client, gains, lambda _: None, label="t")
        # Exactly one apply — no Ki=0 pre-step (firmware reset() handles integral).
        self.assertEqual(client.calls, [(28.778, 0.27, 88.2)])


class StatusLogTest(unittest.TestCase):
    def test_format_status_includes_pid(self) -> None:
        line = format_status(
            {
                "ct": 88.0,
                "tt": 92.0,
                "kp": 34.0,
                "ki": 0.27,
                "kd": 80.0,
                "p": 1.2,
                "i": 0.3,
                "d": 0.4,
                "out": 55.0,
                "frozen": 0,
            }
        )
        self.assertIn("ct=88.0", line)
        self.assertIn("Kp=34.00", line)
        self.assertNotIn("P=", line)
        self.assertRegex(line, r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")


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

    def test_severe_overshoot_does_not_compound(self) -> None:
        # >1.0C must fire ONLY the severe tier (Kp×0.75), not also the >0.5 tier.
        metrics = RunMetrics(
            target_temp=92.0,
            max_overshoot_c=1.5,
            time_to_band_s=90.0,
            in_band_fraction=0.5,
        )
        gains, reason = suggest_gains(metrics, 100.0, 0.27, 100.0)
        self.assertAlmostEqual(gains.kp, 75.0, places=3)
        self.assertAlmostEqual(gains.kd, 150.0, places=3)
        self.assertIn("severe-overshoot", reason)
        self.assertNotIn("overshoot>0.5", reason)


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

    def test_rejects_when_not_ending_at_target(self) -> None:
        config = load_config()
        metrics = RunMetrics(
            target_temp=92.0,
            band_c=0.5,
            time_to_band_s=120.0,
            max_overshoot_c=0.2,
            final_error_c=0.51,
            in_band_fraction=0.14,
        )
        self.assertFalse(acceptance_ok(metrics, config))

    def test_rejects_overshoot_above_limit(self) -> None:
        config = load_config()
        metrics = RunMetrics(
            target_temp=92.0,
            band_c=0.5,
            time_to_band_s=90.0,
            max_overshoot_c=0.51,
            final_error_c=0.0,
        )
        self.assertFalse(acceptance_ok(metrics, config))

    def test_rejects_time_to_band_only_without_final_in_band(self) -> None:
        config = load_config()
        metrics = RunMetrics(
            target_temp=92.0,
            band_c=0.5,
            time_to_band_s=90.0,
            max_overshoot_c=0.1,
            final_error_c=0.6,
            in_band_fraction=0.5,
        )
        self.assertFalse(acceptance_ok(metrics, config))

    def test_rejects_grazing_run_low_in_band_fraction(self) -> None:
        # Reaches band briefly, ends in band, but barely dwells there → reject.
        config = load_config()
        metrics = RunMetrics(
            target_temp=92.0,
            band_c=0.5,
            time_to_band_s=90.0,
            max_overshoot_c=0.1,
            final_error_c=0.0,
            in_band_fraction=0.05,
        )
        self.assertFalse(acceptance_ok(metrics, config))

    def test_tolerates_few_frozen_samples(self) -> None:
        config = load_config()
        metrics = RunMetrics(
            target_temp=92.0,
            band_c=0.5,
            time_to_band_s=90.0,
            max_overshoot_c=0.1,
            final_error_c=0.0,
            in_band_fraction=0.6,
            frozen_samples=config.acceptance.max_frozen_samples,
        )
        self.assertTrue(acceptance_ok(metrics, config))
        metrics.frozen_samples = config.acceptance.max_frozen_samples + 1
        self.assertFalse(acceptance_ok(metrics, config))


class ConfigTest(unittest.TestCase):
    def test_record_max_s_near_88_is_480_seconds(self) -> None:
        config = load_config()
        self.assertEqual(config.record_max_s_for("near_88"), 480.0)
        self.assertEqual(config.record_max_s_for("near_85"), 480.0)

    def test_cool_timeouts_three_hours(self) -> None:
        config = load_config()
        self.assertEqual(config.cool_timeouts_s["85"], 10800.0)
        self.assertEqual(config.cool_timeouts_s["60"], 10800.0)
        self.assertEqual(config.cool_timeouts_s["30"], 10800.0)

    def test_verification_laps_default(self) -> None:
        config = load_config()
        self.assertEqual(config.verification_laps, 1)

    def test_prep_hold_s_default(self) -> None:
        config = load_config()
        self.assertEqual(config.prep_hold_s, 600.0)

    def test_load_config_picks_up_file_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.yaml"
            path.write_text(
                json.dumps({"record_max_s_default": 100.0}),
                encoding="utf-8",
            )
            first = load_config(path)
            self.assertEqual(first.record_max_s_default, 100.0)
            path.write_text(
                json.dumps({"record_max_s_default": 200.0}),
                encoding="utf-8",
            )
            second = load_config(path)
            self.assertEqual(second.record_max_s_default, 200.0)


class StateTest(unittest.TestCase):
    def test_resume_completed(self) -> None:
        state = new_state("host", PidGains(30, 0.2, 70))
        state.mark_completed("near_88")
        self.assertIn("near_88", state.completed)
        self.assertIsNone(state.current_scenario)

    def test_mark_verified(self) -> None:
        state = new_state("host", PidGains(30, 0.2, 70))
        state.mark_verified("near_88")
        self.assertIn("near_88", state.verified)

    def test_load_state_none_uses_default_path(self) -> None:
        # Must not raise when path is explicitly None (--resume without state_path).
        result = load_state(None)
        self.assertTrue(result is None or isinstance(result, LadderState))


class PlanLadderTest(unittest.TestCase):
    def test_plan_marks_done(self) -> None:
        config = load_config()
        state = LadderState(host="h", started_at="t", completed=["near_88"])
        lines = plan_ladder(88.0, config, state)
        self.assertTrue(any("[done] near_88" in line for line in lines))

    def test_plan_shows_hold_after_cool(self) -> None:
        config = load_config()
        lines = plan_ladder(95.0, config, None)
        near_88 = next(line for line in lines if "near_88" in line)
        self.assertIn("record hold 600s at tt=88", near_88)
        self.assertIn("ramp tt=92", near_88)


class SummarizeHoldTest(unittest.TestCase):
    def test_summarize_hold_stats(self) -> None:
        samples = [
            StatusSample(t=0, ct=88.0, tt=88.0),
            StatusSample(t=150, ct=88.5, tt=88.0),
            StatusSample(t=300, ct=88.2, tt=88.0),
        ]
        summary = summarize_hold(samples, 88.0)
        self.assertAlmostEqual(summary.ct_min, 88.0)
        self.assertAlmostEqual(summary.ct_max, 88.5)
        self.assertAlmostEqual(summary.ct_mean, 88.233, places=2)
        self.assertAlmostEqual(summary.final_error_c, 0.2)
        self.assertEqual(summary.sample_count, 3)
        self.assertAlmostEqual(summary.duration_s, 300.0)

    def test_summarize_hold_empty(self) -> None:
        summary = summarize_hold([], 88.0)
        self.assertEqual(summary.sample_count, 0)


class RecordPrepHoldTest(unittest.TestCase):
    def test_dry_run_logs_planned_hold(self) -> None:
        config = load_config()
        scenario = scenario_by_id("near_88")
        messages: list[str] = []

        record_prep_hold(
            client=object(),  # type: ignore[arg-type]
            scenario=scenario,
            cool_tt=88.0,
            config=config,
            dry_run=True,
            log=messages.append,
        )
        self.assertEqual(messages, ["near_88: dry-run — would record hold 600s at tt=88"])

    def test_zero_hold_is_noop(self) -> None:
        config = load_config()
        config.prep_hold_s = 0.0
        scenario = scenario_by_id("near_88")
        messages: list[str] = []

        samples = record_prep_hold(
            client=object(),  # type: ignore[arg-type]
            scenario=scenario,
            cool_tt=88.0,
            config=config,
            dry_run=False,
            log=messages.append,
        )
        self.assertEqual(samples, [])
        self.assertEqual(messages, [])

    def test_ramp_test_reloads_prep_hold_before_ramp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.yaml"
            path.write_text(json.dumps({"prep_hold_s": 120.0}), encoding="utf-8")
            config = load_config()
            scenario = scenario_by_id("near_88")
            messages: list[str] = []
            gains = PidGains(1.0, 0.1, 100.0)

            with patch("pid_tune.runner.fresh_config", return_value=load_config(path)):
                run_ramp_test(
                    client=object(),  # type: ignore[arg-type]
                    scenario=scenario,
                    gains=gains,
                    config=config,
                    dry_run=True,
                    needs_prep_hold=True,
                    log=messages.append,
                )
        self.assertEqual(
            messages,
            [
                "near_88: dry-run — would record hold 120s at tt=88",
                "near_88: recording ramp 240s — peak + integral tail",
            ],
        )


if __name__ == "__main__":
    unittest.main()
