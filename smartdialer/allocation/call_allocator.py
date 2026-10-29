"""Call Allocator: Manages borrower claim queues, call record generation, and outcome processing."""

from __future__ import annotations
import logging
import time
import uuid
from typing import List, Optional

from smartdialer.models.agent import Agent
from smartdialer.models.borrower import Borrower, BorrowerState
from smartdialer.models.call import Call, CallState
from smartdialer.storage.repository import Repository

logger = logging.getLogger(__name__)


class CallAllocator:
    """
    Allocates borrowers to outgoing calls.
    Prevents duplicate calling across workers via atomic batch claim transactions.
    """

    def __init__(self, repository: Repository):
        self.repository = repository

    def prepare_calls(
        self,
        campaign_id: str,
        worker_id: str,
        count: int,
        agents: Optional[List[Agent]] = None,
        lease_duration_sec: float = 10.0,
    ) -> List[Call]:
        """
        Claims up to count eligible borrowers and instantiates Call entities.
        If agents are provided (Progressive Mode), links each call to a reserved agent.
        """
        if count <= 0:
            return []

        borrowers = self.repository.claim_next_borrowers_batch(
            campaign_id=campaign_id,
            max_count=count,
        )

        calls: List[Call] = []
        now = time.time()
        lease_expires = now + lease_duration_sec

        for idx, borrower in enumerate(borrowers):
            agent_id = agents[idx].id if (agents and idx < len(agents)) else None
            call_id = f"call_{uuid.uuid4().hex[:10]}"

            call = Call(
                id=call_id,
                borrower_id=borrower.id,
                campaign_id=campaign_id,
                status=CallState.RESERVED,
                agent_id=agent_id,
                worker_id=worker_id,
                phone_number=borrower.phone_number,
                created_at=now,
                lease_expires_at=lease_expires,
            )
            self.repository.create_call(call)
            calls.append(call)

            # In progressive mode, link agent with the call
            if agent_id:
                self.repository.update_agent_status(
                    agent_id=agent_id,
                    new_status=self.repository.get_agent(agent_id).status,  # Preserve RESERVED
                    worker_id=worker_id,
                    call_id=call_id,
                )

        return calls

    def handle_call_success(self, call_id: str) -> None:
        """Marks borrower as COMPLETED."""
        call = self.repository.get_call(call_id)
        if not call:
            return
        self.repository.update_borrower_outcome(
            borrower_id=call.borrower_id,
            new_status=BorrowerState.COMPLETED,
        )

    def handle_call_failure(self, call_id: str, reason: str) -> None:
        """
        Handles call failure (busy/no-answer/carrier drop).
        Applies retry policies and cooldowns.
        """
        call = self.repository.get_call(call_id)
        if not call:
            return

        borrower = self.repository.get_borrower(call.borrower_id)
        if not borrower:
            return

        if borrower.retry_count >= borrower.max_retries:
            # Retries exhausted
            self.repository.update_borrower_outcome(
                borrower_id=borrower.id,
                new_status=BorrowerState.FAILED,
            )
            logger.info(f"Borrower {borrower.id} marked FAILED (max retries {borrower.max_retries} reached).")
        else:
            # Place in cooldown for later retry
            self.repository.update_borrower_outcome(
                borrower_id=borrower.id,
                new_status=BorrowerState.COOLDOWN,
            )
            logger.info(
                f"Borrower {borrower.id} placed in COOLDOWN for retry attempt {borrower.retry_count + 1}."
            )
