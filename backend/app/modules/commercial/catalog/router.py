"""Catalog under /api/v1/commercial (DATA_MODEL §4.3). `viewer` reads; a module admin decides the canonical fields,
families and rubro mappings, audited. No endpoint creates a product or writes a price: only the articles import
does (F1.3)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.modules.commercial.catalog import service
from app.modules.commercial.catalog.models import Product
from app.modules.commercial.catalog.schemas import (
    FamilyIn,
    FamilyOut,
    FamilyPatch,
    PriceOut,
    ProductDetail,
    ProductOut,
    ProductPage,
    ProductPatch,
    SourceCategoryMappingIn,
    SourceCategoryMappingOut,
    UnmappedSourceCategory,
)
from app.platform.auth.access import require_module_access
from app.platform.auth.deps import DbSession
from app.platform.auth.models import User

router = APIRouter()

Viewer = Annotated[User, Depends(require_module_access("commercial", "viewer"))]
Admin = Annotated[User, Depends(require_module_access("commercial", "admin"))]


def _detail(db: DbSession, product: Product) -> ProductDetail:
    prices = [
        PriceOut(
            id=price.id,
            list_number=price_list.list_number,
            price_list_name=price_list.name,
            price=price.price,
            valid_from=price.valid_from,
            valid_to=price.valid_to,
            voided=price.voided_by_job_id is not None,
        )
        for price, price_list in service.price_history(db, product.id)
    ]
    return ProductDetail(**ProductOut.model_validate(product).model_dump(), prices=prices)


@router.get("/products", response_model=ProductPage, summary="Productos")
def list_products(
    db: DbSession,
    _: Viewer,
    q: Annotated[str | None, Query(max_length=100)] = None,
    category_id: int | None = None,
    family_id: int | None = None,
    without_category: bool = False,
    analytics_eligible: bool | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ProductPage:
    rows, total = service.list_products(
        db,
        q=q,
        category_id=category_id,
        family_id=family_id,
        without_category=without_category,
        analytics_eligible=analytics_eligible,
        limit=limit,
        offset=offset,
    )
    return ProductPage(items=[ProductOut.model_validate(r) for r in rows], total=total)


@router.get("/products/{product_id}", response_model=ProductDetail, summary="Producto con su historia de precios")
def get_product(db: DbSession, _: Viewer, product_id: int) -> ProductDetail:
    return _detail(db, service.get_product(db, product_id))


@router.patch("/products/{product_id}", response_model=ProductDetail, summary="Clasificar producto")
def update_product(db: DbSession, user: Admin, product_id: int, body: ProductPatch) -> ProductDetail:
    changes = body.model_dump(exclude_unset=True, exclude={"reason"})
    return _detail(db, service.set_canonical(db, user, product_id, changes, reason=body.reason))


@router.get("/product-families", response_model=list[FamilyOut], summary="Familias de producto")
def list_families(db: DbSession, _: Viewer) -> list[FamilyOut]:
    return [FamilyOut.model_validate(r) for r in service.list_families(db)]


@router.post("/product-families", response_model=FamilyOut, status_code=201, summary="Crear familia")
def create_family(db: DbSession, user: Admin, body: FamilyIn) -> FamilyOut:
    family = service.create_family(db, user, code=body.code, name=body.name, description=body.description)
    return FamilyOut.model_validate(family)


@router.patch("/product-families/{family_id}", response_model=FamilyOut, summary="Modificar familia")
def update_family(db: DbSession, user: Admin, family_id: int, body: FamilyPatch) -> FamilyOut:
    return FamilyOut.model_validate(service.update_family(db, user, family_id, body.model_dump(exclude_unset=True)))


@router.get(
    "/source-category-mappings", response_model=list[SourceCategoryMappingOut], summary="Rubros de origen mapeados"
)
def list_source_categories(db: DbSession, _: Viewer) -> list[SourceCategoryMappingOut]:
    return [SourceCategoryMappingOut.model_validate(r) for r in service.list_source_categories(db)]


@router.get(
    "/source-category-mappings/unmapped",
    response_model=list[UnmappedSourceCategory],
    summary="Rubros de productos sin categoría",
)
def unmapped_source_categories(db: DbSession, _: Viewer) -> list[UnmappedSourceCategory]:
    return [
        UnmappedSourceCategory(source_category=name, products=count)
        for name, count in service.unmapped_source_categories(db)
    ]


@router.put("/source-category-mappings", response_model=SourceCategoryMappingOut, summary="Mapear rubro a categoría")
def set_source_category(db: DbSession, user: Admin, body: SourceCategoryMappingIn) -> SourceCategoryMappingOut:
    row = service.set_source_category(db, user, source_category=body.source_category, category_id=body.category_id)
    return SourceCategoryMappingOut.model_validate(row)
