"""Import every model module so Base.metadata is complete (used by Alembic)."""

from app.modules.settlements.cheques import models as settlements_cheques_models
from app.modules.settlements.delivery import models as settlements_delivery_models
from app.modules.settlements.employee_finance import models as settlements_employee_finance_models
from app.modules.settlements.ledger import models as settlements_ledger_models
from app.modules.settlements.outlet_settlements import models as settlements_outlet_settlements_models
from app.modules.settlements.outlets import models as settlements_outlets_models
from app.modules.settlements.payments import models as settlements_payments_models
from app.modules.settlements.pending import models as settlements_pending_models
from app.platform.audit import models as audit_models
from app.platform.auth import models as auth_models
from app.platform.banks import models as banks_models
from app.platform.outlets import models as outlets_models
from app.platform.parties import models as parties_models
from app.platform.settings import models as settings_models
from app.platform.sources import models as sources_models
from app.platform.third_parties import models as third_parties_models
from app.platform.vehicles import models as vehicles_models

__all__ = [
    "audit_models",
    "auth_models",
    "banks_models",
    "outlets_models",
    "parties_models",
    "settings_models",
    "settlements_cheques_models",
    "settlements_delivery_models",
    "settlements_employee_finance_models",
    "settlements_ledger_models",
    "settlements_outlet_settlements_models",
    "settlements_outlets_models",
    "settlements_payments_models",
    "settlements_pending_models",
    "sources_models",
    "third_parties_models",
    "vehicles_models",
]
