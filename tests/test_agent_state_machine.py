"""Unit tests for Agent State Machine."""

import pytest
from smartdialer.models.agent import Agent, AgentState, InvalidAgentTransitionError


def test_agent_initial_state():
    agent = Agent(id="ag_01", name="Alice")
    assert agent.status == AgentState.OFFLINE
    assert agent.version == 1


def test_agent_legal_lifecycle():
    agent = Agent(id="ag_01", name="Alice", status=AgentState.AVAILABLE)

    # AVAILABLE -> RESERVED
    agent.transition_to(AgentState.RESERVED, worker_id="w1")
    assert agent.status == AgentState.RESERVED
    assert agent.reserved_by_worker == "w1"
    assert agent.version == 2

    # RESERVED -> DIALING
    agent.transition_to(AgentState.DIALING, call_id="c1")
    assert agent.status == AgentState.DIALING
    assert agent.current_call_id == "c1"

    # DIALING -> CONNECTED
    agent.transition_to(AgentState.CONNECTED, call_id="c1")
    assert agent.status == AgentState.CONNECTED

    # CONNECTED -> WRAP_UP
    agent.transition_to(AgentState.WRAP_UP)
    assert agent.status == AgentState.WRAP_UP

    # WRAP_UP -> AVAILABLE
    agent.transition_to(AgentState.AVAILABLE)
    assert agent.status == AgentState.AVAILABLE
    assert agent.current_call_id is None
    assert agent.reserved_by_worker is None


def test_agent_illegal_transition():
    agent = Agent(id="ag_02", name="Bob", status=AgentState.OFFLINE)
    # Cannot jump from OFFLINE directly to CONNECTED
    with pytest.raises(InvalidAgentTransitionError):
        agent.transition_to(AgentState.CONNECTED)


def test_agent_disconnect_from_any_state():
    # If agent's computer shuts down during DIALING
    agent = Agent(id="ag_03", name="Charlie", status=AgentState.DIALING)
    agent.transition_to(AgentState.OFFLINE)
    assert agent.status == AgentState.OFFLINE


def test_agent_idempotent_transition():
    agent = Agent(id="ag_04", name="Dave", status=AgentState.AVAILABLE)
    agent.transition_to(AgentState.AVAILABLE)
    assert agent.status == AgentState.AVAILABLE
    assert agent.version == 1  # Version unchanged on idempotent no-op
