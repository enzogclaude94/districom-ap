"""F1.3: STORE SYS · Comprobantes emitidos (docs/IMPORTS.md §3.4). The type's class, sign and universe come from
`document_types`; a document is never overwritten; `codcli` links a customer only by exact mapping."""

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import app.api.v1.router  # noqa: F401  (installs the modules: import types and quality codes)
from app.modules.commercial.config.models import DocumentType
from app.modules.commercial.config.seed import seed_config
from app.modules.commercial.documents.models import Document
from app.modules.commercial.imports.documents import parse_nommov
from app.platform.auth.models import User
from app.platform.imports.models import ImportRow
from app.platform.mappings import service as mappings
from app.platform.quality.models import DataQualityIssue
from tests.modules.commercial.store_sys import (
    CUSTOMERS,
    DOCUMENTS,
    analyze,
    confirm,
    customer,
    customers_file,
    document,
    documents_file,
    run,
)

DAY = date(2026, 10, 23)


@pytest.fixture
def operator(db: Session, make_user, grant) -> User:  # type: ignore[no-untyped-def]
    user = make_user(email="operator@districom.test", role="viewer")
    grant("operator", "commercial", "operator")
    seed_config(db)
    db.refresh(user)
    run(db, user, customers_file([customer(1, "CLIENTE UNO"), customer(8, "CLIENTE OCHO")]), CUSTOMERS)
    return user  # type: ignore[no-any-return]


def docs(db: Session) -> dict[str, Document]:
    return {d.source_type_name + " " + d.number: d for d in db.scalars(select(Document))}


def rows(db: Session, job_id: int) -> dict[int, tuple[str, set[str]]]:
    found = db.scalars(select(ImportRow).where(ImportRow.job_id == job_id)).all()
    return {r.row_number: (r.status, {m["code"] for m in r.messages}) for r in found}


def open_issues(db: Session, code: str) -> list[DataQualityIssue]:
    return list(
        db.scalars(select(DataQualityIssue).where(DataQualityIssue.code == code, DataQualityIssue.status == "open"))
    )


def customer_id(db: Session, code: int) -> int | None:
    return mappings.resolve(db, mappings.MappingKey("customer", "STORE_SYS", "customer", str(code))).entity_id


@pytest.mark.parametrize(
    ("nommov", "parts"),
    [
        ("FACTURA A 0003-00012345", ("FACTURA A", "A", "0003", "00012345")),
        ("NOTA DE CREDITO A 0003-00000077", ("NOTA DE CREDITO A", "A", "0003", "00000077")),
        ("RETIRO PR.  0001-00000004", ("RETIRO PR.", None, "0001", "00000004")),
        ("FACTURA  0000-00000002", ("FACTURA", None, "0000", "00000002")),
        ("VALE PERSONAL V 0001-00000009", ("VALE PERSONAL V", "V", "0001", "00000009")),
        ("SIN NUMERO", None),
    ],
)
def test_nommov_is_read_as_type_letter_point_of_sale_and_number(
    nommov: str, parts: tuple[str, str | None, str, str] | None
) -> None:
    assert parse_nommov(nommov) == parts


def test_types_signs_and_universe_come_from_document_types(db: Session, operator: User) -> None:
    data = documents_file(
        [
            document("FACTURA A 0003-00000001", code=1, net=Decimal("1000.00"), total=Decimal("1210.00")),
            document("FACTURA B 0003-00000002", code=1, net=Decimal("50.00"), total=Decimal("60.50")),
            document("NOTA DE CREDITO A 0003-00000003", code=1, net=Decimal("100.00"), total=Decimal("-121.00")),
            document("DEVOLUCION X 0002-00000004", code=1, net=Decimal("20.00"), total=Decimal("-20.00")),
            document("RETIRO PR.  0001-00000005", code=0, name=None, net=0, total=0),
            document("RECIBOS X 0002-00000006", code=1, net=0, total=Decimal("500.00")),
            document("VALE PERSONAL V 0001-00000007", code=8, net=0, total=Decimal("3000.00")),
            document("COMP. INTERNO X 0002-00000008", code=1, net=Decimal("10.00"), total=Decimal("10.00")),
            document("FACTURA  0000-00000009", code=1, net=0, total=Decimal("15.00")),
            document("TICKET Z 0009-00000010", code=1, net=Decimal("5.00"), total=Decimal("5.00")),
        ]
    )
    job = analyze(db, operator, data, None)
    assert (job.status, job.import_type) == ("pending", DOCUMENTS), (job.failure_reason, job.failure_detail)
    assert (job.period_from, job.period_to) == (DAY, DAY)
    assert job.job_metadata["preview"]["new_types"] == ["TICKET Z"]
    job = confirm(db, operator, job)
    assert job.rows_inserted == 10
    found = docs(db)
    types = {t.id: t for t in db.scalars(select(DocumentType))}

    def signed(key: str) -> Decimal:
        d = found[key]
        return d.net_amount * types[d.document_type_id].sign

    assert signed("FACTURA A 00000001") == Decimal("1000.00")
    assert signed("NOTA DE CREDITO A 00000003") == Decimal("-100.00")
    assert signed("DEVOLUCION X 00000004") == Decimal("-20.00")
    universe = {k for k, d in found.items() if types[d.document_type_id].commercial_universe}
    assert universe == {
        "FACTURA A 00000001",
        "FACTURA B 00000002",
        "NOTA DE CREDITO A 00000003",
        "DEVOLUCION X 00000004",
    }
    assert found["RETIRO PR. 00000005"].customer_id is None
    assert found["FACTURA A 00000001"].customer_id == customer_id(db, 1)
    assert found["RECIBOS X 00000006"].customer_id == customer_id(db, 1)
    # A voucher's codcli is not a customer code: kept, never linked (even if a customer 8 exists).
    vale = found["VALE PERSONAL V 00000007"]
    assert (vale.customer_id, vale.source_customer_code) == (None, "8")
    assert found["FACTURA A 00000001"].issued_at == datetime(
        2026, 10, 23, 9, 30, tzinfo=ZoneInfo("America/Argentina/Buenos_Aires")
    )
    new_type = db.scalars(select(DocumentType).where(DocumentType.source_name == "TICKET Z")).one()
    assert (new_type.document_class, new_type.commercial_universe) == ("UNCLASSIFIED", False)
    unclassified = {i.entity_id for i in open_issues(db, "commercial.document.unclassified_type")}
    assert {types[i].source_name for i in unclassified} == {"TICKET Z", "COMP. INTERNO X", "FACTURA"}
    statuses = rows(db, job.id)
    assert statuses[11] == ("warning", {"new_document_type"})
    assert statuses[6][0] == "valid"  # RETIRO with codcli 0: outside the universe, nothing to warn


def test_commercial_warnings(db: Session, operator: User) -> None:
    job = analyze(
        db,
        operator,
        documents_file(
            [
                document("FACTURA A 0003-00000001", code=0),
                document("FACTURA A 0003-00000002", code=1, total=Decimal("-121.00")),
                document("FACTURA B 0003-00000003", code=1, net=0),
            ]
        ),
        DOCUMENTS,
    )
    statuses = rows(db, job.id)
    assert statuses[2] == ("warning", {"without_customer"})
    assert statuses[3] == ("warning", {"sign_mismatch"})
    assert statuses[4] == ("warning", {"zero_net"})


def test_an_unknown_customer_code_stays_pending_with_an_issue(db: Session, operator: User) -> None:
    job = run(db, operator, documents_file([document("FACTURA A 0003-00000001", code=999)]), DOCUMENTS)
    assert rows(db, job.id)[2] == ("pending_resolution", {"customer_unresolved"})
    assert docs(db)["FACTURA A 00000001"].customer_id is None
    assert docs(db)["FACTURA A 00000001"].source_customer_code == "999"
    assert [i.external_ref for i in open_issues(db, "commercial.customer.unresolved_code")] == [
        {"source_system": "STORE_SYS", "namespace": "customer", "external_id": "999"}
    ]


def test_the_same_document_again_is_a_duplicate_and_a_changed_one_is_never_overwritten(
    db: Session, operator: User
) -> None:
    first = [document("FACTURA A 0003-00000001", code=1), document("FACTURA B 0003-00000002", code=1)]
    run(db, operator, documents_file(first), DOCUMENTS)
    again = analyze(
        db,
        operator,
        documents_file([*first, document("FACTURA A 0003-00000003", code=1, total=Decimal("99.00"))]),
        DOCUMENTS,
    )
    assert [rows(db, again.id)[n][0] for n in (2, 3, 4)] == ["duplicate", "duplicate", "valid"]
    confirm(db, operator, again)
    assert db.scalar(select(func.count()).select_from(Document)) == 3

    changed = analyze(
        db, operator, documents_file([document("FACTURA A 0003-00000001", code=1, total=Decimal("1.00"))]), DOCUMENTS
    )
    assert rows(db, changed.id)[2] == ("rejected", {"document_changed"})
    assert changed.job_metadata["preview"]["documents"]["changed"] == 1
    confirm(db, operator, changed)
    original = docs(db)["FACTURA A 00000001"]
    assert original.total_amount == Decimal("121.00")
    assert [i.entity_id for i in open_issues(db, "commercial.document.changed")] == [original.id]


def test_the_same_key_twice_in_a_file(db: Session, operator: User) -> None:
    job = analyze(
        db,
        operator,
        documents_file(
            [
                document("FACTURA A 0003-00000001", code=1),
                document("FACTURA A 0003-00000001", code=1),
                document("FACTURA B 0003-00000002", code=1),
                document("FACTURA B 0003-00000002", code=1, total=Decimal("5.00")),
            ]
        ),
        DOCUMENTS,
    )
    statuses = rows(db, job.id)
    assert [statuses[n][0] for n in (2, 3, 4, 5)] == ["valid", "duplicate", "rejected", "rejected"]


def test_invalid_rows_are_rejected_with_a_reason(db: Session, operator: User) -> None:
    job = analyze(
        db,
        operator,
        documents_file(
            [
                document("SIN NUMERO", code=1),
                [None, "FACTURA A 0003-00000002", "X", 1, 100, 121, 0, None],
                [datetime(2026, 10, 23, 9), "FACTURA A 0003-00000003", "X", "uno", 100, 121, 0, DAY],
            ]
        ),
        DOCUMENTS,
    )
    statuses = rows(db, job.id)
    assert statuses[2] == ("rejected", {"invalid_document_number"})
    assert statuses[3] == ("rejected", {"missing_business_date"})
    assert statuses[4] == ("rejected", {"invalid_customer_code"})
