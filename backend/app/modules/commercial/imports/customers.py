"""STORE SYS · Clientes (`commercial.store_sys.customers`, docs/IMPORTS.md §3.2). Contract: the original export
`clientes` (BIFF7, one sheet, the header in row 1): codigo, fantasia, razons, direccion, telefono, cuit, zona, vend.

The STORE SYS code is the customer's identity (mapping `customer/STORE_SYS/customer`); neither CUIT nor name is. This
is the only import that creates customers. Each file is a snapshot dated by the operator (default: today's business
date): zone and seller assignments keep their history, a customer missing from the snapshot is never deactivated,
and a seller is only ever a label until someone maps it (never an employee created or guessed).
"""

from collections import Counter
from collections.abc import Sequence
from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.clock import business_today
from app.modules.commercial.assignments import service as assignments
from app.modules.commercial.config.models import Zone
from app.modules.commercial.config.service import ensure_zone, source_key
from app.modules.commercial.imports import cells
from app.platform.customers import service as customers
from app.platform.customers.models import Customer
from app.platform.imports.headers import Column, find_header
from app.platform.imports.models import COMPLETED_STATUSES, ImportJob
from app.platform.imports.registry import (
    Analysis,
    AnalyzeContext,
    AnalyzedRow,
    ConfirmContext,
    RowMessage,
    RowOutcome,
    StagedRow,
    StructuralError,
)
from app.platform.imports.workbook import Workbook
from app.platform.mappings import service as mappings
from app.platform.mappings.models import ExternalEntityMapping
from app.platform.quality.service import record_issue, resolve_open

TYPE_KEY = "commercial.store_sys.customers"
SOURCE = "STORE_SYS"

COLUMNS = (
    Column("code", ("codigo", "código")),
    Column("display_name", ("fantasia", "fantasía")),
    Column("legal_name", ("razons", "razon social")),
    Column("address", ("direccion", "dirección")),
    Column("phone", ("telefono", "teléfono")),
    Column("tax_id", ("cuit",)),
    Column("zone", ("zona",)),
    Column("seller", ("vend", "vendedor")),
)


def customer_key(code: str) -> mappings.MappingKey:
    return mappings.MappingKey("customer", SOURCE, "customer", mappings.code_key(code))


def seller_key(label: str) -> mappings.MappingKey:
    return mappings.MappingKey("employee", SOURCE, "seller", mappings.label_key(label))


def last_snapshot(db: Session) -> date | None:
    """The latest snapshot already applied: a new one may be that day or later, never earlier."""
    return db.scalar(
        select(func.max(ImportJob.snapshot_date)).where(
            ImportJob.import_type == TYPE_KEY, ImportJob.status.in_(COMPLETED_STATUSES)
        )
    )


def _check_snapshot(db: Session, snapshot: date) -> None:
    latest = last_snapshot(db)
    if latest is not None and snapshot < latest:
        raise StructuralError(
            "snapshot_out_of_order",
            "La fecha de la foto es anterior a la última foto de clientes aplicada; no se reescribe la historia.",
            {"snapshot_date": snapshot.isoformat(), "last_snapshot_date": latest.isoformat()},
        )


def _seller_pending(resolution: mappings.Resolution) -> bool:
    return resolution.status not in ("confirmed", "excluded")


class CustomersAdapter:
    version = "store-sys-customers-1"

    def score(self, workbook: Workbook) -> float:
        matches = [find_header(sheet, COLUMNS) for sheet in workbook.sheets]
        return max((m.score for m in matches if m is not None), default=0.0)

    def analyze(self, db: Session, workbook: Workbook, context: AnalyzeContext) -> Analysis:
        found = [(sheet, find_header(sheet, COLUMNS)) for sheet in workbook.sheets]
        sheet, header = max(found, key=lambda pair: pair[1].score if pair[1] else 0.0)
        if header is None or header.missing:
            raise StructuralError(
                "missing_columns",
                "Faltan columnas del reporte de clientes.",
                {"missing": list(header.missing) if header else [c.name for c in COLUMNS]},
            )
        snapshot = context.declared.snapshot_date or business_today()
        _check_snapshot(db, snapshot)

        parsed: list[tuple[Any, dict[str, Any] | None, list[RowMessage]]] = []
        for index, row in enumerate(sheet.rows):
            if index <= header.row_index or row.is_blank():
                parsed.append((row, None, []))
                continue
            cell = cells.getter(header, row)
            messages: list[RowMessage] = []
            try:
                code = cells.code(cell("code"))
            except cells.InvalidCellError:
                code = None
                messages.append(cells.error("invalid_code", "El código de cliente no es un número entero.", "code"))
            else:
                if code is None:
                    messages.append(cells.error("missing_code", "Falta el código de cliente.", "code"))
            name = cells.text(cell("display_name"))
            if name is None:
                messages.append(cells.error("missing_name", "Falta el nombre de fantasía.", "display_name"))
            if messages:
                parsed.append((row, {"code": code} if code else None, messages))
                continue
            assert code is not None
            assert name is not None
            record = customers.normalize(
                customers.MasterRecord(
                    display_name=name,
                    legal_name=cells.text(cell("legal_name")),
                    tax_id=cells.text(cell("tax_id")),
                    address=cells.text(cell("address")),
                    phone=cells.text(cell("phone")),
                )
            )
            zone = cells.text(cell("zone"))
            seller = cells.text(cell("seller"))
            parsed.append(
                (
                    row,
                    {
                        "code": code,
                        "display_name": record.display_name,
                        "legal_name": record.legal_name,
                        "tax_id": record.tax_id,
                        "address": record.address,
                        "phone": record.phone,
                        "zone": zone,
                        "zone_key": source_key(zone) if zone else None,
                        "seller": seller,
                        "seller_key": mappings.label_key(seller) if seller else None,
                        "warnings": list(record.warnings),
                    },
                    messages,
                )
            )

        codes = Counter(n["code"] for _, n, m in parsed if n is not None and not m)
        known = mappings.resolve_many(db, [customer_key(code) for code in codes])
        sellers = {n["seller"] for _, n, m in parsed if n is not None and not m and n["seller"]}
        seller_resolutions = mappings.resolve_many(db, [seller_key(label) for label in sellers])
        zone_ids = dict(db.execute(select(Zone.name_key, Zone.id)).all())
        customer_ids = [r.entity_id for r in known.values() if r.resolved and r.entity_id is not None]
        current = {c.id: c for c in db.scalars(select(Customer).where(Customer.id.in_(customer_ids)))}
        current_zones = assignments.current_zone_ids(db, customer_ids)
        current_sellers = assignments.current_seller_keys(db, customer_ids)

        rows: list[AnalyzedRow] = []
        tally: Counter[str] = Counter()
        new_zones: set[str] = set()
        unknown_sellers: set[str] = set()
        for row, normalized, messages in parsed:
            if normalized is None and not messages:
                rows.append(AnalyzedRow(sheet, row, "skipped"))
                continue
            if messages:
                rows.append(
                    AnalyzedRow(
                        sheet,
                        row,
                        "rejected",
                        normalized=normalized,
                        business_key=normalized["code"] if normalized else None,
                        entity_type="customer",
                        messages=tuple(messages),
                    )
                )
                continue
            assert normalized is not None
            code = normalized["code"]
            if codes[code] > 1:
                messages.append(cells.error("duplicate_code", "El código de cliente se repite en el archivo.", "code"))
                rows.append(AnalyzedRow(sheet, row, "rejected", normalized, code, "customer", messages=tuple(messages)))
                continue
            if "invalid_tax_id" in normalized["warnings"]:
                messages.append(cells.warning("invalid_tax_id", "El CUIT no es válido; se guarda sin CUIT.", "tax_id"))
            resolution = known[customer_key(code)]
            existing = current.get(resolution.entity_id) if resolution.entity_id is not None else None
            if resolution.status != "new" and not resolution.resolved:
                messages.append(
                    cells.warning("customer_mapping_pending", "El código tiene un vínculo pendiente de revisión.")
                )
            changed = False
            if normalized["zone_key"] is None:
                messages.append(cells.warning("without_zone", "El cliente no tiene zona.", "zone"))
                changed |= existing is not None and existing.id in current_zones
            else:
                zone_id = zone_ids.get(normalized["zone_key"])
                if zone_id is None:
                    new_zones.add(normalized["zone"])
                    messages.append(cells.info("new_zone", "Zona nueva: se crea al confirmar.", "zone"))
                changed |= existing is not None and current_zones.get(existing.id) != zone_id
            pending = False
            if normalized["seller_key"] is None:
                messages.append(cells.warning("without_salesperson", "El cliente no tiene vendedor.", "seller"))
                changed |= existing is not None and current_sellers.get(existing.id) is not None
            else:
                seller_resolution = seller_resolutions[seller_key(normalized["seller"])]
                if _seller_pending(seller_resolution):
                    pending = True
                    unknown_sellers.add(normalized["seller"])
                    messages.append(
                        RowMessage(
                            "seller_unmapped", "warning", "El vendedor no está vinculado a un empleado.", "seller"
                        )
                    )
                elif seller_resolution.status == "excluded":
                    messages.append(cells.info("seller_excluded", "Vendedor excluido (pseudo-vendedor).", "seller"))
                changed |= existing is not None and current_sellers.get(existing.id) != normalized["seller_key"]
            if existing is None:
                tally["new"] += 1
            elif changed or _attributes_changed(existing, normalized):
                tally["updated"] += 1
            else:
                tally["unchanged"] += 1
            rows.append(
                AnalyzedRow(
                    sheet,
                    row,
                    cells.status_of(messages, pending=pending),
                    normalized=normalized,
                    business_key=code,
                    entity_type="customer",
                    messages=tuple(messages),
                )
            )

        in_file = {code for code, count in codes.items() if count == 1}
        missing = db.scalar(
            select(func.count())
            .select_from(ExternalEntityMapping)
            .where(
                ExternalEntityMapping.entity_type == "customer",
                ExternalEntityMapping.source_system_id == context.source_system_id,
                ExternalEntityMapping.namespace == "customer",
                ExternalEntityMapping.status == "confirmed",
                ExternalEntityMapping.external_id.not_in(in_file) if in_file else ExternalEntityMapping.id.is_not(None),
            )
        )
        return Analysis(
            rows=rows,
            snapshot_date=snapshot,
            preview={
                "snapshot_date": snapshot.isoformat(),
                "customers": {"new": tally["new"], "updated": tally["updated"], "unchanged": tally["unchanged"]},
                "new_zones": sorted(new_zones),
                "unknown_sellers": sorted(unknown_sellers),
                "missing_from_master": int(missing or 0),
            },
        )

    def write(self, db: Session, context: ConfirmContext, rows: Sequence[StagedRow]) -> Sequence[RowOutcome]:
        job = db.get_one(ImportJob, context.job_id)
        snapshot = job.snapshot_date or business_today()
        _check_snapshot(db, snapshot)
        outcomes: list[RowOutcome] = []
        seen: set[str] = set()
        for row in rows:
            assert row.normalized is not None
            outcome, customer_id = self._write_row(db, context, row.normalized, snapshot)
            seen.add(row.normalized["code"])
            outcomes.append(RowOutcome(row.id, outcome, "customer", customer_id))
        self._flag_missing(db, context, seen)
        return outcomes

    def _write_row(
        self, db: Session, context: ConfirmContext, record: dict[str, Any], snapshot: date
    ) -> tuple[str, int | None]:
        key = customer_key(record["code"])
        resolution = mappings.resolve(db, key)
        normalized = customers.NormalizedRecord(
            display_name=record["display_name"],
            legal_name=record["legal_name"],
            tax_id=record["tax_id"],
            address=record["address"],
            phone=record["phone"],
        )
        if resolution.status == "new":
            customer = customers.create_from_master(db, normalized, job_id=context.job_id, seen_on=snapshot)
            mappings.observe(
                db,
                key,
                label=record["display_name"],
                job_id=context.job_id,
                entity_id=customer.id,
                method="created_from_master",
            )
            outcome = "inserted"
        elif resolution.resolved and resolution.entity_id is not None:
            customer = db.get_one(Customer, resolution.entity_id)
            changed = customers.update_from_master(db, customer, normalized, job_id=context.job_id, seen_on=snapshot)
            mappings.observe(db, key, label=record["display_name"], job_id=context.job_id)
            outcome = "updated" if changed else "unchanged"
        else:
            # Someone left this code's mapping pending, ambiguous or rejected: never create a second customer.
            mappings.observe(db, key, label=record["display_name"], job_id=context.job_id)
            record_issue(
                db,
                "commercial.customer.unresolved_code",
                f"customer_code:{key.external_id}",
                job_id=context.job_id,
                external_ref={"source_system": SOURCE, "namespace": "customer", "external_id": key.external_id},
                details={"origin": TYPE_KEY, "mapping_status": resolution.status},
            )
            return "unchanged", None

        moved = self._zone(db, context, customer.id, record, snapshot)
        moved |= self._seller(db, context, customer.id, record, snapshot)
        resolve_open(
            db,
            "commercial.customer.missing_from_master",
            f"customer:{customer.id}",
            user_id=None,
            note=f"Volvió a aparecer en la foto del job {context.job_id}",
        )
        if outcome == "unchanged" and moved:
            outcome = "updated"
        return outcome, customer.id

    def _zone(self, db: Session, context: ConfirmContext, customer_id: int, record: dict[str, Any], day: date) -> bool:
        dedupe = f"customer:{customer_id}"
        if record["zone"] is None:
            result = assignments.apply_zone(
                db, customer_id, None, snapshot=day, job_id=context.job_id, user_id=context.user_id
            )
            record_issue(
                db,
                "commercial.customer.without_zone",
                dedupe,
                job_id=context.job_id,
                entity_type="customer",
                entity_id=customer_id,
                details={"customer_id": customer_id},
            )
            return result == assignments.CLOSED
        zone, _ = ensure_zone(db, record["zone"], effective_from=day, job_id=context.job_id, user_id=context.user_id)
        result = assignments.apply_zone(
            db, customer_id, zone.id, snapshot=day, job_id=context.job_id, user_id=context.user_id
        )
        resolve_open(
            db, "commercial.customer.without_zone", dedupe, user_id=None, note=f"Zona en el job {context.job_id}"
        )
        return result in (assignments.OPENED, assignments.CHANGED)

    def _seller(
        self, db: Session, context: ConfirmContext, customer_id: int, record: dict[str, Any], day: date
    ) -> bool:
        dedupe = f"customer:{customer_id}"
        if record["seller"] is None:
            result = assignments.apply_seller(
                db,
                customer_id,
                label=None,
                key=None,
                salesperson_id=None,
                mapping_id=None,
                snapshot=day,
                job_id=context.job_id,
                user_id=context.user_id,
            )
            record_issue(
                db,
                "commercial.customer.without_salesperson",
                dedupe,
                job_id=context.job_id,
                entity_type="customer",
                entity_id=customer_id,
                details={"customer_id": customer_id},
            )
            return result == assignments.CLOSED
        mapping = mappings.observe(db, seller_key(record["seller"]), label=record["seller"], job_id=context.job_id)
        if mapping.status not in ("confirmed", "excluded"):
            flag_unmapped_seller(db, mapping, context.job_id, TYPE_KEY)
        result = assignments.apply_seller(
            db,
            customer_id,
            label=record["seller"],
            key=record["seller_key"],
            salesperson_id=mapping.entity_id if mapping.status == "confirmed" else None,
            mapping_id=mapping.id,
            snapshot=day,
            job_id=context.job_id,
            user_id=context.user_id,
        )
        resolve_open(
            db,
            "commercial.customer.without_salesperson",
            dedupe,
            user_id=None,
            note=f"Vendedor en el job {context.job_id}",
        )
        return result in (assignments.OPENED, assignments.CHANGED)

    def _flag_missing(self, db: Session, context: ConfirmContext, seen: set[str]) -> None:
        """A known customer absent from the snapshot stays as it is (never deactivated); a person reviews it."""
        absent = db.execute(
            select(ExternalEntityMapping.entity_id, ExternalEntityMapping.external_id).where(
                ExternalEntityMapping.entity_type == "customer",
                ExternalEntityMapping.source_system_id == context.source_system_id,
                ExternalEntityMapping.namespace == "customer",
                ExternalEntityMapping.status == "confirmed",
            )
        ).all()
        for entity_id, external_id in absent:
            if external_id in seen or entity_id is None:
                continue
            record_issue(
                db,
                "commercial.customer.missing_from_master",
                f"customer:{entity_id}",
                job_id=context.job_id,
                entity_type="customer",
                entity_id=entity_id,
                details={"customer_id": entity_id},
            )


def flag_unmapped_seller(db: Session, mapping: ExternalEntityMapping, job_id: int, origin: str) -> None:
    record_issue(
        db,
        "commercial.seller.unmapped",
        f"mapping:{mapping.id}",
        job_id=job_id,
        external_ref={"source_system": SOURCE, "namespace": "seller", "mapping_id": mapping.id},
        details={"mapping_id": mapping.id, "mapping_status": mapping.status, "origin": origin},
    )


def _attributes_changed(customer: Customer, record: dict[str, Any]) -> bool:
    return any(
        getattr(customer, field) != record[field]
        for field in ("display_name", "legal_name", "tax_id", "address", "phone")
    )
