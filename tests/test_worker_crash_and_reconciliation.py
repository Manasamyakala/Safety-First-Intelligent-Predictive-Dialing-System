"""Tests for Worker Crash recovery and Reconciler watchdog."""

import time
import pytest
from smartdialer.allocation.agent_allocator import AgentAllocator
from smartdialer.allocation.call_allocator import CallAllocator
from smartdialer.engine.reconciler import Reconciler
from smartdialer.models.agent import Agent, AgentState
from smartdialer.models.borrower import Borrower, BorrowerState
from smartdialer.models.call import Call, CallState
from smartdialer.storage.repository import Repository


def test_worker_crash_recovery(tmp_path):
    """
    Scenario from PDF Page 5:
    Agent reserved > Borrower reserved > Call initiated > Worker crashes!
    What happens when system comes back?
    """
    repo = Repository(str(tmp_path / "test_crash.db"))
    agent_alloc = AgentAllocator(repo)
    call_alloc = CallAllocator(repo)
    reconciler = Reconciler(repo, agent_alloc, call_alloc)

    # 1. Agent reserved
    agent = Agent(id="ag_crashed", name="Agent Smith", status=AgentState.RESERVED, reserved_by_worker="worker_doomed")
    repo.create_agent(agent)

    # 2. Borrower reserved / claimed
    borrower = Borrower(id="borr_crashed", name="John Doe", phone_number="+15551234567", campaign_id="camp_1", status=BorrowerState.IN_FLIGHT)
    repo.create_borrower(borrower)

    # 3. Call initiated
    now = time.time()
    call = Call(
        id="call_crashed",
        borrower_id=borrower.id,
        campaign_id="camp_1",
        status=CallState.INITIATED,
        agent_id=agent.id,
        worker_id="worker_doomed",
        lease_expires_at=now - 5.0,  # Lease already expired because worker died!
    )
    repo.create_call(call)

    # 4. Watchdog Reconciler runs
    reconciled_calls, reconciled_agents = reconciler.reconcile_all(current_time=now)

    assert reconciled_calls == 1

    # Invariants after recovery:
    # A. Agent must be restored to AVAILABLE so other workers can use them
    recovered_agent = repo.get_agent("ag_crashed")
    assert recovered_agent.status == AgentState.AVAILABLE
    assert recovered_agent.reserved_by_worker is None

    # B. Borrower must be freed from IN_FLIGHT and placed in COOLDOWN for retry
    recovered_borrower = repo.get_borrower("borr_crashed")
    assert recovered_borrower.status == BorrowerState.COOLDOWN

    # C. Call must be transitioned to FAILED (worker lease expired)
    recovered_call = repo.get_call("call_crashed")
    assert recovered_call.status == CallState.FAILED
    assert recovered_call.failure_reason == "WORKER_LEASE_EXPIRED"
