"""Unit tests for Safety Controller decisions and regulatory limits."""

import pytest
from smartdialer.models.campaign import Campaign, DialingMode
from smartdialer.pacing.metrics import PacingInputs, PacingProposal
from smartdialer.providers.base import ProviderHealth
from smartdialer.safety.controller import SafetyController
from smartdialer.safety.rules import SafetyAction


def test_safety_controller_approves_safe_request():
    controller = SafetyController(max_abandonment_rate=0.03)
    campaign = Campaign(id="c1", name="Test", mode=DialingMode.PREDICTIVE)

    inputs = PacingInputs(
        available_agents=10,
        connected_calls=20,
        ringing_calls=5,
        estimated_answer_rate=0.25,
        avg_talk_time_sec=90.0,
        avg_ring_time_sec=15.0,
    )
    proposal = PacingProposal(
        mode="PREDICTIVE",
        proposed_dials=15,
        raw_target_calls=53.3,
        expected_freed_agents=3.3,
        expected_total_capacity=13.3,
        reason="Test proposal",
        inputs=inputs,
    )

    decision = controller.evaluate(proposal, campaign)
    assert decision.action in (SafetyAction.APPROVE, SafetyAction.REDUCE)
    assert decision.approved_dials > 0


def test_safety_controller_reduces_overaggressive_dials():
    controller = SafetyController(max_abandonment_rate=0.03)
    campaign = Campaign(id="c1", name="Test", mode=DialingMode.PREDICTIVE)

    # Only 2 available agents, 0 connected, answer rate 50%
    inputs = PacingInputs(
        available_agents=2,
        connected_calls=0,
        ringing_calls=0,
        estimated_answer_rate=0.50,
        avg_talk_time_sec=90.0,
        avg_ring_time_sec=15.0,
    )
    # Pacing engine wants to speculate and dial 20 calls!
    proposal = PacingProposal(
        mode="PREDICTIVE",
        proposed_dials=20,
        raw_target_calls=4.0,
        expected_freed_agents=0.0,
        expected_total_capacity=2.0,
        reason="Aggressive proposal",
        inputs=inputs,
    )

    decision = controller.evaluate(proposal, campaign)
    assert decision.action == SafetyAction.REDUCE
    # Must be reduced to safe limit (around 2 to 7 dials max)
    assert decision.approved_dials < 20


def test_safety_controller_rejects_zero_capacity():
    controller = SafetyController(max_abandonment_rate=0.03)
    campaign = Campaign(id="c1", name="Test", mode=DialingMode.PREDICTIVE)

    # 0 agents available, 0 connected calls
    inputs = PacingInputs(
        available_agents=0,
        connected_calls=0,
        ringing_calls=2,
        estimated_answer_rate=0.30,
        avg_talk_time_sec=90.0,
        avg_ring_time_sec=15.0,
    )
    proposal = PacingProposal(
        mode="PREDICTIVE",
        proposed_dials=10,
        raw_target_calls=0.0,
        expected_freed_agents=0.0,
        expected_total_capacity=0.0,
        reason="Zero capacity proposal",
        inputs=inputs,
    )

    decision = controller.evaluate(proposal, campaign)
    assert decision.action == SafetyAction.REJECT
    assert decision.approved_dials == 0


def test_safety_controller_fallback_on_provider_degradation():
    controller = SafetyController()
    campaign = Campaign(id="c1", name="Test", mode=DialingMode.PREDICTIVE)

    inputs = PacingInputs(
        available_agents=5,
        connected_calls=5,
        ringing_calls=0,
        estimated_answer_rate=0.30,
        avg_talk_time_sec=90.0,
        avg_ring_time_sec=15.0,
    )
    proposal = PacingProposal(
        mode="PREDICTIVE",
        proposed_dials=15,
        raw_target_calls=20.0,
        expected_freed_agents=1.0,
        expected_total_capacity=6.0,
        reason="Predictive",
        inputs=inputs,
    )

    # Provider is failing with 35% error rate
    health = ProviderHealth(
        provider_id="prov_b",
        is_healthy=False,
        total_calls=100,
        successful_calls=65,
        failed_calls=35,
        timeouts=10,
        avg_latency_ms=800.0,
        error_rate=0.35,
        circuit_state="OPEN",
    )

    decision = controller.evaluate(proposal, campaign, provider_health=health)
    assert decision.action == SafetyAction.FALLBACK_PROGRESSIVE
    # Clamped to progressive safe limit (5 available agents)
    assert decision.approved_dials <= 5


def test_safety_controller_fallback_on_abandonment_spike():
    controller = SafetyController(max_abandonment_rate=0.03)
    campaign = Campaign(id="c1", name="Test", mode=DialingMode.PREDICTIVE)

    inputs = PacingInputs(
        available_agents=10,
        connected_calls=10,
        ringing_calls=0,
        estimated_answer_rate=0.30,
        avg_talk_time_sec=90.0,
        avg_ring_time_sec=15.0,
        recent_abandonments=2,  # Spike in abandonments!
    )
    proposal = PacingProposal(
        mode="PREDICTIVE",
        proposed_dials=20,
        raw_target_calls=20.0,
        expected_freed_agents=1.0,
        expected_total_capacity=11.0,
        reason="Predictive",
        inputs=inputs,
    )

    # Abandonment rate already at 4% (> 3% threshold)
    decision = controller.evaluate(proposal, campaign, current_abandonment_rate=0.04)
    assert decision.action == SafetyAction.FALLBACK_PROGRESSIVE
    assert decision.approved_dials <= 10
