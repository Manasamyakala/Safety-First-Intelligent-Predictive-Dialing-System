"""Provider B: Slow, jittery provider with simulated timeouts, duplicate events, and out-of-order delivery."""

from __future__ import annotations
import asyncio
import random
import uuid
from typing import Awaitable, Callable, Optional

from smartdialer.providers.base import (
    TelecomProvider,
    ProviderEvent,
    ProviderEventType,
    ProviderHealth,
)
from smartdialer.providers.circuit_breaker import CircuitBreaker, CircuitBreakerOpenError


class ProviderB(TelecomProvider):
    """
    Provider B simulates real-world telecom chaos:
    - Slower response times (200ms - 800ms)
    - Occasional timeouts
    - Duplicate event injection (e.g. triple ANSWERED)
    - Out-of-order events (e.g. COMPLETED before ANSWERED, or RINGING after ANSWERED)
    """

    def __init__(
        self,
        provider_id: str = "provider_b",
        timeout_rate: float = 0.10,
        duplicate_event_rate: float = 0.35,
        out_of_order_rate: float = 0.25,
    ):
        super().__init__(provider_id)
        self.timeout_rate = timeout_rate
        self.duplicate_event_rate = duplicate_event_rate
        self.out_of_order_rate = out_of_order_rate
        self.circuit_breaker = CircuitBreaker(name=provider_id, failure_rate_threshold=0.40)
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
        talk_time_sec: float = 2.0,
        will_answer: bool = True,
    ) -> str:
        if not self.circuit_breaker.is_available or self.is_outage_simulated:
            self.circuit_breaker.record_failure()
            self.failed_calls += 1
            raise CircuitBreakerOpenError(f"Provider {self.provider_id} circuit is open or outage active!")

        self.total_calls += 1

        # Simulate network timeout
        if random.random() < self.timeout_rate:
            self.timeouts += 1
            self.circuit_breaker.record_timeout()
            await asyncio.sleep(0.3)
            raise TimeoutError(f"Provider {self.provider_id} connection timed out during initiation")

        # Simulate slow response
        lat_ms = random.uniform(150, 400)
        self.latencies.append(lat_ms)
        await asyncio.sleep(lat_ms / 1000.0)

        provider_call_id = f"prov_b_{uuid.uuid4().hex[:8]}"

        # 1. INITIATED
        await event_callback(
            ProviderEvent(
                event_id=f"evt_{uuid.uuid4().hex[:8]}",
                call_id=call_id,
                provider_call_id=provider_call_id,
                event_type=ProviderEventType.INITIATED,
                sequence_num=1,
            )
        )

        # Stream lifecycle with chaos injections
        asyncio.create_task(
            self._stream_chaotic_lifecycle(
                call_id=call_id,
                provider_call_id=provider_call_id,
                event_callback=event_callback,
                talk_time_sec=talk_time_sec,
                will_answer=will_answer,
            )
        )
        self.circuit_breaker.record_success()
        return provider_call_id

    async def _stream_chaotic_lifecycle(
        self,
        call_id: str,
        provider_call_id: str,
        event_callback: Callable[[ProviderEvent], Awaitable[None]],
        talk_time_sec: float,
        will_answer: bool,
    ) -> None:
        try:
            should_out_of_order = random.random() < self.out_of_order_rate
            should_duplicate = random.random() < self.duplicate_event_rate

            if should_out_of_order and will_answer:
                # CHAOS CASE: Send COMPLETED first, then ANSWERED, then RINGING
                # (As explicitly asked in PDF page 4: "COMPLETED, ANSWERED, RINGING")
                await asyncio.sleep(0.15)
                # COMPLETED arrives early
                await event_callback(
                    ProviderEvent(
                        event_id=f"evt_{uuid.uuid4().hex[:8]}",
                        call_id=call_id,
                        provider_call_id=provider_call_id,
                        event_type=ProviderEventType.COMPLETED,
                        sequence_num=4,
                        duration_sec=talk_time_sec,
                    )
                )

                # Late ANSWERED arrives
                await asyncio.sleep(0.05)
                await event_callback(
                    ProviderEvent(
                        event_id=f"evt_{uuid.uuid4().hex[:8]}",
                        call_id=call_id,
                        provider_call_id=provider_call_id,
                        event_type=ProviderEventType.ANSWERED,
                        sequence_num=3,
                    )
                )

                # Stale RINGING arrives
                await asyncio.sleep(0.05)
                await event_callback(
                    ProviderEvent(
                        event_id=f"evt_{uuid.uuid4().hex[:8]}",
                        call_id=call_id,
                        provider_call_id=provider_call_id,
                        event_type=ProviderEventType.RINGING,
                        sequence_num=2,
                    )
                )
                self.successful_calls += 1
                return

            # Canonical order with potential duplicate events
            await asyncio.sleep(0.1)
            await event_callback(
                ProviderEvent(
                    event_id=f"evt_{uuid.uuid4().hex[:8]}",
                    call_id=call_id,
                    provider_call_id=provider_call_id,
                    event_type=ProviderEventType.RINGING,
                    sequence_num=2,
                )
            )

            if will_answer:
                await asyncio.sleep(0.1)
                answered_evt_id = f"evt_ans_{uuid.uuid4().hex[:8]}"

                # Send ANSWERED
                await event_callback(
                    ProviderEvent(
                        event_id=answered_evt_id,
                        call_id=call_id,
                        provider_call_id=provider_call_id,
                        event_type=ProviderEventType.ANSWERED,
                        sequence_num=3,
                    )
                )

                if should_duplicate:
                    # DUPLICATE EVENTS CASE: Send ANSWERED 2 more times (page 3/4 of PDF)
                    # Once with exact same event_id (idempotency key test),
                    # once with different event_id (state machine idempotency test)
                    await asyncio.sleep(0.02)
                    await event_callback(
                        ProviderEvent(
                            event_id=answered_evt_id,  # Identical event_id
                            call_id=call_id,
                            provider_call_id=provider_call_id,
                            event_type=ProviderEventType.ANSWERED,
                            sequence_num=3,
                        )
                    )
                    await asyncio.sleep(0.02)
                    await event_callback(
                        ProviderEvent(
                            event_id=f"evt_ans_dup_{uuid.uuid4().hex[:8]}",
                            call_id=call_id,
                            provider_call_id=provider_call_id,
                            event_type=ProviderEventType.ANSWERED,
                            sequence_num=3,
                        )
                    )

                await asyncio.sleep(talk_time_sec)

                # COMPLETED
                await event_callback(
                    ProviderEvent(
                        event_id=f"evt_{uuid.uuid4().hex[:8]}",
                        call_id=call_id,
                        provider_call_id=provider_call_id,
                        event_type=ProviderEventType.COMPLETED,
                        sequence_num=4,
                        duration_sec=talk_time_sec,
                    )
                )
                self.successful_calls += 1
            else:
                await asyncio.sleep(0.1)
                await event_callback(
                    ProviderEvent(
                        event_id=f"evt_{uuid.uuid4().hex[:8]}",
                        call_id=call_id,
                        provider_call_id=provider_call_id,
                        event_type=ProviderEventType.FAILED,
                        sequence_num=3,
                        failure_reason="BUSY_OR_REJECTED",
                    )
                )
                self.failed_calls += 1
        except Exception:
            self.circuit_breaker.record_failure()
            self.failed_calls += 1

    async def terminate_call(self, provider_call_id: str) -> bool:
        await asyncio.sleep(0.08)
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
