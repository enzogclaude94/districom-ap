"""How Comercial's facts follow a mapping decision (F1.5, docs/IMPORTS.md §6.4).

Registered in `ModuleSpec.mapping_resolvers`; the platform applies them inside the decision's locked transaction.
Each fact is found by the mapping id it stored at import time, never by a name or a code, so a row resolved by
another mapping is never touched:

- Cliente STORE SYS → `sale_lines.customer_id`, `documents.customer_id`;
- Cliente AXUM → `no_purchase_events.customer_id`;
- Vendedor STORE SYS → `customer_salesperson_assignments.salesperson_id` (the same row: no new validity period,
  the history stays as it was) and `sale_lines.salesperson_id`;
- Preventista AXUM → `no_purchase_events.salesperson_id`.

An issue is closed only when its condition went away: the mapping is decided (confirmed or excluded) and, for a
STORE SYS customer code, no fact with that code is left without a mapping to complete it.
"""

from typing import Any

from sqlalchemy import ColumnElement, exists, func, or_, select
from sqlalchemy.orm import InstrumentedAttribute, Session

from app.modules.commercial.assignments.models import CustomerSalespersonAssignment
from app.modules.commercial.config.models import DocumentType
from app.modules.commercial.documents.models import Document
from app.modules.commercial.imports.documents import NOT_CUSTOMER_CODE_CLASSES
from app.modules.commercial.no_purchase.models import NoPurchaseEvent
from app.modules.commercial.sales.models import SaleLine
from app.platform.mappings.models import ExternalEntityMapping
from app.platform.mappings.resolvers import FactLink, MappingResolverSpec
from app.platform.quality.service import resolve_open

DECIDED = ("confirmed", "excluded")


def _note(mapping: ExternalEntityMapping) -> str:
    return f"Vínculo {mapping.id} decidido ({mapping.status})"


def _close(db: Session, mapping: ExternalEntityMapping, user_id: int, codes: tuple[str, ...], dedupe: str) -> int:
    if mapping.status not in DECIDED:
        return 0  # rejected: the code is still unlinked and its facts still pending
    return sum(resolve_open(db, code, dedupe, user_id=user_id, note=_note(mapping)) for code in codes)


def _keyed(column: InstrumentedAttribute[Any]) -> ColumnElement[str]:
    """`mappings.code_key` in SQL: trimmed, inner whitespace collapsed. Only to tell whether an issue still has
    facts behind it; facts are never updated by code."""
    return func.regexp_replace(func.btrim(column), r"\s+", " ", "g")


def _store_sys_customer_issues(db: Session, mapping: ExternalEntityMapping, user_id: int) -> int:
    if mapping.status not in DECIDED:
        return 0
    code = mapping.external_id
    # Facts with this code imported before the mapping existed carry no mapping id: the decision cannot complete
    # them, so the issue stays open for them.
    orphan_lines = exists().where(
        SaleLine.customer_mapping_id.is_(None),
        SaleLine.customer_id.is_(None),
        _keyed(SaleLine.source_customer_code) == code,
    )
    orphan_documents = exists().where(
        Document.customer_mapping_id.is_(None),
        Document.customer_id.is_(None),
        Document.source_customer_code.is_not(None),
        _keyed(Document.source_customer_code) == code,
        Document.document_type_id.in_(
            select(DocumentType.id).where(DocumentType.document_class.not_in(NOT_CUSTOMER_CODE_CLASSES))
        ),
    )
    if db.scalar(select(or_(orphan_lines, orphan_documents))):
        return 0
    return _close(db, mapping, user_id, ("commercial.customer.unresolved_code",), f"customer_code:{code}")


def _axum_customer_issues(db: Session, mapping: ExternalEntityMapping, user_id: int) -> int:
    codes = ("commercial.axum_customer.unresolved", "commercial.axum_customer.name_mismatch")
    return _close(db, mapping, user_id, codes, f"axum_customer:{mapping.external_id}")


def _store_sys_seller_issues(db: Session, mapping: ExternalEntityMapping, user_id: int) -> int:
    return _close(db, mapping, user_id, ("commercial.seller.unmapped",), f"mapping:{mapping.id}")


def _axum_salesperson_issues(db: Session, mapping: ExternalEntityMapping, user_id: int) -> int:
    return _close(db, mapping, user_id, ("commercial.axum_salesperson.unmapped",), f"mapping:{mapping.id}")


SALE_LINES = "commercial.sale_lines"
DOCUMENTS = "commercial.documents"
NO_PURCHASE_EVENTS = "commercial.no_purchase_events"
SELLER_ASSIGNMENTS = "commercial.customer_salesperson_assignments"

# Superseded lines and events are history: re-resolved too, counted apart.
ACTIVE_LINE = SaleLine.status == "ACTIVE"
ACTIVE_EVENT = NoPurchaseEvent.status == "ACTIVE"

RESOLVERS = (
    MappingResolverSpec(
        "customer",
        "STORE_SYS",
        "customer",
        "Cliente STORE SYS",
        facts=(
            FactLink(SALE_LINES, "líneas de venta", SaleLine.customer_id, SaleLine.customer_mapping_id, ACTIVE_LINE),
            FactLink(DOCUMENTS, "comprobantes", Document.customer_id, Document.customer_mapping_id),
        ),
        close_issues=_store_sys_customer_issues,
    ),
    MappingResolverSpec(
        "customer",
        "AXUM",
        "customer",
        "Cliente AXUM",
        facts=(
            FactLink(
                NO_PURCHASE_EVENTS,
                "eventos de no compra",
                NoPurchaseEvent.customer_id,
                NoPurchaseEvent.customer_mapping_id,
                ACTIVE_EVENT,
            ),
        ),
        close_issues=_axum_customer_issues,
    ),
    MappingResolverSpec(
        "employee",
        "STORE_SYS",
        "seller",
        "Vendedor STORE SYS",
        facts=(
            FactLink(
                SELLER_ASSIGNMENTS,
                "asignaciones de cartera",
                CustomerSalespersonAssignment.salesperson_id,
                CustomerSalespersonAssignment.seller_mapping_id,
                CustomerSalespersonAssignment.voided_by_job_id.is_(None),
            ),
            FactLink(SALE_LINES, "líneas de venta", SaleLine.salesperson_id, SaleLine.seller_mapping_id, ACTIVE_LINE),
        ),
        close_issues=_store_sys_seller_issues,
    ),
    MappingResolverSpec(
        "employee",
        "AXUM",
        "salesperson",
        "Preventista AXUM",
        facts=(
            FactLink(
                NO_PURCHASE_EVENTS,
                "eventos de no compra",
                NoPurchaseEvent.salesperson_id,
                NoPurchaseEvent.salesperson_mapping_id,
                ACTIVE_EVENT,
            ),
        ),
        close_issues=_axum_salesperson_issues,
    ),
)
