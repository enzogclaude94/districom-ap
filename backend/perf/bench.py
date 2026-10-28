"""R9-4 benchmark: the reads and the Store Sys import of Rendiciones against a dataset made by `perf.generate`.

    PERF_DATABASE_URL=postgresql+psycopg://districom:districom@localhost:5432/districom_perf \
        uv run python -m perf.bench --runs 30 --out ../perf-results/realistic

Three layers, never compared with each other:

- HTTP local: the app in process through its whole HTTP stack (FastAPI TestClient: session, permissions,
  serialization), without a network. Median, p95 and max of `--runs` requests after warm-up, and the queries of
  one request (count and SQL time, from the engine's own events).
- Service in process: the pieces of the Cierre del día called directly with a session.
- SQL: `EXPLAIN (ANALYZE, BUFFERS)` of the slowest statements each endpoint sent, with their real parameters,
  inside a transaction that is rolled back.

Reads run first. The Store Sys import writes: run the benchmark on a copy restored from the dataset's dump.
"""

import argparse
import contextlib
import json
import re
import statistics
import subprocess
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from perf.harness import PASSWORD, Recorder, cli, client, login, use_perf_database

ADMIN = "perf-admin@districom.local"  # has Store Sys and Empleados (the generator's user)
OPERATOR = "perf-no-area@districom.local"  # platform admin without the Empleados area: the privacy variant
S = "/api/v1/settlements"
EF = f"{S}/employee-finance"


@dataclass
class Result:
    name: str
    group: str
    profile: str
    path: str
    status: int
    runs: int
    median_ms: float
    p95_ms: float
    max_ms: float
    queries: int
    queries_max: int
    sql_ms: float
    rows: int | None
    bytes: int
    plans: list[dict[str, Any]] = field(default_factory=list)


def percentile(values: list[float], share: float) -> float:
    """Nearest rank: the value below which `share` of the runs fall."""
    ordered = sorted(values)
    return ordered[max(0, min(len(ordered) - 1, round(share * len(ordered) + 0.5) - 1))]


def rows_of(payload: Any) -> int | None:
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict):
        for key in ("items", "movements", "accounts", "results"):
            if isinstance(payload.get(key), list):
                return len(payload[key])
    return None


class Bench:
    def __init__(self, runs: int, out: Path, explain_top: int) -> None:
        self.runs = runs
        self.out = out
        self.explain_top = explain_top
        self.recorder = Recorder()
        self.recorder.install()
        self.results: list[Result] = []
        self.http: dict[str, Any] = {}

    def profile(self, name: str, email: str) -> None:
        http = client()
        login(http, email)
        self.http[name] = http

    def measure(self, group: str, name: str, path: str, profile: str = "area", explain: bool = True) -> Result:
        http = self.http[profile]
        for _ in range(3):
            http.get(path)
        times: list[float] = []
        counts: list[int] = []
        sql: list[float] = []
        statements: list[tuple[str, Any, float]] = []
        response = None
        for _ in range(self.runs):
            with self.recorder.record() as recorded:
                started = time.perf_counter()
                response = http.get(path)
                times.append((time.perf_counter() - started) * 1000)
            counts.append(len(recorded))
            sql.append(sum(e for _, _, e in recorded) * 1000)
            statements = list(recorded)
        assert response is not None
        ok = response.status_code == 200
        result = Result(
            name=name,
            group=group,
            profile=profile,
            path=path,
            status=response.status_code,
            runs=self.runs,
            median_ms=round(statistics.median(times), 1),
            p95_ms=round(percentile(times, 0.95), 1),
            max_ms=round(max(times), 1),
            queries=round(statistics.median(counts)),
            queries_max=max(counts),
            sql_ms=round(statistics.median(sql), 1),
            rows=rows_of(response.json())
            if ok and response.headers["content-type"].startswith("application/json")
            else (response.text.count("\n") if ok else None),
            bytes=len(response.content),
        )
        if explain and ok:
            result.plans = self.explain(f"{group}-{name}-{profile}", statements)
        self.results.append(result)
        print(
            f"{group:16} {name:38} {profile:7} {result.status} p50={result.median_ms:7.1f} p95={result.p95_ms:7.1f} "
            f"max={result.max_ms:7.1f} q={result.queries:3} sql={result.sql_ms:6.1f} rows={result.rows}",
            flush=True,
        )
        return result

    def explain(self, label: str, statements: list[tuple[str, Any, float]]) -> list[dict[str, Any]]:
        """EXPLAIN (ANALYZE, BUFFERS) of the slowest reads of one request, rolled back."""
        from app.db.session import get_engine

        reads = [s for s in statements if s[0].lstrip().upper().startswith(("SELECT", "WITH"))]
        slowest = sorted(reads, key=lambda s: -s[2])[: self.explain_top]
        plans = []
        raw = get_engine().raw_connection()
        try:
            cursor = raw.cursor()
            for index, (statement, parameters, elapsed) in enumerate(slowest, 1):
                cursor.execute("EXPLAIN (ANALYZE, BUFFERS) " + statement, parameters)
                text = "\n".join(row[0] for row in cursor.fetchall())
                raw.rollback()
                name = re.sub(r"[^a-z0-9.-]+", "_", f"{label}-{index}".lower())
                (self.out / "plans").mkdir(parents=True, exist_ok=True)
                (self.out / "plans" / f"{name}.txt").write_text(
                    f"-- {label} #{index}, {elapsed * 1000:.1f} ms in the request\n"
                    f"{statement}\n-- {parameters}\n\n{text}\n"
                )
                plans.append({"file": f"plans/{name}.txt", "request_ms": round(elapsed * 1000, 1), **summarize(text)})
        finally:
            raw.rollback()
            raw.close()
        return plans

    def save(self, extra: dict[str, Any]) -> None:
        self.out.mkdir(parents=True, exist_ok=True)
        (self.out / "results.json").write_text(
            json.dumps({**extra, "results": [asdict(r) for r in self.results]}, indent=2, default=str)
        )


def summarize(plan: str) -> dict[str, Any]:
    execution = re.search(r"Execution Time: ([\d.]+) ms", plan)
    hit = re.search(r"Buffers: shared hit=(\d+)", plan)
    read = re.search(r"Buffers: shared (?:hit=\d+ )?read=(\d+)", plan)
    return {
        "execution_ms": float(execution.group(1)) if execution else None,
        "seq_scans": sorted(set(re.findall(r"Seq Scan on (\w+)", plan))),
        "index_scans": sorted(set(re.findall(r"Index(?: Only)? Scan(?: Backward)? using (\w+)", plan))),
        "shared_hit": int(hit.group(1)) if hit else 0,
        "shared_read": int(read.group(1)) if read else 0,
        "max_loops": max((int(x) for x in re.findall(r"loops=(\d+)", plan)), default=0),
    }


# --- what the dataset holds ------------------------------------------------------------------------------------------


def context() -> dict[str, Any]:
    """Ids and dates to ask about, chosen as the heaviest of their kind (the busiest box, the employee with the
    most movements...), read straight from the dataset."""
    from sqlalchemy import text

    from app.db.session import get_engine

    def one(sql: str) -> Any:
        with get_engine().connect() as conn:
            return conn.execute(text(sql)).scalar()

    def counts(sql: str) -> dict[str, int]:
        with get_engine().connect() as conn:
            return {str(k): int(v) for k, v in conn.execute(text(sql)).all()}

    last = one("select max(business_date) from settlements.financial_operations")
    return {
        "last_day": last,
        "mid_day": last - timedelta(days=182),
        "main": one("select id from settlements.accounts where kind = 'CASH_BOX' order by id limit 1"),
        "bank": one("select id from settlements.accounts where kind = 'BANK' order by id limit 1"),
        "busiest_account": one(
            "select account_id from settlements.ledger_entries e join settlements.accounts a on a.id = e.account_id "
            "where a.kind in ('CASH_BOX', 'BANK') group by account_id order by count(*) desc limit 1"
        ),
        "delivery": one(
            "select s.id from settlements.settlements s left join settlements.settlement_reopenings r "
            "on r.settlement_id = s.id where s.type = 'DELIVERY' group by s.id order by count(r.id) desc, s.id desc "
            "limit 1"
        ),
        "outlet_settlement": one(
            "select id from settlements.settlements where type = 'OUTLET' and status = 'CLOSED' "
            "order by id desc limit 1"
        ),
        "outlet": one("select outlet_id from settlements.settlements where type = 'OUTLET' limit 1"),
        "employee": one(
            "select employee_id from settlements.accounts a join settlements.ledger_entries e on e.account_id = a.id "
            "where a.kind = 'EMPLOYEE' group by employee_id order by count(*) desc limit 1"
        ),
        "driver": one("select employee_id from settlements.settlements where type = 'DELIVERY' limit 1"),
        "payroll": one("select id from settlements.employee_payrolls order by id desc limit 1"),
        "payout": one("select id from settlements.employee_payouts order by id desc limit 1"),
        "loan": one("select id from settlements.employee_loans order by id desc limit 1"),
        "cheque": one(
            "select cheque_id from settlements.cheque_events group by cheque_id order by count(*) desc limit 1"
        ),
        "pending": one("select id from settlements.pending_collections order by id desc limit 1"),
        "operation": one(
            "select operation_id from settlements.ledger_entries group by operation_id order by count(*) desc limit 1"
        ),
        "third_party": one("select third_party_id from settlements.payments group by 1 order by count(*) desc limit 1"),
        "volume": counts(
            "select relname, n_live_tup from pg_stat_user_tables where schemaname in ('settlements', 'public') "
            "and n_live_tup > 0 order by n_live_tup desc"
        ),
    }


# --- the cases ---------------------------------------------------------------------------------------------------


def reads(b: Bench, c: dict[str, Any]) -> None:
    last, mid = c["last_day"].isoformat(), c["mid_day"].isoformat()
    week = (c["last_day"] - timedelta(days=6)).isoformat()
    month = (c["last_day"] - timedelta(days=30)).isoformat()
    for profile in ("area", "no-area"):
        b.measure("cierre", "daily-close last day", f"{S}/daily-close?on={last}", profile)
        b.measure("cierre", "daily-close.csv last day", f"{S}/daily-close.csv?on={last}", profile)
    b.measure("cierre", "daily-close mid-year", f"{S}/daily-close?on={mid}")
    b.measure("cierre", "daily-close.csv mid-year", f"{S}/daily-close.csv?on={mid}")

    for profile in ("area", "no-area"):
        b.measure("cajas", "balances today", f"{S}/accounts", profile)
    b.measure("cajas", "balances on mid-year", f"{S}/accounts?on={mid}")
    b.measure("cajas", "balance of busiest on mid", f"{S}/accounts/{c['busiest_account']}?on={mid}")
    b.measure("cajas", "movements busiest p1", f"{S}/accounts/{c['busiest_account']}/movements?limit=20&offset=0")
    b.measure("cajas", "movements busiest p50", f"{S}/accounts/{c['busiest_account']}/movements?limit=20&offset=1000")
    b.measure(
        "cajas",
        "movements busiest 1 month",
        f"{S}/accounts/{c['busiest_account']}/movements?date_from={month}&date_to={last}&limit=20&offset=0",
    )
    b.measure("cajas", "operation detail (widest)", f"{S}/operations/{c['operation']}")

    b.measure("rendiciones", "deliveries board (day)", f"{S}/deliveries?date_from={last}&date_to={last}&limit=20")
    b.measure("rendiciones", "deliveries list p1", f"{S}/deliveries?limit=20&offset=0")
    b.measure("rendiciones", "deliveries list p100", f"{S}/deliveries?limit=20&offset=2000")
    b.measure("rendiciones", "deliveries by driver", f"{S}/deliveries?employee_id={c['driver']}&limit=20")
    b.measure("rendiciones", "deliveries week", f"{S}/deliveries?date_from={week}&date_to={last}&limit=20")
    b.measure("rendiciones", "delivery detail", f"{S}/deliveries/{c['delivery']}")
    b.measure("rendiciones", "delivery history", f"{S}/deliveries/{c['delivery']}/history")
    b.measure("rendiciones", "delivery closing-preview", f"{S}/deliveries/{c['delivery']}/closing-preview")
    b.measure("rendiciones", "outlet board (day)", f"{S}/outlet-settlements/board?business_date={last}")
    b.measure("rendiciones", "outlet list p1", f"{S}/outlet-settlements?limit=20&offset=0")
    b.measure("rendiciones", "outlet detail", f"{S}/outlet-settlements/{c['outlet_settlement']}")
    b.measure("rendiciones", "outlet settlements summary", f"{S}/outlets/{c['outlet']}/settlements-summary")
    b.measure("rendiciones", "rendiciones status", f"{S}/status")

    b.measure("cheques", "portfolio", f"{S}/cheques?in_portfolio=true&limit=20")
    b.measure("cheques", "all p1", f"{S}/cheques?limit=20&offset=0")
    b.measure("cheques", "expiring soon", f"{S}/cheques?expiring_soon=true&limit=20")
    b.measure("cheques", "summary", f"{S}/cheques/summary")
    b.measure("cheques", "detail (most events)", f"{S}/cheques/{c['cheque']}")

    b.measure("pendientes", "open p1", f"{S}/pending-collections?state=OPEN&limit=20")
    b.measure("pendientes", "all p1", f"{S}/pending-collections?limit=20")
    b.measure("pendientes", "by driver", f"{S}/pending-collections?driver_id={c['driver']}&limit=20")
    b.measure("pendientes", "alerts", f"{S}/pending-collections?alert=true&limit=20")
    b.measure("pendientes", "summary", f"{S}/pending-collections/summary")
    b.measure("pendientes", "detail", f"{S}/pending-collections/{c['pending']}")

    b.measure("pagos", "payments p1", f"{S}/payments?limit=20")
    b.measure("pagos", "third party summary", f"{S}/payments/third-parties/{c['third_party']}/summary")
    b.measure("pagos", "expenses p1", f"{S}/expenses?limit=20")
    b.measure("pagos", "fuel loads p1", f"{S}/fuel-loads?limit=25")

    e = c["employee"]
    b.measure("empleados", "employees list", f"{EF}/employees?limit=25")
    b.measure("empleados", "employee (internal account)", f"{EF}/employees/{e}")
    b.measure("empleados", "movements p1", f"{EF}/employees/{e}/movements?limit=20&offset=0")
    b.measure("empleados", "movements last page", f"{EF}/employees/{e}/movements?limit=20&offset=400")
    b.measure("empleados", "loans", f"{EF}/employees/{e}/loans")
    b.measure("empleados", "loan detail", f"{EF}/loans/{c['loan']}")
    b.measure("empleados", "pay summary", f"{EF}/employees/{e}/pay-summary")
    b.measure("empleados", "payrolls list", f"{EF}/payrolls?limit=25")
    b.measure("empleados", "payroll detail (closed)", f"{EF}/payrolls/{c['payroll']}")
    b.measure("empleados", "payouts list", f"{EF}/payouts?limit=25")
    b.measure("empleados", "payout detail", f"{EF}/payouts/{c['payout']}")
    b.measure("empleados", "employees list", f"{EF}/employees?limit=25", "no-area", explain=False)


def daily_close_parts(b: Bench, c: dict[str, Any]) -> dict[str, Any]:
    """Service in process: each piece of the Cierre del día on its own, `--runs` times."""
    from sqlalchemy.orm import Session

    from app.db.session import get_engine
    from app.modules.settlements.daily_close import service as dc
    from app.modules.settlements.ledger import service as ledger

    def pieces(day: date) -> dict[str, Callable[[Session], Any]]:
        return {
            "day_flows": lambda db: ledger.day_flows(db, day, dc.KINDS),
            "opening balances (D-1)": lambda db: ledger.account_balances(
                db, on=day - timedelta(days=1), include_inactive=True
            ),
            "closing balances (D)": lambda db: ledger.account_balances(db, on=day, include_inactive=True),
            "accounts_existing_on": lambda db: ledger.accounts_existing_on(db, day, dc.KINDS),
            "settlements of the day": lambda db: dc._settlements(db, day),
            "cheques of the day": lambda db: dc._cheques(db, day),
            "whole service": lambda db: dc.daily_close(db, day, can_read_employee_finance=True),
        }

    out: dict[str, Any] = {}
    for label, day in (("last", c["last_day"]), ("mid", c["mid_day"])):
        parts = pieces(day)
        for name, call in parts.items():
            times = []
            with Session(get_engine()) as db:
                for _ in range(3):
                    call(db)
                    db.rollback()
                for _ in range(b.runs):
                    started = time.perf_counter()
                    call(db)
                    times.append((time.perf_counter() - started) * 1000)
                    db.rollback()
            out[f"{label}: {name}"] = {
                "median_ms": round(statistics.median(times), 2),
                "p95_ms": round(percentile(times, 0.95), 2),
                "max_ms": round(max(times), 2),
            }
            print(f"service {label:4} {name:28} p50={out[f'{label}: {name}']['median_ms']:7.2f}", flush=True)
    return out


def store_sys(b: Bench, c: dict[str, Any], repeats: int) -> list[dict[str, Any]]:
    """HTTP local, Store Sys import of 1, 50 and 200 records (the contract's limit; never more).

    - APPLIED: open deliveries of a new day get their first amount (the real daily path).
    - REJECTED_CLOSED: a new revision for closed settlements of the past (stored, refused to apply).
    - replay: the same request again (idempotent, nothing new stored).
    - not found: records of a day with no settlements (refused per record, nothing stored)."""
    from sqlalchemy import text

    from app.db.session import get_engine

    http = b.http["area"]
    with get_engine().connect() as conn:
        drivers = [
            (row[0], row[1])
            for row in conn.execute(
                text(
                    "select e.id, e.external_store_id from employees e where e.external_store_id is not null "
                    "and exists (select 1 from settlements.settlements s where s.employee_id = e.id) order by e.id"
                )
            )
        ]
        closed = [
            (row[0], row[1], row[2], row[3])
            for row in conn.execute(
                text(
                    "select external_reference, max(revision), min(business_date), min(employee_store_code) from ("
                    "select ev.external_reference, ev.revision, s.business_date, "
                    "e.external_store_id as employee_store_code from settlements.expected_amount_events ev "
                    "join settlements.settlements s on s.id = ev.settlement_id "
                    "join employees e on e.id = s.employee_id where ev.source = 'STORE_SYS' and s.type = 'DELIVERY' "
                    "and s.status = 'CLOSED') x group by external_reference order by min(business_date) desc limit 4000"
                )
            )
        ]
    out: list[dict[str, Any]] = []
    # New deliveries go on the day after the dataset (today: a business date is never in the future), several per
    # driver, told apart by their number; "not found" goes to a Sunday, when the dataset has no settlements.
    day = c["last_day"] + timedelta(days=1)
    sunday = c["last_day"] - timedelta(days=(c["last_day"].weekday() + 1) % 7)
    batch = 0
    used = 0

    def post(records: list[dict[str, Any]], request_id: str) -> tuple[float, int, float, dict[str, Any]]:
        with b.recorder.record() as recorded:
            started = time.perf_counter()
            response = http.post(
                f"{S}/imports/store-sys/expected-amounts",
                json={"source": "STORE_SYS", "client_request_id": request_id, "records": records},
            )
            elapsed = (time.perf_counter() - started) * 1000
        assert response.status_code == 200, response.text[:300]
        return elapsed, len(recorded), sum(e for _, _, e in recorded) * 1000, response.json()["counts"]

    for size in (1, 50, 200):
        series: dict[str, list[tuple[float, int, float]]] = {"APPLIED": [], "REJECTED_CLOSED": [], "replay": []}
        series["not found"] = []
        last_counts: dict[str, Any] = {}
        for _ in range(repeats):
            # APPLIED: `size` new deliveries on a new day, then one batch with their amounts.
            batch += 1
            records = []
            for n in range(size):
                employee_id, code = drivers[n % len(drivers)]
                created = http.post(
                    f"{S}/deliveries",
                    json={
                        "client_request_id": str(uuid.uuid4()),
                        "business_date": day.isoformat(),
                        "employee_id": employee_id,
                    },
                )
                assert created.status_code in (200, 201), created.text[:300]
                records.append(
                    {
                        "settlement_type": "DELIVERY",
                        "business_date": day.isoformat(),
                        "external_reference": f"BENCH-{day}-{size}-{batch}-{n}",
                        "revision": 1,
                        "employee_store_code": code,
                        "sequence_number": created.json()["sequence_number"],
                        "expected_amount": f"{100000 + n}.00",
                    }
                )
            request_id = str(uuid.uuid4())
            *measured, counts = post(records, request_id)
            series["APPLIED"].append(tuple(measured))  # type: ignore[arg-type]
            last_counts["APPLIED"] = counts
            *measured, counts = post(records, request_id)
            series["replay"].append(tuple(measured))  # type: ignore[arg-type]
            last_counts["replay"] = counts
            # REJECTED_CLOSED: a new revision for `size` closed deliveries of the past.
            chunk = closed[used : used + size]
            used += size
            records = [
                {
                    "settlement_type": "DELIVERY",
                    "business_date": when.isoformat(),
                    "external_reference": reference,
                    "revision": revision + 1,
                    "employee_store_code": code,
                    "expected_amount": "123456.00",
                }
                for reference, revision, when, code in chunk
            ]
            *measured, counts = post(records, str(uuid.uuid4()))
            series["REJECTED_CLOSED"].append(tuple(measured))  # type: ignore[arg-type]
            last_counts["REJECTED_CLOSED"] = counts
            far = sunday.isoformat()
            records = [
                {**r, "business_date": far, "external_reference": f"NF-{far}-{i}"} for i, r in enumerate(records)
            ]
            *measured, counts = post(records, str(uuid.uuid4()))
            series["not found"].append(tuple(measured))  # type: ignore[arg-type]
            last_counts["not found"] = counts
        for path, values in series.items():
            times = [v[0] for v in values]
            row = {
                "records": size,
                "path": path,
                "runs": len(times),
                "median_ms": round(statistics.median(times), 1),
                "p95_ms": round(percentile(times, 0.95), 1),
                "max_ms": round(max(times), 1),
                "ms_per_record": round(statistics.median(times) / size, 2),
                "queries": round(statistics.median(v[1] for v in values)),
                "queries_per_record": round(statistics.median(v[1] for v in values) / size, 1),
                "sql_ms": round(statistics.median(v[2] for v in values), 1),
                "counts": last_counts.get(path),
            }
            out.append(row)
            print(f"store-sys {size:3} {path:16} p50={row['median_ms']:8.1f} q={row['queries']} {row['counts']}")
    return out


def writes(b: Bench, c: dict[str, Any], repeats: int) -> list[dict[str, Any]]:
    """HTTP local, a payment in cash out of Caja principal: a debit of the busiest box, which checks that no day from
    its date on goes negative (in the app and in the database trigger). Dated today and 90 days back."""
    http = b.http["area"]
    out = []
    for label, day in (
        ("today", c["last_day"] + timedelta(days=1)),
        ("90 days back", c["last_day"] - timedelta(days=90)),
    ):
        times, counts, sql = [], [], []
        for n in range(repeats):
            body = {
                "client_request_id": str(uuid.uuid4()),
                "business_date": day.isoformat(),
                "third_party_id": c["third_party"],
                "concept": "MERCHANDISE",
                "reference": f"BENCH-{label}-{n}",
                "items": [{"method": "CASH", "account_id": c["main"], "amount": "10.00"}],
            }
            with b.recorder.record() as recorded:
                started = time.perf_counter()
                response = http.post(f"{S}/payments", json=body)
                times.append((time.perf_counter() - started) * 1000)
            assert response.status_code in (200, 201), response.text[:300]
            counts.append(len(recorded))
            sql.append(sum(e for _, _, e in recorded) * 1000)
        row = {
            "case": f"payment in cash, {label}",
            "runs": repeats,
            "median_ms": round(statistics.median(times), 1),
            "p95_ms": round(percentile(times, 0.95), 1),
            "max_ms": round(max(times), 1),
            "queries": round(statistics.median(counts)),
            "sql_ms": round(statistics.median(sql), 1),
        }
        out.append(row)
        print(
            f"write {row['case']:32} p50={row['median_ms']:7.1f} p95={row['p95_ms']:7.1f} q={row['queries']}",
            flush=True,
        )
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=30)
    parser.add_argument("--explain-top", type=int, default=2, help="slowest statements of each read to EXPLAIN")
    parser.add_argument("--store-sys-repeats", type=int, default=5, help="0 skips the import (it writes)")
    parser.add_argument("--writes", type=int, default=20, help="payments to time per case; 0 skips them (they write)")
    parser.add_argument("--only", default="", help="regex over 'group name' to run a subset of the reads")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    url = use_perf_database()
    with contextlib.suppress(subprocess.CalledProcessError):  # already there from an earlier run
        cli("create-admin", "--email", OPERATOR, "--name", "Perf sin área", password=PASSWORD)
    b = Bench(args.runs, args.out, args.explain_top)
    b.profile("area", ADMIN)
    b.profile("no-area", OPERATOR)
    c = context()
    print(json.dumps({k: v for k, v in c.items() if k != "volume"}, default=str))
    if args.only:
        wanted = re.compile(args.only)
        measure = b.measure

        def only(group: str, name: str, *rest: Any, **kw: Any) -> Any:
            return measure(group, name, *rest, **kw) if wanted.search(f"{group} {name}") else None

        b.measure = only  # type: ignore[method-assign]
    reads(b, c)
    b.measure = Bench.measure.__get__(b)  # type: ignore[method-assign]
    service = daily_close_parts(b, c) if not args.only else {}
    imports = store_sys(b, c, args.store_sys_repeats) if args.store_sys_repeats else []
    payments = writes(b, c, args.writes) if args.writes else []
    b.save(
        {
            "database": url.rsplit("/", 1)[-1],
            "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "runs": args.runs,
            "context": {k: v for k, v in c.items() if k != "volume"},
            "volume": c["volume"],
            "service": service,
            "store_sys": imports,
            "writes": payments,
            "today": date.today(),
        }
    )


if __name__ == "__main__":
    main()
