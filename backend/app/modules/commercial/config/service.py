"""Zones, visit days, classification rules, price lists, categories, document types and no-purchase reasons
(DATA_MODEL §4.2, docs/IMPORTS.md §3).

`ensure_zone`, `ensure_document_type` and `ensure_reason` are what the adapters (F1.3/F1.4) call when a file names
one that does not exist yet; everything else is a module admin's decision, audited as `commercial.config.*`.
"""

import unicodedata
from collections.abc import Sequence
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.commercial.config.models import (
    DOCUMENT_CLASSES,
    PRICE_LIST_KINDS,
    PRODUCT_KINDS,
    ZONE_TYPES,
    DocumentType,
    NoPurchaseReasonCategory,
    NoPurchaseReasonMapping,
    PriceList,
    ProductCategory,
    Zone,
    ZoneClassificationRule,
    ZoneVisitDay,
)
from app.platform.audit.service import record_change
from app.platform.auth.models import User
from app.platform.mappings.service import source_system_id
from app.platform.quality.service import record_issue, resolve_open

REASON_LENGTH = 500


def source_key(value: str) -> str:
    """The comparison key of a name as a source writes it (zones, document types, rubros, reasons): upper case, no
    accents, single spaces. «Ruta 07 - Tacañitas/Bandera» → «RUTA 07 - TACANITAS/BANDERA»."""
    decomposed = unicodedata.normalize("NFKD", value.upper())
    plain = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(plain.split())


def _words(value: str) -> list[str]:
    return "".join(ch if ch.isalnum() else " " for ch in source_key(value)).split()


def _clean(value: str | None) -> str | None:
    clean = " ".join(value.split()) if value else ""
    return clean or None


def _reason(reason: str | None, *, required: bool) -> str | None:
    clean = _clean(reason)
    if required and clean is None:
        raise AppError(422, "reason_required", "Indicá el motivo del cambio.")
    if clean and len(clean) > REASON_LENGTH:
        raise AppError(422, "reason_too_long", f"El motivo tiene como máximo {REASON_LENGTH} caracteres.")
    return clean


def _choice(value: str, allowed: Sequence[str], code: str) -> str:
    if value not in allowed:
        raise AppError(422, code, "Valor no permitido.", {"allowed": list(allowed)})
    return value


def _audit(
    db: Session,
    actor: User,
    kind: str,
    action: str,
    entity_id: int,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> None:
    record_change(
        db,
        f"commercial.config.{kind}_{action}",
        action=action,
        user_id=actor.id,
        entity_type=f"commercial.{kind}",
        entity_id=entity_id,
        before=before,
        after=after,
    )


# --- zones ------------------------------------------------------------------------------------------------------


def rule_matches(rule: ZoneClassificationRule, name_key: str) -> bool:
    """CONTAINS: the pattern's words appear together, as whole words, in the zone's name. `LUVI` matches
    `RUTA 01 - LUVI` but not `RUTA 01 - LUVIANO`; `BANDERA` matches `RUTA 07 - TACANITAS/BANDERA`."""
    pattern, words = _words(rule.pattern), _words(name_key)
    if not pattern:
        return False
    return any(words[i : i + len(pattern)] == pattern for i in range(len(words) - len(pattern) + 1))


def matching_rule(db: Session, name_key: str) -> ZoneClassificationRule | None:
    rules = db.scalars(
        select(ZoneClassificationRule)
        .where(ZoneClassificationRule.active.is_(True))
        .order_by(ZoneClassificationRule.priority, ZoneClassificationRule.id)
    ).all()
    return next((rule for rule in rules if rule_matches(rule, name_key)), None)


def find_zone(db: Session, source_name: str) -> Zone | None:
    return db.scalars(select(Zone).where(Zone.name_key == source_key(source_name))).first()


def ensure_zone(
    db: Session, source_name: str, *, effective_from: date, job_id: int | None, user_id: int | None
) -> tuple[Zone, bool]:
    """The zone a file names, created when it is new (returns `(zone, created)`), in the caller's transaction.

    A new zone takes the first active rule that matches (type, visit and days from `effective_from`), once. With no
    rule it is UNCLASSIFIED, without visits, and an issue asks an admin to configure it (MISA, until Enzo defines
    its days). An existing zone is returned as it is: a rule never changes it."""
    key = source_key(source_name)
    if not key:
        raise ValueError("A zone needs a name")
    existing = db.scalars(select(Zone).where(Zone.name_key == key).with_for_update()).first()
    if existing is not None:
        return existing, False
    rule = matching_rule(db, key)
    zone = Zone(
        source_name=" ".join(source_name.split())[:120],
        name_key=key[:120],
        display_name=" ".join(source_name.split())[:120],
        zone_type=rule.zone_type if rule else "UNCLASSIFIED",
        requires_visit=rule.requires_visit if rule else False,
        configuration_origin="RULE" if rule else "NONE",
        rule_id=rule.id if rule else None,
        first_seen_job_id=job_id,
    )
    db.add(zone)
    db.flush()
    if rule is not None:
        _open_days(db, zone.id, sorted(set(rule.weekdays)), effective_from, user_id)
    else:
        record_issue(
            db,
            "commercial.zone.unconfigured",
            f"zone:{zone.id}",
            job_id=job_id,
            entity_type="commercial.zone",
            entity_id=zone.id,
            details={"zone_id": zone.id},
        )
    db.flush()
    return zone, True


def _open_days(db: Session, zone_id: int, weekdays: Sequence[int], valid_from: date, user_id: int | None) -> None:
    for weekday in weekdays:
        db.add(ZoneVisitDay(zone_id=zone_id, weekday=weekday, valid_from=valid_from, created_by=user_id))


def current_weekdays(db: Session, zone_id: int, on: date) -> list[int]:
    return sorted(
        db.scalars(
            select(ZoneVisitDay.weekday).where(
                ZoneVisitDay.zone_id == zone_id,
                ZoneVisitDay.voided_at.is_(None),
                ZoneVisitDay.valid_from <= on,
                (ZoneVisitDay.valid_to.is_(None)) | (ZoneVisitDay.valid_to >= on),
            )
        )
    )


def _set_days(db: Session, zone_id: int, weekdays: set[int], effective_from: date, user_id: int) -> None:
    """From `effective_from` on, the zone is visited on exactly `weekdays`: rows that go on stay, the others end
    the day before (or are voided if they had not started), the missing ones open."""
    open_rows = db.scalars(
        select(ZoneVisitDay)
        .where(ZoneVisitDay.zone_id == zone_id, ZoneVisitDay.valid_to.is_(None), ZoneVisitDay.voided_at.is_(None))
        .with_for_update()
    ).all()
    kept = set()
    for row in open_rows:
        if row.weekday in weekdays and row.valid_from <= effective_from:
            kept.add(row.weekday)
        elif row.valid_from < effective_from:
            row.valid_to = effective_from - timedelta(days=1)
        else:
            row.voided_at = func.now()
    db.flush()
    _open_days(db, zone_id, sorted(weekdays - kept), effective_from, user_id)
    db.flush()


def _check_forward(db: Session, zone_id: int, effective_from: date) -> None:
    """Visit days are written forward only: a change cannot start before the last one did (nor inside a range that
    already ended), or the history would overlap. Reordering history is not an admin edit."""
    latest_start, latest_end = db.execute(
        select(func.max(ZoneVisitDay.valid_from), func.max(ZoneVisitDay.valid_to)).where(
            ZoneVisitDay.zone_id == zone_id, ZoneVisitDay.voided_at.is_(None)
        )
    ).one()
    if (latest_start and effective_from < latest_start) or (latest_end and effective_from <= latest_end):
        raise AppError(
            409,
            "zone_change_out_of_order",
            "La fecha de vigencia es anterior al último cambio de días de la zona.",
            {"latest_change": max(d for d in (latest_start, latest_end) if d).isoformat()},
        )


def _zone_state(db: Session, zone: Zone, on: date) -> dict[str, Any]:
    return {
        "display_name": zone.display_name,
        "zone_type": zone.zone_type,
        "requires_visit": zone.requires_visit,
        "weekdays": current_weekdays(db, zone.id, on),
        "active": zone.active,
        "configuration_origin": zone.configuration_origin,
    }


def configure_zone(
    db: Session,
    actor: User,
    zone_id: int,
    *,
    zone_type: str,
    requires_visit: bool,
    weekdays: Sequence[int],
    effective_from: date,
    display_name: str | None,
    active: bool | None,
    reason: str | None,
) -> Zone:
    """An admin's explicit configuration: from now on the zone is MANUAL and no rule touches it again."""
    actor_id = actor.id  # read before any change: an expired actor would autoflush a half-made configuration
    zone = db.scalars(select(Zone).where(Zone.id == zone_id).with_for_update()).first()
    if zone is None:
        raise AppError(404, "zone_not_found", "Esa zona no existe.")
    _choice(zone_type, ZONE_TYPES, "invalid_zone_type")
    days = set(weekdays)
    if not days <= set(range(1, 8)):
        raise AppError(422, "invalid_weekday", "Los días van de 1 (lunes) a 7 (domingo).")
    if zone_type in ("UNCLASSIFIED", "OUTLET") and requires_visit:
        raise AppError(422, "zone_without_visit", "Una zona sin clasificar o de boca de expendio no se visita.")
    if requires_visit and not days:
        raise AppError(422, "visit_days_required", "Indicá los días de visita.")
    if not requires_visit and days:
        raise AppError(422, "visit_days_without_visit", "Una zona sin visita no tiene días.")
    clean_reason = _reason(reason, required=True)
    _check_forward(db, zone.id, effective_from)
    before = _zone_state(db, zone, effective_from)
    zone.zone_type = zone_type
    zone.requires_visit = requires_visit
    if display_name is not None:
        if not (name := _clean(display_name)):
            raise AppError(422, "invalid_zone_name", "El nombre no puede quedar vacío.")
        zone.display_name = name[:120]
    if active is not None:
        zone.active = active
    zone.configuration_origin = "MANUAL"
    zone.configured_by = actor_id
    zone.configured_at = func.now()
    db.flush()
    _set_days(db, zone.id, days, effective_from, actor_id)
    after = _zone_state(db, zone, effective_from)
    _audit(
        db,
        actor,
        "zone",
        "configured",
        zone.id,
        before,
        {**after, "effective_from": effective_from.isoformat(), "reason": clean_reason},
    )
    resolve_open(db, "commercial.zone.unconfigured", f"zone:{zone.id}", user_id=actor.id, note="Zona configurada")
    db.commit()
    db.refresh(zone)
    return zone


def list_zones(db: Session) -> Sequence[Zone]:
    return db.scalars(select(Zone).order_by(Zone.name_key)).all()


def get_zone(db: Session, zone_id: int) -> Zone:
    zone = db.get(Zone, zone_id)
    if zone is None:
        raise AppError(404, "zone_not_found", "Esa zona no existe.")
    return zone


def visit_day_history(db: Session, zone_id: int) -> Sequence[ZoneVisitDay]:
    return db.scalars(
        select(ZoneVisitDay)
        .where(ZoneVisitDay.zone_id == zone_id)
        .order_by(ZoneVisitDay.valid_from, ZoneVisitDay.weekday)
    ).all()


# --- zone classification rules ---------------------------------------------------------------------------------


def _check_rule(zone_type: str, requires_visit: bool, weekdays: Sequence[int]) -> list[int]:
    if zone_type == "UNCLASSIFIED":
        raise AppError(422, "invalid_zone_type", "Una regla clasifica: no puede proponer UNCLASSIFIED.")
    _choice(zone_type, ZONE_TYPES, "invalid_zone_type")
    days = sorted(set(weekdays))
    if not set(days) <= set(range(1, 8)):
        raise AppError(422, "invalid_weekday", "Los días van de 1 (lunes) a 7 (domingo).")
    if zone_type == "OUTLET" and requires_visit:
        raise AppError(422, "zone_without_visit", "La boca de expendio no se visita.")
    if requires_visit != bool(days):
        raise AppError(422, "visit_days_mismatch", "Con visita hacen falta días; sin visita, ninguno.")
    return days


def _rule_state(rule: ZoneClassificationRule) -> dict[str, Any]:
    return {
        "priority": rule.priority,
        "pattern": rule.pattern,
        "zone_type": rule.zone_type,
        "requires_visit": rule.requires_visit,
        "weekdays": list(rule.weekdays),
        "active": rule.active,
    }


def create_rule(
    db: Session,
    actor: User,
    *,
    priority: int,
    pattern: str,
    zone_type: str,
    requires_visit: bool,
    weekdays: Sequence[int],
    description: str | None,
) -> ZoneClassificationRule:
    """A new rule only affects zones created after it; existing zones never change."""
    key = source_key(pattern)
    if not key:
        raise AppError(422, "invalid_rule_pattern", "La regla necesita un patrón.")
    if db.scalar(select(ZoneClassificationRule.id).where(ZoneClassificationRule.pattern == key)) is not None:
        raise AppError(409, "rule_pattern_taken", "Ya hay una regla con ese patrón.")
    rule = ZoneClassificationRule(
        priority=priority,
        match_type="CONTAINS",
        pattern=key[:120],
        zone_type=zone_type,
        requires_visit=requires_visit,
        weekdays=_check_rule(zone_type, requires_visit, weekdays),
        description=_clean(description),
    )
    db.add(rule)
    db.flush()
    _audit(db, actor, "zone_rule", "created", rule.id, None, _rule_state(rule))
    db.commit()
    db.refresh(rule)
    return rule


def update_rule(db: Session, actor: User, rule_id: int, changes: dict[str, Any]) -> ZoneClassificationRule:
    rule = db.scalars(
        select(ZoneClassificationRule).where(ZoneClassificationRule.id == rule_id).with_for_update()
    ).first()
    if rule is None:
        raise AppError(404, "zone_rule_not_found", "Esa regla no existe.")
    before = _rule_state(rule)
    zone_type = changes.get("zone_type", rule.zone_type)
    requires_visit = changes.get("requires_visit", rule.requires_visit)
    weekdays = changes.get("weekdays", list(rule.weekdays))
    rule.weekdays = _check_rule(zone_type, requires_visit, weekdays)
    rule.zone_type, rule.requires_visit = zone_type, requires_visit
    for name in ("priority", "active"):
        if name in changes and changes[name] is not None:
            setattr(rule, name, changes[name])
    if "description" in changes:
        rule.description = _clean(changes["description"])
    after = _rule_state(rule)
    if after != before:
        _audit(db, actor, "zone_rule", "updated", rule.id, before, after)
    db.commit()
    db.refresh(rule)
    return rule


def list_rules(db: Session) -> Sequence[ZoneClassificationRule]:
    return db.scalars(
        select(ZoneClassificationRule).order_by(ZoneClassificationRule.priority, ZoneClassificationRule.id)
    ).all()


# --- price lists ------------------------------------------------------------------------------------------------


def _list_state(row: PriceList) -> dict[str, Any]:
    return {
        "list_number": row.list_number,
        "code": row.code,
        "name": row.name,
        "kind": row.kind,
        "commercially_relevant": row.commercially_relevant,
        "active": row.active,
    }


def create_price_list(
    db: Session,
    actor: User,
    *,
    list_number: int,
    name: str,
    kind: str,
    commercially_relevant: bool,
    description: str | None,
) -> PriceList:
    _choice(kind, PRICE_LIST_KINDS, "invalid_price_list_kind")
    if not (clean_name := _clean(name)):
        raise AppError(422, "invalid_price_list_name", "La lista necesita un nombre.")
    if db.scalar(select(PriceList.id).where(PriceList.list_number == list_number)) is not None:
        raise AppError(409, "price_list_taken", "Ya existe una lista con ese número.")
    row = PriceList(
        list_number=list_number,
        code=f"LISTA_{list_number}",
        name=clean_name[:120],
        description=_clean(description),
        kind=kind,
        commercially_relevant=commercially_relevant,
    )
    db.add(row)
    db.flush()
    _audit(db, actor, "price_list", "created", row.id, None, _list_state(row))
    db.commit()
    db.refresh(row)
    return row


def update_price_list(db: Session, actor: User, list_id: int, changes: dict[str, Any]) -> PriceList:
    row = db.scalars(select(PriceList).where(PriceList.id == list_id).with_for_update()).first()
    if row is None:
        raise AppError(404, "price_list_not_found", "Esa lista no existe.")
    before = _list_state(row)
    if changes.get("kind") is not None:
        row.kind = _choice(changes["kind"], PRICE_LIST_KINDS, "invalid_price_list_kind")
    if changes.get("name") is not None:
        if not (clean_name := _clean(changes["name"])):
            raise AppError(422, "invalid_price_list_name", "La lista necesita un nombre.")
        row.name = clean_name[:120]
    if "description" in changes:
        row.description = _clean(changes["description"])
    for name in ("commercially_relevant", "active"):
        if changes.get(name) is not None:
            setattr(row, name, changes[name])
    after = _list_state(row)
    if after != before:
        _audit(db, actor, "price_list", "updated", row.id, before, after)
    db.commit()
    db.refresh(row)
    return row


def price_list_by_number(db: Session, list_number: int) -> PriceList | None:
    return db.scalars(select(PriceList).where(PriceList.list_number == list_number)).first()


def list_price_lists(db: Session) -> Sequence[PriceList]:
    return db.scalars(select(PriceList).order_by(PriceList.list_number)).all()


# --- product categories -----------------------------------------------------------------------------------------


def _category_state(row: ProductCategory) -> dict[str, Any]:
    return {
        "code": row.code,
        "name": row.name,
        "default_product_kind": row.default_product_kind,
        "default_analytics_eligible": row.default_analytics_eligible,
        "parent_id": row.parent_id,
        "active": row.active,
    }


def create_category(
    db: Session,
    actor: User,
    *,
    code: str,
    name: str,
    default_product_kind: str | None,
    default_analytics_eligible: bool,
    parent_id: int | None,
    sort_order: int,
) -> ProductCategory:
    clean_code = source_key(code).replace(" ", "_")
    if not clean_code or not (clean_name := _clean(name)):
        raise AppError(422, "invalid_category", "La categoría necesita código y nombre.")
    if default_product_kind is not None:
        _choice(default_product_kind, PRODUCT_KINDS, "invalid_product_kind")
    if db.scalar(select(ProductCategory.id).where(ProductCategory.code == clean_code)) is not None:
        raise AppError(409, "category_code_taken", "Ya existe una categoría con ese código.")
    if parent_id is not None and db.get(ProductCategory, parent_id) is None:
        raise AppError(422, "invalid_parent_category", "La categoría padre no existe.")
    row = ProductCategory(
        code=clean_code[:40],
        name=clean_name[:120],
        default_product_kind=default_product_kind,
        default_analytics_eligible=default_analytics_eligible,
        parent_id=parent_id,
        sort_order=sort_order,
    )
    db.add(row)
    db.flush()
    _audit(db, actor, "category", "created", row.id, None, _category_state(row))
    db.commit()
    db.refresh(row)
    return row


def update_category(db: Session, actor: User, category_id: int, changes: dict[str, Any]) -> ProductCategory:
    """Changing a category's defaults never rewrites products already classified (their eligibility was decided
    when they got the category); it applies to products that get the category from now on."""
    row = db.scalars(select(ProductCategory).where(ProductCategory.id == category_id).with_for_update()).first()
    if row is None:
        raise AppError(404, "category_not_found", "Esa categoría no existe.")
    before = _category_state(row)
    if changes.get("name") is not None:
        if not (clean_name := _clean(changes["name"])):
            raise AppError(422, "invalid_category", "La categoría necesita un nombre.")
        row.name = clean_name[:120]
    if "default_product_kind" in changes:
        kind = changes["default_product_kind"]
        row.default_product_kind = None if kind is None else _choice(kind, PRODUCT_KINDS, "invalid_product_kind")
    for name in ("default_analytics_eligible", "active", "sort_order"):
        if changes.get(name) is not None:
            setattr(row, name, changes[name])
    after = _category_state(row)
    if after != before:
        _audit(db, actor, "category", "updated", row.id, before, after)
    db.commit()
    db.refresh(row)
    return row


def list_categories(db: Session) -> Sequence[ProductCategory]:
    return db.scalars(select(ProductCategory).order_by(ProductCategory.sort_order, ProductCategory.code)).all()


# --- document types ---------------------------------------------------------------------------------------------


def find_document_type(db: Session, source_name: str) -> DocumentType | None:
    return db.scalars(select(DocumentType).where(DocumentType.source_name_key == source_key(source_name))).first()


def ensure_document_type(db: Session, source_name: str, *, job_id: int | None) -> tuple[DocumentType, bool]:
    """The type a file names; a new one enters UNCLASSIFIED, outside the commercial universe, with an issue, until
    an admin classifies it. Never classified by its name."""
    key = source_key(source_name)
    if not key:
        raise ValueError("A document type needs a name")
    existing = db.scalars(select(DocumentType).where(DocumentType.source_name_key == key)).first()
    if existing is not None:
        return existing, False
    row = DocumentType(
        source_name=" ".join(source_name.split())[:120],
        source_name_key=key[:120],
        document_class="UNCLASSIFIED",
        sign=0,
        commercial_universe=False,
    )
    db.add(row)
    db.flush()
    record_issue(
        db,
        "commercial.document.unclassified_type",
        f"document_type:{row.id}",
        job_id=job_id,
        entity_type="commercial.document_type",
        entity_id=row.id,
        details={"document_type_id": row.id},
    )
    return row, True


def _type_state(row: DocumentType) -> dict[str, Any]:
    return {
        "document_class": row.document_class,
        "sign": row.sign,
        "commercial_universe": row.commercial_universe,
        "active": row.active,
    }


def classify_document_type(
    db: Session,
    actor: User,
    type_id: int,
    *,
    document_class: str,
    sign: int,
    commercial_universe: bool,
    reason: str | None,
) -> DocumentType:
    """An accounting interpretation: admin only, always with a reason (CLAUDE.md §12)."""
    row = db.scalars(select(DocumentType).where(DocumentType.id == type_id).with_for_update()).first()
    if row is None:
        raise AppError(404, "document_type_not_found", "Ese tipo de comprobante no existe.")
    _choice(document_class, DOCUMENT_CLASSES, "invalid_document_class")
    if sign not in (-1, 0, 1):
        raise AppError(422, "invalid_sign", "El signo es -1, 0 o 1.")
    if document_class == "UNCLASSIFIED" and (commercial_universe or sign != 0):
        raise AppError(422, "unclassified_outside", "Un tipo sin clasificar queda fuera del universo y sin signo.")
    if commercial_universe and sign == 0:
        raise AppError(422, "universe_needs_sign", "Un tipo del universo comercial necesita signo.")
    clean = _reason(reason, required=True)
    before = _type_state(row)
    row.document_class, row.sign, row.commercial_universe = document_class, sign, commercial_universe
    row.decided_by = actor.id
    row.decided_at = func.now()
    _audit(db, actor, "document_type", "classified", row.id, before, {**_type_state(row), "reason": clean})
    if document_class != "UNCLASSIFIED":
        resolve_open(
            db, "commercial.document.unclassified_type", f"document_type:{row.id}", user_id=actor.id, note="Clasificado"
        )
    db.commit()
    db.refresh(row)
    return row


def list_document_types(db: Session) -> Sequence[DocumentType]:
    return db.scalars(select(DocumentType).order_by(DocumentType.source_name_key)).all()


# --- no-purchase reasons ----------------------------------------------------------------------------------------


def create_reason_category(db: Session, actor: User, *, code: str, name: str) -> NoPurchaseReasonCategory:
    clean_code = source_key(code).replace(" ", "_")
    if not clean_code or not (clean_name := _clean(name)):
        raise AppError(422, "invalid_reason_category", "La categoría necesita código y nombre.")
    if db.scalar(select(NoPurchaseReasonCategory.id).where(NoPurchaseReasonCategory.code == clean_code)) is not None:
        raise AppError(409, "reason_category_taken", "Ya existe una categoría con ese código.")
    row = NoPurchaseReasonCategory(code=clean_code[:40], name=clean_name[:120])
    db.add(row)
    db.flush()
    _audit(db, actor, "no_purchase_category", "created", row.id, None, {"code": row.code, "name": row.name})
    db.commit()
    db.refresh(row)
    return row


def update_reason_category(
    db: Session, actor: User, category_id: int, *, name: str | None, active: bool | None
) -> NoPurchaseReasonCategory:
    row = db.scalars(
        select(NoPurchaseReasonCategory).where(NoPurchaseReasonCategory.id == category_id).with_for_update()
    ).first()
    if row is None:
        raise AppError(404, "reason_category_not_found", "Esa categoría no existe.")
    before = {"name": row.name, "active": row.active}
    if name is not None:
        if not (clean_name := _clean(name)):
            raise AppError(422, "invalid_reason_category", "La categoría necesita un nombre.")
        row.name = clean_name[:120]
    if active is not None:
        row.active = active
    after = {"name": row.name, "active": row.active}
    if after != before:
        _audit(db, actor, "no_purchase_category", "updated", row.id, before, after)
    db.commit()
    db.refresh(row)
    return row


def ensure_reason(db: Session, source_system: str, reason_source: str) -> tuple[NoPurchaseReasonMapping, bool]:
    """A raw reason a file names; a new one arrives without category (the adapter opens the issue)."""
    key = source_key(reason_source)
    if not key:
        raise ValueError("A reason needs a text")
    system_id = source_system_id(db, source_system)
    existing = db.scalars(
        select(NoPurchaseReasonMapping).where(
            NoPurchaseReasonMapping.source_system_id == system_id, NoPurchaseReasonMapping.reason_key == key
        )
    ).first()
    if existing is not None:
        return existing, False
    row = NoPurchaseReasonMapping(
        source_system_id=system_id, reason_source=" ".join(reason_source.split())[:200], reason_key=key[:200]
    )
    db.add(row)
    db.flush()
    return row, True


def set_reason_category(
    db: Session, actor: User, mapping_id: int, category_id: int | None, *, reason: str | None
) -> NoPurchaseReasonMapping:
    row = db.scalars(
        select(NoPurchaseReasonMapping).where(NoPurchaseReasonMapping.id == mapping_id).with_for_update()
    ).first()
    if row is None:
        raise AppError(404, "reason_mapping_not_found", "Ese motivo no existe.")
    if category_id is not None and db.get(NoPurchaseReasonCategory, category_id) is None:
        raise AppError(422, "invalid_reason_category", "La categoría no existe.")
    before = {"category_id": row.category_id}
    row.category_id = category_id
    row.decided_by = actor.id if category_id is not None else None
    row.decided_at = func.now() if category_id is not None else None
    _audit(
        db,
        actor,
        "no_purchase_reason",
        "mapped",
        row.id,
        before,
        {"category_id": category_id, "reason": _clean(reason)},
    )
    db.commit()
    db.refresh(row)
    return row


def list_reason_categories(db: Session) -> Sequence[NoPurchaseReasonCategory]:
    return db.scalars(select(NoPurchaseReasonCategory).order_by(NoPurchaseReasonCategory.code)).all()


def list_reason_mappings(db: Session) -> Sequence[NoPurchaseReasonMapping]:
    return db.scalars(select(NoPurchaseReasonMapping).order_by(NoPurchaseReasonMapping.reason_key)).all()
