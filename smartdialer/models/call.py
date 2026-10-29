"""Call model and call state machine lifecycle."""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
import time
from typing import Dict, Optional, Set


class CallState(str, Enum):
    QUEUED = "QUEUED"
    RESERVED = "RESERVED"
    INITIATED = "INITIATED"
    RINGING = "RINGING"
    ANSWERED = "ANSWERED"
    CONNECTED = "CONNECTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


# State monotonicity rank to quickly detect out-of-order / regressive events
CALL_STATE_RANK = {
    CallState.QUEUED: 10,
    CallState.RESERVED: 20,
    CallState.INITIATED: 30,
    CallState.RINGING: 40,
    CallState.ANSWERED: 50,
    CallState.CONNECTED: 60,
    CallState.COMPLETED: 100,
    CallState.FAILED: 100,
    CallState.CANCELLED: 100,
}

TERMINAL_CALL_STATES = {CallState.COMPLETED, CallState.FAILED, CallState.CANCELLED}

# Legal forward transitions
VALID_CALL_TRANSITIONS: Dict[CallState, Set[CallState]] = {
    CallState.QUEUED: {CallState.RESERVED, CallState.CANCELLED},
    CallState.RESERVED: {CallState.INITIATED, CallState.FAILED, CallState.CANCELLED},
    CallState.INITIATED: {
        CallState.RINGING,
        CallState.ANSWERED,    # Fast pickup before ringing webhook
        CallState.FAILED,      # Invalid number / immediate drop
        CallState.CANCELLED,   # Canceled during initiation
        CallState.COMPLETED,   # Out-of-order quick hangup
    },
    CallState.RINGING: {
        CallState.ANSWERED,
        CallState.FAILED,      # No answer / busy / rejected
        CallState.CANCELLED,
        CallState.COMPLETED,   # Out-of-order webhook delivery
    },
    CallState.ANSWERED: {
        CallState.CONNECTED,   # Bridged to agent
        CallState.CANCELLED,   # Overdial / no agent available -> abandoned!
        CallState.COMPLETED,   # Immediate caller disconnect
        CallState.FAILED,
    },
    CallState.CONNECTED: {
        CallState.COMPLETED,   # Normal conversation end
        CallState.FAILED,      # Mid-call disconnect
    },
    CallState.COMPLETED: set(),
    CallState.FAILED: set(),
    CallState.CANCELLED: set(),
}


class InvalidCallTransitionError(Exception):
    """Raised when an illegal call state transition is attempted."""
    pass


@dataclass
class Call:
    id: str
    borrower_id: str
    campaign_id: str
    status: CallState = CallState.QUEUED
    agent_id: Optional[str] = None
    worker_id: Optional[str] = None
    provider_id: Optional[str] = None
    provider_call_id: Optional[str] = None
    phone_number: str = ""
    created_at: float = field(default_factory=time.time)
    initiated_at: Optional[float] = None
    ringing_at: Optional[float] = None
    answered_at: Optional[float] = None
    connected_at: Optional[float] = None
    completed_at: Optional[float] = None
    lease_expires_at: Optional[float] = None
    abandoned: bool = False
    failure_reason: Optional[str] = None
    version: int = 1

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_CALL_STATES

    def can_transition_to(self, new_state: CallState) -> bool:
        if new_state == self.status:
            return True  # Idempotent
        if self.is_terminal:
            # Cannot leave terminal state under any circumstance
            return False
        allowed = VALID_CALL_TRANSITIONS.get(self.status, set())
        return new_state in allowed

    def transition_to(
        self,
        new_state: CallState,
        agent_id: Optional[str] = None,
        provider_call_id: Optional[str] = None,
        reason: Optional[str] = None,
        abandoned: Optional[bool] = None,
        timestamp: Optional[float] = None,
    ) -> bool:
        """
        Transitions call state.
        Returns:
            True if state changed.
            False if event was ignored (idempotent duplicate or out-of-order regressive event).
        Raises:
            InvalidCallTransitionError if an invalid forward transition was attempted.
        """
        if new_state == self.status:
            # Idempotent duplicate event
            return False

        if self.is_terminal:
            # Terminal lock: do not allow regressive events to undo completion
            return False

        # Check for out-of-order regressive event (e.g. RINGING arriving after ANSWERED)
        current_rank = CALL_STATE_RANK.get(self.status, 0)
        target_rank = CALL_STATE_RANK.get(new_state, 0)
        if target_rank < current_rank:
            # Out of order event: ignore regression
            return False

        if not self.can_transition_to(new_state):
            raise InvalidCallTransitionError(
                f"Call {self.id} cannot transition from {self.status.value} to {new_state.value}"
            )

        now = timestamp if timestamp is not None else time.time()
        self.status = new_state
        self.version += 1

        if agent_id:
            self.agent_id = agent_id
        if provider_call_id:
            self.provider_call_id = provider_call_id
        if reason:
            self.failure_reason = reason
        if abandoned is not None:
            self.abandoned = abandoned

        # Timestamp tracking
        if new_state == CallState.INITIATED:
            self.initiated_at = now
        elif new_state == CallState.RINGING:
            self.ringing_at = now
        elif new_state == CallState.ANSWERED:
            self.answered_at = now
        elif new_state == CallState.CONNECTED:
            self.connected_at = now
        elif new_state in TERMINAL_CALL_STATES:
            self.completed_at = now

        return True
