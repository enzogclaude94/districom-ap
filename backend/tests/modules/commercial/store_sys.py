"""Synthetic STORE SYS exports with the structure of the real originals (docs/IMPORTS.md §3.2-§3.5, inputs.md):
BIFF7, cp1252, one sheet named as STORE SYS names it, the raw field names in row 1, codes as numbers, dates as
date-formatted numbers. No value comes from a real file."""

import io
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.platform.auth.models import User
from app.platform.imports import service
from app.platform.imports.models import ImportJob
from app.platform.imports.registry import Decision, Declared
from tests.imports.files import biff7_xls

CUSTOMERS = "commercial.store_sys.customers"
DOCUMENTS = "commercial.store_sys.documents"
SALES = "commercial.store_sys.sales_lines"
PRODUCTS = "commercial.store_sys.products"

NO_CUIT = "  -        -"  # how STORE SYS writes an empty CUIT

CUSTOMER_HEADER = ["codigo", "fantasia", "razons", "direccion", "telefono", "cuit", "zona", "vend"]
ARTICLE_HEADER = ["codart", "desart", "precio"]
DOCUMENT_HEADER = ["fechor", "nommov", "nombre", "codcli", "netosa", "total", "descue", "fechas"]
SALES_HEADER = [
    "codigo",
    "descripcion",
    "cantidad",
    "rubro",
    "cliente",
    "vendedor",
    "zona",
    "fecha",
    "lista_de_precios",
]


def customer(
    code: int,
    name: str,
    *,
    zone: str | None = "RUTA 01 - LUVI",
    seller: str | None = "VENDEDOR UNO",
    cuit: str | None = NO_CUIT,
    legal: str | None = None,
    address: str | None = "CALLE FALSA 123",
    phone: str | None = None,
) -> list[Any]:
    return [code, name, legal, address, phone, cuit, zone, seller]


def customers_file(rows: Sequence[Sequence[Any]]) -> bytes:
    return biff7_xls([CUSTOMER_HEADER, *rows], sheet_name="clientes")


def article(code: Any, description: str | None = "ARTICULO SINTETICO X6", price: Any = Decimal("1000.00")) -> list[Any]:
    return [code, description, price]


def articles_file(rows: Sequence[Sequence[Any]], header: Sequence[str] = ARTICLE_HEADER) -> bytes:
    return biff7_xls([list(header), *rows], sheet_name="lista de articulos")


def document(
    nommov: str,
    *,
    code: int = 1,
    name: str | None = "CLIENTE SINTETICO",
    net: Decimal | int = Decimal("100.00"),
    total: Decimal | int = Decimal("121.00"),
    discount: Decimal | int = 0,
    day: date = date(2026, 10, 23),
    at: datetime | None = None,
) -> list[Any]:
    moment = at or datetime(day.year, day.month, day.day, 9, 30)
    return [moment, nommov, name, code, net, total, discount, day]


def documents_file(rows: Sequence[Sequence[Any]]) -> bytes:
    return biff7_xls([DOCUMENT_HEADER, *rows], sheet_name="comprobantes emitidos")


def sale(
    code: int,
    customer_name: str,
    *,
    quantity: Decimal | int = 1,
    day: date = date(2026, 10, 23),
    seller: str | None = "VENDEDOR UNO",
    zone: str | None = "RUTA 01 - LUVI",
    price_list: str | None = None,
    description: str = "ARTICULO SINTETICO",
    category: str | None = "GASEOSAS",
) -> list[Any]:
    return [code, description, quantity, category, customer_name, seller, zone, day, price_list]


def sales_file(rows: Sequence[Sequence[Any]]) -> bytes:
    return biff7_xls([SALES_HEADER, *rows], sheet_name="ventas")


def analyze(
    db: Session,
    user: User,
    data: bytes,
    import_type: str | None,
    *,
    snapshot_date: date | None = None,
    scope: str | None = None,
    scope_note: str | None = None,
    name: str = "export.xls",
) -> ImportJob:
    return service.upload(
        db,
        user,
        get_settings(),
        service.Upload(io.BytesIO(data), name, None),
        import_type,
        Declared(snapshot_date, scope, scope_note),
    )


def confirm(db: Session, user: User, job: ImportJob, decision: Decision | None = None) -> ImportJob:
    return service.confirm(db, user, get_settings(), job.id, decision)


def run(db: Session, user: User, data: bytes, import_type: str | None, **declared: Any) -> ImportJob:
    job = analyze(db, user, data, import_type, **declared)
    assert job.status == "pending", (job.status, job.failure_reason, job.failure_detail)
    return confirm(db, user, job)
