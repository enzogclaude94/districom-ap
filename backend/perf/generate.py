"""R9-4 dataset generator: one year of a distributor's Rendiciones, reproducible, in a separate database.

    PERF_DATABASE_URL=postgresql+psycopg://districom:districom@localhost:5432/districom_perf \
        uv run python -m perf.generate --scale 1 --seed 2026

Everything goes through the app's own HTTP API, in process (FastAPI TestClient), as an admin with the Store Sys and
Empleados areas: every row is one the real system would write, through the ledger's single door, with its audit,
idempotency and invariants. Nothing is inserted by hand. The same seed and scale give the same business data.

`--scale` multiplies the people and the activity of one year (drivers, bocas, suppliers, payments); scale 1 is a
medium distributor. Business dates run from 365 days ago to yesterday; Sundays have no activity.
"""

import argparse
import multiprocessing
import random
import re
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from perf.harness import PASSWORD, cli, client, login, use_perf_database

EMAIL = "perf-admin@districom.local"
EF = "/api/v1/settlements/employee-finance"
S = "/api/v1/settlements"


@dataclass
class World:
    """What the generator created and still has to act on."""

    rnd: random.Random
    worker: int = 1
    cheques: int = 0
    main: int = 0
    change: int = 0
    bank: int = 0
    categories: list[int] = field(default_factory=list)
    fuel: int = 0
    drivers: list[dict[str, Any]] = field(default_factory=list)
    outlets: list[dict[str, Any]] = field(default_factory=list)
    suppliers: list[int] = field(default_factory=list)
    galicia: int = 0
    denominations: list[tuple[str, int]] = field(default_factory=list)
    pendings: dict[date, list[tuple[int, int]]] = field(default_factory=lambda: defaultdict(list))
    portfolio: list[int] = field(default_factory=list)
    deposited: dict[date, list[int]] = field(default_factory=lambda: defaultdict(list))
    corrections: dict[date, list[dict[str, Any]]] = field(default_factory=lambda: defaultdict(list))
    calls: int = 0
    spent: dict[str, list[float]] = field(default_factory=lambda: defaultdict(lambda: [0, 0.0]))


class Api:
    def __init__(self, world: World) -> None:
        self.http = client()
        self.world = world

    def __call__(self, method: str, path: str, body: Any = None, ok: tuple[int, ...] = (200, 201)) -> Any:
        self.world.calls += 1
        started = time.perf_counter()
        response = self.http.request(method, path, json=body)
        spent = self.world.spent[f"{method} {re.sub(r'/\d+', '/{id}', path)}"]
        spent[0] += 1
        spent[1] += time.perf_counter() - started
        if response.status_code not in ok:
            raise RuntimeError(f"{method} {path} -> {response.status_code}: {response.text[:500]}")
        return response.json() if response.content else None


def rid() -> str:
    return str(uuid.uuid4())


def money(amount: int) -> str:
    return f"{amount}.00"


def round_to(amount: float, step: int) -> int:
    return max(step, int(amount) // step * step)


# --- setup -------------------------------------------------------------------------------------------------------


def shared_setup(api: Api, scale: int, start: date) -> None:
    """Once, before the workers: expense categories and the opening balances of Caja principal and Banco."""
    for name in ("Peajes", "Mantenimiento", "Librería", "Limpieza"):
        api("POST", f"{S}/expense-categories", {"name": name})
    by_code = {a["code"]: a["id"] for a in api("GET", f"{S}/accounts") if a["code"]}
    for account, amount in ((by_code["main_cash"], 30_000_000 * scale), (by_code["bank"], 80_000_000 * scale)):
        api(
            "POST",
            f"{S}/operations/opening-balance",
            {
                "client_request_id": rid(),
                "business_date": (start - timedelta(days=1)).isoformat(),
                "account_id": account,
                "amount": money(amount),
            },
        )


def setup(api: Api, world: World, scale: int, start: date, worker: int) -> None:
    """A worker's own people and places: drivers (with Store Sys code, salary and truck), bocas and suppliers."""
    rnd = world.rnd
    by_code = {a["code"]: a["id"] for a in api("GET", f"{S}/accounts") if a["code"]}
    world.main, world.change, world.bank = by_code["main_cash"], by_code["change_cash"], by_code["bank"]
    categories = api("GET", f"{S}/expense-categories")
    world.fuel = next(c["id"] for c in categories if c["name"] == "Combustible")
    world.categories = [c["id"] for c in categories if c["name"] != "Combustible"]
    world.galicia = next(b["id"] for b in api("GET", "/api/v1/financial-institutions") if b["bcra_code"] == 7)
    world.denominations = sorted(
        ((d["code"], int(float(d["value"]))) for d in api("GET", f"{S}/cash-denominations") if d["active"]),
        key=lambda d: -d[1],
    )
    for n in range(12 * scale):
        tag = f"{worker}{n + 1:03d}"
        employee = api(
            "POST", "/api/v1/employees", {"full_name": f"Repartidor {tag}", "hire_date": None, "phone": None}
        )
        api(
            "POST",
            f"/api/v1/employees/{employee['id']}/functions",
            {"function_code": "settlements.delivery_driver", "valid_from": "2020-01-01"},
        )
        code = f"SS{tag}"
        api("PUT", f"{S}/store-sys/employee-codes/{employee['id']}", {"store_code": code})
        api(
            "POST",
            f"{EF}/employees/{employee['id']}/compensation-terms",
            {
                "valid_from": start.replace(day=1).isoformat(),
                "base_salary": money(rnd.randrange(800, 1400) * 1000),
                "notes": None,
            },
        )
        vehicle = api("POST", "/api/v1/vehicles", {"plate": f"AA{tag}PF", "description": f"Camión {tag}"})
        world.drivers.append({"id": employee["id"], "code": code, "vehicle": vehicle["id"]})
    for n in range(3 * scale):
        code = f"B{worker}{n + 1:03d}"
        outlet = api("POST", "/api/v1/outlets", {"code": code, "name": f"Boca {code}", "description": None})
        world.outlets.append({"id": outlet["id"], "code": code})
    for n in range(40 * scale):
        third = api(
            "POST", "/api/v1/third-parties", {"name": f"Proveedor {worker}{n + 1:04d}", "tax_id": None, "notes": None}
        )
        world.suppliers.append(third["id"])


# --- a delivery ----------------------------------------------------------------------------------------------------


def count_lines(world: World, cash: int) -> list[dict[str, Any]]:
    """Cash in notes, the big ones to Caja principal and the small ones to Caja de cambio."""
    lines = []
    for code, value in world.denominations:
        quantity, cash = divmod(cash, value)
        if quantity:
            box = world.main if value >= 1000 else world.change
            lines.append({"denomination_code": code, "quantity": quantity, "destination_account_id": box})
    return lines


def allocations(world: World, preview: dict[str, Any], driver: int) -> list[dict[str, Any]]:
    kind, amount = preview["difference_kind"], preview["amount_to_allocate"]
    if kind == "SHORTAGE":
        return [{"target": "EMPLOYEE", "employee_id": driver, "amount": amount}]
    if kind == "SURPLUS":
        return [{"target": "COMPANY", "amount": amount, "reason": "Sobrante del día"}]
    return []


def close(api: Api, world: World, path: str, driver: int | None) -> None:
    preview = api("GET", f"{path}/closing-preview")
    parts = allocations(world, preview, driver) if driver else []
    if not driver and preview["difference_kind"] in ("SHORTAGE", "SURPLUS"):
        parts = [{"target": "COMPANY", "amount": preview["amount_to_allocate"], "reason": "Diferencia de la boca"}]
    api(
        "POST",
        f"{path}/close",
        {
            "version": preview["version"],
            "preview_token": preview["preview_token"],
            "allocations": parts,
            "confirm": True,
        },
    )


def delivery(api: Api, world: World, day: date, driver: dict[str, Any], expected: int) -> dict[str, Any]:
    rnd = world.rnd
    created = api(
        "POST",
        f"{S}/deliveries",
        {"client_request_id": rid(), "business_date": day.isoformat(), "employee_id": driver["id"]},
    )
    path = f"{S}/deliveries/{created['id']}"
    rest = expected
    for _ in range(rnd.choice((0, 1, 1, 2, 2, 3))):
        amount = round_to(expected * rnd.uniform(0.04, 0.15), 100)
        transfer = api(
            "POST",
            f"{path}/transfers",
            {
                "client_request_id": rid(),
                "amount": money(amount),
                "bank_account_id": world.bank,
                "reference": None,
                "payer_name": "Cliente",
            },
        )
        verified = rnd.random() < 0.95
        api(
            "POST",
            f"{path}/transfers/{transfer['transfers'][-1]['id']}/status",
            {"status": "VERIFIED" if verified else "REJECTED", "reason": None if verified else "No se acreditó"},
        )
        rest -= amount if verified else 0
    if rnd.random() < 0.25:
        amount = round_to(expected * rnd.uniform(0.05, 0.2), 100)
        world.cheques += 1
        loaded = api(
            "POST",
            f"{path}/cheques",
            {
                "client_request_id": rid(),
                "kind": "PHYSICAL",
                "payment_type": "COMMON",
                "amount": money(amount),
                "issuer_bank_id": world.galicia,
                "cheque_number": f"{world.worker}{world.cheques:07d}",  # unique: never a duplicate to confirm
                "issue_date": day.isoformat(),
                "drawer_name": "Librador",
                "delivered_by_name": "Cliente",
                "physical_receipt_confirmed": True,
                "received_by_employee_id": driver["id"],
            },
        )
        world.portfolio.append(loaded["cheques"][-1]["id"])
        rest -= amount
    if rnd.random() < 0.3:
        liters, price = rnd.randrange(20, 120), rnd.randrange(900, 1300)
        api(
            "POST",
            f"{path}/expenses",
            {
                "client_request_id": rid(),
                "category_id": world.fuel,
                "description": None,
                "fuel": {
                    "vehicle_id": driver["vehicle"],
                    "employee_id": driver["id"],
                    "liters": str(liters),
                    "price_per_liter": str(price),
                    "odometer_km": None,
                },
            },
        )
        rest -= liters * price
    if rnd.random() < 0.1:
        amount = round_to(rnd.uniform(2000, 30000), 100)
        api(
            "POST",
            f"{path}/expenses",
            {
                "client_request_id": rid(),
                "category_id": rnd.choice(world.categories),
                "amount": money(amount),
                "description": "Gasto de ruta",
            },
        )
        rest -= amount
    if rnd.random() < 0.3:
        amount = round_to(expected * rnd.uniform(0.03, 0.12), 100)
        loaded = api(
            "POST",
            f"{path}/pending-collections",
            {
                "client_request_id": rid(),
                "customer_name": f"Cliente {rnd.randrange(1, 400 * len(world.drivers) // 12 + 1):05d}",
                "amount": money(amount),
                "reason": "Paga después",
                "responsible_employee_id": driver["id"],
                "expected_date": None,
            },
        )
        world.pendings[day + timedelta(days=rnd.randrange(1, 12))].append(
            (loaded["pending_collections"][-1]["id"], amount)
        )
        rest -= amount
    roll = rnd.random()
    if roll < 0.10:
        rest -= round_to(rnd.uniform(1000, 20000), 100)  # a shortage
    elif roll < 0.15:
        rest += round_to(rnd.uniform(1000, 5000), 100)  # a surplus
    api("PUT", f"{path}/count", {"lines": count_lines(world, max(rest, 0) // 100 * 100)})
    return {"id": created["id"], "path": path, "driver": driver}


# --- the year ------------------------------------------------------------------------------------------------------


def store_sys(api: Api, day: date, records: list[dict[str, Any]], revision: int = 1) -> None:
    for start in range(0, len(records), 200):  # the contract's limit (R9-1): never more than 200 per request
        batch = [{"business_date": day.isoformat(), "revision": revision, **r} for r in records[start : start + 200]]
        api(
            "POST",
            f"{S}/imports/store-sys/expected-amounts",
            {"source": "STORE_SYS", "client_request_id": rid(), "records": batch},
        )


def a_day(api: Api, world: World, day: date, scale: int, workers: int = 1) -> None:
    rnd = world.rnd
    iso = day.isoformat()
    # Deliveries open without their expected amount; Store Sys reports them in one batch, then they close.
    opened, records = [], []
    for driver in world.drivers:
        expected = round_to(rnd.uniform(300_000, 1_500_000), 100)
        opened.append(delivery(api, world, day, driver, expected))
        records.append(
            {
                "settlement_type": "DELIVERY",
                "external_reference": f"LIQ-{iso}-{driver['code']}",
                "employee_store_code": driver["code"],
                "expected_amount": money(expected),
            }
        )
    # Bocas: Store Sys too, with two remittances, a transfer and the final count.
    outlet_paths = []
    for outlet in world.outlets:
        expected = round_to(rnd.uniform(400_000, 1_200_000), 100)
        created = api(
            "POST",
            f"{S}/outlet-settlements",
            {"client_request_id": rid(), "business_date": iso, "outlet_id": outlet["id"], "confirm_additional": False},
        )
        path = f"{S}/outlet-settlements/{created['id']}"
        records.append(
            {
                "settlement_type": "OUTLET",
                "external_reference": f"BOCA-{iso}-{outlet['code']}",
                "outlet_code": outlet["code"],
                "expected_amount": money(expected),
            }
        )
        rest = expected
        for hour in ("12:00", "17:00"):
            amount = round_to(expected * rnd.uniform(0.25, 0.35), 1000)
            api(
                "POST",
                f"{path}/remittances",
                {
                    "client_request_id": rid(),
                    "amount": money(amount),
                    "destination_account_id": world.main,
                    "delivered_at": f"{iso}T{hour}:00-03:00",
                    "confirm_above_expected": True,
                },
            )
            rest -= amount
        amount = round_to(expected * rnd.uniform(0.05, 0.15), 100)
        transfer = api(
            "POST",
            f"{path}/transfers",
            {
                "client_request_id": rid(),
                "amount": money(amount),
                "bank_account_id": world.bank,
                "reference": None,
                "payer_name": None,
            },
        )
        api(
            "POST", f"{path}/transfers/{transfer['transfers'][-1]['id']}/status", {"status": "VERIFIED", "reason": None}
        )
        rest -= amount
        if rnd.random() < 0.1:
            rest -= round_to(rnd.uniform(500, 5000), 100)
        api("PUT", f"{path}/count", {"lines": count_lines(world, max(rest, 0) // 100 * 100)})
        outlet_paths.append(path)
    store_sys(api, day, records)
    for d in opened:
        close(api, world, d["path"], d["driver"]["id"])
        if rnd.random() < 0.03:
            world.corrections[day + timedelta(days=rnd.randrange(1, 4))].append({**d, "day": day})
    for path in outlet_paths:
        close(api, world, path, None)

    # Pendings due today: most paid in full, some in two parts, a few resolved against the driver.
    for pending_id, amount in world.pendings.pop(day, []):
        roll = rnd.random()
        if roll < 0.75:
            cash = rnd.random() < 0.6
            api(
                "POST",
                f"{S}/pending-collections/{pending_id}/receipts",
                {
                    "client_request_id": rid(),
                    "amount": money(amount),
                    "method": "CASH" if cash else "TRANSFER",
                    "destination_account_id": world.main if cash else world.bank,
                    "business_date": iso,
                },
            )
        elif roll < 0.92:
            first = round_to(amount / 2, 100)
            api(
                "POST",
                f"{S}/pending-collections/{pending_id}/receipts",
                {
                    "client_request_id": rid(),
                    "amount": money(first),
                    "method": "CASH",
                    "destination_account_id": world.main,
                    "business_date": iso,
                },
            )
            world.pendings[day + timedelta(days=rnd.randrange(1, 8))].append((pending_id, amount - first))
        else:
            detail = api("GET", f"{S}/pending-collections/{pending_id}")
            api(
                "POST",
                f"{S}/pending-collections/{pending_id}/resolutions",
                {
                    "client_request_id": rid(),
                    "amount": money(amount),
                    "reason": "No pagó",
                    "allocations": [
                        {
                            "target": "EMPLOYEE",
                            "employee_id": detail["responsible_employee_id"],
                            "amount": money(amount),
                        }
                    ],
                    "business_date": iso,
                },
            )

    # Cheques: deposited on Mondays and Thursdays, credited two days later; a few come back rejected.
    if day.weekday() in (0, 3) and world.portfolio:
        api(
            "POST",
            f"{S}/cheque-deposits",
            {
                "client_request_id": rid(),
                "bank_account_id": world.bank,
                "cheque_ids": world.portfolio,
                "business_date": iso,
            },
        )
        world.deposited[day + timedelta(days=2)].extend(world.portfolio)
        world.portfolio = []
    due = world.deposited.pop(day, [])
    rejected = [c for c in due if rnd.random() < 0.03]
    credited = [c for c in due if c not in rejected]
    if credited:
        api(
            "POST",
            f"{S}/cheque-credits",
            {"client_request_id": rid(), "bank_account_id": world.bank, "cheque_ids": credited, "business_date": iso},
        )
    for cheque in rejected:
        api(
            "POST",
            f"{S}/cheques/{cheque}/rejection",
            {
                "client_request_id": rid(),
                "reason": "Sin fondos",
                "business_date": iso,
                "responsible_employee_id": rnd.choice(world.drivers)["id"],
            },
        )

    # Payments to suppliers and direct expenses.
    for _ in range(8 * scale):
        amount = round_to(rnd.uniform(20_000, 500_000), 100)
        cash = rnd.random() < 0.4
        api(
            "POST",
            f"{S}/payments",
            {
                "client_request_id": rid(),
                "business_date": iso,
                "third_party_id": rnd.choice(world.suppliers),
                "concept": "MERCHANDISE",
                "reference": f"FC-{rnd.randrange(10**6)}",
                "items": [
                    {
                        "method": "CASH" if cash else "TRANSFER",
                        "account_id": world.main if cash else world.bank,
                        "amount": money(amount),
                    }
                ],
            },
        )
    for _ in range(2 * scale):
        api(
            "POST",
            f"{S}/expenses",
            {
                "client_request_id": rid(),
                "business_date": iso,
                "category_id": rnd.choice(world.categories),
                "paid_from_account_id": rnd.choice((world.main, world.bank)),
                "amount": money(round_to(rnd.uniform(5000, 150_000), 100)),
            },
        )

    # Employees: advances and consumptions through the month.
    for driver in world.drivers:
        if rnd.random() < 2 / 26:
            api(
                "POST",
                f"{EF}/employees/{driver['id']}/advances",
                {
                    "client_request_id": rid(),
                    "business_date": iso,
                    "amount": money(round_to(rnd.uniform(30_000, 150_000), 1000)),
                    "description": "Adelanto",
                    "funding_account_id": world.main,
                },
            )
        if rnd.random() < 1 / 26:
            api(
                "POST",
                f"{EF}/employees/{driver['id']}/consumptions",
                {
                    "client_request_id": rid(),
                    "business_date": iso,
                    "amount": money(round_to(rnd.uniform(5_000, 40_000), 100)),
                    "description": "Mercadería",
                },
            )
        if rnd.random() < 1 / 313:
            api(
                "POST",
                f"{EF}/employees/{driver['id']}/loans",
                {
                    "client_request_id": rid(),
                    "business_date": iso,
                    "principal": money(round_to(rnd.uniform(200_000, 900_000), 1000)),
                    "funding_account_id": world.bank,
                    "installment_count": 6,
                    "first_due_on": iso,
                },
            )

    # Corrections a few days later: reopen, Store Sys sends a revision while it is corrected, count again, close.
    for c in world.corrections.pop(day, []):
        current = api("GET", c["path"])
        api("POST", f"{c['path']}/reopen", {"version": current["version"], "reason": "Recuento"})
        store_sys(
            api,
            c["day"],
            [
                {
                    "settlement_type": "DELIVERY",
                    "external_reference": f"LIQ-{c['day'].isoformat()}-{c['driver']['code']}",
                    "employee_store_code": c["driver"]["code"],
                    "expected_amount": money(int(float(current["expected_amount"])) + 1000),
                }
            ],
            revision=2,
        )
        reloaded = api("GET", c["path"])
        if reloaded.get("expected_pending"):
            api("POST", f"{S}/expected-updates/{reloaded['expected_pending']['event_id']}/accept")
        lines = [{**line_in(line), "quantity": line["quantity"]} for line in reloaded["count_lines"]]
        if lines:
            lines[0]["quantity"] += 1  # the recount found one more note
        api("PUT", f"{c['path']}/count", {"lines": lines})
        close(api, world, c["path"], c["driver"]["id"])

    # Cash to the bank at the end of the day, keeping a float; each worker deposits its share of the excess.
    balance = int(float(next(a for a in api("GET", f"{S}/accounts") if a["id"] == world.main)["balance"]))
    keep = 25_000_000 * scale * workers
    if balance > keep:
        share = (balance - keep) // workers // 100 * 100
        api(
            "POST",
            f"{S}/operations/bank-deposit",
            {
                "client_request_id": rid(),
                "business_date": iso,
                "cash_account_id": world.main,
                "bank_account_id": world.bank,
                "amount": money(share),
            },
        )


def line_in(line: dict[str, Any]) -> dict[str, Any]:
    return {"denomination_code": line["denomination_code"], "destination_account_id": line["destination_account_id"]}


def month_end(api: Api, world: World, first: date, last: date) -> None:
    """The REGULAR payroll of the month for every driver, closed and paid half in cash and half by transfer."""
    for driver in world.drivers:
        payroll = api(
            "POST",
            f"{EF}/payrolls",
            {
                "client_request_id": rid(),
                "employee_id": driver["id"],
                "kind": "REGULAR",
                "period_start": first.isoformat(),
                "period_end": last.isoformat(),
                "payment_date": last.isoformat(),
            },
        )
        hint = payroll["salary_hint"]
        api(
            "POST",
            f"{EF}/payrolls/{payroll['id']}/lines",
            {
                "kind": "SALARY",
                "amount": hint.get("amount") or "900000.00",
                # A partial first month makes even the proposed amount a hand-loaded one: it always carries its note.
                "description": "Sueldo del mes",
            },
        )
        current = api("GET", f"{EF}/payrolls/{payroll['id']}")
        api(
            "POST",
            f"{EF}/payrolls/{payroll['id']}/close",
            {"version": current["version"], "preview_token": current["preview"]["preview_token"], "confirm": True},
        )
        closed = api("GET", f"{EF}/payrolls/{payroll['id']}")
        net = float(closed["payment"]["pending"])
        if net <= 0:
            continue
        cash = round(net / 2, 2)
        api(
            "POST",
            f"{EF}/payrolls/{payroll['id']}/payouts",
            {
                "client_request_id": rid(),
                "payroll_closing_id": closed["closing"]["id"],
                "business_date": last.isoformat(),
                "items": [
                    {"method": "CASH", "account_id": world.main, "amount": f"{cash:.2f}"},
                    {"method": "TRANSFER", "account_id": world.bank, "amount": f"{net - cash:.2f}"},
                ],
            },
        )


def run_worker(worker: int, workers: int, scale: int, seed: int, start: date, today: date) -> dict[str, list[float]]:
    """One worker's year: its own drivers, bocas and suppliers, day after day, with its own seed."""
    use_perf_database()
    world = World(rnd=random.Random(seed * 100 + worker), worker=worker)
    api = Api(world)
    login(api.http, EMAIL)
    setup(api, world, scale, start, worker)
    started = time.perf_counter()
    day = start
    while day < today:
        if day.weekday() != 6:
            a_day(api, world, day, scale, workers)
        tomorrow = day + timedelta(days=1)
        if tomorrow.day == 1 and tomorrow <= today:
            month_end(api, world, max(day.replace(day=1), start), day)
        if day.day == 1:
            print(f"worker {worker} {day} calls={world.calls} {time.perf_counter() - started:.0f}s", flush=True)
        day = tomorrow
    print(f"worker {worker} done: {world.calls} calls in {time.perf_counter() - started:.0f}s", flush=True)
    return dict(world.spent)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--scale", type=int, default=1, help="people and activity of each worker (1 = medium distributor)"
    )
    parser.add_argument("--workers", type=int, default=1, help="independent workers; the dataset is scale x workers")
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--days", type=int, default=365)
    args = parser.parse_args()
    use_perf_database()
    from app.core.clock import business_today

    cli("create-admin", "--email", EMAIL, "--name", "Perf", password=PASSWORD)
    world = World(rnd=random.Random(args.seed))
    api = Api(world)
    login(api.http, EMAIL)
    for area in ("settlements.store_sys_import", "settlements.employee_finance"):
        api("PUT", "/api/v1/auth/access-grants", {"email": EMAIL, "key": area, "role": "admin"})
    today = business_today()
    start = today - timedelta(days=args.days)
    shared_setup(api, args.scale * args.workers, start)

    started = time.perf_counter()
    jobs = [(w + 1, args.workers, args.scale, args.seed, start, today) for w in range(args.workers)]
    if args.workers == 1:
        results = [run_worker(*jobs[0])]
    else:
        with multiprocessing.get_context("spawn").Pool(args.workers) as pool:
            results = pool.starmap(run_worker, jobs)
    total: dict[str, list[float]] = defaultdict(lambda: [0, 0.0])
    for spent in results:
        for name, (count, seconds) in spent.items():
            total[name][0] += count
            total[name][1] += seconds
    calls = sum(int(count) for count, _ in total.values())
    print(f"done: {calls} calls in {time.perf_counter() - started:.0f}s (scale {args.scale} x {args.workers} workers)")
    for name, (count, seconds) in sorted(total.items(), key=lambda kv: -kv[1][1])[:25]:
        print(f"{seconds:8.1f}s {int(count):7d} calls {seconds / count * 1000:7.1f} ms  {name}")


if __name__ == "__main__":
    main()
