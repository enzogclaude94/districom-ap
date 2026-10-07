"""E2E only: makes a pending collection look registered some hours ago, so its alert can be seen on screen.

`created_at` is immutable for the app (a trigger refuses changing it), so this disables triggers for its
own transaction (`session_replication_role = replica`, which the CI database user is allowed to do). It
never runs in the application. Usage: `uv run --directory ../backend python ../frontend/e2e/age_pending.py ID HOURS`.
"""

import os
import sys

from sqlalchemy import create_engine, text

# Run from the backend directory (`uv run --directory ../backend`), which is not this script's directory.
sys.path.insert(0, os.getcwd())
from app.core.config import get_settings  # noqa: E402

pending_id, hours = int(sys.argv[1]), int(sys.argv[2])
engine = create_engine(get_settings().database_url)
with engine.begin() as connection:
    connection.execute(text("SET LOCAL session_replication_role = replica"))
    connection.execute(
        text(
            "UPDATE settlements.pending_collections SET created_at = now() - make_interval(hours => :hours) "
            "WHERE id = :id"
        ),
        {"id": pending_id, "hours": hours},
    )
