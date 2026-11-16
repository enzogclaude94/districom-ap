"""AXUM · Compró / No compró (`commercial.axum.no_purchase`, ADR 018, docs/IMPORTS.md §3.6, §5.4).

Contract: the XLSX AXUM exports, sheet `Data`. Header in row 1: (empty), Cliente Id, Nombre, Direccion, Compro?,
Fecha y Hora Subida, Hora Ini., Hora Fin., Minutos. Rows are grouped by salesperson: a row `Vendedor: NN` in the
first column opens each group, and a blank row closes it. Every cell is text.

- A row whose `Compro?` is `Si` is a purchase, not a no-purchase: it stays in the staging, `skipped`, with an info
  message, and creates nothing. Visits are never modeled nor inferred from this report.
- Any other `Compro?` is the no-purchase reason, kept as written (`reason_source`) and categorized only by an exact
  `no_purchase_reason_mappings` entry; a new reason arrives without category, with an issue.
- The event's day is the day of `Fecha y Hora Subida` (the only full date). The report's times are kept as they
  come; a visit that starts after the upload keeps the upload's day, with a warning.
- Customer (`Cliente Id`), in this order: its own `customer/AXUM/customer` mapping; else, while the admin rule
  `commercial.axum_customer_code_matches_store_sys` is on, the STORE SYS customer with the same code **and** the
  same name (display or legal name) → the AXUM mapping is created `confirmed` by `rule`; a different name → pending
  with that customer as the only candidate; an unknown code → pending. Never by name alone, never fuzzy, never a
  new customer.
- Salesperson: the group's code, by `employee/AXUM/salesperson` only (pending until someone confirms it); never the
  STORE SYS sellers, never a new employee.
- Identical rows are separate events (`occurrence`); a period already loaded always waits for an admin
  (`no_purchase.service.plan`).

Closed against the real report of October 2026 (2761 rows, 26 days, docs/IMPORTS.md §3.6).
"""

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.commercial.config.models import NoPurchaseReasonCategory, NoPurchaseReasonMapping
from app.modules.commercial.config.service import axum_customer_code_matches_store_sys, ensure_reason, source_key
from app.modules.commercial.imports import cells
from app.modules.commercial.no_purchase import service as no_purchase
from app.modules.commercial.no_purchase.models import NoPurchaseEvent
from app.platform.audit.service import record_event
from app.platform.customers.models import Customer
from app.platform.imports.headers import Column, find_header
from app.platform.imports.models import ImportJob
from app.platform.imports.registry import (
    Analysis,
    AnalyzeContext,
    AnalyzedRow,
    ConfirmationBlockedError,
    ConfirmContext,
    RowMessage,
    RowOutcome,
    StagedRow,
    StructuralError,
)
from app.platform.imports.workbook import Cell, RawRow, Workbook
from app.platform.mappings import service as mappings
from app.platform.mappings.models import ExternalEntityMapping
from app.platform.quality.service import record_issue

TYPE_KEY = "commercial.axum.no_purchase"
SOURCE = "AXUM"
ENTITY = "commercial.no_purchase_event"

COLUMNS = (
    Column("customer_code", ("cliente id", "id cliente")),
    Column("customer_name", ("nombre",)),
    Column("address", ("direccion", "dirección")),
    Column("result", ("compro", "compró")),
    Column("uploaded_at", ("fecha y hora subida",)),
    Column("start_time", ("hora ini", "hora inicio")),
    Column("end_time", ("hora fin",)),
    Column("minutes", ("minutos",)),
)

GROUP = re.compile(r"^\s*VENDEDOR\s*:\s*(\S.*?)\s*$", re.IGNORECASE)
MOMENT = re.compile(r"^(\d{2})/(\d{2})/(\d{4}) (\d{2}):(\d{2}):(\d{2})$")
CLOCK = re.compile(r"^(\d{1,2}):(\d{2}):(\d{2})$")
DURATION = re.compile(r"^(\d{1,4}):(\d{1,2})$")
PURCHASED = "SI"
PURCHASE_TEXT = "Compró: la fila queda trazable pero no genera un evento de no compra."

# How the customer of a row resolves (analysis predicts it, the confirmation applies it).
MAPPED, PENDING, BY_RULE, NAME_MISMATCH, UNKNOWN_CODE = "mapped", "pending", "by_rule", "name_mismatch", "unknown_code"


def customer_key(code: str) -> mappings.MappingKey:
    return mappings.MappingKey("customer", SOURCE, "customer", mappings.code_key(code))


def store_sys_customer_key(code: str) -> mappings.MappingKey:
    return mappings.MappingKey("customer", "STORE_SYS", "customer", mappings.code_key(code))


def salesperson_key(code: str) -> mappings.MappingKey:
    return mappings.MappingKey("employee", SOURCE, "salesperson", mappings.code_key(code))


def group_salesperson(row: RawRow) -> str | None:
    """The salesperson code of a `Vendedor: NN` row (first cell, every other cell empty), else None."""
    if not row.cells or not isinstance(row.cells[0], str):
        return None
    if any(c is not None and not (isinstance(c, str) and not c.strip()) for c in row.cells[1:]):
        return None
    match = GROUP.match(row.cells[0])
    return mappings.code_key(match.group(1)) if match else None


def _moment(cell: Cell) -> datetime | None:
    if cell is None or (isinstance(cell, str) and not cell.strip()):
        return None
    if isinstance(cell, datetime):
        return cell
    if isinstance(cell, str):
        match = MOMENT.match(cell.strip())
        if match:
            day, month, year, hour, minute, second = (int(g) for g in match.groups())
            try:
                return datetime(year, month, day, hour, minute, second)
            except ValueError:
                pass
    raise cells.InvalidCellError("datetime")


def _clock(cell: Cell) -> time | None:
    if cell is None or (isinstance(cell, str) and not cell.strip()):
        return None
    if isinstance(cell, datetime):
        return cell.time()
    if isinstance(cell, str):
        match = CLOCK.match(cell.strip())
        if match:
            hour, minute, second = (int(g) for g in match.groups())
            try:
                return time(hour, minute, second)
            except ValueError:
                pass
    raise cells.InvalidCellError("time")


def _duration(cell: Cell) -> int | None:
    if cell is None or (isinstance(cell, str) and not cell.strip()):
        return None
    match = DURATION.match(cells.text(cell) or "")
    if match is None or int(match.group(2)) > 59:
        raise cells.InvalidCellError("duration")
    return int(match.group(1)) * 60 + int(match.group(2))


def _seconds(value: time) -> int:
    return value.hour * 3600 + value.minute * 60 + value.second


def _parse(cell: cells.CellGetter, salesperson: str | None) -> tuple[dict[str, Any] | None, list[RowMessage]]:
    """A data row: None plus errors when it cannot be an event; the record with its warnings otherwise. A purchase
    row (`Si`) parses too: the caller skips it."""
    messages: list[RowMessage] = []
    if salesperson is None:
        messages.append(cells.error("missing_salesperson", "La fila no está dentro de un grupo «Vendedor».", None))
    try:
        code = cells.code(cell("customer_code"))
    except cells.InvalidCellError:
        code = None
        messages.append(cells.error("invalid_customer_code", "El «Cliente Id» no es un número.", "customer_code"))
    else:
        if code is None:
            messages.append(cells.error("missing_customer_code", "Falta el «Cliente Id».", "customer_code"))
    result = cells.text(cell("result"))
    if result is None:
        messages.append(cells.error("missing_result", "Falta «Compro?»: ni compra ni motivo.", "result"))
    try:
        uploaded = _moment(cell("uploaded_at"))
    except cells.InvalidCellError:
        uploaded = None
        messages.append(
            cells.error("invalid_uploaded_at", "«Fecha y Hora Subida» no es una fecha y hora válida.", "uploaded_at")
        )
    else:
        if uploaded is None:
            messages.append(cells.error("missing_uploaded_at", "Falta «Fecha y Hora Subida».", "uploaded_at"))
    if messages:
        return None, messages
    assert code is not None
    assert result is not None
    assert uploaded is not None
    assert salesperson is not None

    clocks: dict[str, time | None] = {}
    for name, label in (("start_time", "Hora Ini."), ("end_time", "Hora Fin.")):
        try:
            clocks[name] = _clock(cell(name))
        except cells.InvalidCellError:
            clocks[name] = None
            messages.append(cells.warning(f"invalid_{name}", f"«{label}» no es una hora: se guarda vacía.", name))
        else:
            if clocks[name] is None:
                messages.append(cells.warning(f"missing_{name}", f"Falta «{label}».", name))
    duration_text = cells.text(cell("minutes"))
    try:
        duration = _duration(cell("minutes"))
    except cells.InvalidCellError:
        duration = None
        messages.append(
            cells.warning("invalid_minutes", "«Minutos» no tiene la forma m:ss: se guarda vacío.", "minutes")
        )
    start, end = clocks["start_time"], clocks["end_time"]
    if start is not None and end is not None:
        if end < start:
            messages.append(cells.warning("end_before_start", "«Hora Fin.» es anterior a «Hora Ini.».", "end_time"))
        elif duration is not None and abs(_seconds(end) - _seconds(start) - duration) > 1:
            messages.append(
                cells.warning("minutes_mismatch", "«Minutos» no coincide con «Hora Fin.» menos «Hora Ini.».", "minutes")
            )
    if start is not None and start > uploaded.time():
        messages.append(
            cells.warning(
                "start_after_upload",
                "La visita empieza después de la hora de subida: el día del evento es incierto "
                "(se usa el de la subida).",
                "start_time",
            )
        )
    customer_name = cells.text(cell("customer_name"))
    if customer_name is None:
        messages.append(cells.warning("missing_customer_name", "Falta el nombre del cliente.", "customer_name"))
    return {
        "customer_code": code,
        "customer_name": customer_name,
        "address": cells.text(cell("address")),
        "salesperson": salesperson,
        "result": result,
        "purchased": source_key(result) == PURCHASED,
        "reason_key": source_key(result),
        "business_date": uploaded.date().isoformat(),
        "uploaded_at": uploaded.isoformat(),
        "start_time": start.isoformat() if start else None,
        "end_time": end.isoformat() if end else None,
        "minutes": duration_text,
        "duration_seconds": duration,
    }, messages


def _content(record: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(record[k] for k in sorted(record) if k != "occurrence")


def _names_agree(name: str | None, customer: Customer) -> bool:
    if not name:
        return False
    key = mappings.label_key(name)
    return key in {mappings.label_key(n) for n in (customer.display_name, customer.legal_name) if n}


@dataclass(frozen=True)
class CustomerMatch:
    kind: str  # MAPPED, PENDING, BY_RULE, NAME_MISMATCH or UNKNOWN_CODE
    entity_id: int | None = None  # the customer it resolves to (MAPPED, BY_RULE) or proposes (NAME_MISMATCH)


class CustomerResolver:
    """The customer of each «Cliente Id», by ADR 018 §3: the AXUM mapping first; the STORE SYS code with the same
    name only through the admin rule. Reads only: `NoPurchaseAdapter.write` applies it."""

    def __init__(self, db: Session, codes: set[str], names: dict[str, str | None]) -> None:
        self.rule = axum_customer_code_matches_store_sys(db)
        own = mappings.resolve_many(db, [customer_key(c) for c in codes])
        store = mappings.resolve_many(db, [store_sys_customer_key(c) for c in codes]) if self.rule else {}
        ids = {r.entity_id for r in store.values() if r.resolved and r.entity_id is not None}
        customers = {c.id: c for c in db.scalars(select(Customer).where(Customer.id.in_(ids)))} if ids else {}
        self.matches: dict[str, CustomerMatch] = {}
        for code in codes:
            mine = own[customer_key(code)]
            if mine.status != "new":
                self.matches[code] = CustomerMatch(MAPPED if mine.resolved else PENDING, mine.entity_id)
                continue
            theirs = store.get(store_sys_customer_key(code))
            if not self.rule or theirs is None:
                self.matches[code] = CustomerMatch(PENDING)
            elif not theirs.resolved or theirs.entity_id not in customers:
                self.matches[code] = CustomerMatch(UNKNOWN_CODE)
            elif _names_agree(names.get(code), customers[theirs.entity_id]):
                self.matches[code] = CustomerMatch(BY_RULE, theirs.entity_id)
            else:
                self.matches[code] = CustomerMatch(NAME_MISMATCH, theirs.entity_id)


def _reasons(db: Session) -> dict[str, NoPurchaseReasonMapping]:
    axum = mappings.source_system_id(db, SOURCE)
    rows = db.scalars(select(NoPurchaseReasonMapping).where(NoPurchaseReasonMapping.source_system_id == axum))
    return {r.reason_key: r for r in rows}


class NoPurchaseAdapter:
    version = "axum-no-purchase-1"

    def score(self, workbook: Workbook) -> float:
        matches = [find_header(sheet, COLUMNS) for sheet in workbook.sheets]
        return max((m.score for m in matches if m is not None), default=0.0)

    def analyze(self, db: Session, workbook: Workbook, context: AnalyzeContext) -> Analysis:
        found = [(sheet, find_header(sheet, COLUMNS)) for sheet in workbook.sheets]
        sheet, header = max(found, key=lambda pair: pair[1].score if pair[1] else 0.0)
        if header is None or header.missing:
            raise StructuralError(
                "missing_columns",
                "Faltan columnas del reporte «Compró / No compró» de AXUM.",
                {"missing": list(header.missing) if header else [c.name for c in COLUMNS]},
            )
        parsed: list[tuple[RawRow, str, dict[str, Any] | None, list[RowMessage]]] = []
        occurrences: Counter[tuple[Any, ...]] = Counter()
        salesperson: str | None = None
        groups = 0
        for index, row in enumerate(sheet.rows):
            if index <= header.row_index or row.is_blank():
                parsed.append((row, "blank", None, []))
                continue
            group = group_salesperson(row)
            if group is not None:
                salesperson = group
                groups += 1
                parsed.append((row, "group", None, []))
                continue
            record, messages = _parse(cells.getter(header, row), salesperson)
            if record is not None and not record["purchased"]:
                content = _content(record)
                occurrences[content] += 1
                record["occurrence"] = occurrences[content]
            parsed.append((row, "data", record, messages))

        events = [r for _, _, r, _ in parsed if r is not None and not r["purchased"]]
        valid = [r for _, _, r, _ in parsed if r is not None]
        names = {r["customer_code"]: r["customer_name"] for r in events}
        customers = CustomerResolver(db, set(names), names)
        people = mappings.resolve_many(db, [salesperson_key(r["salesperson"]) for r in valid])
        reasons = _reasons(db)

        rows: list[AnalyzedRow] = []
        tally: Counter[str] = Counter()
        customer_kinds: dict[str, set[str]] = {
            k: set() for k in (MAPPED, PENDING, BY_RULE, NAME_MISMATCH, UNKNOWN_CODE)
        }
        by_category: Counter[str] = Counter()
        unmapped_reasons: set[str] = set()
        for row, kind, record, parse_messages in parsed:
            if kind != "data":
                rows.append(AnalyzedRow(sheet, row, "skipped"))
                continue
            if record is None:
                tally["rejected"] += 1
                rows.append(AnalyzedRow(sheet, row, "rejected", entity_type=ENTITY, messages=tuple(parse_messages)))
                continue
            if any(m.code == "start_after_upload" for m in parse_messages):
                tally["date_warnings"] += 1
            if record["purchased"]:
                tally["purchases"] += 1
                # Kept in the staging with its own warnings (times), for a later design of visits.
                rows.append(
                    AnalyzedRow(
                        sheet,
                        row,
                        "skipped",
                        normalized=record,
                        messages=(*parse_messages, cells.info("purchase_reported", PURCHASE_TEXT, "result")),
                    )
                )
                continue
            messages = list(parse_messages)
            pending = False
            match = customers.matches[record["customer_code"]]
            customer_kinds[match.kind].add(record["customer_code"])
            tally[f"customer_{match.kind}"] += 1
            if match.kind in (PENDING, NAME_MISMATCH, UNKNOWN_CODE):
                pending = True
                text = {
                    PENDING: "El «Cliente Id» de AXUM todavía no está vinculado a un cliente.",
                    NAME_MISMATCH: "El código existe en STORE SYS pero el nombre no coincide: no se vincula solo.",
                    UNKNOWN_CODE: "El «Cliente Id» no existe como código de cliente de STORE SYS.",
                }[match.kind]
                code = {
                    PENDING: "customer_unresolved",
                    NAME_MISMATCH: "customer_name_mismatch",
                    UNKNOWN_CODE: "customer_unknown_code",
                }[match.kind]
                messages.append(RowMessage(code, "warning", text, "customer_code"))
            elif match.kind == BY_RULE:
                messages.append(
                    cells.info(
                        "customer_by_rule",
                        "Se vincula al cliente de STORE SYS con el mismo código y nombre (regla aprobada).",
                        "customer_code",
                    )
                )
            if people[salesperson_key(record["salesperson"])].status not in ("confirmed", "excluded"):
                pending = True
                tally["salesperson_pending"] += 1
                messages.append(
                    RowMessage(
                        "salesperson_unmapped",
                        "warning",
                        "El preventista de AXUM no está vinculado a un empleado.",
                        "salesperson",
                    )
                )
            reason = reasons.get(record["reason_key"])
            if reason is None or reason.category_id is None:
                pending = True
                unmapped_reasons.add(record["reason_key"])
                tally["reason_unmapped"] += 1
                messages.append(
                    RowMessage(
                        "reason_unmapped",
                        "warning",
                        "Motivo de no compra sin categoría: se guarda tal cual.",
                        "result",
                    )
                )
                by_category["SIN_CATEGORIA"] += 1
            else:
                by_category[str(reason.category_id)] += 1
            if record["occurrence"] > 1:
                messages.append(cells.info("identical_row", "Fila idéntica a otra: se conserva como otro evento."))
            if any(m.code == "start_after_upload" for m in messages):
                tally["date_warnings_events"] += 1
            rows.append(
                AnalyzedRow(
                    sheet,
                    row,
                    cells.status_of(messages, pending=pending),
                    normalized=record,
                    entity_type=ENTITY,
                    messages=tuple(messages),
                )
            )

        days = {date.fromisoformat(r["business_date"]) for r in valid}
        period_from, period_to = (min(days), max(days)) if days else (None, None)
        salespeople = {r["salesperson"] for r in valid}
        replacement = (
            no_purchase.plan(db, period_from=period_from, period_to=period_to, salespeople=salespeople)
            if period_from is not None and period_to is not None
            else no_purchase.Plan(no_purchase.INSERT)
        )
        categories = _category_codes(db)
        return Analysis(
            rows=rows,
            period_from=period_from,
            period_to=period_to,
            scope={
                "days": sorted(d.isoformat() for d in days),
                "salespeople": sorted(salespeople),
                # The report shows neither the period it was asked for nor its filters.
                "filters_detected": [],
            },
            preview={
                "replacement": replacement.as_json(),
                # Counts for the operator's summary, never the rows' values (names, addresses).
                "report": {
                    "data_rows": sum(1 for _, kind, _r, _m in parsed if kind == "data"),
                    "salesperson_groups": groups,
                    "purchases_skipped": tally["purchases"],
                    "no_purchase_events": len(events),
                    "rejected": tally["rejected"],
                    "days": len(days),
                },
                "customers": {
                    "rule_enabled": customers.rule,
                    "mapped": len(customer_kinds[MAPPED]),
                    "by_rule": len(customer_kinds[BY_RULE]),
                    "pending": len(customer_kinds[PENDING]),
                    "unknown_code": len(customer_kinds[UNKNOWN_CODE]),
                    "name_mismatch": len(customer_kinds[NAME_MISMATCH]),
                    "unresolved_events": tally[f"customer_{PENDING}"]
                    + tally[f"customer_{UNKNOWN_CODE}"]
                    + tally[f"customer_{NAME_MISMATCH}"],
                },
                "salespeople": {
                    "seen": sorted(salespeople),
                    # Also a salesperson whose rows are all purchases: the confirmation observes every one.
                    "pending": sorted(
                        p for p in salespeople if people[salesperson_key(p)].status not in ("confirmed", "excluded")
                    ),
                    "pending_events": tally["salesperson_pending"],
                },
                "reasons": {
                    "by_category": {categories.get(key, key): count for key, count in sorted(by_category.items())},
                    "unmapped": len(unmapped_reasons),
                    "unmapped_events": tally["reason_unmapped"],
                },
                # Rows whose visit starts after the upload: the day is uncertain (all rows, and among the events).
                "date_warnings": tally["date_warnings"],
                "date_warnings_events": tally["date_warnings_events"],
            },
        )

    def write(self, db: Session, context: ConfirmContext, rows: Sequence[StagedRow]) -> Sequence[RowOutcome]:
        job = db.get_one(ImportJob, context.job_id)
        records = [r.normalized for r in rows if r.normalized]
        # The salespeople the report brings (purchases included): the scope a `replace` decision applies to.
        superseded = self._replace(db, context, job, set((job.scope or {}).get("salespeople") or ()))
        names = {r["customer_code"]: r["customer_name"] for r in records}
        customers = CustomerResolver(db, set(names), names)
        customer_mappings: dict[str, ExternalEntityMapping] = {}
        people: dict[str, ExternalEntityMapping] = {}
        # Every salesperson the report names is observed, also one whose rows are all purchases.
        for person_code in sorted(set((job.scope or {}).get("salespeople") or ())):
            people[person_code] = self._salesperson_mapping(db, context, person_code)
        reasons: dict[str, NoPurchaseReasonMapping] = {}
        by_rule: list[int] = []
        events: list[tuple[int, NoPurchaseEvent]] = []
        for row in rows:
            record = row.normalized
            assert record is not None
            code = record["customer_code"]
            if code not in customer_mappings:
                customer_mappings[code] = self._customer_mapping(db, context, code, record, customers.matches[code])
                if customers.matches[code].kind == BY_RULE:
                    by_rule.append(customer_mappings[code].id)
            customer = customer_mappings[code]
            person_code = record["salesperson"]
            if person_code not in people:
                people[person_code] = self._salesperson_mapping(db, context, person_code)
            person = people[person_code]
            if record["reason_key"] not in reasons:
                reason, _created = ensure_reason(db, SOURCE, record["result"])
                reasons[record["reason_key"]] = reason
                if reason.category_id is None:
                    record_issue(
                        db,
                        "commercial.no_purchase.reason_unmapped",
                        f"reason:{reason.id}",
                        job_id=context.job_id,
                        details={"reason_mapping_id": reason.id, "origin": TYPE_KEY},
                    )
            reason = reasons[record["reason_key"]]
            event = NoPurchaseEvent(
                business_date=date.fromisoformat(record["business_date"]),
                customer_id=customer.entity_id if customer.status == "confirmed" else None,
                source_customer_code=code,
                source_customer_name=(record["customer_name"] or "")[:200] or None,
                source_customer_address=(record["address"] or "")[:300] or None,
                customer_mapping_id=customer.id,
                salesperson_id=person.entity_id if person.status == "confirmed" else None,
                source_salesperson=person_code,
                salesperson_mapping_id=person.id,
                reason_source=" ".join(record["result"].split())[:200],
                reason_mapping_id=reason.id,
                reason_category_id=reason.category_id,
                uploaded_at_source=datetime.fromisoformat(record["uploaded_at"]),
                start_time_source=time.fromisoformat(record["start_time"]) if record["start_time"] else None,
                end_time_source=time.fromisoformat(record["end_time"]) if record["end_time"] else None,
                duration_source=record["minutes"],
                duration_seconds=record["duration_seconds"],
                import_job_id=context.job_id,
                import_row_id=row.id,
                occurrence=record["occurrence"],
            )
            db.add(event)
            events.append((row.id, event))
        db.flush()  # one flush for all the events: their ids are known only after it
        if by_rule:
            record_event(
                db,
                "commercial.axum.customers_linked_by_rule",
                user_id=context.user_id,
                entity_type="import_job",
                entity_id=str(context.job_id),
                payload={"rule": "commercial.axum_customer_code_matches_store_sys", "mapping_ids": sorted(by_rule)},
            )
        if superseded:
            job.job_metadata = {**(job.job_metadata or {}), "superseded_events": superseded}
        return [RowOutcome(row_id, "inserted", ENTITY, event.id) for row_id, event in events]

    def _salesperson_mapping(self, db: Session, context: ConfirmContext, code: str) -> ExternalEntityMapping:
        mapping = mappings.observe(db, salesperson_key(code), label=f"Vendedor {code}", job_id=context.job_id)
        if mapping.status not in ("confirmed", "excluded"):
            record_issue(
                db,
                "commercial.axum_salesperson.unmapped",
                f"mapping:{mapping.id}",
                job_id=context.job_id,
                external_ref={"source_system": SOURCE, "namespace": "salesperson", "mapping_id": mapping.id},
                details={"mapping_status": mapping.status, "origin": TYPE_KEY},
            )
        return mapping

    def _customer_mapping(
        self, db: Session, context: ConfirmContext, code: str, record: dict[str, Any], match: CustomerMatch
    ) -> ExternalEntityMapping:
        key = customer_key(code)
        label = record["customer_name"]
        if match.kind == BY_RULE:
            return mappings.observe(
                db, key, label=label, job_id=context.job_id, entity_id=match.entity_id, method="rule"
            )
        candidates = [match.entity_id] if match.kind == NAME_MISMATCH and match.entity_id is not None else []
        mapping = mappings.observe(db, key, label=label, job_id=context.job_id, candidates=candidates)
        if mapping.status not in ("confirmed", "excluded"):
            name_mismatch = match.kind == NAME_MISMATCH
            record_issue(
                db,
                "commercial.axum_customer.name_mismatch" if name_mismatch else "commercial.axum_customer.unresolved",
                f"axum_customer:{key.external_id}",
                job_id=context.job_id,
                external_ref={"source_system": SOURCE, "namespace": "customer", "external_id": key.external_id},
                details={"mapping_id": mapping.id, "mapping_status": mapping.status, "match": match.kind},
            )
        return mapping

    def _replace(self, db: Session, context: ConfirmContext, job: ImportJob, salespeople: set[str]) -> int:
        if job.period_from is None or job.period_to is None:
            return 0
        replacement = no_purchase.plan(
            db, period_from=job.period_from, period_to=job.period_to, salespeople=salespeople
        )
        decision = context.decision
        if replacement.action == no_purchase.BLOCKED and decision is None:
            raise ConfirmationBlockedError(
                "import_decision_required",
                "Ya hay no compras de AXUM cargadas en este período: un administrador tiene que decidir.",
                replacement.as_json(),
            )
        if replacement.action != no_purchase.BLOCKED and decision is not None:
            raise ConfirmationBlockedError(
                "import_decision_not_needed",
                "Esta importación no necesita una decisión: confirmala sin decisión.",
                replacement.as_json(),
            )
        if decision is None or decision.action == "additional":
            return 0
        superseded = no_purchase.supersede(
            db, period_from=job.period_from, period_to=job.period_to, salespeople=salespeople, job_id=context.job_id
        )
        record_event(
            db,
            "commercial.no_purchase.events_superseded",
            user_id=context.user_id,
            entity_type="import_job",
            entity_id=str(context.job_id),
            payload={
                "events": superseded,
                "period_from": job.period_from.isoformat(),
                "period_to": job.period_to.isoformat(),
                "salespeople": sorted(salespeople),
                "replaced_job_ids": [r.job_id for r in replacement.replaces],
            },
        )
        return superseded


def _category_codes(db: Session) -> dict[str, str]:
    return {str(c.id): c.code for c in db.scalars(select(NoPurchaseReasonCategory))}
