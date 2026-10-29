"""Dialer Worker: Distributed worker loop executing the campaign dialing pipeline."""

from __future__ import annotations
import asyncio
import logging
import time
import uuid
from typing import Dict, List, Optional

from smartdialer.allocation.agent_allocator import AgentAllocator
from smartdialer.allocation.call_allocator import CallAllocator
from smartdialer.engine.event_processor import EventProcessor
from smartdialer.models.agent import Agent, AgentState
from smartdialer.models.call import Call, CallState
from smartdialer.models.campaign import Campaign, DialingMode
from smartdialer.pacing.metrics import MetricsTracker, PacingInputs, PacingProposal
from smartdialer.pacing.predictive import PredictivePacingEngine
from smartdialer.pacing.progressive import ProgressivePacingEngine
from smartdialer.providers.base import TelecomProvider
from smartdialer.safety.controller import SafetyController
from smartdialer.safety.rules import SafetyAction, SafetyDecision
from smartdialer.storage.repository import Repository

logger = logging.getLogger(__name__)


class DialerWorker:
    """
    Independent distributed dialer worker.
    Executes the pipeline:
    Campaign -> Pacing Engine -> Safety Controller -> Allocator -> Telecom Provider
    """

    def __init__(
        self,
        worker_id: str,
        campaign_id: str,
        repository: Repository,
        provider: TelecomProvider,
        safety_controller: SafetyController,
        metrics_tracker: MetricsTracker,
        event_processor: EventProcessor,
        agent_allocator: AgentAllocator,
        call_allocator: CallAllocator,
    ):
        self.worker_id = worker_id
        self.campaign_id = campaign_id
        self.repository = repository
        self.provider = provider
        self.safety_controller = safety_controller
        self.metrics_tracker = metrics_tracker
        self.event_processor = event_processor
        self.agent_allocator = agent_allocator
        self.call_allocator = call_allocator

        self.progressive_engine = ProgressivePacingEngine()
        self.predictive_engine = PredictivePacingEngine()
        self.is_running = False

    async def execute_pacing_tick(self) -> Dict[str, Any]:
        """
        Executes one atomic pacing iteration:
        1. Fetch current system capacity & active calls
        2. Consult Pacing Engine
        3. Enforce Safety Controller decisions
        4. Atomically claim resources and place outbound calls
        """
        campaign = self.repository.get_campaign(self.campaign_id)
        if not campaign or not campaign.is_active:
            return {"status": "campaign_inactive", "placed_calls": 0}

        # Step 1: Query authoritative state from database
        agent_counts = self.repository.count_agents_by_status()
        call_counts = self.repository.count_calls_by_status(self.campaign_id)
        provider_health = self.provider.get_health()

        avail_agents = agent_counts.get(AgentState.AVAILABLE, 0)
        connected_calls = call_counts.get(CallState.CONNECTED, 0)
        ringing_calls = call_counts.get(CallState.INITIATED, 0) + call_counts.get(CallState.RINGING, 0)

        inputs = PacingInputs(
            available_agents=avail_agents,
            connected_calls=connected_calls,
            ringing_calls=ringing_calls,
            estimated_answer_rate=self.metrics_tracker.answer_rate,
            avg_talk_time_sec=self.metrics_tracker.avg_talk_time_sec,
            avg_ring_time_sec=self.metrics_tracker.avg_ring_time_sec,
            provider_error_rate=provider_health.error_rate,
            recent_abandonments=self.metrics_tracker.abandoned_calls,
        )

        # Step 2: Generate Pacing Proposal
        if campaign.mode == DialingMode.PREDICTIVE:
            proposal = self.predictive_engine.calculate_dials(inputs)
        else:
            proposal = self.progressive_engine.calculate_dials(inputs)

        # Step 3: SAFETY CONTROLLER EVALUATION (Hard Boundary)
        current_abandonment_rate = self.metrics_tracker.get_abandonment_rate()
        decision: SafetyDecision = self.safety_controller.evaluate(
            proposal=proposal,
            campaign=campaign,
            provider_health=provider_health,
            current_abandonment_rate=current_abandonment_rate,
        )

        # Log decision for auditability and compliance
        self.repository.log_pacing_decision(
            decision_id=f"pace_{uuid.uuid4().hex[:8]}",
            campaign_id=self.campaign_id,
            mode=proposal.mode,
            available_agents=avail_agents,
            connected_calls=connected_calls,
            ringing_calls=ringing_calls,
            estimated_answer_rate=self.metrics_tracker.answer_rate,
            pacing_proposed_dials=proposal.proposed_dials,
            safety_action=decision.action.value,
            approved_dials=decision.approved_dials,
            reason=f"{proposal.reason} | SAFETY: {decision.reason}",
        )

        # Step 4: Execute dialing according to safety decision
        approved_count = decision.approved_dials
        if approved_count <= 0:
            return {
                "status": "no_dials_approved",
                "proposed": proposal.proposed_dials,
                "approved": 0,
                "safety_action": decision.action.value,
                "placed_calls": 0,
            }

        effective_mode = (
            DialingMode.PROGRESSIVE
            if decision.action == SafetyAction.FALLBACK_PROGRESSIVE
            else campaign.mode
        )

        placed_calls = []

        if effective_mode == DialingMode.PROGRESSIVE:
            # In Progressive mode: Reserve available agents first!
            reserved_agents = self.agent_allocator.reserve_agents_for_progressive(
                worker_id=self.worker_id,
                count=approved_count,
            )
            if not reserved_agents:
                # Concurrent worker won the race or agents disappeared
                return {
                    "status": "agents_claimed_by_concurrent_worker",
                    "placed_calls": 0,
                }

            # Prepare calls for reserved agents
            calls = self.call_allocator.prepare_calls(
                campaign_id=self.campaign_id,
                worker_id=self.worker_id,
                count=len(reserved_agents),
                agents=reserved_agents,
            )
            placed_calls = await self._dispatch_calls(calls)

        else:
            # In Predictive mode: Prepare calls without pre-reserving agents
            calls = self.call_allocator.prepare_calls(
                campaign_id=self.campaign_id,
                worker_id=self.worker_id,
                count=approved_count,
            )
            placed_calls = await self._dispatch_calls(calls)

        # Refresh heartbeat lease for active calls
        self.repository.refresh_worker_lease(self.worker_id, lease_duration=10.0)

        return {
            "status": "success",
            "proposed": proposal.proposed_dials,
            "approved": decision.approved_dials,
            "safety_action": decision.action.value,
            "placed_calls": len(placed_calls),
            "effective_mode": effective_mode.value,
        }

    async def _dispatch_calls(self, calls: List[Call]) -> List[Call]:
        """Dispatches call requests concurrently to telecom provider."""
        dispatched: List[Call] = []

        async def _dispatch_single(call: Call):
            try:
                will_ans = (hash(call.id) % 100) < (self.metrics_tracker.answer_rate * 100)
                talk_dur = self.metrics_tracker.avg_talk_time_sec

                provider_call_id = await self.provider.initiate_call(
                    call_id=call.id,
                    phone_number=call.phone_number,
                    event_callback=self.event_processor.process_event,
                    talk_time_sec=talk_dur,
                    will_answer=will_ans,
                )
                call.provider_call_id = provider_call_id
                return call
            except Exception as e:
                logger.error(f"Failed to initiate call {call.id} with provider: {e}")
                self.repository.transition_call_state(
                    call_id=call.id,
                    new_state=CallState.FAILED,
                    reason=f"PROVIDER_INIT_ERROR: {str(e)}",
                )
                if call.agent_id:
                    self.agent_allocator.release_agent_to_available(call.agent_id)
                self.call_allocator.handle_call_failure(call.id, reason="PROVIDER_ERROR")
                return None

        results = await asyncio.gather(*(_dispatch_single(c) for c in calls))
        dispatched = [c for c in results if c is not None]
        return dispatched

