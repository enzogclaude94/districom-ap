"""R9-5-1: what a session costs each request, counted, not timed: statements, commits and `user_sessions` writes.

    PERF_DATABASE_URL=postgresql+psycopg://districom:districom@localhost:5432/districom_perf_small \
        uv run python -m perf.sessions --requests 30

HTTP local, as the generator's admin: a burst of GETs on the same session (`/auth/me` and the Cajas balances), so
every request after the first falls inside the touch window.
"""

import argparse
import json
import statistics
from typing import Any

from perf.bench import ADMIN, S
from perf.harness import Recorder, client, login, use_perf_database


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--requests", type=int, default=30)
    args = parser.parse_args()
    use_perf_database()

    from sqlalchemy import event

    from app.db.session import get_engine

    commits: list[int] = [0]

    @event.listens_for(get_engine(), "commit")
    def _commit(conn: Any) -> None:
        commits[0] += 1

    recorder = Recorder()
    recorder.install()
    http = client()
    login(http, ADMIN)
    out = {}
    for path in ("/api/v1/auth/me", f"{S}/accounts"):
        statements, done, touches = [], [], []
        for _ in range(args.requests):
            commits[0] = 0
            with recorder.record() as recorded:
                assert http.get(path).status_code == 200
            statements.append(len(recorded))
            done.append(commits[0])
            touches.append(sum("UPDATE user_sessions" in s for s, _, _ in recorded))
        out[path] = {
            "requests": args.requests,
            "statements_median": statistics.median(statements),
            "commits_total": sum(done),
            "commits_per_request": round(sum(done) / args.requests, 2),
            "session_updates_total": sum(touches),
        }
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
