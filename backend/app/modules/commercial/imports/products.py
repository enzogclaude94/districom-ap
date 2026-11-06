"""STORE SYS · Maestro de artículos (`commercial.store_sys.products`, docs/IMPORTS.md §3.5). Contract: the original
export `lista de articulos` (BIFF7, one sheet, the header in row 1): codart, desart, precio.

`codart` is the product's identity (`products.source_code`). This is the only import that creates products; a
re-import refreshes only `source_*` and never what DistriCom decided (category, kind, eligibility, family). The
export has no rubro, so every product starts without category and outside the analytics until someone classifies it;
nothing is inferred from the description (no family, capacity or pack read out of it).

The export has a single `precio` and does not say which price list it is. Which list it feeds is a confirmed
parameter (`commercial.store_sys_articles_price_list`); while nobody sets it, prices stay in staging, are not
loaded, and an issue says so. Each file is a snapshot (declared date, default the upload's business day): prices
keep their history from that day, and a product missing from the snapshot is never deactivated.
"""

from collections import Counter
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.clock import business_today
from app.modules.commercial.catalog import service as catalog
from app.modules.commercial.catalog.models import Product
from app.modules.commercial.config.models import PriceList
from app.modules.commercial.config.service import ARTICLES_PRICE_LIST_SETTING, articles_price_list
from app.modules.commercial.imports import cells
from app.platform.imports.headers import Column, find_header, header_key
from app.platform.imports.models import COMPLETED_STATUSES, ImportJob, ImportRow
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
from app.platform.quality.service import record_issue, resolve_open

TYPE_KEY = "commercial.store_sys.products"
PRICE_LIST_SETTING = ARTICLES_PRICE_LIST_SETTING
DUPLICATE_CODE = "commercial.product.duplicate_code"
MISSING_FROM_MASTER = "commercial.product.missing_from_master"
PRICES_NOT_LOADED = "commercial.product.prices_not_loaded"

COLUMNS = (
    Column("code", ("codart",)),
    Column("description", ("desart",)),
    Column("price", ("precio",)),
)
CENT = Decimal("0.01")


def last_snapshot(db: Session) -> date | None:
    """The latest articles snapshot already applied: prices are written forward only."""
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
            "La fecha de la foto es anterior al último maestro de artículos aplicado; no se reescribe la historia.",
            {"snapshot_date": snapshot.isoformat(), "last_snapshot_date": latest.isoformat()},
        )


def configured_price_list(db: Session) -> PriceList | None:
    """The list `precio` feeds, once someone confirmed it; None while not set (or set to a list that is not
    configured: a list is never created from an export)."""
    number = articles_price_list(db)
    if number is None:
        return None
    return db.scalars(select(PriceList).where(PriceList.list_number == number)).first()


def _price(cell: Any, messages: list[RowMessage]) -> Decimal | None:
    try:
        value = cells.number(cell)
    except cells.InvalidCellError:
        messages.append(cells.warning("invalid_price", "El precio no es un número; no se carga.", "price"))
        return None
    if value is None:
        messages.append(cells.info("without_price", "El artículo no tiene precio.", "price"))
        return None
    if value < 0 or value != value.quantize(CENT):
        messages.append(
            cells.warning("invalid_price", "El precio es negativo o tiene más de dos decimales; no se carga.", "price")
        )
        return None
    if value == 0:
        messages.append(cells.warning("zero_price", "El precio es 0.", "price"))
    return value.quantize(CENT)


class ProductsAdapter:
    version = "store-sys-products-1"

    def score(self, workbook: Workbook) -> float:
        matches = [find_header(sheet, COLUMNS) for sheet in workbook.sheets]
        return max((m.score for m in matches if m is not None), default=0.0)

    def analyze(self, db: Session, workbook: Workbook, context: AnalyzeContext) -> Analysis:
        found = [(sheet, find_header(sheet, COLUMNS)) for sheet in workbook.sheets]
        sheet, header = max(found, key=lambda pair: pair[1].score if pair[1] else 0.0)
        if header is None or header.missing:
            raise StructuralError(
                "missing_columns",
                "Faltan columnas del maestro de artículos.",
                {"missing": list(header.missing) if header else [c.name for c in COLUMNS]},
            )
        snapshot = context.declared.snapshot_date or business_today()
        _check_snapshot(db, snapshot)
        price_list = configured_price_list(db)
        # Columns the contract does not name are kept as they come, never interpreted (source_extra).
        header_cells = sheet.rows[header.row_index].cells
        known = set(header.positions.values())
        extra_columns = {
            i: header_key(cell) for i, cell in enumerate(header_cells) if i not in known and header_key(cell)
        }

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
                messages.append(cells.error("invalid_code", "El código de artículo no es un número entero.", "code"))
            else:
                if code is None:
                    messages.append(cells.error("missing_code", "Falta el código de artículo.", "code"))
            description = cells.text(cell("description"))
            if description is None:
                messages.append(
                    cells.warning("without_description", "El artículo no tiene descripción.", "description")
                )
            price = _price(cell("price"), messages)
            extra = {name: cells.text(row.cells[i]) for i, name in extra_columns.items() if i < len(row.cells)}
            parsed.append(
                (
                    row,
                    {
                        "code": code,
                        "description": description,
                        "price": str(price) if price is not None else None,
                        "extra": {k: v for k, v in extra.items() if v is not None},
                    },
                    messages,
                )
            )

        codes = Counter(n["code"] for _, n, m in parsed if n is not None and n["code"] is not None)
        existing = dict(db.execute(select(Product.source_code, Product.source_row_hash)).all())
        rows: list[AnalyzedRow] = []
        tally: Counter[str] = Counter()
        duplicates: set[str] = set()
        for row, normalized, messages in parsed:
            if normalized is None:
                rows.append(AnalyzedRow(sheet, row, "skipped"))
                continue
            code = normalized["code"]
            if code is not None and codes[code] > 1:
                duplicates.add(code)
                messages.append(cells.error("duplicate_code", "El código de artículo se repite en el archivo.", "code"))
            status = cells.status_of(messages)
            if status != "rejected":
                if code not in existing:
                    tally["new"] += 1
                elif existing[code] != catalog.row_hash(_source(normalized)):
                    tally["updated"] += 1
                else:
                    tally["unchanged"] += 1
                if normalized["price"] is not None:
                    tally["with_price"] += 1
            rows.append(
                AnalyzedRow(
                    sheet,
                    row,
                    status,
                    normalized=normalized,
                    business_key=code,
                    entity_type="commercial.product",
                    messages=tuple(messages),
                )
            )
        in_file = {code for code, count in codes.items() if count == 1}
        missing = sum(1 for code in existing if code not in in_file)
        return Analysis(
            rows=rows,
            snapshot_date=snapshot,
            preview={
                "snapshot_date": snapshot.isoformat(),
                "products": {"new": tally["new"], "updated": tally["updated"], "unchanged": tally["unchanged"]},
                "duplicate_codes": len(duplicates),
                "missing_from_master": missing,
                "source_columns_without_contract": sorted(extra_columns.values()),
                "prices": {
                    "rows_with_price": tally["with_price"],
                    "price_list_number": price_list.list_number if price_list else None,
                    "loaded": price_list is not None,
                },
            },
        )

    def write(self, db: Session, context: ConfirmContext, rows: Sequence[StagedRow]) -> Sequence[RowOutcome]:
        job = db.get_one(ImportJob, context.job_id)
        snapshot = job.snapshot_date or business_today()
        _check_snapshot(db, snapshot)
        # The list the preview announced is the one written: a parameter changed in between does not apply.
        number = ((job.job_metadata or {}).get("preview") or {}).get("prices", {}).get("price_list_number")
        price_list = db.scalars(select(PriceList).where(PriceList.list_number == number)).first() if number else None
        outcomes: list[RowOutcome] = []
        seen: set[str] = set()
        prices: Counter[str] = Counter()
        for row in rows:
            assert row.normalized is not None
            record = row.normalized
            product, outcome = catalog.upsert_from_source(db, _source(record), job_id=context.job_id)
            seen.add(product.source_code)
            resolve_open(
                db,
                MISSING_FROM_MASTER,
                f"product:{product.id}",
                user_id=None,
                note=f"Volvió a aparecer en el maestro del job {context.job_id}",
            )
            if price_list is not None and record["price"] is not None:
                result = catalog.record_price(
                    db, product.id, price_list.id, Decimal(record["price"]), valid_from=snapshot, job_id=context.job_id
                )
                prices[result] += 1
                if outcome == "unchanged" and result != "unchanged":
                    outcome = "updated"
            outcomes.append(RowOutcome(row.id, outcome, "commercial.product", product.id))
        self._flag_missing(db, context, seen)
        self._flag_duplicates(db, context)
        with_price = sum(1 for r in rows if r.normalized and r.normalized["price"] is not None)
        if price_list is None:
            record_issue(
                db,
                PRICES_NOT_LOADED,
                "store_sys_articles_price_list",
                job_id=context.job_id,
                details={"setting": PRICE_LIST_SETTING, "rows_with_price": with_price},
            )
        else:
            resolve_open(
                db, PRICES_NOT_LOADED, "store_sys_articles_price_list", user_id=None, note=f"Job {context.job_id}"
            )
        job.job_metadata = {**(job.job_metadata or {}), "prices_written": dict(prices)}
        return outcomes

    def _flag_missing(self, db: Session, context: ConfirmContext, seen: set[str]) -> None:
        """A known product absent from the snapshot stays as it is (never deactivated); a person reviews it."""
        for product_id, code in db.execute(select(Product.id, Product.source_code)).all():
            if code in seen:
                continue
            record_issue(
                db,
                MISSING_FROM_MASTER,
                f"product:{product_id}",
                job_id=context.job_id,
                entity_type="commercial.product",
                entity_id=product_id,
                details={"product_id": product_id},
            )

    def _flag_duplicates(self, db: Session, context: ConfirmContext) -> None:
        """A code repeated in the file: none of its rows was applied (rejected at analysis); a person reviews it."""
        rejected = db.execute(
            select(ImportRow.business_key, ImportRow.messages).where(
                ImportRow.job_id == context.job_id, ImportRow.status == "rejected", ImportRow.business_key.is_not(None)
            )
        ).all()
        codes = {code for code, messages in rejected if any(m.get("code") == "duplicate_code" for m in messages)}
        for code in sorted(codes):
            record_issue(
                db,
                DUPLICATE_CODE,
                f"product_code:{code}",
                job_id=context.job_id,
                external_ref={"source_system": "STORE_SYS", "namespace": "product", "external_id": code},
                details={"product_code": code, "origin": TYPE_KEY},
            )


def _source(record: dict[str, Any]) -> catalog.SourceProduct:
    return catalog.SourceProduct(code=record["code"], description=record["description"], extra=record["extra"])
