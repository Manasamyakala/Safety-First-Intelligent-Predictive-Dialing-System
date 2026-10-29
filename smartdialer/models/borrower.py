"""Borrower model and retry/cooldown status."""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
import time
from typing import Optional


class BorrowerState(str, Enum):
    PENDING = "PENDING"
    IN_FLIGHT = "IN_FLIGHT"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    COOLDOWN = "COOLDOWN"


@dataclass
class Borrower:
    id: str
    name: str
    phone_number: str
    campaign_id: str
    status: BorrowerState = BorrowerState.PENDING
    priority: int = 100  # Higher = called first
    retry_count: int = 0
    max_retries: int = 3
    cooldown_seconds: float = 60.0
    last_attempt_at: Optional[float] = None
    version: int = 1

    def is_eligible(self, current_time: Optional[float] = None) -> bool:
        now = current_time if current_time is not None else time.time()
        if self.status == BorrowerState.PENDING:
            return True
        if self.status == BorrowerState.COOLDOWN:
            if self.retry_count >= self.max_retries:
                return False
            elapsed = now - (self.last_attempt_at or 0.0)
            return elapsed >= self.cooldown_seconds
        return False
