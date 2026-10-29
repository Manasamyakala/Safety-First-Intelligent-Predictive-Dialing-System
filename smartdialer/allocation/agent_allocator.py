"""Agent Allocator: Manages atomic agent reservations and re-assignment on agent disconnect."""

from __future__ import annotations
import logging
from typing import List, Optional

from smartdialer.models.agent import Agent, AgentState
from smartdialer.storage.repository import Repository

logger = logging.getLogger(__name__)


class AgentAllocationError(Exception):
    pass


class AgentAllocator:
    """
    Handles atomic, race-free agent allocation and re-routing.
    Uses database row-level atomic CAS and transactions to ensure two workers
    cannot reserve the same agent concurrently.
    """

    def __init__(self, repository: Repository):
        self.repository = repository

    def reserve_agents_for_progressive(
        self,
        worker_id: str,
        count: int,
    ) -> List[Agent]:
        """
        Atomically claims available agents for progressive calls.
        Fair scheduling: prioritizes agents idle longest.
        """
        if count <= 0:
            return []
        return self.repository.reserve_available_agents_batch(
            worker_id=worker_id,
            max_count=count,
        )

    def allocate_agent_for_answered_call(
        self,
        call_id: str,
        worker_id: str,
    ) -> Optional[Agent]:
        """
        For predictive calls when a borrower answers:
        Atomically finds and reserves an available agent to bridge immediately.
        """
        reserved = self.repository.reserve_available_agents_batch(
            worker_id=worker_id,
            max_count=1,
        )
        if not reserved:
            return None
        agent = reserved[0]
        # Link agent with call
        self.repository.update_agent_status(
            agent_id=agent.id,
            new_status=AgentState.CONNECTED,
            worker_id=worker_id,
            call_id=call_id,
        )
        agent.status = AgentState.CONNECTED
        agent.current_call_id = call_id
        return agent

    def release_agent_to_available(self, agent_id: str) -> bool:
        """Returns agent to AVAILABLE pool after call wrap-up or canceled dial."""
        agent = self.repository.get_agent(agent_id)
        if not agent:
            return False
        if agent.status == AgentState.OFFLINE:
            # Agent logged off; preserve offline status
            return False
        return self.repository.update_agent_status(
            agent_id=agent_id,
            new_status=AgentState.AVAILABLE,
            worker_id=None,
            call_id=None,
        )

    def handle_agent_disappearance(
        self,
        agent_id: str,
        call_id: str,
        worker_id: str,
    ) -> Optional[Agent]:
        """
        Handles case where assigned agent goes OFFLINE or disappears during call setup.
        Attempts to re-route in-flight call to another available agent.
        """
        logger.warning(f"Agent {agent_id} disappeared during setup for call {call_id}! Attempting re-route.")
        # Mark disappearing agent as OFFLINE
        self.repository.update_agent_status(
            agent_id=agent_id,
            new_status=AgentState.OFFLINE,
            worker_id=None,
            call_id=None,
        )

        # Attempt to claim replacement agent
        replacement = self.allocate_agent_for_answered_call(call_id=call_id, worker_id=worker_id)
        return replacement
