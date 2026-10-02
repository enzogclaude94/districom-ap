"""Hand-verifiable scenarios for route execution metrics (METRICS.md "Ejecución de rutas").

Executable specification for Phase 3: the analytics tests will load these plans,
compute `route.schedule_compliance` and `route.coverage`, and compare against `expected`.
Do not change expected values without changing METRICS.md in the same commit.
"""

from dataclasses import dataclass
from datetime import date
from fractions import Fraction

MONDAY = date(2026, 6, 1)
THURSDAY = date(2026, 6, 4)
FRIDAY = date(2026, 6, 5)


@dataclass(frozen=True)
class RouteExecutionScenario:
    name: str
    scheduled: frozenset[tuple[str, date]]
    visited: frozenset[tuple[str, date]]
    expected: dict[str, Fraction | int]


_SCHEDULED = frozenset(
    {
        ("A", MONDAY),
        ("A", THURSDAY),
        ("B", MONDAY),
        ("C", THURSDAY),
        ("D", MONDAY),
        ("D", THURSDAY),
    }
)

COMPLIANCE_VS_COVERAGE = RouteExecutionScenario(
    name="A misses Thursday, C never visited",
    scheduled=_SCHEDULED,
    visited=frozenset({("A", MONDAY), ("B", MONDAY), ("D", MONDAY), ("D", THURSDAY)}),
    expected={
        "route.scheduled_visits": 6,
        "route.completed_visits": 4,
        "route.schedule_compliance": Fraction(4, 6),
        "route.scheduled_customers": 4,
        "route.visited_customers": 3,
        "route.coverage": Fraction(3, 4),
        "route.unscheduled_visits": 0,
    },
)

OFF_DAY_VISIT = RouteExecutionScenario(
    name="C visited on an unscheduled Friday: reached (coverage) but plan not met (compliance)",
    scheduled=_SCHEDULED,
    visited=COMPLIANCE_VS_COVERAGE.visited | {("C", FRIDAY)},
    expected={
        "route.scheduled_visits": 6,
        "route.completed_visits": 4,
        "route.schedule_compliance": Fraction(4, 6),
        "route.scheduled_customers": 4,
        "route.visited_customers": 4,
        "route.coverage": Fraction(1),
        "route.unscheduled_visits": 1,
    },
)

SCENARIOS = (COMPLIANCE_VS_COVERAGE, OFF_DAY_VISIT)
