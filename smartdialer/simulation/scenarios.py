"""Simulation scenario definitions for Progressive, Predictive, and Failure scenarios."""

from __future__ import annotations
from dataclasses import dataclass
from typing import Callable, Optional

from smartdialer.models.campaign import DialingMode


@dataclass
class ScenarioConfig:
    name: str
    description: str
    mode: DialingMode
    initial_agents: int
    borrowers_count: int
    base_answer_rate: float
    base_talk_time_sec: float
    base_ring_time_sec: float
    provider_type: str = "A"  # "A" or "B"
    ticks: int = 15
    tick_delay_sec: float = 0.15
    # Dynamic mutator functions for Scenario D / shocks
    answer_rate_fn: Optional[Callable[[int], float]] = None
    talk_time_fn: Optional[Callable[[int], float]] = None
    failure_trigger_tick: Optional[int] = None
    failure_type: Optional[str] = None  # "worker_crash", "provider_outage", "agent_drop", "duplicates"


def dynamic_scenario_d_answer_rate(tick: int) -> float:
    """Simulates changing answer rate: starts at 20%, surges to 65%, then collapses to 15%."""
    if tick < 4:
        return 0.20
    elif tick < 9:
        return 0.65
    elif tick < 12:
        return 0.15  # Sudden drop!
    else:
        return 0.40


def dynamic_scenario_d_talk_time(tick: int) -> float:
    """Simulates changing average talk duration."""
    if tick < 5:
        return 1.2
    elif tick < 10:
        return 2.5
    else:
        return 0.8


# Page 6 Scenarios
SCENARIOS = {
    "A": ScenarioConfig(
        name="Scenario A (Low Answer Rate)",
        description="20% Answer Rate, 120s Talk Time (scaled). Demonstrates aggressive pacing without overdialing.",
        mode=DialingMode.PREDICTIVE,
        initial_agents=50,
        borrowers_count=300,
        base_answer_rate=0.20,
        base_talk_time_sec=1.5,
        base_ring_time_sec=0.2,
        provider_type="A",
        ticks=15,
    ),
    "B": ScenarioConfig(
        name="Scenario B (Moderate Answer Rate)",
        description="50% Answer Rate, 90s Talk Time (scaled). Balanced predictive pacing and steady throughput.",
        mode=DialingMode.PREDICTIVE,
        initial_agents=50,
        borrowers_count=300,
        base_answer_rate=0.50,
        base_talk_time_sec=1.2,
        base_ring_time_sec=0.2,
        provider_type="A",
        ticks=15,
    ),
    "C": ScenarioConfig(
        name="Scenario C (High Answer Rate)",
        description="70% Answer Rate, 180s Talk Time (scaled). High answer rate requires safety controller throttling.",
        mode=DialingMode.PREDICTIVE,
        initial_agents=50,
        borrowers_count=300,
        base_answer_rate=0.70,
        base_talk_time_sec=2.0,
        base_ring_time_sec=0.2,
        provider_type="A",
        ticks=15,
    ),
    "D": ScenarioConfig(
        name="Scenario D (Volatile Dynamic Environment)",
        description="Changing Answer Rate (20% -> 65% -> 15% shock) & Changing Talk Time. Shows real-time adaptation.",
        mode=DialingMode.PREDICTIVE,
        initial_agents=50,
        borrowers_count=400,
        base_answer_rate=0.20,
        base_talk_time_sec=1.5,
        base_ring_time_sec=0.2,
        provider_type="B",
        ticks=18,
        answer_rate_fn=dynamic_scenario_d_answer_rate,
        talk_time_fn=dynamic_scenario_d_talk_time,
    ),
    "PROGRESSIVE": ScenarioConfig(
        name="Progressive Baseline",
        description="Deterministic 1:1 Dialing. Zero risk of abandonment, baseline agent utilization comparison.",
        mode=DialingMode.PROGRESSIVE,
        initial_agents=50,
        borrowers_count=300,
        base_answer_rate=0.30,
        base_talk_time_sec=1.2,
        base_ring_time_sec=0.2,
        provider_type="A",
        ticks=15,
    ),
    # Failure Scenarios from Page 5 & 6
    "FAIL_WORKER_CRASH": ScenarioConfig(
        name="Failure Case 1: Worker Crash",
        description="Agent reserved > Borrower claimed > Call initiated > Worker crashes! Watchdog reconciles state.",
        mode=DialingMode.PROGRESSIVE,
        initial_agents=20,
        borrowers_count=50,
        base_answer_rate=0.50,
        base_talk_time_sec=1.0,
        base_ring_time_sec=0.2,
        provider_type="A",
        ticks=8,
        failure_trigger_tick=3,
        failure_type="worker_crash",
    ),
    "FAIL_PROVIDER_OUTAGE": ScenarioConfig(
        name="Failure Case 2: Provider Outage",
        description="Telecom provider starts timing out -> Circuit breaker trips -> Safety controller forces Progressive fallback.",
        mode=DialingMode.PREDICTIVE,
        initial_agents=50,
        borrowers_count=200,
        base_answer_rate=0.40,
        base_talk_time_sec=1.2,
        base_ring_time_sec=0.2,
        provider_type="B",
        ticks=12,
        failure_trigger_tick=4,
        failure_type="provider_outage",
    ),
    "FAIL_AGENT_DROP": ScenarioConfig(
        name="Failure Case 3: Sudden Agent Drop",
        description="100 agents available -> 40 abruptly disconnect. Safety controller immediately clamps in-flight dials.",
        mode=DialingMode.PREDICTIVE,
        initial_agents=100,
        borrowers_count=400,
        base_answer_rate=0.40,
        base_talk_time_sec=1.5,
        base_ring_time_sec=0.2,
        provider_type="A",
        ticks=12,
        failure_trigger_tick=4,
        failure_type="agent_drop",
    ),
    "FAIL_CHAOS_EVENTS": ScenarioConfig(
        name="Failure Cases 4 & 5: Duplicate and Out-of-Order Events",
        description="Provider B sends duplicate ANSWERED events and COMPLETED before ANSWERED. System preserves consistency.",
        mode=DialingMode.PREDICTIVE,
        initial_agents=40,
        borrowers_count=200,
        base_answer_rate=0.50,
        base_talk_time_sec=1.0,
        base_ring_time_sec=0.2,
        provider_type="B",
        ticks=10,
    ),
}
