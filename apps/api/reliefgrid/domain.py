"""Pure business rules; all quantities use Decimal with three decimal places."""
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from math import exp

TRANSITIONS: dict[str, set[str]] = {
    "DRAFT": {"SUBMITTED", "CANCELLED", "EXPIRED"},
    "SUBMITTED": {"VERIFIED", "REJECTED", "CANCELLED", "EXPIRED"},
    "VERIFIED": {"MATCHED", "REJECTED", "CANCELLED", "EXPIRED"},
    "MATCHED": {"PARTIALLY_ALLOCATED", "FULLY_ALLOCATED", "CANCELLED", "EXPIRED"},
    "PARTIALLY_ALLOCATED": {"FULLY_ALLOCATED", "IN_TRANSIT"},
    "FULLY_ALLOCATED": {"IN_TRANSIT"},
    "IN_TRANSIT": {"FULFILLED"},
    "FULFILLED": set(), "CANCELLED": set(), "EXPIRED": set(), "REJECTED": set(),
}


def can_transition(current: str, target: str) -> bool:
    return target in TRANSITIONS[current]


@dataclass(frozen=True)
class Score:
    urgency: int
    distance: int
    coverage: int
    expiry: int
    deadline: int

    @property
    def total(self) -> int:
        return self.urgency + self.distance + self.coverage + self.expiry + self.deadline


def score_match(priority: str, distance_m: float, available: Decimal,
                required: Decimal, expires_on: date | None, required_by: datetime,
                now: datetime | None = None) -> Score:
    """Weights sum to 100; expired lots are filtered before scoring."""
    now = now or datetime.now(UTC)
    days_left = max(0.0, (required_by - now).total_seconds() / 86400)
    expiry_days = (expires_on - now.date()).days if expires_on else None
    # FEFO preference is strongest when the lot remains usable through the required date.
    expiry_points = 15 if expiry_days is not None and expiry_days >= days_left and expiry_days <= 30 else (10 if expiry_days is None or expiry_days >= days_left else 0)
    return Score(
        urgency={"routine": 8, "high": 16, "critical": 25}[priority],
        distance=round(20 * exp(-max(distance_m, 0) / 100000)),
        coverage=round(30 * min(available / required, Decimal(1))),
        expiry=expiry_points,
        deadline=10 if days_left <= 1 else (6 if days_left <= 3 else 3),
    )
