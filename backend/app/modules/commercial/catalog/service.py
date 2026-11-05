"""Products, families and prices (DATA_MODEL §4.3, docs/IMPORTS.md §3.4).

`upsert_from_source` and `record_price` are what the STORE SYS articles adapter (F1.3) calls, inside the
confirmation's transaction: they refresh only `source_*` and the price history. Category, kind, eligibility and
family are DistriCom's: set by an explicit source-category mapping (only while the product has none) or by an admin,
never by an import and never inferred from the description.
"""

import hashlib
import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.commercial.catalog.models import Product, ProductFamily, ProductPrice
from app.modules.commercial.config.models import PRODUCT_KINDS, PriceList, ProductCategory, SourceCategoryMapping
from app.modules.commercial.config.service import source_key
from app.platform.audit.service import record_change
from app.platform.auth.models import User
from app.platform.mappings.service import source_system_id
from app.platform.quality.service import record_issue, resolve_open

STORE_SYS = "STORE_SYS"
WITHOUT_CATEGORY = "commercial.product.without_category"


@dataclass(frozen=True)
class SourceProduct:
    """An article as STORE SYS describes it, already parsed. `extra` keeps the columns DistriCom does not model."""

    code: str
    description: str | None = None
    category: str | None = None
    line: str | None = None
    capacity: str | None = None
    pack: str | None = None
    barcode: str | None = None
    internal_tax: Decimal | None = None
    discount_cap: Decimal | None = None
    vat: Decimal | None = None
    weight: Decimal | None = None
    extra: dict[str, Any] = field(default_factory=dict)


SOURCE_FIELDS = {
    "description": "source_description",
    "category": "source_category",
    "line": "source_line",
    "capacity": "source_capacity",
    "pack": "source_pack",
    "barcode": "source_barcode",
    "internal_tax": "source_internal_tax",
    "discount_cap": "source_discount_cap",
    "vat": "source_vat",
    "weight": "source_weight",
    "extra": "source_extra",
}


def _row_hash(record: SourceProduct) -> bytes:
    payload = {k: (str(v) if isinstance(v, Decimal) else v) for k, v in asdict(record).items()}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode()).digest()


def _category_for(db: Session, source_category: str | None) -> ProductCategory | None:
    key = source_key(source_category or "")
    if not key:
        return None
    return db.scalars(
        select(ProductCategory)
        .join(SourceCategoryMapping, SourceCategoryMapping.category_id == ProductCategory.id)
        .where(
            SourceCategoryMapping.source_system_id == source_system_id(db, STORE_SYS),
            SourceCategoryMapping.source_category_key == key,
        )
    ).first()


def _give_category(product: Product, category: ProductCategory, origin: str) -> None:
    """The category's defaults apply to what nobody decided by hand: eligibility unless MANUAL, kind while
    UNCLASSIFIED."""
    product.canonical_category_id = category.id
    product.category_origin = origin
    if product.eligibility_origin != "MANUAL":
        product.analytics_eligible = category.default_analytics_eligible
        product.eligibility_origin = "CATEGORY_DEFAULT"
    if product.product_kind == "UNCLASSIFIED" and category.default_product_kind:
        product.product_kind = category.default_product_kind


def _drop_category(product: Product) -> None:
    product.canonical_category_id = None
    product.category_origin = "NONE"
    product.analytics_eligible = False
    if product.eligibility_origin == "CATEGORY_DEFAULT":
        product.eligibility_origin = "NONE"


def _flag_without_category(db: Session, product: Product, job_id: int | None) -> None:
    if product.canonical_category_id is None:
        record_issue(
            db,
            WITHOUT_CATEGORY,
            f"product:{product.id}",
            job_id=job_id,
            entity_type="commercial.product",
            entity_id=product.id,
            details={"product_id": product.id},
        )


def upsert_from_source(db: Session, record: SourceProduct, *, job_id: int) -> tuple[Product, str]:
    """`(product, "inserted" | "updated" | "unchanged")`. Only `source_*` and the last job change on an existing
    product; its canonical fields stay as they are. A product without category gets one from an explicit
    source-category mapping, or stays out of the analytics with an issue."""
    code = " ".join(record.code.split())
    if not code:
        raise ValueError("A product needs a code")
    row_hash = _row_hash(record)
    product = db.scalars(select(Product).where(Product.source_code == code).with_for_update()).first()
    if product is None:
        product = Product(
            source_code=code[:40], source_row_hash=row_hash, first_seen_job_id=job_id, last_seen_job_id=job_id
        )
        outcome = "inserted"
    elif product.source_row_hash == row_hash:
        product.last_seen_job_id = job_id
        db.flush()
        return product, "unchanged"
    else:
        outcome = "updated"
    for name, column in SOURCE_FIELDS.items():
        setattr(product, column, getattr(record, name))
    product.source_row_hash = row_hash
    product.last_seen_job_id = job_id
    if product.id is None:
        db.add(product)
    db.flush()
    if product.category_origin == "NONE" and (category := _category_for(db, product.source_category)):
        _give_category(product, category, "SOURCE_MAPPING")
        db.flush()
    _flag_without_category(db, product, job_id)
    return product, outcome


def set_source_category(
    db: Session, actor: User, *, source_category: str, category_id: int, source_system: str = STORE_SYS
) -> SourceCategoryMapping:
    """Map a source rubro to a category (create or change), and give that category to the products of the rubro
    that have none yet. Products already classified, by an earlier mapping or by hand, keep theirs."""
    key = source_key(source_category)
    if not key:
        raise AppError(422, "invalid_source_category", "Indicá el rubro de origen.")
    category = db.get(ProductCategory, category_id)
    if category is None:
        raise AppError(422, "invalid_category", "La categoría no existe.")
    system_id = source_system_id(db, source_system)
    row = db.scalars(
        select(SourceCategoryMapping)
        .where(SourceCategoryMapping.source_system_id == system_id, SourceCategoryMapping.source_category_key == key)
        .with_for_update()
    ).first()
    before = None if row is None else {"category_id": row.category_id}
    if row is None:
        row = SourceCategoryMapping(
            source_system_id=system_id,
            source_category=" ".join(source_category.split())[:120],
            source_category_key=key[:120],
            category_id=category.id,
            decided_by=actor.id,
        )
        db.add(row)
    else:
        row.category_id, row.decided_by, row.decided_at = category.id, actor.id, func.now()
    db.flush()
    applied = 0
    if source_system == STORE_SYS:
        candidates = db.scalars(
            select(Product)
            .where(Product.category_origin == "NONE", Product.source_category.is_not(None))
            .with_for_update()
        ).all()
        for product in candidates:
            if source_key(product.source_category or "") == key:
                _give_category(product, category, "SOURCE_MAPPING")
                resolve_open(db, WITHOUT_CATEGORY, f"product:{product.id}", user_id=actor.id, note="Categoría asignada")
                applied += 1
    record_change(
        db,
        "commercial.config.source_category_mapped",
        action="create" if before is None else "update",
        user_id=actor.id,
        entity_type="commercial.source_category_mapping",
        entity_id=row.id,
        before=before,
        after={"category_id": category.id, "products_classified": applied},
    )
    db.commit()
    db.refresh(row)
    return row


def list_source_categories(db: Session) -> Sequence[SourceCategoryMapping]:
    return db.scalars(select(SourceCategoryMapping).order_by(SourceCategoryMapping.source_category_key)).all()


def unmapped_source_categories(db: Session) -> list[tuple[str, int]]:
    """Rubros of products still without category, with how many products each: what an admin maps next."""
    rows = db.execute(
        select(Product.source_category, func.count())
        .where(Product.category_origin == "NONE")
        .group_by(Product.source_category)
        .order_by(Product.source_category)
    ).all()
    return [(name or "", int(count)) for name, count in rows]


# --- canonical fields -------------------------------------------------------------------------------------------


def _canonical_state(product: Product) -> dict[str, Any]:
    return {
        "canonical_category_id": product.canonical_category_id,
        "category_origin": product.category_origin,
        "product_kind": product.product_kind,
        "analytics_eligible": product.analytics_eligible,
        "eligibility_origin": product.eligibility_origin,
        "family_id": product.family_id,
        "active": product.active,
    }


def set_canonical(db: Session, actor: User, product_id: int, changes: dict[str, Any], *, reason: str | None) -> Product:
    """An admin's decision on what DistriCom says of a product. Keys present in `changes` are applied:
    `category_id` (None removes it), `product_kind`, `analytics_eligible` (needs a category), `family_id`
    (None removes it), `active`. Audited with a reason; no import undoes it."""
    product = db.scalars(select(Product).where(Product.id == product_id).with_for_update()).first()
    if product is None:
        raise AppError(404, "product_not_found", "Ese producto no existe.")
    clean_reason = " ".join((reason or "").split())
    if not clean_reason:
        raise AppError(422, "reason_required", "Indicá el motivo del cambio.")
    before = _canonical_state(product)
    if "category_id" in changes:
        if changes["category_id"] is None:
            _drop_category(product)
        else:
            category = db.get(ProductCategory, changes["category_id"])
            if category is None:
                raise AppError(422, "invalid_category", "La categoría no existe.")
            _give_category(product, category, "MANUAL")
    if changes.get("product_kind") is not None:
        if changes["product_kind"] not in PRODUCT_KINDS:
            raise AppError(
                422, "invalid_product_kind", "Tipo de producto no permitido.", {"allowed": list(PRODUCT_KINDS)}
            )
        product.product_kind = changes["product_kind"]
    if changes.get("analytics_eligible") is not None:
        if changes["analytics_eligible"] and product.canonical_category_id is None:
            raise AppError(422, "eligible_needs_category", "Un producto sin categoría no entra en la analítica.")
        product.analytics_eligible = changes["analytics_eligible"]
        product.eligibility_origin = "MANUAL"
    if "family_id" in changes:
        family_id = changes["family_id"]
        if family_id is not None and db.get(ProductFamily, family_id) is None:
            raise AppError(422, "invalid_family", "La familia no existe.")
        product.family_id = family_id
    if changes.get("active") is not None:
        product.active = changes["active"]
    after = _canonical_state(product)
    if after != before:
        record_change(
            db,
            "commercial.catalog.product_updated",
            action="update",
            user_id=actor.id,
            entity_type="commercial.product",
            entity_id=product.id,
            before=before,
            after={**after, "reason": clean_reason[:500]},
        )
        if product.canonical_category_id is not None:
            resolve_open(db, WITHOUT_CATEGORY, f"product:{product.id}", user_id=actor.id, note="Categoría asignada")
        else:
            _flag_without_category(db, product, None)
    db.commit()
    db.refresh(product)
    return product


def get_product(db: Session, product_id: int) -> Product:
    product = db.get(Product, product_id)
    if product is None:
        raise AppError(404, "product_not_found", "Ese producto no existe.")
    return product


def list_products(
    db: Session,
    *,
    q: str | None,
    category_id: int | None,
    family_id: int | None,
    without_category: bool,
    analytics_eligible: bool | None,
    limit: int,
    offset: int,
) -> tuple[Sequence[Product], int]:
    query = select(Product)
    if q and (text := " ".join(q.split())):
        query = query.where(
            Product.source_code.icontains(text, autoescape=True)
            | Product.source_description.icontains(text, autoescape=True)
        )
    if category_id is not None:
        query = query.where(Product.canonical_category_id == category_id)
    if family_id is not None:
        query = query.where(Product.family_id == family_id)
    if without_category:
        query = query.where(Product.canonical_category_id.is_(None))
    if analytics_eligible is not None:
        query = query.where(Product.analytics_eligible.is_(analytics_eligible))
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(query.order_by(Product.source_code).offset(offset).limit(limit)).all()
    return rows, total


# --- families ---------------------------------------------------------------------------------------------------


def create_family(db: Session, actor: User, *, code: str, name: str, description: str | None) -> ProductFamily:
    clean_code = source_key(code).replace(" ", "_")
    clean_name = " ".join(name.split())
    if not clean_code or not clean_name:
        raise AppError(422, "invalid_family", "La familia necesita código y nombre.")
    if db.scalar(select(ProductFamily.id).where(ProductFamily.code == clean_code)) is not None:
        raise AppError(409, "family_code_taken", "Ya existe una familia con ese código.")
    family = ProductFamily(
        code=clean_code[:40], name=clean_name[:120], description=" ".join((description or "").split()) or None
    )
    db.add(family)
    db.flush()
    record_change(
        db,
        "commercial.catalog.family_created",
        action="create",
        user_id=actor.id,
        entity_type="commercial.product_family",
        entity_id=family.id,
        before=None,
        after={"code": family.code, "name": family.name},
    )
    db.commit()
    db.refresh(family)
    return family


def update_family(db: Session, actor: User, family_id: int, changes: dict[str, Any]) -> ProductFamily:
    family = db.scalars(select(ProductFamily).where(ProductFamily.id == family_id).with_for_update()).first()
    if family is None:
        raise AppError(404, "family_not_found", "Esa familia no existe.")
    before = {"name": family.name, "description": family.description, "active": family.active}
    if changes.get("name") is not None:
        if not (clean := " ".join(changes["name"].split())):
            raise AppError(422, "invalid_family", "La familia necesita un nombre.")
        family.name = clean[:120]
    if "description" in changes:
        family.description = " ".join((changes["description"] or "").split()) or None
    if changes.get("active") is not None:
        family.active = changes["active"]
    after = {"name": family.name, "description": family.description, "active": family.active}
    if after != before:
        record_change(
            db,
            "commercial.catalog.family_updated",
            action="update",
            user_id=actor.id,
            entity_type="commercial.product_family",
            entity_id=family.id,
            before=before,
            after=after,
        )
    db.commit()
    db.refresh(family)
    return family


def list_families(db: Session) -> Sequence[ProductFamily]:
    return db.scalars(select(ProductFamily).order_by(ProductFamily.code)).all()


# --- prices -----------------------------------------------------------------------------------------------------


class PriceOutOfOrderError(ValueError):
    """A price dated before the open one: history is written forward only; the adapter rejects the row."""


def record_price(
    db: Session, product_id: int, price_list_id: int, price: Decimal, *, valid_from: date, job_id: int
) -> str:
    """`"unchanged"`, `"opened"` or `"changed"`. The same price changes nothing; a new one closes the open row the day
    before and opens another (or voids it, when it opened that same day). Never an update of a price in place."""
    if price < 0:
        raise ValueError("A price cannot be negative")
    rows = db.scalars(
        select(ProductPrice)
        .where(
            ProductPrice.product_id == product_id,
            ProductPrice.price_list_id == price_list_id,
            ProductPrice.voided_by_job_id.is_(None),
        )
        .order_by(ProductPrice.valid_from.desc())
        .with_for_update()
    ).all()
    latest = rows[0] if rows else None
    if latest is not None and latest.valid_to is None and latest.price == price:
        return "unchanged"
    if latest is not None and (latest.valid_from > valid_from or (latest.valid_to and latest.valid_to >= valid_from)):
        raise PriceOutOfOrderError(f"Price for {valid_from} is older than the history of product {product_id}")
    outcome = "opened"
    if latest is not None and latest.valid_to is None:
        outcome = "changed"
        if latest.valid_from == valid_from:
            latest.voided_by_job_id = job_id
            latest.voided_at = func.now()
        else:
            latest.valid_to = valid_from - timedelta(days=1)
        db.flush()
    db.add(
        ProductPrice(
            product_id=product_id, price_list_id=price_list_id, price=price, valid_from=valid_from, import_job_id=job_id
        )
    )
    db.flush()
    return outcome


def price_history(db: Session, product_id: int) -> Sequence[tuple[ProductPrice, PriceList]]:
    return db.execute(
        select(ProductPrice, PriceList)
        .join(PriceList, PriceList.id == ProductPrice.price_list_id)
        .where(ProductPrice.product_id == product_id)
        .order_by(PriceList.list_number, ProductPrice.valid_from, ProductPrice.id)
    ).all()


def price_on(db: Session, product_id: int, price_list_id: int, on: date) -> Decimal | None:
    return db.scalar(
        select(ProductPrice.price).where(
            ProductPrice.product_id == product_id,
            ProductPrice.price_list_id == price_list_id,
            ProductPrice.voided_by_job_id.is_(None),
            ProductPrice.valid_from <= on,
            (ProductPrice.valid_to.is_(None)) | (ProductPrice.valid_to >= on),
        )
    )
