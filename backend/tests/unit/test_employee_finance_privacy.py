"""Protected areas and what Cajas shows of employee finance without one (R8-1, ADR 010, Enzo's D2 and D12).

R8 operation types do not exist yet in the ledger: their redaction is specified here on synthetic data, so
that the phase adding each one finds the rule already in place.
"""

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi import APIRouter

from app.core.errors import AppError
from app.modules.settlements.employee_finance import privacy
from app.modules.settlements.employee_finance.access import AREA
from app.modules.settlements.ledger.service import AccountBalance, EntryDetail, Movement, OperationDetail
from app.platform import modules
from app.platform.auth.access import area_role, has_area_access, require_area_access
from app.platform.auth.models import User, UserModuleRole
from app.platform.modules import ProtectedAreaSpec

NOW = datetime(2026, 10, 15, 12, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _installed_modules() -> None:
    import app.api.v1.router  # noqa: F401  (installs commercial and settlements)


def _user(role: str, **grants: str) -> User:
    rows = [UserModuleRole(module_key=key.replace("__", "."), role=r) for key, r in grants.items()]
    return User(email=f"{role}@districom.test", full_name="Test", role=role, module_roles=rows)


# --- the authorization layer -----------------------------------------------------------------------


def test_explicit_grant_even_for_the_platform_admin() -> None:
    assert area_role(_user("admin"), AREA) is None
    assert area_role(_user("admin", settlements__employee_finance="viewer"), AREA) == "viewer"


def test_the_area_needs_its_module() -> None:
    assert area_role(_user("viewer", settlements__employee_finance="admin"), AREA) is None
    assert area_role(_user("viewer", settlements="viewer", settlements__employee_finance="admin"), AREA) == "admin"
    assert area_role(_user("viewer", settlements="admin"), AREA) is None


def test_an_area_may_inherit_the_platform_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    """The flag is per area, in the module spec: an area that does not ask for an explicit grant inherits."""
    module, _ = modules.installed_area(AREA) or (None, None)
    assert module is not None
    lenient = ProtectedAreaSpec(AREA, "Empleados", explicit_grant_required=False)
    monkeypatch.setattr(modules, "installed_area", lambda key: (module, lenient) if key == AREA else None)
    monkeypatch.setattr("app.platform.auth.access.installed_area", modules.installed_area)
    assert area_role(_user("admin"), AREA) == "admin"


def test_unknown_area_and_role_order() -> None:
    assert area_role(_user("admin"), "settlements.inexistente") is None
    user = _user("viewer", settlements="viewer", settlements__employee_finance="operator")
    assert has_area_access(user, AREA, "viewer")
    assert has_area_access(user, AREA, "operator")
    assert not has_area_access(user, AREA, "admin")


def test_require_area_access_hides_with_404_and_refuses_with_403() -> None:
    check = require_area_access(AREA, "admin")
    with pytest.raises(AppError) as hidden:
        check(_user("admin"))
    assert (hidden.value.status_code, hidden.value.code, hidden.value.details) == (404, "not_found", None)
    with pytest.raises(AppError) as refused:
        check(_user("viewer", settlements="viewer", settlements__employee_finance="viewer"))
    assert (refused.value.status_code, refused.value.code) == (403, "forbidden")
    with pytest.raises(ValueError, match="Unknown module role"):
        require_area_access(AREA, "owner")


def test_area_keys_belong_to_their_module() -> None:
    with pytest.raises(ValueError, match="settlements"):
        modules.install(
            modules.ModuleSpec(
                key="otro",
                name="Otro",
                router=APIRouter(),
                protected_areas=(ProtectedAreaSpec("settlements.robada", "Robada"),),
            )
        )


# --- Cajas -----------------------------------------------------------------------------------------


def _balance(kind: str) -> AccountBalance:
    return AccountBalance(1, None, kind, kind, False, True, Decimal("10"))


def test_internal_accounts_are_not_listed() -> None:
    kinds = ["CASH_BOX", "BANK", "OWNER", "EMPLOYEE", "PENDING", "EMPLOYEE"]
    assert [b.kind for b in privacy.visible_balances([_balance(k) for k in kinds])] == [
        "CASH_BOX",
        "BANK",
        "OWNER",
        "PENDING",
    ]
    assert privacy.is_private_account(_balance("EMPLOYEE"))


def _operation(type_: str, **changes: Any) -> OperationDetail:
    detail = OperationDetail(
        id=7,
        type=type_,
        business_date=date(2026, 10, 15),
        description="Adelanto a Ana Pérez",
        created_by=1,
        created_by_name="Cajero",
        created_at=NOW,
        source_type="employee_movement",
        source_id=3,
        reverses_operation_id=None,
        reversed_by_operation_id=None,
        entries=[
            EntryDetail(10, "Caja principal", "CASH_BOX", Decimal("-50000")),
            EntryDetail(11, "Cuenta interna de Ana Pérez (#4)", "EMPLOYEE", Decimal("50000")),
        ],
    )
    return replace(detail, **changes)


def _movement(operation_id: int, type_: str) -> Movement:
    return Movement(
        entry_id=operation_id * 10,
        operation_id=operation_id,
        operation_type=type_,
        business_date=date(2026, 10, 15),
        description="Adelanto a Ana Pérez",
        amount=Decimal("-50000"),
        created_by=1,
        created_by_name="Cajero",
        created_at=NOW,
        source_type="employee_movement",
        source_id=3,
        reverses_operation_id=None,
        reversed_by_operation_id=None,
    )


class _Rows:
    def __init__(self, rows: list[tuple[int, Any]]) -> None:
        self._rows = rows

    def all(self) -> list[tuple[int, Any]]:
        return self._rows


class _FakeSession:
    """Answers the two lookups of a reversal: which operation it reverses, then that operation's type."""

    def __init__(self, reverses: dict[int, int], types: dict[int, str]) -> None:
        self._answers = [list(reverses.items()), list(types.items())]
        self.queries = 0

    def execute(self, _statement: object) -> _Rows:
        self.queries += 1
        return _Rows(self._answers.pop(0))


def test_an_r8_operation_is_redacted_to_what_reconciling_needs() -> None:
    db = _FakeSession({}, {})
    redacted = privacy.operation_for(db, _operation("EMPLOYEE_ADVANCE"), can_read=False)  # type: ignore[arg-type]
    assert (redacted.type, redacted.description) == ("EMPLOYEE_RESERVED", "Movimiento de empleado (reservado)")
    assert (redacted.source_type, redacted.source_id) == (None, None)
    assert [(e.account_kind, e.amount) for e in redacted.entries] == [("CASH_BOX", Decimal("-50000"))]
    assert (redacted.business_date, redacted.id) == (date(2026, 10, 15), 7)
    assert "Ana" not in repr(redacted)
    assert db.queries == 0


@pytest.mark.parametrize("type_", sorted(privacy.PRIVATE_OPERATION_TYPES))
def test_every_r8_type_is_private(type_: str) -> None:
    db = _FakeSession({}, {})
    assert privacy.operation_for(db, _operation(type_), can_read=False).type == "EMPLOYEE_RESERVED"  # type: ignore[arg-type]
    assert privacy.operation_for(db, _operation(type_), can_read=True).type == type_  # type: ignore[arg-type]


def test_settlement_closes_and_write_offs_are_not_redacted() -> None:
    db = _FakeSession({}, {})
    for type_ in ("SETTLEMENT_CLOSE", "PENDING_WRITE_OFF", "CASH_TRANSFER", "EXPENSE"):
        detail = _operation(type_)
        assert privacy.operation_for(db, detail, can_read=False) is detail  # type: ignore[arg-type]


def test_a_reversal_of_an_r8_operation_is_redacted_too() -> None:
    db = _FakeSession({8: 7}, {7: "EMPLOYEE_PAYOUT"})
    reversal = _operation("REVERSAL", id=8, reverses_operation_id=7)
    assert privacy.operation_for(db, reversal, can_read=False).type == "EMPLOYEE_RESERVED"  # type: ignore[arg-type]
    db = _FakeSession({8: 6}, {6: "SETTLEMENT_CLOSE"})
    reversal = _operation("REVERSAL", id=8, reverses_operation_id=6)
    assert privacy.operation_for(db, reversal, can_read=False) is reversal  # type: ignore[arg-type]


def test_cash_box_movements_redacted_one_by_one() -> None:
    db = _FakeSession({3: 1}, {1: "EMPLOYEE_ADVANCE"})
    movements = [_movement(1, "EMPLOYEE_ADVANCE"), _movement(2, "SETTLEMENT_CLOSE"), _movement(3, "REVERSAL")]
    shown = privacy.movements_for(db, movements, can_read=False)  # type: ignore[arg-type]
    assert [(m.operation_id, m.operation_type, m.description) for m in shown] == [
        (1, "EMPLOYEE_RESERVED", "Movimiento de empleado (reservado)"),
        (2, "SETTLEMENT_CLOSE", "Adelanto a Ana Pérez"),
        (3, "EMPLOYEE_RESERVED", "Movimiento de empleado (reservado)"),
    ]
    assert [m.amount for m in shown] == [Decimal("-50000")] * 3  # the box still reconciles
    assert all(m.source_id is None for m in shown if m.operation_type == "EMPLOYEE_RESERVED")
    assert privacy.movements_for(db, movements, can_read=True) is movements  # type: ignore[arg-type]
