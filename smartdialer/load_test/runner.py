"""Multi-worker concurrency and load testing benchmark."""

from __future__ import annotations
import asyncio
import time
import uuid
from typing import Any, Dict, List

from smartdialer.allocation.agent_allocator import AgentAllocator
from smartdialer.allocation.call_allocator import CallAllocator
from smartdialer.engine.dialer_worker import DialerWorker
from smartdialer.engine.event_processor import EventProcessor
from smartdialer.models.agent import Agent, AgentState
from smartdialer.models.borrower import Borrower, BorrowerState
from smartdialer.models.call import CallState
from smartdialer.models.campaign import Campaign, DialingMode
from smartdialer.pacing.metrics import MetricsTracker
from smartdialer.providers.provider_a import ProviderA
from smartdialer.safety.controller import SafetyController
from smartdialer.storage.repository import Repository



async def run_load_test(
    num_workers: int = 8,
    num_agents: int = 100,
    num_borrowers: int = 1000,
    iterations_per_worker: int = 15,
    db_path: str = ":memory:",
) -> Dict[str, Any]:
    """
    Executes high-concurrency load test with multiple simultaneous dialer workers
    competing for the same campaign, agents, and borrower pool.
    """
    print("\n" + "=" * 80)
    print(f" STARTING LOAD TEST: {num_workers} Concurrent Workers, {num_agents} Agents, {num_borrowers} Borrowers")
    print("=" * 80)

    repo = Repository(db_path=db_path)
    campaign_id = f"load_{uuid.uuid4().hex[:6]}"

    # Setup campaign
    campaign = Campaign(
        id=campaign_id,
        name="High Concurrency Stress Test",
        mode=DialingMode.PROGRESSIVE,
    )
    repo.create_campaign(campaign)

    # Seed agents
    for i in range(num_agents):
        repo.create_agent(Agent(id=f"ag_{i:04d}", name=f"Agent_{i}", status=AgentState.AVAILABLE))

    # Seed borrowers
    for i in range(num_borrowers):
        repo.create_borrower(
            Borrower(
                id=f"borr_{i:05d}",
                name=f"Borrower_{i}",
                phone_number=f"+1555{i:07d}",
                campaign_id=campaign_id,
            )
        )

    provider = ProviderA(base_latency_ms=2.0)
    metrics_tracker = MetricsTracker(default_talk_time_sec=0.05, default_ring_time_sec=0.02)
    agent_allocator = AgentAllocator(repo)

    call_allocator = CallAllocator(repo)
    safety_controller = SafetyController()
    event_processor = EventProcessor(
        repository=repo,
        agent_allocator=agent_allocator,
        call_allocator=call_allocator,
        metrics_tracker=metrics_tracker,
        wrap_up_duration_sec=0.05,
    )

    workers = [
        DialerWorker(
            worker_id=f"worker_{w:02d}",
            campaign_id=campaign_id,
            repository=repo,
            provider=provider,
            safety_controller=safety_controller,
            metrics_tracker=metrics_tracker,
            event_processor=event_processor,
            agent_allocator=agent_allocator,
            call_allocator=call_allocator,
        )
        for w in range(num_workers)
    ]

    total_ticks = 0
    total_calls_placed = 0

    async def worker_loop(w: DialerWorker):
        nonlocal total_ticks, total_calls_placed
        for _ in range(iterations_per_worker):
            res = await w.execute_pacing_tick()
            total_ticks += 1
            total_calls_placed += res.get("placed_calls", 0)
            await asyncio.sleep(0.02)  # High frequency pounding

    start_time = time.time()
    await asyncio.gather(*(worker_loop(w) for w in workers))
    elapsed = time.time() - start_time

    # Drain remaining async events
    await asyncio.sleep(0.5)

    # Concurrency Invariant Verifications:
    # 1. Did any agent get assigned to multiple active calls?
    conn = repo._get_connection()
    multi_assigned_agents = conn.execute(
        """
        SELECT agent_id, COUNT(*) as cnt
        FROM calls
        WHERE agent_id IS NOT NULL AND status IN ('RESERVED', 'INITIATED', 'RINGING', 'CONNECTED')
        GROUP BY agent_id
        HAVING cnt > 1
        """
    ).fetchall()

    # 2. Did any borrower get dialed simultaneously by multiple calls?
    multi_called_borrowers = conn.execute(
        """
        SELECT borrower_id, COUNT(*) as cnt
        FROM calls
        GROUP BY borrower_id
        HAVING cnt > 1
        """
    ).fetchall()

    final_call_counts = repo.count_calls_by_status(campaign_id)
    tps = total_calls_placed / elapsed if elapsed > 0 else 0.0

    print("\n LOAD TEST RESULTS:")
    print(f"  * Total Time Elapsed       : {elapsed:.2f} seconds")
    print(f"  * Total Pacing Ticks Run   : {total_ticks}")
    print(f"  * Total Calls Placed       : {total_calls_placed}")
    print(f"  * Throughput               : {tps:.1f} calls/sec across {num_workers} workers")
    print(f"  * Calls Completed          : {final_call_counts.get(CallState.COMPLETED, 0)}")
    print(f"  * Multi-Agent Collisions   : {len(multi_assigned_agents)} (MUST BE 0)")
    print(f"  * Multi-Borrower Collisions: {len(multi_called_borrowers)} (MUST BE 0)")

    assert len(multi_assigned_agents) == 0, f"RACE CONDITION DETECTED! {len(multi_assigned_agents)} agents double-booked!"
    assert len(multi_called_borrowers) == 0, f"BORROWER COLLISION DETECTED! {len(multi_called_borrowers)} borrowers double-called!"

    print("  [PASS] CONCURRENCY VERIFICATION: 100% CLEAN. ZERO RACE CONDITIONS DETECTED.")
    print("=" * 80 + "\n")


    return {
        "elapsed_sec": elapsed,
        "total_calls_placed": total_calls_placed,
        "throughput_calls_per_sec": tps,
        "agent_collisions": len(multi_assigned_agents),
        "borrower_collisions": len(multi_called_borrowers),
    }
