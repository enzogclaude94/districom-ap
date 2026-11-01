"""R9-5-3: audit of the app's logs against a realistic dataset. Runs the benchmark's reads and Store Sys import with the
access log on, plus the error paths that see personal data (login failures, validation errors, rule conflicts and an
unhandled database error), writes every log line to a file and searches it for every email, name, phone and CUIT
in the dataset.

    PERF_DATABASE_URL=postgresql+psycopg://districom:districom@localhost:5432/districom_perf_audit \
        uv run python -m perf.log_audit --log ../perf-results/audit/app.log --out ../perf-results/audit

It writes: run it on a copy of the dataset (`CREATE DATABASE … TEMPLATE districom_perf_small`).
"""

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

from perf import bench, harness


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    harness.use_perf_database()

    from sqlalchemy import text

    from app.db.session import get_engine
    from app.main import app  # configures the app's logging

    with get_engine().begin() as conn:  # the generated dataset has no phones: the audit copy gets one per employee
        conn.execute(text("UPDATE employees SET phone = format('11-%s-%s', 4000 + id, 1000 + id) WHERE phone IS NULL"))
    args.log.parent.mkdir(parents=True, exist_ok=True)
    log = args.log.open("w", encoding="utf-8")
    handler = logging.getLogger().handlers[0]
    assert isinstance(handler, logging.StreamHandler)
    handler.setStream(log)

    original = harness.client

    def logged_client() -> Any:
        http = original()
        logging.getLogger("app.access").setLevel(logging.INFO)  # the benchmark silences it
        return http

    harness.client = logged_client
    bench.client = logged_client  # type: ignore[attr-defined]
    sys.argv = ["bench", "--runs", "2", "--explain-top", "0", "--store-sys-repeats", "1", "--writes", "0"]
    sys.argv += ["--out", str(args.out / "bench")]
    bench.main()

    with get_engine().connect() as conn:
        sample = conn.execute(text("SELECT full_name, phone FROM employees WHERE phone IS NOT NULL LIMIT 1")).one()
        email = conn.execute(text("SELECT email FROM users ORDER BY id LIMIT 1")).scalar_one()
    http = logged_client()
    # Error paths that carry personal data in the request or in the database's own message.
    http.post("/api/v1/auth/login", json={"email": email, "password": "incorrecta-1234"})
    harness.login(http, bench.ADMIN)
    party = {"name": "Distribuidora Auditoría S.A.", "tax_id": "30712345671"}  # the dataset's have no CUIT
    http.post("/api/v1/third-parties", json=party)
    http.post("/api/v1/third-parties", json=party)  # duplicate CUIT
    http.post("/api/v1/third-parties", json={"name": sample.full_name, "tax_id": "20123456789"})  # invalid CUIT
    http.post("/api/v1/employees", json={"full_name": sample.full_name, "phone": sample.phone, "bogus": True})
    http.get("/api/v1/third-parties", params={"q": party["name"], "tax_id": party["tax_id"]})

    def broken() -> None:  # an unhandled database error whose DETAIL quotes a real row
        with get_engine().begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO third_parties (name, name_key, tax_id, notes, created_by, updated_by)"
                    " VALUES (:name, '', :cuit, :notes, 1, 1)"
                ),
                {"name": sample.full_name, "cuit": party["tax_id"], "notes": f"{sample.phone} {email}"},
            )

    app.add_api_route("/api/v1/_audit/broken", broken)
    from fastapi.testclient import TestClient

    with TestClient(app, raise_server_exceptions=False) as raw:
        assert raw.get("/api/v1/_audit/broken").status_code == 500
    app.router.routes.pop()
    log.close()

    with get_engine().connect() as conn:
        people = conn.execute(text("SELECT full_name, phone FROM employees")).all()
        parties = conn.execute(text("SELECT name, tax_id FROM third_parties")).all()
        emails = conn.execute(text("SELECT email FROM users")).scalars().all()
    values = {v for row in people for v in row if v} | {v for row in parties for v in row if v} | set(emails)
    values = {v for v in values if len(v) >= 6}
    content = args.log.read_text(encoding="utf-8")
    hits = sorted(v for v in values if v in content)
    lines = content.splitlines()
    report = {
        "log_lines": len(lines),
        "access_lines": sum('"message": "request"' in line for line in lines),
        "error_lines": sum('"level": "ERROR"' in line for line in lines),
        "values_searched": len(values),
        "by_kind": {
            "names": len({r.full_name for r in people} | {p.name for p in parties}),
            "phones": len({r.phone for r in people if r.phone}),
            "cuits": len({p.tax_id for p in parties if p.tax_id}),
            "emails": len(emails),
        },
        "hits": len(hits),
    }
    print(json.dumps(report, indent=2))
    (args.out / "log_audit.json").write_text(json.dumps({**report, "hit_values_redacted": len(hits)}, indent=2))
    if hits:
        sys.exit(f"{len(hits)} personal values found in the log")


if __name__ == "__main__":
    main()
