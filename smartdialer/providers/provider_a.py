"""Provider A: Fast, reliable, low-latency telecom provider mock."""

from __future__ import annotations
import asyncio
import random
import time
import uuid
from typing import Awaitable, Callable, Optional

from smartdialer.providers.base import (
    TelecomProvider,
    ProviderEvent,
    ProviderEventType,
    ProviderHealth,
)
from smartdialer.providers.circuit_breaker import CircuitBreaker, CircuitBreakerOpenError


class ProviderA(TelecomProvider):
    """
    Provider A represents the gold standard:
    - Fast response times (30ms - 100ms)
    - 99% reliability
    - Strictly ordered, canonical event progression
    """

    def __init__(self, provider_id: str = "provider_a", base_latency_ms: float = 40.0):
        super().__init__(provider_id)
        self.base_latency_ms = base_latency_ms
        self.circuit_breaker = CircuitBreaker(name=provider_id)
        self.total_calls = 0
        self.successful_calls = 0
        self.failed_calls = 0
        self.timeouts = 0
        self.latencies: list[float] = []

    async def initiate_call(
        self,
        call_id: str,
        phone_number: str,
        event_callback: Callable[[ProviderEvent], Awaitable[None]],
        talk_time_sec: float = 3.0,
        will_answer: bool = True,
    ) -> str:
        if not self.circuit_breaker.is_available or self.is_outage_simulated:
            self.circuit_breaker.record_failure()
            self.failed_calls += 1
            raise CircuitBreakerOpenError(f"Provider {self.provider_id} is down or circuit open!")

        self.total_calls += 1
        provider_call_id = f"prov_a_{uuid.uuid4().hex[:8]}"
        seq = 1

        # Simulate fast network latency
        lat = (self.base_latency_ms + random.uniform(5, 25)) / 1000.0
        self.latencies.append(lat * 1000.0)
        await asyncio.sleep(lat)

        # 1. INITIATED
        await event_callback(
            ProviderEvent(
                event_id=f"evt_{uuid.uuid4().hex[:8]}",
                call_id=call_id,
                provider_call_id=provider_call_id,
                event_type=ProviderEventType.INITIATED,
                sequence_num=seq,
            )
        )
        seq += 1

        # Background task to stream remaining lifecycle
        asyncio.create_task(
            self._stream_lifecycle(
                call_id=call_id,
                provider_call_id=provider_call_id,
                seq=seq,
                event_callback=event_callback,
                talk_time_sec=talk_time_sec,
                will_answer=will_answer,
            )
        )
        self.circuit_breaker.record_success()
        return provider_call_id

    async def _stream_lifecycle(
        self,
        call_id: str,
        provider_call_id: str,
        seq: int,
        event_callback: Callable[[ProviderEvent], Awaitable[None]],
        talk_time_sec: float,
        will_answer: bool,
    ) -> None:
        try:
            # 2. RINGING
            await asyncio.sleep(0.08)
            await event_callback(
                ProviderEvent(
                    event_id=f"evt_{uuid.uuid4().hex[:8]}",
                    call_id=call_id,
                    provider_call_id=provider_call_id,
                    event_type=ProviderEventType.RINGING,
                    sequence_num=seq,
                )
            )
            seq += 1

            if will_answer:
                # 3. ANSWERED
                await asyncio.sleep(0.08)
                await event_callback(
                    ProviderEvent(
                        event_id=f"evt_{uuid.uuid4().hex[:8]}",
                        call_id=call_id,
                        provider_call_id=provider_call_id,
                        event_type=ProviderEventType.ANSWERED,
                        sequence_num=seq,
                    )
                )
                seq += 1

                # Conversation active
                await asyncio.sleep(talk_time_sec)

                # 4. COMPLETED
                await event_callback(
                    ProviderEvent(
                        event_id=f"evt_{uuid.uuid4().hex[:8]}",
                        call_id=call_id,
                        provider_call_id=provider_call_id,
                        event_type=ProviderEventType.COMPLETED,
                        sequence_num=seq,
                        duration_sec=talk_time_sec,
                    )
                )
                self.successful_calls += 1
            else:
                # No answer / busy
                await asyncio.sleep(0.1)
                await event_callback(
                    ProviderEvent(
                        event_id=f"evt_{uuid.uuid4().hex[:8]}",
                        call_id=call_id,
                        provider_call_id=provider_call_id,
                        event_type=ProviderEventType.FAILED,
                        sequence_num=seq,
                        failure_reason="NO_ANSWER",
                    )
                )
                self.failed_calls += 1
        except Exception as e:
            self.circuit_breaker.record_failure()
            self.failed_calls += 1

    async def terminate_call(self, provider_call_id: str) -> bool:
        await asyncio.sleep(0.02)
        return True

    def get_health(self) -> ProviderHealth:
        avg_lat = sum(self.latencies[-50:]) / max(1, len(self.latencies[-50:]))
        err_rate = self.circuit_breaker.get_error_rate()
        return ProviderHealth(
            provider_id=self.provider_id,
            is_healthy=self.circuit_breaker.is_available and not self.is_outage_simulated,
            total_calls=self.total_calls,
            successful_calls=self.successful_calls,
            failed_calls=self.failed_calls,
            timeouts=self.timeouts,
            avg_latency_ms=avg_lat,
            error_rate=err_rate,
            circuit_state=self.circuit_breaker.state.value,
        )
