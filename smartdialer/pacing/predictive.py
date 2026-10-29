"""Predictive Pacing Engine: Dynamically calculates outbound dials based on Erlang/Little's Law principles."""

from __future__ import annotations
import math
from smartdialer.pacing.metrics import PacingInputs, PacingProposal


class PredictivePacingEngine:
    """
    Predictive Pacing Engine:
    Calculates outbound dials needed to keep agents utilized at 100% capacity
    while forecasting call completions during call ring/setup delay.
    """

    def calculate_dials(self, inputs: PacingInputs) -> PacingProposal:
        # Step 1: Calculate expected agents becoming free during the ring setup window
        # Rate of completions = connected_calls / avg_talk_time
        # In ring_time seconds, expected completions = connected_calls * (ring_time / talk_time)
        completion_ratio = min(1.0, inputs.avg_ring_time_sec / max(1.0, inputs.avg_talk_time_sec))
        expected_freed_agents = inputs.connected_calls * completion_ratio

        # Step 2: Total expected agent capacity by the time newly dialed calls start answering
        expected_total_capacity = inputs.available_agents + expected_freed_agents

        # Step 3: Total answered calls needed = expected_total_capacity
        # Since answer_rate fraction of calls answer, total calls required in-flight = capacity / answer_rate
        effective_answer_rate = max(0.05, min(0.95, inputs.estimated_answer_rate))
        raw_target_calls = expected_total_capacity / effective_answer_rate

        # Step 4: Subtract calls already in flight / ringing
        proposed_dials = max(0, math.floor(raw_target_calls) - inputs.ringing_calls)

        # Transparent human-readable explainability
        reason = (
            f"Predictive Engine analysis: {inputs.available_agents} currently available agents + "
            f"{expected_freed_agents:.2f} expected to finish talk ({inputs.connected_calls} connected * "
            f"({inputs.avg_ring_time_sec}s ring / {inputs.avg_talk_time_sec:.1f}s talk)) = "
            f"{expected_total_capacity:.2f} total anticipated agent capacity. "
            f"At estimated answer rate {effective_answer_rate * 100:.1f}%, target in-flight calls is "
            f"{raw_target_calls:.1f}. Subtracting {inputs.ringing_calls} already ringing calls leaves "
            f"{proposed_dials} new dials proposed."
        )

        return PacingProposal(
            mode="PREDICTIVE",
            proposed_dials=proposed_dials,
            raw_target_calls=raw_target_calls,
            expected_freed_agents=expected_freed_agents,
            expected_total_capacity=expected_total_capacity,
            reason=reason,
            inputs=inputs,
        )
