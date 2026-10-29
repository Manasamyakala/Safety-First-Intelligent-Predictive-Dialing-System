"""Real-time metrics estimator for answer rates and talk duration using EWMA."""

from __future__ import annotations
from dataclasses import dataclass
import time
from typing import Optional


@dataclass
class PacingInputs:
    available_agents: int
    connected_calls: int
    ringing_calls: int
    estimated_answer_rate: float
    avg_talk_time_sec: float
    avg_ring_time_sec: float
    provider_error_rate: float = 0.0
    recent_abandonments: int = 0


@dataclass
class PacingProposal:
    mode: str
    proposed_dials: int
    raw_target_calls: float
    expected_freed_agents: float
    expected_total_capacity: float
    reason: str
    inputs: PacingInputs


class MetricsTracker:
    """
    Maintains rolling statistics with Exponentially Weighted Moving Average (EWMA)
    and Bayesian updating for answer rate and talk time.
    """

    def __init__(
        self,
        default_answer_rate: float = 0.25,
        default_talk_time_sec: float = 90.0,
        default_ring_time_sec: float = 15.0,
        alpha_decay: float = 0.15,  # Weight on newest observation
    ):
        self.answer_rate = default_answer_rate
        self.avg_talk_time_sec = default_talk_time_sec
        self.avg_ring_time_sec = default_ring_time_sec
        self.alpha_decay = alpha_decay

        self.total_completed_calls = 0
        self.total_answered_calls = 0
        self.total_placed_calls = 0
        self.abandoned_calls = 0

    def record_call_outcome(self, was_answered: bool, talk_duration_sec: Optional[float] = None) -> None:
        self.total_placed_calls += 1
        if was_answered:
            self.total_answered_calls += 1
            # Update EWMA for answer rate
            self.answer_rate = (self.alpha_decay * 1.0) + ((1.0 - self.alpha_decay) * self.answer_rate)
            if talk_duration_sec is not None and talk_duration_sec > 0:
                self.avg_talk_time_sec = (self.alpha_decay * talk_duration_sec) + (
                    (1.0 - self.alpha_decay) * self.avg_talk_time_sec
                )
        else:
            # Update EWMA for unanswered call
            self.answer_rate = (self.alpha_decay * 0.0) + ((1.0 - self.alpha_decay) * self.answer_rate)

        # Keep answer rate strictly bounded between 1% and 99%
        self.answer_rate = max(0.01, min(0.99, self.answer_rate))

    def record_abandonment(self) -> None:
        self.abandoned_calls += 1

    def get_abandonment_rate(self) -> float:
        if self.total_answered_calls == 0:
            return 0.0
        return self.abandoned_calls / self.total_answered_calls
