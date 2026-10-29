"""Telecom Provider interface and event definitions."""

from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
import time
from typing import Any, Awaitable, Callable, Dict, Optional


class ProviderEventType(str, Enum):
    INITIATED = "INITIATED"
    RINGING = "RINGING"
    ANSWERED = "ANSWERED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass
class ProviderEvent:
    event_id: str
    call_id: str
    provider_call_id: str
    event_type: ProviderEventType
    sequence_num: int
    timestamp: float = field(default_factory=time.time)
    duration_sec: Optional[float] = None
    failure_reason: Optional[str] = None
    payload: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ProviderHealth:
    provider_id: str
    is_healthy: bool
    total_calls: int
    successful_calls: int
    failed_calls: int
    timeouts: int
    avg_latency_ms: float
    error_rate: float
    circuit_state: str = "CLOSED"


class TelecomProvider(ABC):
    """Abstract base provider interface decoupled from dialer engine."""

    def __init__(self, provider_id: str):
        self.provider_id = provider_id
        self.is_outage_simulated: bool = False

    @abstractmethod
    async def initiate_call(
        self,
        call_id: str,
        phone_number: str,
        event_callback: Callable[[ProviderEvent], Awaitable[None]],
        talk_time_sec: float = 5.0,
        will_answer: bool = True,
    ) -> str:
        """
        Initiates outbound call.
        Returns provider_call_id.
        Streams lifecycle events asynchronously to event_callback.
        """
        pass

    @abstractmethod
    async def terminate_call(self, provider_call_id: str) -> bool:
        """Terminates active call."""
        pass

    @abstractmethod
    def get_health(self) -> ProviderHealth:
        """Returns health metrics for safety controller and circuit breaker."""
        pass

    def set_outage(self, enabled: bool) -> None:
        """Manually toggle an outage for resilience testing."""
        self.is_outage_simulated = enabled
