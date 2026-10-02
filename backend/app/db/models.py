"""Import every model module so Base.metadata is complete (used by Alembic)."""

from app.audit import models as audit_models
from app.auth import models as auth_models
from app.settings_store import models as settings_models
from app.sources import models as sources_models

__all__ = ["audit_models", "auth_models", "settings_models", "sources_models"]
