"""Unit tests for Call State Machine lifecycle, idempotency, and ranking guards."""

import pytest
from smartdialer.models.call import Call, CallState, InvalidCallTransitionError


def test_call_legal_progression():
    call = Call(id="c_100", borrower_id="b_1", campaign_id="camp_1")
    assert call.status == CallState.QUEUED

    assert call.transition_to(CallState.RESERVED) is True
    assert call.transition_to(CallState.INITIATED, provider_call_id="p_1") is True
    assert call.transition_to(CallState.RINGING) is True
    assert call.transition_to(CallState.ANSWERED) is True
    assert call.transition_to(CallState.CONNECTED, agent_id="ag_1") is True
    assert call.transition_to(CallState.COMPLETED) is True
    assert call.status == CallState.COMPLETED
    assert call.is_terminal is True


def test_call_duplicate_event_is_idempotent():
    call = Call(id="c_101", borrower_id="b_1", campaign_id="camp_1", status=CallState.RINGING)
    # Receiving duplicate RINGING event
    changed = call.transition_to(CallState.RINGING)
    assert changed is False
    assert call.status == CallState.RINGING


def test_call_out_of_order_regressive_event_ignored():
    # Call is in ANSWERED state
    call = Call(id="c_102", borrower_id="b_1", campaign_id="camp_1", status=CallState.ANSWERED)
    # Late RINGING event arrives (rank 40 < rank 50)
    changed = call.transition_to(CallState.RINGING)
    assert changed is False
    assert call.status == CallState.ANSWERED


def test_call_terminal_lock_prevents_regression():
    call = Call(id="c_103", borrower_id="b_1", campaign_id="camp_1", status=CallState.COMPLETED)
    assert call.is_terminal is True

    # COMPLETED arrived first. Later, ANSWERED arrives.
    changed = call.transition_to(CallState.ANSWERED)
    assert changed is False
    assert call.status == CallState.COMPLETED


def test_call_abandoned_flag():
    call = Call(id="c_104", borrower_id="b_1", campaign_id="camp_1", status=CallState.ANSWERED)
    call.transition_to(CallState.CANCELLED, reason="NO_AGENT_AVAILABLE", abandoned=True)
    assert call.status == CallState.CANCELLED
    assert call.abandoned is True
    assert call.failure_reason == "NO_AGENT_AVAILABLE"
