"""STORE SYS · Comprobantes emitidos (`commercial.store_sys.documents`, docs/IMPORTS.md §3.4). Contract: the original
export `comprobantes emitidos` (BIFF7, header in row 1): fechor, nommov, nombre, codcli, netosa, total, descue, fechas.

`nommov` is `<TYPE> PPPP-NNNNNNNN`, where the type is named as STORE SYS names it, its letter included (`FACTURA A`,
`RECIBOS X`, `RETIRO PR.`, `FACTURA` without letter). The natural key is that type + point of sale + number. A
document already imported with the same content is a duplicate; with other content it is never overwritten: the row
is rejected and an issue asks a person to review it.

The type's class, sign and commercial universe come from `document_types` (never from its name). `codcli` is a
STORE SYS customer code, resolved only by exact mapping, except for types whose class says it is not one
(EMPLOYEE_VOUCHER, INTERNAL_MOVEMENT): their code is kept but never linked to a customer.
"""

import re
from collections import Counter
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.modules.commercial.config.models import DocumentType
from app.modules.commercial.config.service import ensure_document_type, source_key
from app.modules.commercial.documents.models import Document
from app.modules.commercial.imports import cells
from app.platform.imports.headers import Column, find_header
from app.platform.imports.models import ImportRow
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
from app.platform.imports.workbook import RawRow, Workbook
from app.platform.mappings import service as mappings
from app.platform.quality.service import record_issue

TYPE_KEY = "commercial.store_sys.documents"
SOURCE = "STORE_SYS"
# Classes whose `codcli` is not a STORE SYS customer code (a voucher's employee, a withdrawal's 0).
NOT_CUSTOMER_CODE_CLASSES = ("EMPLOYEE_VOUCHER", "INTERNAL_MOVEMENT")

COLUMNS = (
    Column("issued_at", ("fechor",)),
    Column("nommov", ("nommov",)),
    Column("customer_name", ("nombre",)),
    Column("customer_code", ("codcli",)),
    Column("net", ("netosa",)),
    Column("total", ("total",)),
    Column("discount", ("descue",)),
    Column("business_date", ("fechas",)),
)

NOMMOV = re.compile(r"^(?P<type>.*\S)\s+(?P<pos>\d{1,5})-(?P<number>\d{1,10})$")
LETTER = re.compile(r"^[A-Z]$")


def parse_nommov(value: str) -> tuple[str, str | None, str, str] | None:
    """(type name, letter, point of sale, number), or None when it does not read as a document number."""
    match = NOMMOV.match(" ".join(value.split()))
    if match is None:
        return None
    type_name = match["type"]
    last = type_name.rsplit(" ", 1)[-1]
    letter = last if " " in type_name and LETTER.match(last) else None
    return type_name, letter, match["pos"], match["number"]


def document_key(type_name: str, point_of_sale: str, number: str) -> str:
    return f"{source_key(type_name)}|{point_of_sale}|{number}"


def links_customer(document_type: DocumentType | None) -> bool:
    return document_type is not None and document_type.document_class not in NOT_CUSTOMER_CODE_CLASSES


def customer_key(code: str) -> mappings.MappingKey:
    return mappings.MappingKey("customer", SOURCE, "customer", mappings.code_key(code))


def _parse(cell: cells.CellGetter) -> tuple[dict[str, Any] | None, list[RowMessage]]:
    messages: list[RowMessage] = []
    raw = cells.text(cell("nommov"))
    parts = parse_nommov(raw) if raw else None
    if parts is None:
        messages.append(
            cells.error("invalid_document_number", "No se reconoce el tipo y número del comprobante.", "nommov")
        )
    values: dict[str, Any] = {}
    for name, reader in (
        ("business_date", cells.day),
        ("issued_at", cells.moment),
        ("customer_code", cells.code),
        ("net", cells.number),
        ("total", cells.number),
        ("discount", cells.number),
    ):
        try:
            values[name] = reader(cell(name))
        except cells.InvalidCellError:
            messages.append(cells.error(f"invalid_{name}", "El valor no tiene el tipo esperado.", name))
            values[name] = None
    if values.get("business_date") is None and not any(m.column == "business_date" for m in messages):
        messages.append(cells.error("missing_business_date", "Falta la fecha del comprobante.", "business_date"))
    for name in ("net", "total"):
        if values.get(name) is None and not any(m.column == name for m in messages):
            messages.append(cells.error(f"missing_{name}", "Falta el importe.", name))
    if messages or parts is None:
        return None, messages
    type_name, letter, point_of_sale, number = parts
    issued: datetime | None = values["issued_at"]
    zone = ZoneInfo(get_settings().business_timezone)
    record = {
        "type_name": type_name,
        "letter": letter,
        "point_of_sale": point_of_sale,
        "number": number,
        "document_key": document_key(type_name, point_of_sale, number),
        "business_date": values["business_date"].isoformat(),
        "issued_at": issued.replace(tzinfo=zone).isoformat() if issued else None,
        "customer_code": values["customer_code"] if values["customer_code"] not in (None, "0") else None,
        "customer_name": cells.text(cell("customer_name")),
        "net": str(values["net"]),
        "total": str(values["total"]),
        "discount": str(values["discount"] or Decimal("0")),
    }
    record["hash"] = cells.content_hash(record)
    return record, messages


class DocumentsAdapter:
    version = "store-sys-documents-1"

    def score(self, workbook: Workbook) -> float:
        matches = [find_header(sheet, COLUMNS) for sheet in workbook.sheets]
        return max((m.score for m in matches if m is not None), default=0.0)

    def analyze(self, db: Session, workbook: Workbook, context: AnalyzeContext) -> Analysis:
        found = [(sheet, find_header(sheet, COLUMNS)) for sheet in workbook.sheets]
        sheet, header = max(found, key=lambda pair: pair[1].score if pair[1] else 0.0)
        if header is None or header.missing:
            raise StructuralError(
                "missing_columns",
                "Faltan columnas del reporte de comprobantes.",
                {"missing": list(header.missing) if header else [c.name for c in COLUMNS]},
            )
        parsed: list[tuple[RawRow, dict[str, Any] | None, list[RowMessage] | None]] = []
        for index, row in enumerate(sheet.rows):
            if index <= header.row_index or row.is_blank():
                parsed.append((row, None, None))
                continue
            record, messages = _parse(cells.getter(header, row))
            parsed.append((row, record, messages))

        records = [r for _, r, _ in parsed if r is not None]
        keys = Counter(r["document_key"] for r in records)
        hashes: dict[str, set[str]] = {}
        for r in records:
            hashes.setdefault(r["document_key"], set()).add(r["hash"])
        existing = {
            key: digest.hex()
            for key, digest in db.execute(
                select(Document.document_key, Document.source_row_hash).where(Document.document_key.in_(list(keys)))
            )
        }
        types = {t.source_name_key: t for t in db.scalars(select(DocumentType))}
        codes = {r["customer_code"] for r in records if r["customer_code"]}
        resolutions = mappings.resolve_many(db, [customer_key(code) for code in codes])

        rows: list[AnalyzedRow] = []
        first_seen: set[str] = set()
        tally: Counter[str] = Counter()
        by_type: dict[str, Counter[str]] = {}
        new_types: set[str] = set()
        dates: list[date] = []
        for row, record, parse_messages in parsed:
            if record is None and parse_messages is None:
                rows.append(AnalyzedRow(sheet, row, "skipped"))
                continue
            if record is None:
                rows.append(
                    AnalyzedRow(
                        sheet, row, "rejected", entity_type="commercial.document", messages=tuple(parse_messages or ())
                    )
                )
                continue
            found_messages = list(parse_messages or [])
            key = record["document_key"]
            common = {"normalized": record, "business_key": key, "entity_type": "commercial.document"}
            if len(hashes[key]) > 1:
                found_messages.append(
                    cells.error("conflicting_rows", "El mismo comprobante aparece con datos distintos en el archivo.")
                )
                rows.append(AnalyzedRow(sheet, row, "rejected", messages=tuple(found_messages), **common))
                continue
            if key in first_seen:
                found_messages.append(cells.info("repeated_in_file", "El comprobante se repite igual en el archivo."))
                rows.append(AnalyzedRow(sheet, row, "duplicate", messages=tuple(found_messages), **common))
                continue
            first_seen.add(key)
            if key in existing:
                if existing[key] == record["hash"]:
                    tally["already_imported"] += 1
                    found_messages.append(cells.info("already_imported", "El comprobante ya estaba importado, igual."))
                    rows.append(AnalyzedRow(sheet, row, "duplicate", messages=tuple(found_messages), **common))
                else:
                    tally["changed"] += 1
                    found_messages.append(
                        cells.error(
                            "document_changed",
                            "El comprobante ya existe con otros datos: no se pisa, queda para revisión.",
                        )
                    )
                    rows.append(AnalyzedRow(sheet, row, "rejected", messages=tuple(found_messages), **common))
                continue

            document_type = types.get(source_key(record["type_name"]))
            stats = by_type.setdefault(record["type_name"], Counter())
            stats["rows"] += 1
            if document_type is None:
                new_types.add(record["type_name"])
                found_messages.append(
                    cells.warning("new_document_type", "Tipo de comprobante nuevo: entra sin clasificar.", "nommov")
                )
            elif document_type.document_class == "UNCLASSIFIED":
                found_messages.append(
                    cells.warning("unclassified_type", "Tipo de comprobante sin clasificar: fuera del universo.")
                )
            universe = document_type is not None and document_type.commercial_universe
            pending = False
            if universe:
                sign = document_type.sign if document_type else 0
                if sign and Decimal(record["total"]) * sign < 0:
                    found_messages.append(
                        cells.warning("sign_mismatch", "El total contradice el signo del tipo de comprobante.", "total")
                    )
                if Decimal(record["net"]) == 0:
                    found_messages.append(cells.warning("zero_net", "Neto en cero en un comprobante comercial.", "net"))
            if links_customer(document_type):
                if record["customer_code"] is None:
                    if universe:
                        found_messages.append(
                            cells.warning("without_customer", "Comprobante comercial sin cliente.", "customer_code")
                        )
                elif not resolutions[customer_key(record["customer_code"])].resolved:
                    pending = True
                    found_messages.append(
                        RowMessage(
                            "customer_unresolved",
                            "warning",
                            "El código de cliente no está en el maestro de clientes.",
                            "customer_code",
                        )
                    )
            dates.append(date.fromisoformat(record["business_date"]))
            tally["new"] += 1
            rows.append(
                AnalyzedRow(
                    sheet,
                    row,
                    cells.status_of(found_messages, pending=pending),
                    messages=tuple(found_messages),
                    **common,
                )
            )

        return Analysis(
            rows=rows,
            period_from=min(dates) if dates else None,
            period_to=max(dates) if dates else None,
            preview={
                "documents": {
                    "new": tally["new"],
                    "already_imported": tally["already_imported"],
                    "changed": tally["changed"],
                },
                "by_type": {name: dict(stats) for name, stats in sorted(by_type.items())},
                "new_types": sorted(new_types),
            },
        )

    def write(self, db: Session, context: ConfirmContext, rows: Sequence[StagedRow]) -> Sequence[RowOutcome]:
        outcomes: list[RowOutcome] = []
        for row in rows:
            assert row.normalized is not None
            outcome, document_id = self._write_row(db, context, row.id, row.normalized)
            outcomes.append(RowOutcome(row.id, outcome, "commercial.document", document_id))
        self._flag_changed(db, context)
        return outcomes

    def _write_row(
        self, db: Session, context: ConfirmContext, row_id: int, record: dict[str, Any]
    ) -> tuple[str, int | None]:
        existing = db.scalars(select(Document).where(Document.document_key == record["document_key"])).first()
        if existing is not None:  # imported by another job after this one was analyzed
            if existing.source_row_hash.hex() != record["hash"]:
                _changed_issue(db, context.job_id, row_id, existing)
            return "unchanged", existing.id
        document_type, _ = ensure_document_type(db, record["type_name"], job_id=context.job_id)
        if document_type.document_class == "UNCLASSIFIED":
            record_issue(
                db,
                "commercial.document.unclassified_type",
                f"document_type:{document_type.id}",
                job_id=context.job_id,
                entity_type="commercial.document_type",
                entity_id=document_type.id,
                details={"document_type_id": document_type.id},
            )
        customer_id = None
        code = record["customer_code"]
        if code is not None and links_customer(document_type):
            resolution = mappings.resolve(db, customer_key(code))
            if resolution.resolved:
                customer_id = resolution.entity_id
            else:
                record_issue(
                    db,
                    "commercial.customer.unresolved_code",
                    f"customer_code:{mappings.code_key(code)}",
                    job_id=context.job_id,
                    row_id=row_id,
                    external_ref={"source_system": SOURCE, "namespace": "customer", "external_id": code},
                    details={"origin": TYPE_KEY, "mapping_status": resolution.status},
                )
        document = Document(
            document_type_id=document_type.id,
            source_type_name=record["type_name"],
            letter=record["letter"],
            point_of_sale=record["point_of_sale"],
            number=record["number"],
            document_key=record["document_key"],
            business_date=date.fromisoformat(record["business_date"]),
            issued_at=datetime.fromisoformat(record["issued_at"]) if record["issued_at"] else None,
            customer_id=customer_id,
            source_customer_code=code,
            source_customer_name=record["customer_name"],
            net_amount=Decimal(record["net"]),
            total_amount=Decimal(record["total"]),
            discount_amount=Decimal(record["discount"]),
            import_job_id=context.job_id,
            import_row_id=row_id,
            source_row_hash=bytes.fromhex(record["hash"]),
        )
        db.add(document)
        db.flush()
        return "inserted", document.id

    def _flag_changed(self, db: Session, context: ConfirmContext) -> None:
        """The rows analysis rejected because their document exists with other content: one issue per document."""
        rejected = db.scalars(
            select(ImportRow).where(ImportRow.job_id == context.job_id, ImportRow.status == "rejected")
        ).all()
        for row in rejected:
            if not any(m.get("code") == "document_changed" for m in row.messages) or not row.business_key:
                continue
            existing = db.scalars(select(Document).where(Document.document_key == row.business_key)).first()
            if existing is not None:
                _changed_issue(db, context.job_id, row.id, existing)


def _changed_issue(db: Session, job_id: int, row_id: int, existing: Document) -> None:
    record_issue(
        db,
        "commercial.document.changed",
        f"document:{existing.id}",
        job_id=job_id,
        row_id=row_id,
        entity_type="commercial.document",
        entity_id=existing.id,
        details={"document_id": existing.id, "first_job_id": existing.import_job_id},
    )
