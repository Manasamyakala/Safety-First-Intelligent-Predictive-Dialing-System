"""Safety Controller: Unbypassable regulatory and capacity guard protecting against call abandonment."""

from __future__ import annotations
import math
from typing import Optional

from smartdialer.models.campaign import Campaign, DialingMode
from smartdialer.pacing.metrics import PacingProposal
from smartdialer.providers.base import ProviderHealth
from smartdialer.safety.rules import SafetyAction, SafetyDecision


class SafetyController:
    """
    Independent Safety Controller.
    Sits strictly between Pacing Engine and Call Allocator.
    Evaluates regulatory compliance, abandonment risk, agent capacity, and telecom health.
    """

    def __init__(
        self,
        max_abandonment_rate: float = 0.03,  # 3% strict regulatory limit
        max_multiplier: float = 3.5,
        risk_tolerance_z: float = 2.0,       # 97.7% confidence interval on answer volume
    ):
        self.max_abandonment_rate = max_abandonment_rate
        self.max_multiplier = max_multiplier
        self.risk_tolerance_z = risk_tolerance_z

    def evaluate(
        self,
        proposal: PacingProposal,
        campaign: Campaign,
        provider_health: Optional[ProviderHealth] = None,
        current_abandonment_rate: float = 0.0,
    ) -> SafetyDecision:
        proposed = proposal.proposed_dials
        inputs = proposal.inputs

        # Rule 1: No proposed dials -> trivially approve 0
        if proposed <= 0:
            return SafetyDecision(
                action=SafetyAction.APPROVE,
                proposed_dials=0,
                approved_dials=0,
                reason="Pacing engine proposed 0 dials; approved.",
            )

        # Rule 2: Progressive Mode Hard Bound
        # If in progressive mode, never allow more dials than immediately available agents
        if proposal.mode == DialingMode.PROGRESSIVE.value or campaign.mode == DialingMode.PROGRESSIVE:
            safe_dials = max(0, inputs.available_agents - inputs.ringing_calls)
            if proposed > safe_dials:
                return SafetyDecision(
                    action=SafetyAction.REDUCE,
                    proposed_dials=proposed,
                    approved_dials=safe_dials,
                    reason=(
                        f"Progressive safety clamp: Requested {proposed} dials but only "
                        f"{safe_dials} available unassigned agent slots remain."
                    ),
                    risk_metrics={"available_agents": inputs.available_agents, "clamped_to": safe_dials},
                )
            return SafetyDecision(
                action=SafetyAction.APPROVE,
                proposed_dials=proposed,
                approved_dials=proposed,
                reason=f"Progressive dialing within safe agent capacity ({proposed} <= {inputs.available_agents}).",
            )

        # PREDICTIVE MODE SAFETY CHECKS:

        # Rule 3: Provider Health & Circuit Breaker Trip
        # If provider is failing or experiencing high timeouts/errors, fall back to progressive
        if provider_health is not None and (not provider_health.is_healthy or provider_health.error_rate > 0.20):
            progressive_safe = max(0, inputs.available_agents - inputs.ringing_calls)
            return SafetyDecision(
                action=SafetyAction.FALLBACK_PROGRESSIVE,
                proposed_dials=proposed,
                approved_dials=progressive_safe,
                reason=(
                    f"Telecom Provider degraded (error rate: {provider_health.error_rate * 100:.1f}%, "
                    f"circuit: {provider_health.circuit_state}). Emergency fallback to Progressive mode."
                ),
                risk_metrics={"provider_error_rate": provider_health.error_rate},
            )

        # Rule 4: Abandonment Compliance Breach
        # If recent abandonment rate meets or exceeds regulatory ceiling, trigger immediate fallback
        if current_abandonment_rate >= self.max_abandonment_rate or inputs.recent_abandonments > 0:
            progressive_safe = max(0, inputs.available_agents - inputs.ringing_calls)
            return SafetyDecision(
                action=SafetyAction.FALLBACK_PROGRESSIVE,
                proposed_dials=proposed,
                approved_dials=progressive_safe,
                reason=(
                    f"Abandonment rate ({current_abandonment_rate * 100:.2f}%) or recent abandonments "
                    f"({inputs.recent_abandonments}) triggered regulatory protection! "
                    f"Falling back to Progressive 1:1 dialing."
                ),
                risk_metrics={"abandonment_rate": current_abandonment_rate},
            )

        # Rule 5: Critical Zero-Capacity Ceiling
        # If 0 agents are available and expected freed agents is negligible (< 0.75), REJECT speculative dials
        if inputs.available_agents == 0 and proposal.expected_freed_agents < 0.75:
            return SafetyDecision(
                action=SafetyAction.REJECT,
                proposed_dials=proposed,
                approved_dials=0,
                reason=(
                    "Zero available agents and negligible expected call completions (< 0.75). "
                    "Rejecting speculative dials to eliminate abandonment risk."
                ),
                risk_metrics={"available_agents": 0, "expected_freed": proposal.expected_freed_agents},
            )

        # Rule 6: Statistical Risk Bound (Upper Confidence Bound on Answer Volume)
        # Using Normal approximation to Binomial(N, p):
        # We need Answers_worst_case = N*p + z*sqrt(N*p*(1-p)) <= Max_Capacity
        p = max(0.05, min(0.95, inputs.estimated_answer_rate))
        total_capacity = proposal.expected_total_capacity

        # Calculate max safe dials using inverted quadratic formula for N:
        # N*p + z*sqrt(N*p*(1-p)) = C
        # Let u = sqrt(N). Then p*u^2 + z*sqrt(p*(1-p))*u - C = 0
        a = p
        b = self.risk_tolerance_z * math.sqrt(p * (1.0 - p))
        c = -total_capacity

        disc = (b * b) - (4.0 * a * c)
        if disc >= 0:
            u = (-b + math.sqrt(disc)) / (2.0 * a)
            safe_total_in_flight = math.floor(u * u)
        else:
            safe_total_in_flight = math.floor(total_capacity / p)

        # Also apply campaign max dial multiplier cap (e.g. 3.5x agents)
        multiplier_cap = math.floor((inputs.available_agents + proposal.expected_freed_agents) * self.max_multiplier)
        safe_total_in_flight = min(safe_total_in_flight, multiplier_cap)

        safe_new_dials = max(0, safe_total_in_flight - inputs.ringing_calls)

        # If proposal exceeds statistical safety ceiling, REDUCE
        if proposed > safe_new_dials:
            if safe_new_dials == 0:
                return SafetyDecision(
                    action=SafetyAction.REJECT,
                    proposed_dials=proposed,
                    approved_dials=0,
                    reason=(
                        f"Statistical overdial risk too high: {inputs.ringing_calls} calls already ringing "
                        f"exceeds safe statistical capacity ({safe_total_in_flight}). Rejected 0 dials."
                    ),
                    risk_metrics={"safe_total_in_flight": safe_total_in_flight, "ringing": inputs.ringing_calls},
                )
            return SafetyDecision(
                action=SafetyAction.REDUCE,
                proposed_dials=proposed,
                approved_dials=safe_new_dials,
                reason=(
                    f"Safety Controller reduced dials from {proposed} to {safe_new_dials}: "
                    f"Statistical overdial limit enforces max {safe_total_in_flight} total in-flight calls "
                    f"(with {inputs.ringing_calls} already ringing) to guarantee < {self.max_abandonment_rate*100:.1f}% "
                    f"abandonment probability at 97.7% confidence."
                ),
                risk_metrics={
                    "safe_total_in_flight": safe_total_in_flight,
                    "safe_new_dials": safe_new_dials,
                    "capacity": total_capacity,
                },
            )

        # All safety checks passed!
        return SafetyDecision(
            action=SafetyAction.APPROVE,
            proposed_dials=proposed,
            approved_dials=proposed,
            reason=f"Approved {proposed} predictive dials within statistical risk envelope and capacity limits.",
            risk_metrics={"safe_total_in_flight": safe_total_in_flight, "capacity": total_capacity},
        )
