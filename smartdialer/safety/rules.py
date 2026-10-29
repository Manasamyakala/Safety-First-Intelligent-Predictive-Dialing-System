"""Safety rules and risk evaluation models."""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
import math
from typing import Any, Dict, Optional


class SafetyAction(str, Enum):
    APPROVE = "APPROVE"
    REDUCE = "REDUCE"
    REJECT = "REJECT"
    FALLBACK_PROGRESSIVE = "FALLBACK_PROGRESSIVE"


@dataclass
class SafetyDecision:
    action: SafetyAction
    proposed_dials: int
    approved_dials: int
    reason: str
    risk_metrics: Dict[str, Any] = field(default_factory=dict)

    @property
    def was_throttled(self) -> bool:
        return self.approved_dials < self.proposed_dials
