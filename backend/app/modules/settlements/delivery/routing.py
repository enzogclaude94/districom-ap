"""Path parameters of the settlement endpoints, checked against the settlement type (R7-2).

A delivery settlement is reached only through `/deliveries/{id}` and a boca de expendio settlement only
through `/outlet-settlements/{id}`; the other path answers 404 `settlement_not_found`. Every load, close and
reopen behind them is the same domain.
"""

from typing import Annotated

from fastapi import Depends

from app.modules.settlements.delivery import service
from app.platform.auth.deps import DbSession


def _delivery_id(settlement_id: int, db: DbSession) -> int:
    return service.require_type(db, settlement_id, service.DELIVERY)


def _outlet_settlement_id(settlement_id: int, db: DbSession) -> int:
    return service.require_type(db, settlement_id, service.OUTLET)


DeliveryId = Annotated[int, Depends(_delivery_id)]
OutletSettlementId = Annotated[int, Depends(_outlet_settlement_id)]
