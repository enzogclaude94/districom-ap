"""Guards the hand-written fixtures themselves until the Phase 3 metrics exist.

Recomputes each expected value from the raw plan with the plain definitions in
METRICS.md, so a typo in a fixture fails here instead of misleading Phase 3.
"""

from fractions import Fraction

import pytest

from tests.fixtures.route_execution import SCENARIOS, RouteExecutionScenario


def _recompute(s: RouteExecutionScenario) -> dict[str, object]:
    completed = s.scheduled & s.visited
    scheduled_customers = {c for c, _ in s.scheduled}
    visited_customers = {c for c, _ in s.visited} & scheduled_customers
    return {
        "route.scheduled_visits": len(s.scheduled),
        "route.completed_visits": len(completed),
        "route.schedule_compliance": Fraction(len(completed), len(s.scheduled)),
        "route.scheduled_customers": len(scheduled_customers),
        "route.visited_customers": len(visited_customers),
        "route.coverage": Fraction(len(visited_customers), len(scheduled_customers)),
        "route.unscheduled_visits": len(s.visited - s.scheduled),
    }


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.name)
def test_fixture_expected_values_match_definitions(scenario: RouteExecutionScenario) -> None:
    assert _recompute(scenario) == scenario.expected


def test_compliance_and_coverage_differ_on_the_reference_plan() -> None:
    reference = SCENARIOS[0].expected
    assert reference["route.schedule_compliance"] != reference["route.coverage"]
