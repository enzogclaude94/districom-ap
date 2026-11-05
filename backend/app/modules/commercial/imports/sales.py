"""STORE SYS · Ventas artículos/clientes (`commercial.store_sys.sales_lines`, docs/IMPORTS.md §3.3, §5.3).
Contract: the original export `ventas` (BIFF7, header in row 1): codigo, descripcion, cantidad, rubro, cliente,
vendedor, zona, fecha, lista_de_precios. Units only: no amounts and no document number.

- Product by `source_code`; this import never creates products: an unknown code keeps the line with
  `product_id NULL` and an issue.
- The customer comes as a **name**: a confirmed `customer/STORE_SYS/customer_name` mapping links it; otherwise the
  only active customer with exactly that name is *proposed* (pending), two or more make it ambiguous, none leaves it
  pending without candidate. Never confirmed automatically, never by similarity.
- Seller by its `employee/STORE_SYS/seller` mapping; zone by its canonical name; the source values are kept.
- Identical rows are distinct sales: all are kept, numbered by `occurrence`.
- Idempotency by period and declared scope (`sales.service.plan`): never by content.

Not closed: the replacement strategy waits for a real export of several days (ADR 014 S7).
"""

import re
from collections import Counter
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.commercial.catalog.models import Product
from app.modules.commercial.config.models import PriceList, Zone
from app.modules.commercial.config.service import source_key
from app.modules.commercial.imports import cells
from app.modules.commercial.imports.customers import flag_unmapped_seller, seller_key
from app.modules.commercial.sales import service as sales
from app.modules.commercial.sales.models import SaleLine
from app.platform.audit.service import record_event
from app.platform.customers.service import exact_name_candidates
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
from app.platform.imports.workbook import Workbook
from app.platform.mappings import service as mappings
from app.platform.quality.service import record_issue

TYPE_KEY = "commercial.store_sys.sales_lines"
SOURCE = "STORE_SYS"

COLUMNS = (
    Column("product_code", ("codigo", "código")),
    Column("description", ("descripcion", "descripción")),
    Column("quantity", ("cantidad",)),
    Column("category", ("rubro",)),
    Column("customer", ("cliente",)),
    Column("seller", ("vendedor",)),
    Column("zone", ("zona",)),
    Column("business_date", ("fecha",)),
    Column("price_list", ("lista_de_precios", "lista de precios", "lista")),
)

PRICE_LIST = re.compile(r"^LISTA\s*(\d{1,3})$")
LINKED = ("confirmed", "excluded")


def customer_name_key(name: str) -> mappings.MappingKey:
    return mappings.MappingKey("customer", SOURCE, "customer_name", mappings.label_key(name))


def price_list_number(label: str) -> int | None:
    match = PRICE_LIST.match(source_key(label))
    return int(match.group(1)) if match else None


def _parse(cell: cells.CellGetter) -> tuple[dict[str, Any] | None, list[RowMessage]]:
    messages: list[RowMessage] = []
    try:
        code = cells.code(cell("product_code"))
    except cells.InvalidCellError:
        code = None
        messages.append(cells.error("invalid_product_code", "El código de artículo no es un número.", "product_code"))
    else:
        if code is None:
            messages.append(cells.error("missing_product_code", "Falta el código de artículo.", "product_code"))
    try:
        quantity = cells.number(cell("quantity"))
    except cells.InvalidCellError:
        quantity = None
        messages.append(cells.error("invalid_quantity", "La cantidad no es un número.", "quantity"))
    else:
        if quantity is None:
            messages.append(cells.error("missing_quantity", "Falta la cantidad.", "quantity"))
        elif quantity != quantity.quantize(Decimal("0.001")):
            messages.append(cells.error("invalid_quantity", "La cantidad tiene más de tres decimales.", "quantity"))
    try:
        day = cells.day(cell("business_date"))
    except cells.InvalidCellError:
        day = None
        messages.append(cells.error("invalid_business_date", "La fecha no es una fecha.", "business_date"))
    else:
        if day is None:
            messages.append(cells.error("missing_business_date", "Falta la fecha.", "business_date"))
    customer = cells.text(cell("customer"))
    if customer is None:
        messages.append(cells.error("missing_customer", "Falta el cliente.", "customer"))
    if messages:
        return None, messages
    assert code is not None
    assert quantity is not None
    assert day is not None
    assert customer is not None
    seller = cells.text(cell("seller"))
    zone = cells.text(cell("zone"))
    price_list = cells.text(cell("price_list"))
    return {
        "product_code": code,
        "description": cells.text(cell("description")),
        "category": cells.text(cell("category")),
        "quantity": str(quantity),
        "customer": customer,
        "seller": seller,
        "zone": zone,
        "zone_key": source_key(zone) if zone else None,
        "business_date": day.isoformat(),
        "price_list": price_list,
        "price_list_number": price_list_number(price_list) if price_list else None,
    }, messages


def _content(record: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(record[k] for k in sorted(record) if k != "occurrence")


class SalesAdapter:
    version = "store-sys-sales-1"

    def score(self, workbook: Workbook) -> float:
        matches = [find_header(sheet, COLUMNS) for sheet in workbook.sheets]
        return max((m.score for m in matches if m is not None), default=0.0)

    def analyze(self, db: Session, workbook: Workbook, context: AnalyzeContext) -> Analysis:
        found = [(sheet, find_header(sheet, COLUMNS)) for sheet in workbook.sheets]
        sheet, header = max(found, key=lambda pair: pair[1].score if pair[1] else 0.0)
        if header is None or header.missing:
            raise StructuralError(
                "missing_columns",
                "Faltan columnas del reporte de ventas.",
                {"missing": list(header.missing) if header else [c.name for c in COLUMNS]},
            )
        parsed: list[tuple[Any, dict[str, Any] | None, list[RowMessage] | None]] = []
        occurrences: Counter[tuple[Any, ...]] = Counter()
        for index, row in enumerate(sheet.rows):
            if index <= header.row_index or row.is_blank():
                parsed.append((row, None, None))
                continue
            record, messages = _parse(cells.getter(header, row))
            if record is not None:
                content = _content(record)
                occurrences[content] += 1
                record["occurrence"] = occurrences[content]
            parsed.append((row, record, messages))

        records = [r for _, r, _ in parsed if r is not None]
        codes = {r["product_code"] for r in records}
        products = dict(db.execute(select(Product.source_code, Product.id).where(Product.source_code.in_(codes))).all())
        lists = dict(db.execute(select(PriceList.list_number, PriceList.id)).all())
        zones = dict(db.execute(select(Zone.name_key, Zone.id)).all())
        names = {r["customer"] for r in records}
        customers = mappings.resolve_many(db, [customer_name_key(n) for n in names])
        candidates = {n: exact_name_candidates(db, n) for n in names if customers[customer_name_key(n)].status == "new"}
        sellers = mappings.resolve_many(db, [seller_key(r["seller"]) for r in records if r["seller"]])

        rows: list[AnalyzedRow] = []
        tally: Counter[str] = Counter()
        days: set[date] = set()
        seen: dict[str, set[str]] = {"sellers": set(), "zones": set(), "price_lists": set()}
        unknown_products: set[str] = set()
        for row, record, parse_messages in parsed:
            if record is None and parse_messages is None:
                rows.append(AnalyzedRow(sheet, row, "skipped"))
                continue
            if record is None:
                rows.append(
                    AnalyzedRow(
                        sheet, row, "rejected", entity_type="commercial.sale_line", messages=tuple(parse_messages or ())
                    )
                )
                continue
            messages = list(parse_messages or ())
            pending = False
            if record["product_code"] not in products:
                pending = True
                unknown_products.add(record["product_code"])
                messages.append(
                    RowMessage(
                        "product_unknown", "warning", "El artículo no está en el maestro de artículos.", "product_code"
                    )
                )
            customer = customers[customer_name_key(record["customer"])]
            if customer.status not in LINKED:
                pending = True
                options = len(candidates.get(record["customer"], customer.candidates))
                status = "ambiguous" if customer.status == "ambiguous" or options >= 2 else "pending"
                tally[f"customer_{status}"] += 1
                text = (
                    "Hay varios clientes con ese nombre: queda ambiguo hasta que alguien elija."
                    if status == "ambiguous"
                    else "Cliente sin vincular: se propone el único cliente con ese nombre, pendiente de confirmar."
                    if options == 1
                    else "Cliente sin vincular y sin candidato en el maestro."
                )
                messages.append(RowMessage(f"customer_{status}", "warning", text, "customer"))
            if record["seller"]:
                seen["sellers"].add(record["seller"])
                if sellers[seller_key(record["seller"])].status not in LINKED:
                    pending = True
                    messages.append(
                        RowMessage(
                            "seller_unmapped", "warning", "El vendedor no está vinculado a un empleado.", "seller"
                        )
                    )
            else:
                messages.append(cells.warning("without_seller", "La línea no tiene vendedor.", "seller"))
            if record["zone"]:
                seen["zones"].add(record["zone"])
                if record["zone_key"] not in zones:
                    messages.append(cells.warning("unknown_zone", "La zona no existe en el maestro de zonas.", "zone"))
            else:
                messages.append(cells.info("without_zone", "La línea no trae zona.", "zone"))
            if record["price_list"]:
                seen["price_lists"].add(record["price_list"])
                if record["price_list_number"] not in lists:
                    messages.append(
                        cells.warning(
                            "unknown_price_list", "Lista de precios desconocida: se guarda sin lista.", "price_list"
                        )
                    )
            if Decimal(record["quantity"]) == 0:
                messages.append(cells.warning("zero_quantity", "Cantidad en cero.", "quantity"))
            if record["occurrence"] > 1:
                messages.append(cells.info("identical_row", "Fila idéntica a otra: se conserva como otra venta."))
            days.add(date.fromisoformat(record["business_date"]))
            rows.append(
                AnalyzedRow(
                    sheet,
                    row,
                    cells.status_of(messages, pending=pending),
                    normalized=record,
                    entity_type="commercial.sale_line",
                    messages=tuple(messages),
                )
            )

        scope = context.declared.scope
        period_from, period_to = (min(days), max(days)) if days else (None, None)
        job_scope = {
            "declared": scope,
            "note": context.declared.scope_note,
            "days": sorted(d.isoformat() for d in days),
            "sellers": sorted(seen["sellers"]),
            "zones": sorted(seen["zones"]),
            "price_lists": sorted(seen["price_lists"]),
            # The export shows no filters (no title rows): none can be detected from the file itself.
            "filters_detected": [],
        }
        replacement = (
            sales.plan(db, days=days, period_from=period_from, period_to=period_to, scope=scope)
            if period_from is not None and period_to is not None
            else sales.Plan(sales.INSERT)
        )
        return Analysis(
            rows=rows,
            period_from=period_from,
            period_to=period_to,
            scope=job_scope,
            preview={
                "replacement": replacement.as_json(),
                "unknown_products": len(unknown_products),
                "customers": {"pending": tally["customer_pending"], "ambiguous": tally["customer_ambiguous"]},
            },
        )

    def write(self, db: Session, context: ConfirmContext, rows: Sequence[StagedRow]) -> Sequence[RowOutcome]:
        job = db.get_one(ImportJob, context.job_id)
        days = {date.fromisoformat(r.normalized["business_date"]) for r in rows if r.normalized}
        superseded = self._replace(db, context, job, days)
        products = dict(
            db.execute(
                select(Product.source_code, Product.id).where(
                    Product.source_code.in_({r.normalized["product_code"] for r in rows if r.normalized})
                )
            ).all()
        )
        lists = dict(db.execute(select(PriceList.list_number, PriceList.id)).all())
        zones = dict(db.execute(select(Zone.name_key, Zone.id)).all())
        customer_mappings: dict[str, Any] = {}
        seller_mappings: dict[str, Any] = {}
        pending_products = 0
        outcomes: list[RowOutcome] = []
        for row in rows:
            record = row.normalized
            assert record is not None
            name = record["customer"]
            if name not in customer_mappings:
                key = customer_name_key(name)
                proposal = exact_name_candidates(db, name) if mappings.resolve(db, key).status == "new" else []
                customer_mappings[name] = mappings.observe(
                    db, key, label=name, job_id=context.job_id, candidates=proposal
                )
                if customer_mappings[name].status not in LINKED:
                    record_issue(
                        db,
                        "commercial.sale_line.customer_pending",
                        f"mapping:{customer_mappings[name].id}",
                        job_id=context.job_id,
                        external_ref={
                            "source_system": SOURCE,
                            "namespace": "customer_name",
                            "mapping_id": customer_mappings[name].id,
                        },
                        details={
                            "mapping_id": customer_mappings[name].id,
                            "mapping_status": customer_mappings[name].status,
                            "candidates": len(customer_mappings[name].candidates),
                        },
                    )
            customer = customer_mappings[name]
            seller = None
            if record["seller"]:
                if record["seller"] not in seller_mappings:
                    seller_mappings[record["seller"]] = mappings.observe(
                        db, seller_key(record["seller"]), label=record["seller"], job_id=context.job_id
                    )
                    if seller_mappings[record["seller"]].status not in LINKED:
                        flag_unmapped_seller(db, seller_mappings[record["seller"]], context.job_id, TYPE_KEY)
                seller = seller_mappings[record["seller"]]
            product_id = products.get(record["product_code"])
            if product_id is None:
                pending_products += 1
                record_issue(
                    db,
                    "commercial.product.unknown",
                    f"product_code:{record['product_code']}",
                    job_id=context.job_id,
                    external_ref={
                        "source_system": SOURCE,
                        "namespace": "product",
                        "external_id": record["product_code"],
                    },
                    details={"product_code": record["product_code"], "origin": TYPE_KEY},
                )
            line = SaleLine(
                business_date=date.fromisoformat(record["business_date"]),
                customer_id=customer.entity_id if customer.status == "confirmed" else None,
                source_customer_name=name[:200],
                customer_mapping_id=customer.id,
                product_id=product_id,
                source_product_code=record["product_code"],
                source_product_description=(record["description"] or "")[:200] or None,
                source_category=record["category"],
                quantity=Decimal(record["quantity"]),
                price_list_id=lists.get(record["price_list_number"]) if record["price_list_number"] else None,
                source_price_list=record["price_list"],
                salesperson_id=seller.entity_id if seller is not None and seller.status == "confirmed" else None,
                source_seller=record["seller"],
                seller_mapping_id=seller.id if seller is not None else None,
                zone_id=zones.get(record["zone_key"]) if record["zone_key"] else None,
                source_zone=record["zone"],
                import_job_id=context.job_id,
                import_row_id=row.id,
                occurrence=record["occurrence"],
            )
            db.add(line)
            db.flush()
            outcomes.append(RowOutcome(row.id, "inserted", "commercial.sale_line", line.id))
        if pending_products:
            record_issue(
                db,
                "commercial.sale_line.product_pending",
                f"job:{context.job_id}",
                job_id=context.job_id,
                details={"lines": pending_products},
            )
        if superseded:
            job.job_metadata = {**(job.job_metadata or {}), "superseded_lines": superseded}
        return outcomes

    def _replace(self, db: Session, context: ConfirmContext, job: ImportJob, days: set[date]) -> int:
        if not days or job.period_from is None or job.period_to is None:
            return 0
        replacement = sales.plan(
            db, days=days, period_from=job.period_from, period_to=job.period_to, scope=context.declared.scope
        )
        decision = context.decision
        if replacement.action == sales.BLOCKED and decision is None:
            raise ConfirmationBlockedError(
                "import_decision_required",
                "No se puede probar qué ventas reemplaza este archivo: un administrador tiene que decidir.",
                replacement.as_json(),
            )
        if replacement.action != sales.BLOCKED and decision is not None:
            raise ConfirmationBlockedError(
                "import_decision_not_needed",
                "Esta importación no necesita una decisión: confirmala sin decisión.",
                replacement.as_json(),
            )
        if replacement.action == sales.INSERT or (decision is not None and decision.action == "additional"):
            return 0
        superseded = sales.supersede(db, replacement.overlap_days, job_id=context.job_id)
        record_event(
            db,
            "commercial.sales.lines_superseded",
            user_id=context.user_id,
            entity_type="import_job",
            entity_id=str(context.job_id),
            payload={
                "lines": superseded,
                "days": [d.isoformat() for d in replacement.overlap_days],
                "replaced_job_ids": [r.job_id for r in replacement.replaces],
                "automatic": decision is None,
            },
        )
        return superseded
