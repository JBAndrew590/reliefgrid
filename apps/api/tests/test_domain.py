from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from reliefgrid.domain import TRANSITIONS, can_transition, score_match


def test_request_state_machine_rejects_skips_and_terminal_changes():
    assert can_transition("DRAFT", "SUBMITTED")
    assert not can_transition("DRAFT", "FULFILLED")
    assert not can_transition("FULFILLED", "MATCHED")
    assert all(not can_transition(status, "VERIFIED") for status in ("CANCELLED", "EXPIRED", "REJECTED"))
    assert "IN_TRANSIT" in TRANSITIONS["FULLY_ALLOCATED"]


def test_score_is_deterministic_bounded_and_explainable():
    now = datetime(2026, 11, 5, tzinfo=UTC)
    before = score_match("critical", 1000, Decimal("8"), Decimal("10"), date(2026, 11, 10), now+timedelta(days=2), now)
    far = score_match("critical", 200000, Decimal("8"), Decimal("10"), date(2026, 11, 10), now+timedelta(days=2), now)
    assert 0 <= before.total <= 100
    assert before.distance > far.distance
    assert before.coverage == 24
    assert before == score_match("critical", 1000, Decimal("8"), Decimal("10"), date(2026, 11, 10), now+timedelta(days=2), now)
