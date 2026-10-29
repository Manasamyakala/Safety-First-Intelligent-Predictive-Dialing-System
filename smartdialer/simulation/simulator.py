"""Simulator: Executes discrete-event simulation runs for progressive, predictive, and failure scenarios."""

from __future__ import annotations
import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from smartdialer.allocation.agent_allocator import AgentAllocator
from smartdialer.allocation.call_allocator import CallAllocator
from smartdialer.engine.dialer_worker import DialerWorker
from smartdialer.engine.event_processor import EventProcessor
from smartdialer.engine.reconciler import Reconciler
from smartdialer.models.agent import Agent, AgentState
from smartdialer.models.borrower import Borrower, BorrowerState
from smartdialer.models.call import CallState
from smartdialer.models.campaign import Campaign, DialingMode
from smartdialer.pacing.metrics import MetricsTracker
from smartdialer.providers.provider_a import ProviderA
from smartdialer.providers.provider_b import ProviderB
from smartdialer.safety.controller import SafetyController
from smartdialer.simulation.scenarios import ScenarioConfig
from smartdialer.storage.repository import Repository

logger = logging.getLogger(__name__)


@dataclass
class TickSnapshot:
    tick: int
    available_agents: int
    connected_agents: int
    ringing_calls: int
    connected_calls: int
    completed_calls: int
    abandoned_calls: int
    proposed_dials: int
    approved_dials: int
    safety_action: str
    estimated_answer_rate: float
    agent_utilization_pct: float
    notes: str = ""


@dataclass
class SimulationReport:
    scenario_name: str
    mode: str
    total_ticks: int
    total_calls_initiated: int
    total_calls_completed: int
    total_abandoned_calls: int
    abandonment_rate_pct: float
    avg_agent_utilization_pct: float
    peak_concurrent_calls: int
    safety_actions_count: Dict[str, int]
    history: List[TickSnapshot] = field(default_factory=list)


class Simulator:
    """Manages an end-to-end simulation experiment with telemetry and fault injection."""

    def __init__(self, config: ScenarioConfig, db_path: str = ":memory:"):
        self.config = config
        self.repository = Repository(db_path=db_path)
        self.campaign_id = f"camp_{uuid.uuid4().hex[:6]}"

        # Initialize components
        self.metrics_tracker = MetricsTracker(
            default_answer_rate=config.base_answer_rate,
            default_talk_time_sec=config.base_talk_time_sec,
            default_ring_time_sec=config.base_ring_time_sec,
        )
        self.agent_allocator = AgentAllocator(self.repository)
        self.call_allocator = CallAllocator(self.repository)
        self.safety_controller = SafetyController(max_abandonment_rate=0.03)

        self.event_processor = EventProcessor(
            repository=self.repository,
            agent_allocator=self.agent_allocator,
            call_allocator=self.call_allocator,
            metrics_tracker=self.metrics_tracker,
            wrap_up_duration_sec=0.2,
        )

        if config.provider_type == "B":
            self.provider = ProviderB()
        else:
            self.provider = ProviderA()

        self.reconciler = Reconciler(
            repository=self.repository,
            agent_allocator=self.agent_allocator,
            call_allocator=self.call_allocator,
        )

        self.worker = DialerWorker(
            worker_id="worker_sim_1",
            campaign_id=self.campaign_id,
            repository=self.repository,
            provider=self.provider,
            safety_controller=self.safety_controller,
            metrics_tracker=self.metrics_tracker,
            event_processor=self.event_processor,
            agent_allocator=self.agent_allocator,
            call_allocator=self.call_allocator,
        )

        self.history: List[TickSnapshot] = []

    def setup_data(self) -> None:
        """Seeds campaign, agents, and borrowers."""
        # 1. Campaign
        campaign = Campaign(
            id=self.campaign_id,
            name=f"Campaign_{self.config.name}",
            mode=self.config.mode,
            target_abandonment_rate=0.03,
            default_talk_time_sec=self.config.base_talk_time_sec,
            default_ring_time_sec=self.config.base_ring_time_sec,
        )
        self.repository.create_campaign(campaign)

        # 2. Agents
        for i in range(self.config.initial_agents):
            agent = Agent(
                id=f"agent_{i+1:03d}",
                name=f"Agent_{i+1:03d}",
                status=AgentState.AVAILABLE,
            )
            self.repository.create_agent(agent)

        # 3. Borrowers
        for i in range(self.config.borrowers_count):
            borrower = Borrower(
                id=f"borr_{i+1:04d}",
                name=f"Borrower_{i+1:04d}",
                phone_number=f"+1555000{i+1:04d}",
                campaign_id=self.campaign_id,
                status=BorrowerState.PENDING,
                priority=100 + (i % 10),
            )
            self.repository.create_borrower(borrower)

    async def run(self) -> SimulationReport:
        self.setup_data()
        safety_counts: Dict[str, int] = {}
        total_initiated = 0
        peak_concurrent = 0

        for tick in range(1, self.config.ticks + 1):
            tick_note = ""

            # Check dynamic rate mutators (Scenario D)
            if self.config.answer_rate_fn:
                new_rate = self.config.answer_rate_fn(tick)
                self.metrics_tracker.answer_rate = new_rate
                tick_note += f"[Rate set to {new_rate * 100:.0f}%] "

            if self.config.talk_time_fn:
                new_talk = self.config.talk_time_fn(tick)
                self.metrics_tracker.avg_talk_time_sec = new_talk

            # Handle fault injections
            if self.config.failure_trigger_tick == tick:
                if self.config.failure_type == "worker_crash":
                    # Simulate worker crash: claim an agent and call, expire lease, kill worker
                    tick_note += "[INJECTION: Worker Crashed during call setup!] "
                    stuck_agent = self.repository.get_all_agents()[0]
                    self.repository.update_agent_status(
                        agent_id=stuck_agent.id,
                        new_status=AgentState.RESERVED,
                        worker_id="crashed_worker",
                    )
                    # Run reconciler immediately or next step
                    await asyncio.sleep(0.05)
                    recovered_calls, recovered_agents = self.reconciler.reconcile_all(
                        current_time=time.time() + 20.0  # Force lease expiry
                    )
                    tick_note += f"[Reconciler recovered {recovered_agents} agents, {recovered_calls} calls] "

                elif self.config.failure_type == "provider_outage":
                    tick_note += "[INJECTION: Telecom Provider Outage Activated!] "
                    self.provider.set_outage(True)

                elif self.config.failure_type == "agent_drop":
                    tick_note += "[INJECTION: 40 Agents Disconnected Instantly!] "
                    all_agents = self.repository.get_all_agents()
                    dropped = 0
                    for a in all_agents:
                        if a.status == AgentState.AVAILABLE and dropped < 40:
                            self.repository.update_agent_status(a.id, AgentState.OFFLINE)
                            dropped += 1
                    tick_note += f"[Dropped {dropped} agents to OFFLINE] "

            # Execute Worker Pacing Tick
            result = await self.worker.execute_pacing_tick()
            safety_action = result.get("safety_action", "N/A")
            safety_counts[safety_action] = safety_counts.get(safety_action, 0) + 1
            total_initiated += result.get("placed_calls", 0)

            # Let async provider events process
            await asyncio.sleep(self.config.tick_delay_sec)

            # Query system snapshot
            agent_counts = self.repository.count_agents_by_status()
            call_counts = self.repository.count_calls_by_status(self.campaign_id)

            avail = agent_counts.get(AgentState.AVAILABLE, 0)
            conn_agents = agent_counts.get(AgentState.CONNECTED, 0)
            offline_agents = agent_counts.get(AgentState.OFFLINE, 0)
            active_agents = max(1, self.config.initial_agents - offline_agents)

            ringing = call_counts.get(CallState.INITIATED, 0) + call_counts.get(CallState.RINGING, 0)
            connected = call_counts.get(CallState.CONNECTED, 0)
            completed = call_counts.get(CallState.COMPLETED, 0)
            abandoned = self.repository.get_abandoned_calls_count(self.campaign_id)

            utilization = (conn_agents / active_agents) * 100.0
            peak_concurrent = max(peak_concurrent, ringing + connected)

            snapshot = TickSnapshot(
                tick=tick,
                available_agents=avail,
                connected_agents=conn_agents,
                ringing_calls=ringing,
                connected_calls=connected,
                completed_calls=completed,
                abandoned_calls=abandoned,
                proposed_dials=result.get("proposed", 0),
                approved_dials=result.get("approved", 0),
                safety_action=safety_action,
                estimated_answer_rate=self.metrics_tracker.answer_rate,
                agent_utilization_pct=utilization,
                notes=tick_note,
            )
            self.history.append(snapshot)

        # Allow final in-flight calls to wrap up
        await asyncio.sleep(0.5)

        # Final counts
        final_call_counts = self.repository.count_calls_by_status(self.campaign_id)
        final_completed = final_call_counts.get(CallState.COMPLETED, 0)
        final_abandoned = self.repository.get_abandoned_calls_count(self.campaign_id)
        total_answers = final_completed + final_abandoned

        abandon_rate = (final_abandoned / total_answers * 100.0) if total_answers > 0 else 0.0
        avg_util = sum(s.agent_utilization_pct for s in self.history) / len(self.history) if self.history else 0.0

        return SimulationReport(
            scenario_name=self.config.name,
            mode=self.config.mode.value,
            total_ticks=self.config.ticks,
            total_calls_initiated=total_initiated,
            total_calls_completed=final_completed,
            total_abandoned_calls=final_abandoned,
            abandonment_rate_pct=abandon_rate,
            avg_agent_utilization_pct=avg_util,
            peak_concurrent_calls=peak_concurrent,
            safety_actions_count=safety_counts,
            history=self.history,
        )
