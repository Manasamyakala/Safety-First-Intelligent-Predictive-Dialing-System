"""Progressive Pacing Engine: Guarantees 1 outbound call per 1 available agent."""

from __future__ import annotations
from smartdialer.pacing.metrics import PacingInputs, PacingProposal


class ProgressivePacingEngine:
    """
    Deterministic 1:1 Progressive Dialing.
    Never initiates more calls than there are immediately available agents.
    """

    def calculate_dials(self, inputs: PacingInputs) -> PacingProposal:
        # In progressive mode, calls in setup/ringing are already assigned to reserved agents.
        # So we only dial for available agents that don't already have a call in flight.
        net_available = max(0, inputs.available_agents - inputs.ringing_calls)

        reason = (
            f"Progressive mode 1:1 rule: {inputs.available_agents} available agents minus "
            f"{inputs.ringing_calls} in-flight ringing calls = {net_available} proposed dials."
        )

        return PacingProposal(
            mode="PROGRESSIVE",
            proposed_dials=net_available,
            raw_target_calls=float(net_available),
            expected_freed_agents=0.0,
            expected_total_capacity=float(inputs.available_agents),
            reason=reason,
            inputs=inputs,
        )
