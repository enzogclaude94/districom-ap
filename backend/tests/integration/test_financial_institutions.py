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
    assert (galicia["name"], galicia["source"], galicia["active"]) == (
        "BANCO DE GALICIA Y BUENOS AIRES S.A.",
        "BCRA",
        True,
    )
    assert "code" not in galicia  # the official integer is the code; "007" is only a way to print it
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
    assert (created.json()["name"], created.json()["source"], created.json()["bcra_code"]) == (
        "Banco Nuevo",
        "MANUAL",
        900,
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


def test_the_seed_is_the_official_bcra_snapshot(db: Session) -> None:
    """Enzo's R4-1 adjustment 1: the 59 entities of the BCRA API de Cheques (/entidades, 2026-10-07), with
    their official integer code and their name verbatim, none MANUAL, none invented from memory."""
    rows = db.execute(
        text("SELECT bcra_code, name FROM financial_institutions WHERE source = 'BCRA' ORDER BY bcra_code")
    ).all()
    assert len(rows) == 59
    official = dict(rows)
    assert official[7] == "BANCO DE GALICIA Y BUENOS AIRES S.A."
    assert official[11] == "BANCO DE LA NACION ARGENTINA"
    assert official[34] == "BANCO PATAGONIA  S.A."  # two spaces, as the source writes it
    assert official[150] == "Banco GGAL SA"
    assert official[386] == "NUEVO BANCO DE ENTRE RÍOS S.A."
    assert (min(official), max(official)) == (5, 448)
    # Entities of the old memory-built seed that the official list does not carry are not there.
    assert 143 not in official
    assert 300 not in official
    manual = db.execute(text("SELECT count(*) FROM financial_institutions WHERE source = 'MANUAL'")).scalar_one()
    assert manual == 0
