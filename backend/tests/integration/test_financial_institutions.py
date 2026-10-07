"""Bank catalog of the platform (R4-1, Enzo's decision D-I): official BCRA entries never change, an admin adds
a missing entity with a reason and (de)activates any; nothing is deleted."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

URL = "/api/v1/financial-institutions"


def test_every_signed_in_user_reads_the_catalog(login) -> None:  # type: ignore[no-untyped-def]
    rows = login("viewer").get(URL).json()
    galicia = next(r for r in rows if r["bcra_code"] == 7)
    assert (galicia["code"], galicia["source"], galicia["active"]) == ("007", "BCRA", True)
    assert [r["bcra_code"] for r in rows] == sorted(r["bcra_code"] for r in rows)


def test_only_a_platform_admin_adds_or_deactivates(login) -> None:  # type: ignore[no-untyped-def]
    viewer = login("viewer")
    assert viewer.post(URL, json={"bcra_code": 900, "name": "Banco Nuevo", "reason": "Falta"}).status_code == 403
    galicia = next(r for r in viewer.get(URL).json() if r["bcra_code"] == 7)
    assert viewer.patch(f"{URL}/{galicia['id']}", json={"active": False}).status_code == 403


def test_a_missing_entity_is_added_with_its_reason(login, db: Session) -> None:  # type: ignore[no-untyped-def]
    admin = login("admin")
    created = admin.post(URL, json={"bcra_code": 900, "name": "  Banco   Nuevo ", "reason": "Aparece en un cheque"})
    assert created.status_code == 201, created.text
    assert (created.json()["name"], created.json()["source"], created.json()["code"]) == (
        "Banco Nuevo",
        "MANUAL",
        "900",
    )
    audit = db.execute(
        text("SELECT payload FROM audit_events WHERE event_type = 'platform.financial_institution.added'")
    ).scalar_one()
    assert audit["after"]["reason"] == "Aparece en un cheque"


def test_an_official_code_is_never_silently_modified(login, db: Session) -> None:  # type: ignore[no-untyped-def]  # [18]
    admin = login("admin")
    taken = admin.post(URL, json={"bcra_code": 7, "name": "Otro banco", "reason": "x"})
    assert (taken.status_code, taken.json()["code"]) == (409, "bank_code_taken")
    for change in ("name = 'Otro'", "bcra_code = 99998", "source = 'MANUAL'"):
        message = _refused(db, f"UPDATE financial_institutions SET {change} WHERE bcra_code = 7")  # noqa: S608
        assert "only active changes" in message
    assert "never deleted" in _refused(db, "DELETE FROM financial_institutions WHERE bcra_code = 7")


def _refused(db: Session, sql: str) -> str:
    def _write() -> None:
        db.execute(text(sql))
        db.commit()

    with pytest.raises(DBAPIError) as caught:
        _write()
    db.rollback()
    return str(caught.value.orig)


def test_deactivated_entities_are_hidden_but_kept(login) -> None:  # type: ignore[no-untyped-def]
    admin = login("admin")
    galicia = next(r for r in admin.get(URL).json() if r["bcra_code"] == 7)
    assert admin.patch(f"{URL}/{galicia['id']}", json={"active": False}).json()["active"] is False
    assert 7 not in [r["bcra_code"] for r in admin.get(URL).json()]
    assert 7 in [r["bcra_code"] for r in admin.get(URL, params={"include_inactive": True}).json()]
    assert admin.patch(f"{URL}/{galicia['id']}", json={"active": True}).json()["active"] is True
