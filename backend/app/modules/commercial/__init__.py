"""Inteligencia Comercial: commercial analytics over AXUM and STORE SYS (ROADMAP.md)."""

from app.modules.commercial.api import router
from app.modules.commercial.identities.resolvers import RESOLVERS
from app.modules.commercial.imports.customers import TYPE_KEY as CUSTOMERS
from app.modules.commercial.imports.customers import CustomersAdapter
from app.modules.commercial.imports.documents import TYPE_KEY as DOCUMENTS
from app.modules.commercial.imports.documents import DocumentsAdapter
from app.modules.commercial.imports.no_purchase import TYPE_KEY as NO_PURCHASE
from app.modules.commercial.imports.no_purchase import NoPurchaseAdapter
from app.modules.commercial.imports.products import TYPE_KEY as PRODUCTS
from app.modules.commercial.imports.products import ProductsAdapter
from app.modules.commercial.imports.sales import TYPE_KEY as SALES
from app.modules.commercial.imports.sales import SalesAdapter
from app.modules.commercial.quality import QUALITY_CODES, QUALITY_SUBJECTS
from app.modules.commercial.settings import SETTINGS
from app.platform.imports.registry import ImportTypeSpec
from app.platform.modules import ModuleSpec
from app.platform.parties.functions import EmployeeFunctionSpec

MODULE = ModuleSpec(
    key="commercial",
    name="Inteligencia Comercial",
    router=router,
    settings=SETTINGS,
    employee_functions=(EmployeeFunctionSpec("commercial.salesperson", "Preventista"),),
    # Identity of each import (docs/IMPORTS.md §5.1): sha256 + type + what `identity` names. Masters are snapshots
    # (the same file as the photo of another day is a new import); the sales export's declared scope decides what it
    # replaces, so the same file with another scope is a new import; documents keep sha256 + type.
    import_types=(
        ImportTypeSpec(CUSTOMERS, "STORE SYS · Clientes", "STORE_SYS", CustomersAdapter(), identity=("snapshot_date",)),
        ImportTypeSpec(
            PRODUCTS, "STORE SYS · Maestro de artículos", "STORE_SYS", ProductsAdapter(), identity=("snapshot_date",)
        ),
        ImportTypeSpec(DOCUMENTS, "STORE SYS · Comprobantes emitidos", "STORE_SYS", DocumentsAdapter()),
        ImportTypeSpec(
            SALES, "STORE SYS · Ventas artículos/clientes", "STORE_SYS", SalesAdapter(), identity=("scope",)
        ),
        # AXUM's report declares nothing (no period, no filters): sha256 + type; an overlap waits for an admin.
        ImportTypeSpec(NO_PURCHASE, "AXUM · Compró / No compró", "AXUM", NoPurchaseAdapter()),
    ),
    quality_codes=QUALITY_CODES,
    quality_subjects=QUALITY_SUBJECTS,
    mapping_resolvers=RESOLVERS,
)
