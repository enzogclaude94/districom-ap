"""Cash denominations used to count a settlement (R2-D3, R2-D4, Enzo's R2-P4).

The list is the parameter `settlements.cash_denominations`, edited by an admin; the default below is
only the starting point. DistriCom does not handle coins nor notes under $100, so none is configured.

Each denomination has a stable `code`, independent of its position and of its value. A code is never
removed nor given another value: it is deactivated instead, so every historical count line, which
copies code and value, keeps meaning the same. The suggested destination is a cash box code; the
operator can change it line by line, and changing the suggestion never touches existing lines.
"""

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.settlements.ledger import service as ledger
from app.platform.audit.service import record_change
from app.platform.auth.models import User
from app.platform.settings.service import get_setting, set_setting

SETTING_KEY = "settlements.cash_denominations"
CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,39}$")
CENT = Decimal("0.01")


def _default(value: int, destination: str) -> dict[str, Any]:
    return {"code": f"ARS_{value}", "value": f"{value}.00", "active": True, "suggested_destination": destination}


# Notes of $1.000 or more usually go banded to Caja principal; smaller ones to Caja de cambio.
DEFAULT_DENOMINATIONS: list[dict[str, Any]] = [
    _default(20000, "main_cash"),
    _default(10000, "main_cash"),
    _default(2000, "main_cash"),
    _default(1000, "main_cash"),
    _default(500, "change_cash"),
    _default(200, "change_cash"),
    _default(100, "change_cash"),
]


@dataclass(frozen=True)
class Denomination:
    code: str
    value: Decimal
    active: bool
    # Code of a cash box, or None for no suggestion.
    suggested_destination: str | None


def configured(db: Session) -> list[Denomination]:
    """Every configured denomination, active or not, in the configured order."""
    return [_parse(item) for item in get_setting(db, SETTING_KEY)]


def by_code(db: Session) -> dict[str, Denomination]:
    return {d.code: d for d in configured(db)}


def cash_boxes(db: Session) -> dict[str, ledger.AccountInfo]:
    """Active cash boxes by code (Caja principal, Caja de cambio...)."""
    return {str(a.code): a for a in ledger.accounts(db) if a.kind == "CASH_BOX" and a.active and a.code is not None}


def update(db: Session, actor: User, items: list[Denomination]) -> list[Denomination]:
    """Replace the configuration after validating it; audited. The caller commits."""
    current = configured(db)
    _validate(db, current, items)
    before = [_dump(d) for d in current]
    after = [_dump(d) for d in items]
    if before != after:
        set_setting(db, SETTING_KEY, after, user_id=actor.id)
        record_change(
            db,
            "settlements.cash_denominations.updated",
            action="update",
            user_id=actor.id,
            entity_type="setting",
            entity_id=SETTING_KEY,
            before={"denominations": before},
            after={"denominations": after},
        )
    return items


def _validate(db: Session, current: list[Denomination], items: list[Denomination]) -> None:
    codes = [d.code for d in items]
    if any(not CODE_PATTERN.match(code) for code in codes):
        raise _invalid("Cada código va en mayúsculas, números y guiones bajos (por ejemplo ARS_1000).")
    if len(set(codes)) != len(codes):
        raise _invalid("Hay códigos de denominación repetidos.")
    if any(d.value <= 0 or d.value != d.value.quantize(CENT) for d in items):
        raise _invalid("Cada denominación tiene un valor mayor que cero, con dos decimales como máximo.")
    if not any(d.active for d in items):
        raise _invalid("Tiene que quedar al menos una denominación activa.")
    boxes = cash_boxes(db)
    if any(d.suggested_destination is not None and d.suggested_destination not in boxes for d in items):
        raise _invalid("El destino sugerido tiene que ser una caja activa.")
    new = {d.code: d for d in items}
    for old in current:
        kept = new.get(old.code)
        if kept is None:
            raise _invalid(f"La denominación {old.code} no se elimina: desactivala.", {"code": old.code})
        if kept.value != old.value:
            raise _invalid(
                f"La denominación {old.code} no cambia de valor: desactivala y creá una nueva.", {"code": old.code}
            )


def _invalid(message: str, details: dict[str, Any] | None = None) -> AppError:
    return AppError(422, "invalid_denominations", message, details)


def _parse(item: dict[str, Any]) -> Denomination:
    try:
        value = Decimal(str(item["value"])).quantize(CENT)
    except (InvalidOperation, KeyError) as exc:  # a stored value is always validated; never expected
        raise ValueError(f"Invalid stored denomination: {item!r}") from exc
    return Denomination(
        code=str(item["code"]),
        value=value,
        active=bool(item.get("active", True)),
        suggested_destination=item.get("suggested_destination"),
    )


def _dump(d: Denomination) -> dict[str, Any]:
    return {
        "code": d.code,
        "value": str(d.value.quantize(CENT)),
        "active": d.active,
        "suggested_destination": d.suggested_destination,
    }
