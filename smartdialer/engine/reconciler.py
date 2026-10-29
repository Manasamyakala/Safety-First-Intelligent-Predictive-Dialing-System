"""Reconciler: Watchdog service that recovers stuck state and handles worker crashes."""

from __future__ import annotations
import logging
import time
from typing import List, Tuple

from smartdialer.allocation.agent_allocator import AgentAllocator
from smartdialer.allocation.call_allocator import CallAllocator
from smartdialer.models.agent import AgentState
from smartdialer.models.borrower import BorrowerState
from smartdialer.models.call import CallState
from smartdialer.storage.repository import Repository

logger = logging.getLogger(__name__)


class Reconciler:
    """
    Background watchdog that audits active state and recovers from:
    1. Worker crashes mid-flight
    2. Abandoned leases on reserved agents and initiated calls
    3. Network partitions
    """

    def __init__(
        self,
        repository: Repository,
        agent_allocator: AgentAllocator,
        call_allocator: CallAllocator,
    ):
        self.repository = repository
        self.agent_allocator = agent_allocator
        self.call_allocator = call_allocator

    def reconcile_all(self, current_time: float = None) -> Tuple[int, int]:
        """
        Executes a reconciliation pass.
        Returns:
            (reconciled_calls_count, reconciled_agents_count)
        """
        now = current_time if current_time is not None else time.time()
        reconciled_calls = self.reconcile_expired_calls(now)
        reconciled_agents = self.reconcile_stale_reserved_agents(now)
        return reconciled_calls, reconciled_agents

    def reconcile_expired_calls(self, now: float) -> int:
        """
        Recovers calls whose worker lease has expired without completion.
        Addresses Failure Case 1:
        Agent reserved > Borrower reserved > Call initiated > Worker crashes.
        """
        stuck_calls = self.repository.get_stuck_calls_with_expired_leases(now)
        count = 0

        for call in stuck_calls:
            logger.warning(
                f"[RECONCILER] Found stuck call {call.id} in state {call.status.value} "
                f"(Worker {call.worker_id} crashed or lease expired)."
            )

            # 1. Release or reconcile agent
            if call.agent_id:
                agent = self.repository.get_agent(call.agent_id)
                if agent and agent.status in (AgentState.RESERVED, AgentState.DIALING):
                    logger.info(f"[RECONCILER] Recovered agent {agent.id} back to AVAILABLE.")
                    self.agent_allocator.release_agent_to_available(agent.id)

            # 2. Release borrower from IN_FLIGHT so they can be retried
            self.call_allocator.handle_call_failure(call.id, reason="WORKER_CRASHED_LEASE_EXPIRED")

            # 3. Transition call to FAILED
            self.repository.transition_call_state(
                call_id=call.id,
                new_state=CallState.FAILED,
                reason="WORKER_LEASE_EXPIRED",
            )
            count += 1

        return count

    def reconcile_stale_reserved_agents(self, now: float) -> int:
        """
        Detects agents that remain RESERVED past threshold with no active call.
        Rolls them back to AVAILABLE.
        """
        stale_agents = self.repository.get_stale_reserved_agents(max_reserved_age_sec=10.0, now=now)
        count = 0
        for agent in stale_agents:
            logger.warning(f"[RECONCILER] Recovering orphaned reserved agent {agent.id} back to AVAILABLE.")
            self.agent_allocator.release_agent_to_available(agent.id)
            count += 1
        return count
