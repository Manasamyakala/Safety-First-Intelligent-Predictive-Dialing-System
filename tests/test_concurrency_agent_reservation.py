"""Concurrency tests verifying atomic agent reservation and zero race conditions."""

import concurrent.futures
import pytest
from smartdialer.models.agent import Agent, AgentState
from smartdialer.storage.repository import Repository


def test_two_workers_race_for_same_agent(tmp_path):
    db_file = str(tmp_path / "test_race.db")
    repo = Repository(db_path=db_file)

    # Insert one available agent
    target_agent = Agent(id="ag_target", name="Agent Target", status=AgentState.AVAILABLE)
    repo.create_agent(target_agent)

    worker_1_id = "worker_alpha"
    worker_2_id = "worker_beta"

    results = []

    def attempt_reservation(worker_id):
        worker_repo = Repository(db_path=db_file)
        success = worker_repo.reserve_single_agent_atomic(
            agent_id="ag_target",
            worker_id=worker_id,
        )
        return worker_id, success

    # Trigger both workers simultaneously using ThreadPoolExecutor
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(attempt_reservation, worker_1_id)
        f2 = executor.submit(attempt_reservation, worker_2_id)
        results.append(f1.result())
        results.append(f2.result())

    success_count = sum(1 for _, ok in results if ok)
    fail_count = sum(1 for _, ok in results if not ok)

    # Exactly ONE worker must succeed, and the other must fail!
    assert success_count == 1, f"Expected exactly 1 winner, got {success_count}!"
    assert fail_count == 1, f"Expected exactly 1 loser, got {fail_count}!"

    # Verify agent state in database
    agent = repo.get_agent("ag_target")
    assert agent.status == AgentState.RESERVED
    assert agent.reserved_by_worker in (worker_1_id, worker_2_id)
    assert agent.version == 2


def test_ten_workers_compete_for_five_agents(tmp_path):
    db_file = str(tmp_path / "test_pool.db")
    repo = Repository(db_path=db_file)

    # Insert 5 available agents
    for i in range(5):
        repo.create_agent(Agent(id=f"ag_{i}", name=f"Agent {i}", status=AgentState.AVAILABLE))

    all_reserved_agents = []

    def worker_claim(worker_id):
        worker_repo = Repository(db_path=db_file)
        claimed = worker_repo.reserve_available_agents_batch(
            worker_id=worker_id,
            max_count=2,
        )
        return [a.id for a in claimed]

    # 10 workers simultaneously trying to claim agents
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(worker_claim, f"w_{i}") for i in range(10)]
        for f in concurrent.futures.as_completed(futures):
            all_reserved_agents.extend(f.result())

    # Total claimed must not exceed 5
    assert len(all_reserved_agents) == 5, f"Expected 5 total claimed agents, got {len(all_reserved_agents)}"

    # Crucial assertion: All claimed agent IDs must be distinct! No two workers got the same agent!
    assert len(set(all_reserved_agents)) == 5, "Duplicate agent reservation detected across concurrent workers!"
