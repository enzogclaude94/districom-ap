"""Import every model module so Base.metadata is complete (used by Alembic)."""

from app.modules.settlements.ledger import models as settlements_ledger_models
from app.platform.audit import models as audit_models
from app.platform.auth import models as auth_models
from app.platform.parties import models as parties_models
from app.platform.settings import models as settings_models
from app.platform.sources import models as sources_models

__all__ = [
    "audit_models",
    "auth_models",
    "parties_models",
    "settings_models",
    "settlements_ledger_models",
    "sources_models",
]
