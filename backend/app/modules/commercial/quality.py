"""Comercial's data quality codes and how the Calidad screen presents them (F1.6, docs/IMPORTS.md §7.1).

Each code declares its business group, what it means, what corrects it, when it closes by itself and, when that
screen exists, where the correction is made (Mapeos, the import). Codes whose correction belongs to Maestros (F1.7)
say so and link nowhere: Calidad never edits zones, products, document types or reasons.
"""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.commercial.catalog.models import Product
from app.modules.commercial.config.models import DocumentType, Zone
from app.modules.commercial.documents.models import Document
from app.platform.quality.registry import QualityCodeSpec, QualityGroup, QualitySubjectSpec

CUSTOMERS = QualityGroup("customers", "Clientes", 10)
SELLERS = QualityGroup("sellers", "Vendedores y preventistas", 20)
PRODUCTS = QualityGroup("products", "Productos", 30)
ZONES = QualityGroup("zones", "Zonas", 40)
DOCUMENTS = QualityGroup("documents", "Comprobantes", 50)
AXUM = QualityGroup("axum", "AXUM", 60)

MASTERS_LATER = "La edición completa llega en Maestros."

QUALITY_CODES = (
    # --- Clientes
    QualityCodeSpec(
        "commercial.customer.without_zone",
        "warning",
        "Cliente sin zona",
        group=CUSTOMERS,
        description="La última foto del maestro de clientes trae al cliente sin zona.",
        action="Cargá la zona del cliente en STORE SYS: se corrige con la próxima foto del maestro.",
        closes_when="Cuando una foto del maestro trae la zona del cliente.",
    ),
    QualityCodeSpec(
        "commercial.customer.without_salesperson",
        "warning",
        "Cliente sin vendedor",
        group=CUSTOMERS,
        description="La última foto del maestro de clientes trae al cliente sin vendedor.",
        action="Cargá el vendedor del cliente en STORE SYS: se corrige con la próxima foto del maestro.",
        closes_when="Cuando una foto del maestro trae el vendedor del cliente.",
    ),
    QualityCodeSpec(
        "commercial.customer.missing_from_master",
        "info",
        "Cliente ausente de la última foto",
        group=CUSTOMERS,
        description="El cliente no apareció en la última foto del maestro. Sigue activo: DistriCom nunca lo da de "
        "baja.",
        action="Revisá en STORE SYS si el cliente se dio de baja. Si es lo esperado, ignoralo.",
        closes_when="Cuando el cliente vuelve a aparecer en una foto del maestro.",
    ),
    QualityCodeSpec(
        "commercial.customer.unresolved_code",
        "warning",
        "Código de cliente STORE SYS sin resolver",
        group=CUSTOMERS,
        description="Ventas o comprobantes traen un código de cliente STORE SYS que no está vinculado con un cliente.",
        action="Revisá el código en Mapeos. Si el cliente es nuevo, se crea con la próxima foto del maestro.",
        closes_when="Cuando el código queda vinculado (o excluido) y no queda ninguna venta ni comprobante de ese "
        "código sin vínculo.",
        fix="mapping",
        mapping_entity_type="customer",
    ),
    # --- Vendedores y preventistas
    QualityCodeSpec(
        "commercial.seller.unmapped",
        "warning",
        "Vendedor STORE SYS sin vincular",
        group=SELLERS,
        description="El maestro de clientes o las ventas traen un vendedor de STORE SYS que no está vinculado con un "
        "empleado.",
        action="Vinculalo con su empleado en Mapeos.",
        closes_when="Cuando el vínculo se confirma o se excluye.",
        fix="mapping",
        mapping_entity_type="employee",
    ),
    QualityCodeSpec(
        "commercial.axum_salesperson.unmapped",
        "warning",
        "Preventista AXUM sin vincular",
        group=SELLERS,
        description="El reporte de AXUM trae un preventista que no está vinculado con un empleado.",
        action="Vinculalo con su empleado en Mapeos.",
        closes_when="Cuando el vínculo se confirma o se excluye.",
        fix="mapping",
        mapping_entity_type="employee",
    ),
    # --- Productos
    QualityCodeSpec(
        "commercial.product.without_category",
        "warning",
        "Producto sin categoría",
        group=PRODUCTS,
        description="El artículo no tiene categoría, así que queda fuera de los análisis.",
        action="Asignale una categoría. La edición de categorías llega con Maestros (F1.7).",
        closes_when="Cuando el artículo recibe una categoría, a mano o por el mapeo de su rubro.",
    ),
    QualityCodeSpec(
        "commercial.product.unknown",
        "warning",
        "Artículo que no está en el maestro",
        group=PRODUCTS,
        description="Las ventas traen un código de artículo que no está en el maestro. Esas líneas quedan sin "
        "artículo.",
        action="Revisá el artículo en STORE SYS e importá un maestro de artículos que lo incluya.",
    ),
    QualityCodeSpec(
        "commercial.product.unmapped_category",
        "warning",
        "Rubro sin categoría canónica",
        group=PRODUCTS,
        description="Un rubro de STORE SYS no tiene una categoría canónica asignada.",
        action=f"Asigná la categoría del rubro. {MASTERS_LATER}",
    ),
    QualityCodeSpec(
        "commercial.product.duplicate_code",
        "error",
        "Código de artículo repetido en el maestro",
        group=PRODUCTS,
        description="El maestro de artículos trae el mismo código en más de una fila. Ninguna de esas filas se aplicó.",
        action="Corregí el código repetido en STORE SYS y volvé a importar el maestro.",
        closes_when="Cuando un maestro posterior trae ese código una sola vez.",
        fix="import",
    ),
    QualityCodeSpec(
        "commercial.product.missing_from_master",
        "info",
        "Artículo ausente del último maestro",
        group=PRODUCTS,
        description="El artículo no apareció en el último maestro. Sigue como estaba: DistriCom nunca lo da de baja.",
        action="Revisá en STORE SYS si el artículo se dio de baja. Si es lo esperado, ignoralo.",
        closes_when="Cuando el artículo vuelve a aparecer en un maestro.",
    ),
    QualityCodeSpec(
        "commercial.product.prices_not_loaded",
        "warning",
        "Precios del maestro sin lista configurada",
        group=PRODUCTS,
        description="El maestro de artículos trae precios, pero no hay una lista de precios configurada para "
        "guardarlos.",
        action="Un admin configura la lista de precios del maestro; se corrige con el próximo maestro.",
        closes_when="Cuando un maestro se importa con la lista de precios configurada.",
    ),
    QualityCodeSpec(
        "commercial.sale_line.product_pending",
        "warning",
        "Ventas con artículo pendiente",
        group=PRODUCTS,
        description="Una importación de ventas cargó líneas con artículos que no están en el maestro.",
        action="Revisá en esa importación qué artículos faltan en el maestro.",
        fix="import",
    ),
    # --- Zonas
    QualityCodeSpec(
        "commercial.zone.unconfigured",
        "warning",
        "Zona sin configurar",
        group=ZONES,
        description="Apareció una zona que ninguna regla clasifica: no tiene tipo ni días de visita.",
        action=f"Requiere configuración de zona. {MASTERS_LATER}",
        closes_when="Cuando un admin configura la zona.",
    ),
    # --- Comprobantes
    QualityCodeSpec(
        "commercial.document.unclassified_type",
        "warning",
        "Tipo de comprobante sin clasificar",
        group=DOCUMENTS,
        description="Apareció un tipo de comprobante sin clasificar: sus comprobantes quedan fuera del universo "
        "comercial.",
        action=f"Un admin lo clasifica (es una interpretación contable). {MASTERS_LATER}",
        closes_when="Cuando un admin clasifica el tipo.",
    ),
    QualityCodeSpec(
        "commercial.document.changed",
        "error",
        "Comprobante reimportado con otros datos",
        group=DOCUMENTS,
        description="Se volvió a importar un comprobante ya cargado, con otros importes o datos. Se conservó el "
        "original.",
        action="Revisá en STORE SYS por qué cambió el comprobante y decidí con un admin cómo corregirlo.",
        fix="import",
    ),
    # --- AXUM
    QualityCodeSpec(
        "commercial.axum_customer.unresolved",
        "warning",
        "Cliente AXUM sin resolver",
        group=AXUM,
        description="El reporte de AXUM trae un cliente que no está vinculado con un cliente de STORE SYS.",
        action="Vinculalo en Mapeos.",
        closes_when="Cuando el vínculo se confirma o se excluye.",
        fix="mapping",
        mapping_entity_type="customer",
    ),
    QualityCodeSpec(
        "commercial.axum_customer.name_mismatch",
        "warning",
        "Cliente AXUM con nombre distinto al de STORE SYS",
        group=AXUM,
        description="El código del cliente AXUM coincide con un cliente de STORE SYS, pero el nombre es distinto: "
        "no se vinculó solo.",
        action="Revisá el candidato en Mapeos y confirmalo si es el mismo cliente.",
        closes_when="Cuando el vínculo se confirma o se excluye.",
        fix="mapping",
        mapping_entity_type="customer",
    ),
    QualityCodeSpec(
        "commercial.no_purchase.reason_unmapped",
        "warning",
        "Motivo de no compra sin categoría",
        group=AXUM,
        description="Un motivo de no compra de AXUM no tiene categoría.",
        action=f"Un admin le asigna una categoría. {MASTERS_LATER}",
        closes_when="Cuando el motivo recibe una categoría.",
    ),
)


def _products(db: Session, ids: Sequence[int]) -> dict[int, str]:
    rows = db.execute(select(Product.id, Product.source_code, Product.source_description).where(Product.id.in_(ids)))
    return {pid: f"Artículo {code}" + (f" · {name}" if name else "") for pid, code, name in rows}


def _zones(db: Session, ids: Sequence[int]) -> dict[int, str]:
    return {zid: f"Zona {name}" for zid, name in db.execute(select(Zone.id, Zone.display_name).where(Zone.id.in_(ids)))}


def _document_types(db: Session, ids: Sequence[int]) -> dict[int, str]:
    rows = db.execute(select(DocumentType.id, DocumentType.source_name).where(DocumentType.id.in_(ids)))
    return {tid: f"Tipo de comprobante {name}" for tid, name in rows}


def _documents(db: Session, ids: Sequence[int]) -> dict[int, str]:
    rows = db.execute(
        select(Document.id, Document.source_type_name, Document.point_of_sale, Document.number).where(
            Document.id.in_(ids)
        )
    )
    return {did: f"Comprobante {kind} {pos}-{number}" for did, kind, pos, number in rows}


QUALITY_SUBJECTS = (
    QualitySubjectSpec("commercial.product", _products),
    QualitySubjectSpec("commercial.zone", _zones),
    QualitySubjectSpec("commercial.document_type", _document_types),
    QualitySubjectSpec("commercial.document", _documents),
)
