"""Business calendar: business dates are `date` values in BUSINESS_TIMEZONE (DATA_MODEL.md §1)."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.core.config import get_settings


def business_today() -> date:
    return datetime.now(ZoneInfo(get_settings().business_timezone)).date()
