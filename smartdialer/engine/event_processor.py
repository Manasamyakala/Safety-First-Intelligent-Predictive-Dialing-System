"""Event Processor: Handles telecom provider events with strict idempotency and out-of-order tolerance."""

from __future__ import annotations
import asyncio
import logging
from typing import Optional

from smartdialer.allocation.agent_allocator import AgentAllocator
from smartdialer.allocation.call_allocator import CallAllocator
from smartdialer.models.agent import AgentState
from smartdialer.models.call import CallState, TERMINAL_CALL_STATES
from smartdialer.pacing.metrics import MetricsTracker
from smartdialer.providers.base import ProviderEvent, ProviderEventType
from smartdialer.storage.repository import Repository

logger = logging.getLogger(__name__)


class EventProcessor:
    """
    Ingests and processes asynchronous telecom provider events.
    Guarantees:
    1. Idempotency: Duplicate events are deduplicated at DB layer.
    2. Out-of-order resilience: Rejects regressive state changes.
    3. Safe bridging: Connects agents or triggers compliance abandonment.
    """

    def __init__(
        self,
        repository: Repository,
        agent_allocator: AgentAllocator,
        call_allocator: CallAllocator,
        metrics_tracker: MetricsTracker,
        wrap_up_duration_sec: float = 0.5,
    ):
        self.repository = repository
        self.agent_allocator = agent_allocator
        self.call_allocator = call_allocator
        self.metrics_tracker = metrics_tracker
        self.wrap_up_duration_sec = wrap_up_duration_sec

    async def process_event(self, event: ProviderEvent) -> bool:
        """
        Main event ingestion entrypoint.
        Returns True if event advanced state, False if duplicate or rejected.
        """
        # Step 1: Idempotency journal entry
        is_new = self.repository.record_event_idempotent(
            event_id=event.event_id,
            call_id=event.call_id,
            event_type=event.event_type.value,
            sequence_num=event.sequence_num,
            idempotency_key=f"{event.call_id}:{event.event_type.value}:{event.sequence_num}",
            payload=event.payload,
        )

        if not is_new:
            logger.info(f"Duplicate event ignored: {event.call_id} -> {event.event_type.value}")
            return False

        call = self.repository.get_call(event.call_id)
        if not call:
            logger.warning(f"Event received for unknown call: {event.call_id}")
            return False

        if call.is_terminal:
            # Call is already terminal (COMPLETED, FAILED, CANCELLED)
            # Regressive event (e.g. late ANSWERED or RINGING) MUST NOT mutate terminal state
            logger.info(
                f"Ignored out-of-order event {event.event_type.value} for already terminal call {call.id} ({call.status.value})"
            )
            return False

        # Step 2: Route by event type
        if event.event_type == ProviderEventType.INITIATED:
            return self._handle_initiated(call, event)

        elif event.event_type == ProviderEventType.RINGING:
            return self._handle_ringing(call, event)

        elif event.event_type == ProviderEventType.ANSWERED:
            return await self._handle_answered(call, event)

        elif event.event_type == ProviderEventType.COMPLETED:
            return await self._handle_completed(call, event)

        elif event.event_type == ProviderEventType.FAILED:
            return self._handle_failed(call, event)

        return False

    def _handle_initiated(self, call, event: ProviderEvent) -> bool:
        changed, updated_call = self.repository.transition_call_state(
            call_id=call.id,
            new_state=CallState.INITIATED,
            provider_call_id=event.provider_call_id,
        )
        if changed and updated_call and updated_call.agent_id:
            # If progressive, transition agent to DIALING
            self.repository.update_agent_status(
                agent_id=updated_call.agent_id,
                new_status=AgentState.DIALING,
                call_id=call.id,
            )
        return changed

    def _handle_ringing(self, call, event: ProviderEvent) -> bool:
        changed, _ = self.repository.transition_call_state(
            call_id=call.id,
            new_state=CallState.RINGING,
            provider_call_id=event.provider_call_id,
        )
        return changed

    async def _handle_answered(self, call, event: ProviderEvent) -> bool:
        # Move call state to ANSWERED
        changed, updated_call = self.repository.transition_call_state(
            call_id=call.id,
            new_state=CallState.ANSWERED,
            provider_call_id=event.provider_call_id,
        )
        if not changed or not updated_call:
            return False

        assigned_agent_id = updated_call.agent_id

        # In predictive mode, agent was not pre-assigned at dialing time
        if not assigned_agent_id:
            agent = self.agent_allocator.allocate_agent_for_answered_call(
                call_id=call.id,
                worker_id=updated_call.worker_id or "system",
            )
            if agent:
                assigned_agent_id = agent.id
            else:
                # COMPLIANCE CRISIS: Borrower answered, but NO AGENTS AVAILABLE!
                # Mark call as CANCELLED with abandoned=True
                logger.critical(
                    f"COMPLIANCE ABANDONMENT! Call {call.id} answered but no available agents in pool!"
                )
                self.repository.transition_call_state(
                    call_id=call.id,
                    new_state=CallState.CANCELLED,
                    reason="CALL_ABANDONED_NO_AGENT",
                    abandoned=True,
                )
                self.metrics_tracker.record_abandonment()
                self.metrics_tracker.record_call_outcome(was_answered=True)
                self.call_allocator.handle_call_failure(call.id, reason="ABANDONED")
                return True

        # Check if pre-assigned agent is still available (e.g. didn't go offline during ringing)
        curr_agent = self.repository.get_agent(assigned_agent_id)
        if not curr_agent or curr_agent.status == AgentState.OFFLINE:
            # Agent disappeared during setup!
            replacement = self.agent_allocator.handle_agent_disappearance(
                agent_id=assigned_agent_id,
                call_id=call.id,
                worker_id=updated_call.worker_id or "system",
            )
            if replacement:
                assigned_agent_id = replacement.id
            else:
                # No replacement available
                self.repository.transition_call_state(
                    call_id=call.id,
                    new_state=CallState.CANCELLED,
                    reason="AGENT_DISAPPEARED_NO_REPLACEMENT",
                    abandoned=True,
                )
                self.metrics_tracker.record_abandonment()
                self.metrics_tracker.record_call_outcome(was_answered=True)
                self.call_allocator.handle_call_failure(call.id, reason="AGENT_DISAPPEARED")
                return True

        # Successfully bridge agent and borrower
        self.repository.transition_call_state(
            call_id=call.id,
            new_state=CallState.CONNECTED,
            agent_id=assigned_agent_id,
        )
        self.repository.update_agent_status(
            agent_id=assigned_agent_id,
            new_status=AgentState.CONNECTED,
            call_id=call.id,
        )
        self.metrics_tracker.record_call_outcome(was_answered=True)
        return True

    async def _handle_completed(self, call, event: ProviderEvent) -> bool:
        changed, updated_call = self.repository.transition_call_state(
            call_id=call.id,
            new_state=CallState.COMPLETED,
            provider_call_id=event.provider_call_id,
        )
        if not changed:
            return False

        agent_id = updated_call.agent_id if updated_call else call.agent_id

        # Wrap up agent if connected
        if agent_id:
            self.repository.update_agent_status(
                agent_id=agent_id,
                new_status=AgentState.WRAP_UP,
                call_id=call.id,
            )
            # Schedule async return to available after post-call work
            asyncio.create_task(self._finish_wrap_up(agent_id))

        # Update borrower to COMPLETED
        self.call_allocator.handle_call_success(call.id)

        # Update talk time statistics
        if event.duration_sec:
            self.metrics_tracker.record_call_outcome(was_answered=True, talk_duration_sec=event.duration_sec)

        return True

    def _handle_failed(self, call, event: ProviderEvent) -> bool:
        changed, updated_call = self.repository.transition_call_state(
            call_id=call.id,
            new_state=CallState.FAILED,
            provider_call_id=event.provider_call_id,
            reason=event.failure_reason or "CALL_FAILED",
        )
        if not changed:
            return False

        agent_id = updated_call.agent_id if updated_call else call.agent_id

        # If agent was reserved/dialing, release agent immediately back to AVAILABLE pool
        if agent_id:
            self.agent_allocator.release_agent_to_available(agent_id)

        # Trigger borrower retry / cooldown
        self.call_allocator.handle_call_failure(call.id, reason=event.failure_reason or "FAILED")
        self.metrics_tracker.record_call_outcome(was_answered=False)

        return True

    async def _finish_wrap_up(self, agent_id: str) -> None:
        """Transitions agent from WRAP_UP back to AVAILABLE after brief disposition window."""
        await asyncio.sleep(self.wrap_up_duration_sec)
        self.agent_allocator.release_agent_to_available(agent_id)
