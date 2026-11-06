"""B-5: the business time zone on Windows, where Python has no system time zone database.

On Windows ``zoneinfo`` finds America/Argentina/Buenos_Aires only through the ``tzdata`` package. W2-B declares it
as a direct dependency of the backend (Enzo, 2026-11-06), so it no longer depends on psycopg asking for it.
"""

import sys
import tomllib
import zoneinfo
from datetime import UTC, date, datetime
from importlib import metadata

import pytest

from app.core.clock import business_today
from app.core.config import get_settings
from checks.conftest import BACKEND

ZONE = "America/Argentina/Buenos_Aires"


@pytest.mark.criterion("B-5")
def test_tzdata_is_a_direct_dependency_of_the_backend() -> None:
    project = tomllib.loads((BACKEND / "pyproject.toml").read_text(encoding="utf-8"))
    names = [dep.split(">")[0].split("=")[0].split("[")[0].strip() for dep in project["project"]["dependencies"]]
    assert "tzdata" in names
    assert metadata.version("tzdata")


@pytest.mark.criterion("B-5")
def test_business_zone_loads() -> None:
    assert get_settings().business_timezone == ZONE
    if sys.platform == "win32":
        # No system database on Windows: the zone can only come from the tzdata package.
        assert zoneinfo.TZPATH == ()
    zone = zoneinfo.ZoneInfo(ZONE)
    assert datetime(2026, 7, 1, 12, tzinfo=zone).utcoffset() is not None


@pytest.mark.criterion("B-5")
@pytest.mark.parametrize(
    ("instant", "business_date"),
    [
        (datetime(2026, 11, 6, 2, 59, 59, tzinfo=UTC), date(2026, 11, 5)),  # 23:59:59 in Buenos Aires
        (datetime(2026, 11, 6, 3, 0, 0, tzinfo=UTC), date(2026, 11, 6)),  # midnight in Buenos Aires
        (datetime(2026, 12, 1, 2, 30, tzinfo=UTC), date(2026, 11, 30)),  # month end
        (datetime(2027, 1, 1, 2, 59, tzinfo=UTC), date(2026, 12, 31)),  # year end
    ],
)
def test_business_date_boundaries(instant: datetime, business_date: date) -> None:
    assert instant.astimezone(zoneinfo.ZoneInfo(ZONE)).date() == business_date


@pytest.mark.criterion("B-5")
def test_business_today_matches_the_zone() -> None:
    before = datetime.now(zoneinfo.ZoneInfo(ZONE)).date()
    today = business_today()
    after = datetime.now(zoneinfo.ZoneInfo(ZONE)).date()
    assert today in (before, after)
