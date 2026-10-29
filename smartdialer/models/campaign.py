"""Campaign configurations and dialing mode definitions."""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
import time


class DialingMode(str, Enum):
    PROGRESSIVE = "PROGRESSIVE"
    PREDICTIVE = "PREDICTIVE"


@dataclass
class Campaign:
    id: str
    name: str
    mode: DialingMode = DialingMode.PROGRESSIVE
    is_active: bool = True
    target_abandonment_rate: float = 0.03  # 3% strict compliance cap
    max_dial_multiplier: float = 3.5       # Maximum allowable dial ratio
    default_talk_time_sec: float = 90.0    # Fallback initial talk time
    default_ring_time_sec: float = 15.0    # Setup and ringing duration
    default_wrap_up_sec: float = 10.0      # Post-call wrap-up
    min_agent_floor: int = 1               # Minimum agents before predictive enables
    created_at: float = field(default_factory=time.time)
