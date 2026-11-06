"""`record_issue` (docs/IMPORTS.md §7): one open issue per `(code, dedupe_key)`, occurrences added on repeat."""

from sqlalchemy import select
from sqlalchemy.orm import Session

import app.api.v1.router  # noqa: F401  (installs the modules: their quality codes)
from app.platform.quality.models import DataQualityIssue
from app.platform.quality.service import record_issue


def test_many_issues_in_one_connection_keep_their_conflict_target(db: Session) -> None:
    """Regression: psycopg prepares a statement after a few executions; a bound parameter in the arbiter's WHERE
    then stopped matching the partial unique index and every later issue failed."""
    for round_ in range(2):
        for n in range(12):
            record_issue(db, "commercial.product.unknown", f"product_code:{n}", details={"round": round_})
    db.commit()
    issues = db.scalars(select(DataQualityIssue).order_by(DataQualityIssue.id)).all()
    assert len(issues) == 12
    assert {i.occurrences for i in issues} == {2}
