"""Agent model and state machine lifecycle."""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
import time
from typing import Dict, Optional, Set


class AgentState(str, Enum):
    OFFLINE = "OFFLINE"
    AVAILABLE = "AVAILABLE"
    RESERVED = "RESERVED"
    DIALING = "DIALING"
    CONNECTED = "CONNECTED"
    WRAP_UP = "WRAP_UP"
    PAUSED = "PAUSED"


# Allowed state transitions for agents
VALID_AGENT_TRANSITIONS: Dict[AgentState, Set[AgentState]] = {
    AgentState.OFFLINE: {AgentState.AVAILABLE},
    AgentState.AVAILABLE: {AgentState.RESERVED, AgentState.PAUSED, AgentState.OFFLINE},
    AgentState.RESERVED: {
        AgentState.DIALING,
        AgentState.AVAILABLE,  # Call failed before dialing or canceled
        AgentState.OFFLINE,    # Agent disconnected during reservation
    },
    AgentState.DIALING: {
        AgentState.CONNECTED,  # Borrower answered and bridged
        AgentState.AVAILABLE,  # Call failed (busy/no-answer) -> agent back to pool
        AgentState.WRAP_UP,    # Direct wrap-up if configured
        AgentState.OFFLINE,    # Agent dropped off while dialing
    },
    AgentState.CONNECTED: {
        AgentState.WRAP_UP,    # Call ended normally
        AgentState.OFFLINE,    # Emergency disconnection
    },
    AgentState.WRAP_UP: {
        AgentState.AVAILABLE,  # Post-call disposition complete
        AgentState.PAUSED,     # Agent takes break after call
        AgentState.OFFLINE,
    },
    AgentState.PAUSED: {
        AgentState.AVAILABLE,
        AgentState.OFFLINE,
    },
}


class InvalidAgentTransitionError(Exception):
    """Raised when an illegal agent state transition is attempted."""
    pass


@dataclass
class Agent:
    id: str
    name: str
    status: AgentState = AgentState.OFFLINE
    reserved_by_worker: Optional[str] = None
    reserved_at: Optional[float] = None
    current_call_id: Optional[str] = None
    version: int = 1
    last_state_change: float = field(default_factory=time.time)
    skills: list[str] = field(default_factory=list)

    def can_transition_to(self, new_state: AgentState) -> bool:
        if new_state == self.status:
            return True  # Idempotent no-op
        # Any state can transition to OFFLINE if agent disconnects
        if new_state == AgentState.OFFLINE:
            return True
        allowed = VALID_AGENT_TRANSITIONS.get(self.status, set())
        return new_state in allowed

    def transition_to(
        self,
        new_state: AgentState,
        worker_id: Optional[str] = None,
        call_id: Optional[str] = None,
        timestamp: Optional[float] = None,
    ) -> None:
        if new_state == self.status:
            return  # Idempotent
        if not self.can_transition_to(new_state):
            raise InvalidAgentTransitionError(
                f"Agent {self.id} cannot transition from {self.status.value} to {new_state.value}"
            )
        now = timestamp if timestamp is not None else time.time()
        self.status = new_state
        self.last_state_change = now
        self.version += 1

        if new_state == AgentState.RESERVED:
            self.reserved_by_worker = worker_id
            self.reserved_at = now
        elif new_state == AgentState.AVAILABLE:
            self.reserved_by_worker = None
            self.reserved_at = None
            self.current_call_id = None
        elif new_state == AgentState.DIALING:
            if call_id:
                self.current_call_id = call_id
        elif new_state == AgentState.CONNECTED:
            if call_id:
                self.current_call_id = call_id
        elif new_state in (AgentState.WRAP_UP, AgentState.PAUSED, AgentState.OFFLINE):
            self.reserved_by_worker = None
            self.reserved_at = None
            if new_state != AgentState.WRAP_UP:
                self.current_call_id = None
