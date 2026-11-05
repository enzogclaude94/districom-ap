"""Inteligencia Comercial: commercial analytics over AXUM and STORE SYS (ROADMAP.md)."""

from app.modules.commercial.api import router
from app.modules.commercial.imports.customers import TYPE_KEY as CUSTOMERS
from app.modules.commercial.imports.customers import CustomersAdapter
from app.modules.commercial.imports.documents import TYPE_KEY as DOCUMENTS
from app.modules.commercial.imports.documents import DocumentsAdapter
from app.modules.commercial.imports.sales import TYPE_KEY as SALES
from app.modules.commercial.imports.sales import SalesAdapter
from app.modules.commercial.settings import SETTINGS
from app.platform.imports.registry import ImportTypeSpec
from app.platform.modules import ModuleSpec
from app.platform.parties.functions import EmployeeFunctionSpec
from app.platform.quality.registry import QualityCodeSpec

MODULE = ModuleSpec(
    key="commercial",
    name="Inteligencia Comercial",
    router=router,
    settings=SETTINGS,
    employee_functions=(EmployeeFunctionSpec("commercial.salesperson", "Preventista"),),
    # The articles master (`commercial.store_sys.products`) waits for a real export (F1.3, docs/IMPORTS.md §3.5).
    import_types=(
        ImportTypeSpec(CUSTOMERS, "STORE SYS · Clientes", "STORE_SYS", CustomersAdapter()),
        ImportTypeSpec(DOCUMENTS, "STORE SYS · Comprobantes emitidos", "STORE_SYS", DocumentsAdapter()),
        ImportTypeSpec(SALES, "STORE SYS · Ventas artículos/clientes", "STORE_SYS", SalesAdapter()),
    ),
    quality_codes=(
        QualityCodeSpec("commercial.zone.unconfigured", "warning", "Zona sin configurar"),
        QualityCodeSpec("commercial.document.unclassified_type", "warning", "Tipo de comprobante sin clasificar"),
        QualityCodeSpec("commercial.document.changed", "error", "Comprobante reimportado con otros datos"),
        QualityCodeSpec("commercial.product.without_category", "warning", "Producto sin categoría"),
        QualityCodeSpec("commercial.product.unknown", "warning", "Artículo que no está en el maestro"),
        QualityCodeSpec("commercial.product.unmapped_category", "warning", "Rubro sin categoría canónica"),
        QualityCodeSpec("commercial.customer.without_zone", "warning", "Cliente sin zona"),
        QualityCodeSpec("commercial.customer.without_salesperson", "warning", "Cliente sin vendedor"),
        QualityCodeSpec("commercial.customer.missing_from_master", "info", "Cliente ausente de la última foto"),
        QualityCodeSpec("commercial.customer.unresolved_code", "warning", "Código de cliente STORE SYS sin resolver"),
        QualityCodeSpec("commercial.seller.unmapped", "warning", "Vendedor STORE SYS sin vincular"),
        QualityCodeSpec("commercial.sale_line.customer_pending", "warning", "Ventas con cliente pendiente"),
        QualityCodeSpec("commercial.sale_line.product_pending", "warning", "Ventas con artículo pendiente"),
    ),
)
