"""Tests for idempotent event processing and out-of-order telecom webhooks."""

import asyncio
import pytest
from smartdialer.allocation.agent_allocator import AgentAllocator
from smartdialer.allocation.call_allocator import CallAllocator
from smartdialer.engine.event_processor import EventProcessor
from smartdialer.models.agent import Agent, AgentState
from smartdialer.models.call import Call, CallState
from smartdialer.pacing.metrics import MetricsTracker
from smartdialer.providers.base import ProviderEvent, ProviderEventType
from smartdialer.storage.repository import Repository


def test_duplicate_answered_events(tmp_path):
    """
    Scenario from PDF Page 3:
    Provider sends:
    - ANSWERED
    - ANSWERED
    - ANSWERED
    - COMPLETED
    System must handle idempotently without invalid transitions or multiple count increments.
    """
    async def _run():
        repo = Repository(str(tmp_path / "test_dup.db"))
        agent_alloc = AgentAllocator(repo)
        call_alloc = CallAllocator(repo)
        metrics = MetricsTracker()
        processor = EventProcessor(repo, agent_alloc, call_alloc, metrics)

        # Setup agent and call
        agent = Agent(id="ag_1", name="Agent 1", status=AgentState.AVAILABLE)
        repo.create_agent(agent)

        call = Call(id="call_dup_1", borrower_id="b_1", campaign_id="camp_1", status=CallState.INITIATED)
        repo.create_call(call)

        # 1. First ANSWERED event
        evt1 = ProviderEvent(
            event_id="evt_ans_1",
            call_id=call.id,
            provider_call_id="prov_1",
            event_type=ProviderEventType.ANSWERED,
            sequence_num=1,
        )
        res1 = await processor.process_event(evt1)
        assert res1 is True
        updated_call = repo.get_call(call.id)
        assert updated_call.status == CallState.CONNECTED

        # 2. Duplicate ANSWERED (same event_id)
        res2 = await processor.process_event(evt1)
        assert res2 is False  # Deduplicated by idempotency key

        # 3. Duplicate ANSWERED (different event_id, e.g. re-sent by telecom provider)
        evt3 = ProviderEvent(
            event_id="evt_ans_different_id",
            call_id=call.id,
            provider_call_id="prov_1",
            event_type=ProviderEventType.ANSWERED,
            sequence_num=1,
        )
        res3 = await processor.process_event(evt3)
        assert res3 is False  # Deduplicated or state machine no-op

        # 4. COMPLETED event
        evt4 = ProviderEvent(
            event_id="evt_comp_1",
            call_id=call.id,
            provider_call_id="prov_1",
            event_type=ProviderEventType.COMPLETED,
            sequence_num=2,
            duration_sec=30.0,
        )
        res4 = await processor.process_event(evt4)
        assert res4 is True
        final_call = repo.get_call(call.id)
        assert final_call.status == CallState.COMPLETED

    asyncio.run(_run())


def test_out_of_order_completed_answered_ringing(tmp_path):
    """
    Scenario from PDF Page 4:
    Provider sends:
    - COMPLETED
    - ANSWERED
    - RINGING
    System must transition to COMPLETED, and safely ignore the regressive late events.
    """
    async def _run():
        repo = Repository(str(tmp_path / "test_ooo.db"))
        agent_alloc = AgentAllocator(repo)
        call_alloc = CallAllocator(repo)
        metrics = MetricsTracker()
        processor = EventProcessor(repo, agent_alloc, call_alloc, metrics)

        call = Call(id="call_ooo_1", borrower_id="b_1", campaign_id="camp_1", status=CallState.INITIATED)
        repo.create_call(call)

        # 1. COMPLETED arrives first!
        evt_comp = ProviderEvent(
            event_id="evt_c_1",
            call_id=call.id,
            provider_call_id="prov_1",
            event_type=ProviderEventType.COMPLETED,
            sequence_num=3,
            duration_sec=10.0,
        )
        res_comp = await processor.process_event(evt_comp)
        assert res_comp is True
        assert repo.get_call(call.id).status == CallState.COMPLETED

        # 2. ANSWERED arrives later
        evt_ans = ProviderEvent(
            event_id="evt_a_1",
            call_id=call.id,
            provider_call_id="prov_1",
            event_type=ProviderEventType.ANSWERED,
            sequence_num=2,
        )
        res_ans = await processor.process_event(evt_ans)
        assert res_ans is False  # Rejected because call is already terminal
        assert repo.get_call(call.id).status == CallState.COMPLETED

        # 3. RINGING arrives last
        evt_ring = ProviderEvent(
            event_id="evt_r_1",
            call_id=call.id,
            provider_call_id="prov_1",
            event_type=ProviderEventType.RINGING,
            sequence_num=1,
        )
        res_ring = await processor.process_event(evt_ring)
        assert res_ring is False  # Rejected
        assert repo.get_call(call.id).status == CallState.COMPLETED

    asyncio.run(_run())
