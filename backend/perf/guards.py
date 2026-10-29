"""R9-4, migration 0031: the writes its guards slow down, timed before and after on the same restored dataset.

    PERF_DATABASE_URL=postgresql+psycopg://districom:districom@localhost:5432/districom_perf_stress \
        uv run python -m perf.guards --runs 20 --out ../perf-results/guards-after

HTTP local (the app in process through its HTTP stack), always as the generator's admin:

- «Entrega a cuenta» of the boca with the most remittances, and the close of its settlement: both run the deferred
  outlet guard (`assert_outlet_advances`) at commit, and the remittance debits nothing but credits Caja principal.
- A payment in cash out of Caja principal, dated today and 90 days before the end of the dataset: the debit runs the
  funds check of the app and the not-negative trigger (`check_account_not_negative`).

Service in process: `ledger.account_balance` of Caja principal (the account with the most entries).

It writes: run it on a copy restored from the dataset's dump, and restore it again between the before and the after.
"""

import argparse
import json
import statistics
import time
import uuid
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

from perf.bench import ADMIN, S, percentile
from perf.harness import Recorder, client, login, use_perf_database


def timed(recorder: Recorder, call: Callable[[], Any]) -> tuple[float, int, Any]:
    with recorder.record() as recorded:
        started = time.perf_counter()
        result = call()
        elapsed = (time.perf_counter() - started) * 1000
    return elapsed, len(recorded), result


def row(case: str, times: list[float], counts: list[int]) -> dict[str, Any]:
    out = {
        "case": case,
        "runs": len(times),
        "median_ms": round(statistics.median(times), 1),
        "p95_ms": round(percentile(times, 0.95), 1),
        "max_ms": round(max(times), 1),
        "queries": round(statistics.median(counts)),
    }
    print(f"{case:40} p50={out['median_ms']:8.1f} p95={out['p95_ms']:8.1f} max={out['max_ms']:8.1f} q={out['queries']}")
    return out


def ok(response: Any) -> dict[str, Any]:
    assert response.status_code in (200, 201), response.text[:300]
    return dict(response.json())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    url = use_perf_database()

    from sqlalchemy import text
    from sqlalchemy.orm import Session

    from app.core.clock import business_today
    from app.db.session import get_engine
    from app.modules.settlements.ledger import service as ledger

    with get_engine().connect() as conn:
        last = conn.execute(text("select max(business_date) from settlements.financial_operations")).scalar_one()
        main_box = conn.execute(
            text("select id from settlements.accounts where kind = 'CASH_BOX' order by id limit 1")
        ).scalar_one()
        outlet = conn.execute(
            text("select outlet_id from settlements.outlet_remittances group by 1 order by count(*) desc limit 1")
        ).scalar_one()
        third_party = conn.execute(
            text("select third_party_id from settlements.payments group by 1 order by count(*) desc limit 1")
        ).scalar_one()
        version = conn.execute(text("select version_num from alembic_version")).scalar_one()
    recorder = Recorder()
    recorder.install()
    http = client()
    login(http, ADMIN)
    day = business_today()  # after the dataset, which ends yesterday at the latest
    results = []

    # Bocas: a settlement of the busiest boca expecting $2.000, two remittances of $1.000, an empty count and the close.
    remittance_t, remittance_q, close_t, close_q = [], [], [], []
    for n in range(args.runs + 2):
        created = ok(
            http.post(
                f"{S}/outlet-settlements",
                json={
                    "client_request_id": str(uuid.uuid4()),
                    "business_date": day.isoformat(),
                    "outlet_id": outlet,
                    "expected_amount": "2000.00",
                    "confirm_additional": True,
                },
            )
        )
        path = f"{S}/outlet-settlements/{created['id']}"
        for hour in ("12:00", "17:00"):
            body = {
                "client_request_id": str(uuid.uuid4()),
                "amount": "1000.00",
                "destination_account_id": main_box,
                "delivered_at": f"{day.isoformat()}T{hour}:00-03:00",
                "confirm_above_expected": True,
            }
            elapsed, count, response = timed(recorder, lambda: http.post(f"{path}/remittances", json=body))  # noqa: B023
            ok(response)
            if n >= 2:  # the first two settlements warm up
                remittance_t.append(elapsed)
                remittance_q.append(count)
        ok(http.put(f"{path}/count", json={"lines": []}))
        preview = ok(http.get(f"{path}/closing-preview"))
        parts = []
        if preview["difference_kind"] in ("SHORTAGE", "SURPLUS"):
            parts = [{"target": "COMPANY", "amount": preview["amount_to_allocate"], "reason": "Bench"}]
        close = {
            "version": preview["version"],
            "preview_token": preview["preview_token"],
            "allocations": parts,
            "confirm": True,
        }
        elapsed, count, response = timed(recorder, lambda: http.post(f"{path}/close", json=close))  # noqa: B023
        ok(response)
        if n >= 2:
            close_t.append(elapsed)
            close_q.append(count)
    results.append(row("outlet remittance", remittance_t, remittance_q))
    results.append(row("outlet close", close_t, close_q))

    # Cash payments out of Caja principal: today and 90 days before the end of the dataset.
    for label, when in (("today", day), ("90 days back", last - timedelta(days=90))):
        times, counts = [], []
        for n in range(args.runs + 3):
            body = {
                "client_request_id": str(uuid.uuid4()),
                "business_date": when.isoformat(),
                "third_party_id": third_party,
                "concept": "MERCHANDISE",
                "reference": f"GUARDS-{label}-{n}",
                "items": [{"method": "CASH", "account_id": main_box, "amount": "10.00"}],
            }
            elapsed, count, response = timed(recorder, lambda: http.post(f"{S}/payments", json=body))  # noqa: B023
            ok(response)
            if n >= 3:
                times.append(elapsed)
                counts.append(count)
        results.append(row(f"payment in cash, {label}", times, counts))

    # Service: the balance of Caja principal.
    times, counts = [], []
    with Session(get_engine()) as db:
        for n in range(args.runs * 5 + 3):
            elapsed, count, _ = timed(recorder, lambda: ledger.account_balance(db, main_box))
            if n >= 3:
                times.append(elapsed)
                counts.append(count)
    results.append(row("service account_balance(Caja principal)", times, counts))

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "guards.json").write_text(
        json.dumps(
            {
                "database": url.rsplit("/", 1)[-1],
                "alembic_version": version,
                "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "results": results,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
