"""`commercial seed-config`: the configuration Enzo confirmed (2026-11-01 and 2026-11-03), loaded once.

Idempotent and conservative: it inserts the rows that are missing and never changes one that exists, so running it
again after an admin edited a list, a rule or a type leaves the admin's decision. Product categories are not seeded:
their list waits for Enzo's confirmation (DATA_MODEL §4.2). MISA has no rule on purpose: it stays UNCLASSIFIED.
"""

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.commercial.config.models import (
    DocumentType,
    NoPurchaseReasonCategory,
    NoPurchaseReasonMapping,
    PriceList,
    ZoneClassificationRule,
)
from app.modules.commercial.config.service import source_key
from app.platform.audit.service import record_event
from app.platform.mappings.service import source_system_id

CONFIRMED = "Confirmado por Enzo (F1.2, 2026-11-03)"

# (priority, pattern, zone_type, requires_visit, weekdays, description). Patterns match whole words of the zone's
# name: MAJU does not match MAJUSA, so both aliases get a rule, like LUVI and LUMIVI.
ZONE_RULES = (
    (10, "BOCA DE EXPENDIO", "OUTLET", False, (), "Boca de Expendio: sin visita"),
    (20, "BANDERA", "BANDERA", True, (4,), "Bandera: jueves"),
    (30, "LUVI", "PRESALE", True, (1, 3, 5), "LUVI = Lumivi: lunes, miércoles y viernes"),
    (31, "LUMIVI", "PRESALE", True, (1, 3, 5), "Lumivi: lunes, miércoles y viernes"),
    (40, "MAJU", "PRESALE", True, (2, 4, 5), "MAJU = Majusa: martes, jueves y viernes"),
    (41, "MAJUSA", "PRESALE", True, (2, 4, 5), "Majusa: martes, jueves y viernes"),
)

# (list_number, name, kind, description)
PRICE_LISTS = (
    (1, "Preventa oficial", "PRESALE_OFFICIAL", "Precio oficial de preventa"),
    (2, "Boca de Expendio", "OUTLET", "Precio de la boca de expendio"),
    (3, "Promo 3+ bultos", "PROMO_VOLUME", "Promocional por 3 bultos o más"),
    (4, "Especiales", "SPECIAL", "Precios especiales"),
    (5, "Bandera", "BANDERA", "Precio de Bandera"),
)

# (source name, class, sign, commercial_universe)
DOCUMENT_TYPES = (
    ("FACTURA A", "INVOICE", 1, True),
    ("FACTURA B", "INVOICE", 1, True),
    ("NOTA DE CREDITO A", "CREDIT_NOTE", -1, True),
    ("DEVOLUCION X", "RETURN", -1, True),
    ("RETIRO PR.", "INTERNAL_MOVEMENT", 0, False),
    ("RECIBOS X", "RECEIPT", 0, False),
    ("VALE PERSONAL V", "EMPLOYEE_VOUCHER", 0, False),
    # Outside the universe until Enzo says otherwise (FACTURA without letter: punto de venta 0000).
    ("COMP. INTERNO X", "UNCLASSIFIED", 0, False),
    ("FACTURA", "UNCLASSIFIED", 0, False),
)

REASON_CATEGORIES = (
    ("STOCK", "Tiene stock"),
    ("NO_DISPONIBLE", "No disponible"),
    ("OTRO_CANAL", "Compra por otro canal"),
    ("RESTRICCION_FINANCIERA", "Restricción financiera"),
    ("OTRO", "Otro"),
)

# Enzo's examples; to be checked against the real AXUM CSV in F1.4.
AXUM_REASONS = (
    ("TIENE STOCK", "STOCK"),
    ("CERRADO", "NO_DISPONIBLE"),
    ("NO ESTA EL ENCARGADO", "NO_DISPONIBLE"),
    ("VACACIONES", "NO_DISPONIBLE"),
    ("COMPRA POR BEES", "OTRO_CANAL"),
    ("SIN DINERO", "RESTRICCION_FINANCIERA"),
)


@dataclass
class SeedResult:
    zone_rules: int = 0
    price_lists: int = 0
    document_types: int = 0
    reason_categories: int = 0
    reason_mappings: int = 0

    def total(self) -> int:
        return self.zone_rules + self.price_lists + self.document_types + self.reason_categories + self.reason_mappings


def seed_config(db: Session) -> SeedResult:
    result = SeedResult()
    for priority, pattern, zone_type, visit, weekdays, description in ZONE_RULES:
        key = source_key(pattern)
        if db.scalar(select(ZoneClassificationRule.id).where(ZoneClassificationRule.pattern == key)) is None:
            db.add(
                ZoneClassificationRule(
                    priority=priority,
                    match_type="CONTAINS",
                    pattern=key,
                    zone_type=zone_type,
                    requires_visit=visit,
                    weekdays=list(weekdays),
                    description=description,
                )
            )
            result.zone_rules += 1
    for number, name, kind, description in PRICE_LISTS:
        if db.scalar(select(PriceList.id).where(PriceList.list_number == number)) is None:
            db.add(
                PriceList(
                    list_number=number,
                    code=f"LISTA_{number}",
                    name=name,
                    description=description,
                    kind=kind,
                    commercially_relevant=True,
                )
            )
            result.price_lists += 1
    for name, document_class, sign, universe in DOCUMENT_TYPES:
        key = source_key(name)
        if db.scalar(select(DocumentType.id).where(DocumentType.source_name_key == key)) is None:
            classified = document_class != "UNCLASSIFIED"
            db.add(
                DocumentType(
                    source_name=name,
                    source_name_key=key,
                    document_class=document_class,
                    sign=sign,
                    commercial_universe=universe,
                    decided_at=func.now() if classified else None,
                    notes=CONFIRMED if classified else "Sin clasificar hasta confirmación de Enzo",
                )
            )
            result.document_types += 1
    categories: dict[str, int] = {}
    for code, name in REASON_CATEGORIES:
        found = db.scalar(select(NoPurchaseReasonCategory.id).where(NoPurchaseReasonCategory.code == code))
        if found is None:
            row = NoPurchaseReasonCategory(code=code, name=name)
            db.add(row)
            db.flush()
            found = row.id
            result.reason_categories += 1
        categories[code] = found
    axum = source_system_id(db, "AXUM")
    for reason, category in AXUM_REASONS:
        key = source_key(reason)
        exists = db.scalar(
            select(NoPurchaseReasonMapping.id).where(
                NoPurchaseReasonMapping.source_system_id == axum, NoPurchaseReasonMapping.reason_key == key
            )
        )
        if exists is None:
            db.add(
                NoPurchaseReasonMapping(
                    source_system_id=axum,
                    reason_source=reason,
                    reason_key=key,
                    category_id=categories[category],
                    decided_at=func.now(),
                )
            )
            result.reason_mappings += 1
    if result.total():
        record_event(db, "commercial.config.seeded", payload=vars(result))
    db.commit()
    return result
