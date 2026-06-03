"""LLM Supervisory Layer for multi-scenario PID calibration."""

from __future__ import annotations

import json
import os
from typing import Any, List
from openai import OpenAI
from pydantic import BaseModel, Field

from pid_tune.metrics import RunMetrics
from pid_tune.suggest_gains import PidGains, clamp_gains


class AdaptiveAdjustments(BaseModel):
    reasoning: str = Field(
        description="Detailed thermodynamic analysis of the trend across all recorded iterations."
    )
    kp_multiplier: float = Field(
        description="Dynamic scale factor for Kp based on error magnitude. Default is 1.0."
    )
    ki_multiplier: float = Field(
        description="Dynamic scale factor for Ki based on steady-state offset. Default is 1.0."
    )
    kd_multiplier: float = Field(
        description="Dynamic scale factor for Kd based on ringing or derivative floor. Default is 1.0."
    )


class GlobalCompromise(BaseModel):
    reasoning: str = Field(
        description="Explanation of the multi-scenario bottleneck and trade-off synthesis."
    )
    recommended_kp: float = Field(description="The balanced global Kp value.")
    recommended_ki: float = Field(description="The balanced global Ki value.")
    recommended_kd: float = Field(description="The balanced global Kd value.")


class LlmSupervisor:
    """Asynchronous optimization layer wrapping the deterministic tuning loop."""

    def __init__(self, api_key: str | None = None, model: str = "gpt-4o"):
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self.model = model
        self._client = OpenAI(api_key=self.api_key) if self.api_key else None

    @property
    def is_available(self) -> bool:
        return self._client is not None

    def optimize_step(
        self,
        scenario_id: str,
        current_gains: PidGains,
        run_history: list[dict[str, Any]],
    ) -> tuple[PidGains, str]:
        """Analyzes historical iterations of a scenario to provide nuanced gain scales."""
        if not self.is_available:
            return current_gains, "LLM unavailable (missing API key); skipped."

        prompt = f"""
        You are an expert control systems engineer tuning an espresso boiler PID loop.
        Scenario: {scenario_id}
        Current Gains: Kp={current_gains.kp:.2f}, Ki={current_gains.ki:.3f}, Kd={current_gains.kd:.1f}
        
        Historical Iterations (Oldest to Newest):
        {json.dumps(run_history, indent=2)}
        
        Analyze the trends in 'max_overshoot_c', 'time_to_band_s', and 'ct_variance_in_band'.
        Determine if the deterministic changes are over-correcting, under-correcting, or hitting physical limits.
        Provide adaptive multipliers to scale the base step adjustments dynamically.
        """

        try:
            completion = self._client.beta.chat.completions.parse(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "You compute highly precise thermodynamic scale multipliers for PID tuning.",
                    },
                    {"role": "user", "content": prompt},
                ],
                response_format=AdaptiveAdjustments,
                timeout=30.0,
            )
            suggestion = completion.choices[0].message.parsed
            if not suggestion:
                return current_gains, "LLM parsed output returned empty configuration."

            new_kp = current_gains.kp * suggestion.kp_multiplier
            new_ki = current_gains.ki * suggestion.ki_multiplier
            new_kd = current_gains.kd * suggestion.kd_multiplier

            clamped = clamp_gains(new_kp, new_ki, new_kd)
            reason = f"LLM Smart Adjust [{suggestion.reasoning[:120]}...]"
            return clamped, reason

        except Exception as e:
            return current_gains, f"LLM Strategy Layer Fault: {str(e)}"

    def resolve_global_conflict(
        self,
        all_scenario_metrics: dict[str, dict[str, Any]],
        conflicting_scenario: str,
        current_gains: PidGains,
    ) -> tuple[PidGains, str]:
        """Arbiter pattern: Resolves tuning deadlocks across conflicting scenarios."""
        if not self.is_available:
            return current_gains, "LLM unavailable; cannot resolve global deadlock."

        prompt = f"""
        The PID tuning pipeline has hit a Pareto conflict.
        Tuning for scenario '{conflicting_scenario}' is actively degrading stability or performance in other environments.
        
        Current System-Wide State Matrix:
        {json.dumps(all_scenario_metrics, indent=2)}
        
        Current Active Gains: Kp={current_gains.kp:.2f}, Ki={current_gains.ki:.3f}, Kd={current_gains.kd:.1f}
        
        Synthesize a compromise set of gains. Prioritize safety (preventing overshoots $> 1.0^\\circ$C), 
        then minimize global variance. Do not output values that violate limits.
        """

        try:
            completion = self._client.beta.chat.completions.parse(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "You act as a global system arbitrator finding optimal Pareto trade-offs for embedded PID controller constants.",
                    },
                    {"role": "user", "content": prompt},
                ],
                response_format=GlobalCompromise,
                timeout=45.0,
            )
            decision = completion.choices[0].message.parsed
            if not decision:
                return current_gains, "LLM compromise schema parsed blank."

            clamped = clamp_gains(
                decision.recommended_kp,
                decision.recommended_ki,
                decision.recommended_kd,
            )
            return clamped, f"LLM Global Compromise: {decision.reasoning}"

        except Exception as e:
            return current_gains, f"LLM Arbitration Failure: {str(e)}"
