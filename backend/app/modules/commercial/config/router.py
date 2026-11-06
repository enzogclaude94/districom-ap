"""Commercial configuration under /api/v1/commercial (DATA_MODEL §4.2). `viewer` reads; every change is a module
admin's, audited as `commercial.config.*`. Zones, document types and raw reasons are created by imports, never here."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends

from app.modules.commercial.config import service
from app.modules.commercial.config.models import Zone
from app.modules.commercial.config.schemas import (
    ArticlesPriceListIn,
    ArticlesPriceListOut,
    CategoryIn,
    CategoryOut,
    CategoryPatch,
    DocumentTypeClassificationIn,
    DocumentTypeOut,
    PriceListIn,
    PriceListOut,
    PriceListPatch,
    ReasonCategoryIn,
    ReasonCategoryOut,
    ReasonCategoryPatch,
    ReasonMappingIn,
    ReasonMappingOut,
    VisitDayOut,
    ZoneConfigurationIn,
    ZoneDetail,
    ZoneOut,
    ZoneRuleIn,
    ZoneRuleOut,
    ZoneRulePatch,
)
from app.platform.auth.access import require_module_access
from app.platform.auth.deps import DbSession
from app.platform.auth.models import User

router = APIRouter()

Viewer = Annotated[User, Depends(require_module_access("commercial", "viewer"))]
Admin = Annotated[User, Depends(require_module_access("commercial", "admin"))]


def _zone(db: DbSession, zone: Zone) -> ZoneOut:
    return ZoneOut(
        id=zone.id,
        source_name=zone.source_name,
        display_name=zone.display_name,
        zone_type=zone.zone_type,
        requires_visit=zone.requires_visit,
        weekdays=service.current_weekdays(db, zone.id, date.today()),
        configuration_origin=zone.configuration_origin,
        rule_id=zone.rule_id,
        active=zone.active,
        configured_by=zone.configured_by,
        configured_at=zone.configured_at,
        created_at=zone.created_at,
    )


def _zone_detail(db: DbSession, zone: Zone) -> ZoneDetail:
    days = [VisitDayOut.model_validate(d) for d in service.visit_day_history(db, zone.id)]
    return ZoneDetail(**_zone(db, zone).model_dump(), visit_days=days)


@router.get("/zones", response_model=list[ZoneOut], summary="Zonas")
def list_zones(db: DbSession, _: Viewer) -> list[ZoneOut]:
    return [_zone(db, z) for z in service.list_zones(db)]


@router.get("/zones/{zone_id}", response_model=ZoneDetail, summary="Zona con su historia de días")
def get_zone(db: DbSession, _: Viewer, zone_id: int) -> ZoneDetail:
    return _zone_detail(db, service.get_zone(db, zone_id))


@router.put("/zones/{zone_id}/configuration", response_model=ZoneDetail, summary="Configurar zona")
def configure_zone(db: DbSession, user: Admin, zone_id: int, body: ZoneConfigurationIn) -> ZoneDetail:
    zone = service.configure_zone(
        db,
        user,
        zone_id,
        zone_type=body.zone_type,
        requires_visit=body.requires_visit,
        weekdays=body.weekdays,
        effective_from=body.effective_from,
        display_name=body.display_name,
        active=body.active,
        reason=body.reason,
    )
    return _zone_detail(db, zone)


@router.get("/zone-rules", response_model=list[ZoneRuleOut], summary="Reglas de clasificación de zonas")
def list_rules(db: DbSession, _: Viewer) -> list[ZoneRuleOut]:
    return [ZoneRuleOut.model_validate(r) for r in service.list_rules(db)]


@router.post("/zone-rules", response_model=ZoneRuleOut, status_code=201, summary="Crear regla de zonas")
def create_rule(db: DbSession, user: Admin, body: ZoneRuleIn) -> ZoneRuleOut:
    rule = service.create_rule(db, user, **body.model_dump())
    return ZoneRuleOut.model_validate(rule)


@router.patch("/zone-rules/{rule_id}", response_model=ZoneRuleOut, summary="Modificar regla de zonas")
def update_rule(db: DbSession, user: Admin, rule_id: int, body: ZoneRulePatch) -> ZoneRuleOut:
    return ZoneRuleOut.model_validate(service.update_rule(db, user, rule_id, body.model_dump(exclude_unset=True)))


@router.get("/price-lists", response_model=list[PriceListOut], summary="Listas de precios")
def list_price_lists(db: DbSession, _: Viewer) -> list[PriceListOut]:
    return [PriceListOut.model_validate(r) for r in service.list_price_lists(db)]


@router.post("/price-lists", response_model=PriceListOut, status_code=201, summary="Crear lista de precios")
def create_price_list(db: DbSession, user: Admin, body: PriceListIn) -> PriceListOut:
    return PriceListOut.model_validate(service.create_price_list(db, user, **body.model_dump()))


@router.get(
    "/price-lists/articles-master",
    response_model=ArticlesPriceListOut,
    summary="Lista que alimenta el precio del maestro de artículos",
)
def get_articles_price_list(db: DbSession, _: Viewer) -> ArticlesPriceListOut:
    return ArticlesPriceListOut(list_number=service.articles_price_list(db))


@router.put(
    "/price-lists/articles-master",
    response_model=ArticlesPriceListOut,
    summary="Confirmar la lista del precio del maestro de artículos",
)
def set_articles_price_list(db: DbSession, user: Admin, body: ArticlesPriceListIn) -> ArticlesPriceListOut:
    number = service.set_articles_price_list(db, user, body.list_number, reason=body.reason)
    return ArticlesPriceListOut(list_number=number)


@router.patch("/price-lists/{list_id}", response_model=PriceListOut, summary="Modificar lista de precios")
def update_price_list(db: DbSession, user: Admin, list_id: int, body: PriceListPatch) -> PriceListOut:
    row = service.update_price_list(db, user, list_id, body.model_dump(exclude_unset=True))
    return PriceListOut.model_validate(row)


@router.get("/product-categories", response_model=list[CategoryOut], summary="Categorías de producto")
def list_categories(db: DbSession, _: Viewer) -> list[CategoryOut]:
    return [CategoryOut.model_validate(r) for r in service.list_categories(db)]


@router.post("/product-categories", response_model=CategoryOut, status_code=201, summary="Crear categoría")
def create_category(db: DbSession, user: Admin, body: CategoryIn) -> CategoryOut:
    return CategoryOut.model_validate(service.create_category(db, user, **body.model_dump()))


@router.patch("/product-categories/{category_id}", response_model=CategoryOut, summary="Modificar categoría")
def update_category(db: DbSession, user: Admin, category_id: int, body: CategoryPatch) -> CategoryOut:
    row = service.update_category(db, user, category_id, body.model_dump(exclude_unset=True))
    return CategoryOut.model_validate(row)


@router.get("/document-types", response_model=list[DocumentTypeOut], summary="Tipos de comprobante")
def list_document_types(db: DbSession, _: Viewer) -> list[DocumentTypeOut]:
    return [DocumentTypeOut.model_validate(r) for r in service.list_document_types(db)]


@router.put(
    "/document-types/{type_id}/classification", response_model=DocumentTypeOut, summary="Clasificar tipo de comprobante"
)
def classify_document_type(
    db: DbSession, user: Admin, type_id: int, body: DocumentTypeClassificationIn
) -> DocumentTypeOut:
    row = service.classify_document_type(db, user, type_id, **body.model_dump())
    return DocumentTypeOut.model_validate(row)


@router.get(
    "/no-purchase-reason-categories",
    response_model=list[ReasonCategoryOut],
    summary="Categorías de motivo de no compra",
)
def list_reason_categories(db: DbSession, _: Viewer) -> list[ReasonCategoryOut]:
    return [ReasonCategoryOut.model_validate(r) for r in service.list_reason_categories(db)]


@router.post(
    "/no-purchase-reason-categories", response_model=ReasonCategoryOut, status_code=201, summary="Crear categoría"
)
def create_reason_category(db: DbSession, user: Admin, body: ReasonCategoryIn) -> ReasonCategoryOut:
    return ReasonCategoryOut.model_validate(service.create_reason_category(db, user, code=body.code, name=body.name))


@router.patch(
    "/no-purchase-reason-categories/{category_id}", response_model=ReasonCategoryOut, summary="Modificar categoría"
)
def update_reason_category(
    db: DbSession, user: Admin, category_id: int, body: ReasonCategoryPatch
) -> ReasonCategoryOut:
    row = service.update_reason_category(db, user, category_id, name=body.name, active=body.active)
    return ReasonCategoryOut.model_validate(row)


@router.get("/no-purchase-reasons", response_model=list[ReasonMappingOut], summary="Motivos de no compra")
def list_reasons(db: DbSession, _: Viewer) -> list[ReasonMappingOut]:
    return [ReasonMappingOut.model_validate(r) for r in service.list_reason_mappings(db)]


@router.put("/no-purchase-reasons/{mapping_id}/category", response_model=ReasonMappingOut, summary="Categorizar motivo")
def set_reason_category(db: DbSession, user: Admin, mapping_id: int, body: ReasonMappingIn) -> ReasonMappingOut:
    row = service.set_reason_category(db, user, mapping_id, body.category_id, reason=body.reason)
    return ReasonMappingOut.model_validate(row)
