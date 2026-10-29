"""Tests for Progressive & Predictive Pacing and Telecom Providers."""

import asyncio
import pytest
from smartdialer.pacing.metrics import PacingInputs
from smartdialer.pacing.predictive import PredictivePacingEngine
from smartdialer.pacing.progressive import ProgressivePacingEngine
from smartdialer.providers.circuit_breaker import CircuitBreaker, CircuitState
from smartdialer.providers.provider_a import ProviderA
from smartdialer.providers.provider_b import ProviderB


def test_progressive_pacing_exact_math():
    engine = ProgressivePacingEngine()
    inputs = PacingInputs(
        available_agents=50,
        connected_calls=10,
        ringing_calls=15,
        estimated_answer_rate=0.30,
        avg_talk_time_sec=90.0,
        avg_ring_time_sec=15.0,
    )
    proposal = engine.calculate_dials(inputs)
    # Available (50) - Ringing (15) = 35 dials
    assert proposal.proposed_dials == 35
    assert "Progressive mode 1:1 rule" in proposal.reason


def test_predictive_pacing_explainability():
    engine = PredictivePacingEngine()
    # 10 available, 20 connected, 15s ring, 60s talk -> expected freed = 20 * (15/60) = 5.0 agents
    # Total capacity = 10 + 5 = 15 agents
    # Answer rate = 0.25 -> total target calls = 15 / 0.25 = 60 calls
    # Already ringing = 10 -> proposed dials = 60 - 10 = 50 dials
    inputs = PacingInputs(
        available_agents=10,
        connected_calls=20,
        ringing_calls=10,
        estimated_answer_rate=0.25,
        avg_talk_time_sec=60.0,
        avg_ring_time_sec=15.0,
    )
    proposal = engine.calculate_dials(inputs)
    assert proposal.expected_freed_agents == 5.0
    assert proposal.expected_total_capacity == 15.0
    assert proposal.proposed_dials == 50
    # Check that explanation breaks down every number clearly
    assert "10 currently available agents + 5.00 expected to finish talk" in proposal.reason
    assert "estimated answer rate 25.0%" in proposal.reason


def test_circuit_breaker_tripping():
    cb = CircuitBreaker(name="test_cb", consecutive_timeout_threshold=3)
    assert cb.state == CircuitState.CLOSED
    assert cb.is_available is True

    # Record 2 timeouts -> still closed
    cb.record_timeout()
    cb.record_timeout()
    assert cb.state == CircuitState.CLOSED

    # 3rd timeout -> trips to OPEN!
    cb.record_timeout()
    assert cb.state == CircuitState.OPEN
    assert cb.is_available is False


def test_provider_a_call_flow():
    async def _run():
        provider = ProviderA(base_latency_ms=5.0)
        events = []

        async def callback(evt):
            events.append(evt.event_type.value)

        prov_call_id = await provider.initiate_call(
            call_id="call_test_a",
            phone_number="+15551112222",
            event_callback=callback,
            talk_time_sec=0.1,
            will_answer=True,
        )
        assert prov_call_id.startswith("prov_a_")

        # Wait for lifecycle to stream
        await asyncio.sleep(0.3)
        assert "INITIATED" in events
        assert "RINGING" in events
        assert "ANSWERED" in events
        assert "COMPLETED" in events

    asyncio.run(_run())

