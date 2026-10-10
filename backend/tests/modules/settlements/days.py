"""The business day of the Rendiciones tests, resolved at the moment of use.

Rule: a test, fixture or helper asks for the business day (America/Argentina/Buenos_Aires, via
`business_today()`) when it needs it, never at import time. A module constant like `T = business_today()`
freezes the day the module was collected; a run crossing midnight in the business timezone then posts
"today" with yesterday's date and fails falsely. Defaults follow the same rule: `on: date | None = None`
and `if on is None: on = today()` inside the function. `tests/unit/test_settlements_test_days.py` guards it.
"""

from datetime import date, timedelta

from app.core.clock import business_today


def today() -> date:
    """The business day right now."""
    return business_today()


def days_ago(n: int) -> date:
    """The business day `n` days before today, resolved now."""
    return business_today() - timedelta(days=n)


def yesterday() -> date:
    return days_ago(1)
