"""F1.1b: the generic import pipeline on a synthetic type (docs/IMPORTS.md §2, §6-§8, ADR 014).

Analysis writes nothing canonical; a confirmation is one transaction under the type's advisory lock; the same file
of the same completed type is a duplicate; a failure rolls everything back; originals are never modified; no row
values reach logs or audit events.
"""

import hashlib
import io
import json
import logging
import threading
import time
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

import app.api.v1.router  # noqa: F401  (composition root: installs the modules)
from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.db.session import get_db
from app.platform.audit.models import AuditEvent
from app.platform.auth.models import User
from app.platform.imports import service
from app.platform.imports.models import ImportFile, ImportJob, ImportRow
from app.platform.quality.models import DataQualityIssue
from tests.imports.adapter import OTHER_TYPE_KEY, TYPE_KEY
from tests.imports.files import biff7_xls, csv_bytes, xlsx
from tests.imports.fixtures import SyntheticTypes

pytestmark = pytest.mark.usefixtures("synthetic_types")

SECRET_NAME = "ALMACEN DOÑA ROSA"  # a value that must never leave the staging tables
REPORT = [
    ["DISTRIBUIDORA DEMO - Ítems"],
    ["codigo", "nombre", "cantidad", "fecha"],
    [1001, SECRET_NAME, 6, date(2026, 10, 23)],
    [1002, "Caña Ñandú", Decimal("1.5"), date(2026, 10, 24)],
    [None, None, None, None],
    [None, "sin código", 1, None],
    [1003, "x", "muchas", None],
    [1002, "repetido", 1, None],
    [1004, None, 2, None],
]


@pytest.fixture
def settings() -> Settings:
    return get_settings()


@pytest.fixture
def operator(db: Session, make_user, grant) -> User:  # type: ignore[no-untyped-def]
    user = make_user(email="operator@districom.test", role="viewer")
    grant("operator", "commercial", "operator")
    db.refresh(user)
    return user  # type: ignore[no-any-return]


@pytest.fixture
def admin(make_user) -> User:  # type: ignore[no-untyped-def]
    return make_user(email="admin@districom.test", role="admin")  # type: ignore[no-any-return]


def upload(
    db: Session, user: User, settings: Settings, data: bytes, name: str = "items.xls", import_type: str | None = None
) -> ImportJob:
    return service.upload(db, user, settings, service.Upload(io.BytesIO(data), name, None), import_type)


def items(db: Session) -> dict[str, tuple[Any, ...]]:
    rows = db.execute(text("SELECT code, name, quantity, day FROM test_import_items ORDER BY code")).all()
    db.rollback()
    return {r.code: (r.name, r.quantity, r.day) for r in rows}


def statuses(db: Session, job_id: int) -> list[tuple[int, str, str | None]]:
    rows = db.execute(
        select(ImportRow.row_number, ImportRow.status, ImportRow.outcome)
        .where(ImportRow.job_id == job_id)
        .order_by(ImportRow.row_number)
    ).all()
    return [tuple(r) for r in rows]  # type: ignore[misc]


# --- analysis -----------------------------------------------------------------------------------------------------


def test_analysis_stages_every_row_and_writes_nothing_canonical(
    db: Session, operator: User, settings: Settings
) -> None:
    job = upload(db, operator, settings, biff7_xls(REPORT))
    assert job.status == "pending", job.failure_detail
    assert (job.import_type, job.parser_version) == (TYPE_KEY, "test-1")
    assert items(db) == {}
    assert statuses(db, job.id) == [
        (1, "skipped", None),
        (2, "skipped", None),
        (3, "valid", None),
        (4, "valid", None),
        (5, "skipped", None),
        (6, "rejected", None),
        (7, "rejected", None),
        (8, "duplicate", None),
        (9, "warning", None),
    ]
    assert (job.rows_detected, job.rows_processed, job.rows_valid, job.rows_rejected, job.rows_duplicate) == (
        9,
        6,
        3,
        2,
        1,
    )
    assert (job.warning_count, job.error_count) == (2, 2)
    assert (job.period_from, job.period_to) == (date(2026, 10, 23), date(2026, 10, 24))
    assert job.job_metadata["preview"] == {"new_codes": 3}
    assert job.job_metadata["reader"]["biff_version"] == 70
    detection = job.job_metadata["detection"]
    assert (detection[TYPE_KEY], detection[OTHER_TYPE_KEY]) == (1.0, 0.5)
    assert all(score < 0.8 for key, score in detection.items() if key not in (TYPE_KEY, OTHER_TYPE_KEY))
    raw = db.scalar(select(ImportRow.raw).where(ImportRow.job_id == job.id, ImportRow.row_number == 4))
    assert raw == [{"n": "1002"}, "Caña Ñandú", {"n": "1.5"}, {"d": "2026-10-24"}]


def test_analysis_runs_read_only(
    db: Session, operator: User, settings: Settings, synthetic_types: SyntheticTypes
) -> None:
    synthetic_types.items.knobs.write_during_analyze = True
    job = upload(db, operator, settings, biff7_xls(REPORT))
    assert (job.status, job.failure_reason) == ("failed", "unexpected_error")
    assert items(db) == {}
    assert db.scalar(select(ImportRow.id).where(ImportRow.job_id == job.id)) is None


def test_forced_type_needs_its_headers(db: Session, operator: User, settings: Settings) -> None:
    job = upload(db, operator, settings, csv_bytes([["codigo", "nombre", "fecha"], ["1", "a", "x"]]), "i.csv", TYPE_KEY)
    assert (job.status, job.failure_reason) == ("failed", "header_mismatch")
    extra = csv_bytes([["codigo", "nombre", "cantidad", "otra"], ["1", "a", "2", "x"]])
    assert upload(db, operator, settings, extra, "i.csv", TYPE_KEY).status == "pending"


def test_structural_error_in_analysis_records_missing_columns(
    db: Session, operator: User, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.platform.imports.service.DETECTION_THRESHOLD", 0.5)
    job = upload(db, operator, settings, csv_bytes([["codigo", "nombre", "x"], ["1", "a", "b"]]), "i.csv", TYPE_KEY)
    assert (job.status, job.failure_reason) == ("failed", "missing_columns")
    issue = db.scalars(select(DataQualityIssue).where(DataQualityIssue.job_id == job.id)).one()
    assert issue.code == "platform.import.missing_columns"
    assert issue.details["missing"] == ["quantity"]


@pytest.mark.parametrize(
    ("rows", "import_type", "reason"),
    [
        ([["codigo", "nombre", "cantidad", "motivo"], ["1", "a", "2", "b"]], None, "ambiguous_type"),
        ([["fecha", "importe"], ["1", "2"]], None, "type_not_detected"),
        ([["codigo", "motivo"], ["1", "b"]], TYPE_KEY, "header_mismatch"),
    ],
)
def test_type_detection(
    db: Session, operator: User, settings: Settings, rows: list[list[str]], import_type: str | None, reason: str
) -> None:
    job = upload(db, operator, settings, csv_bytes(rows), "r.csv", import_type)
    assert (job.status, job.failure_reason) == ("failed", reason)
    assert job.import_type is None


def test_other_type_is_detected_by_its_headers(db: Session, operator: User, settings: Settings) -> None:
    job = upload(db, operator, settings, xlsx([["codigo", "motivo"], ["1", "b"]]), "r.xlsx")
    assert (job.status, job.import_type) == ("pending", OTHER_TYPE_KEY), (job.failure_reason, job.failure_detail)


@pytest.mark.parametrize(
    ("data", "name", "reason"),
    [
        (b"", "vacio.csv", "empty_file"),
        (b"<html><table><tr><td>codigo</td></tr></table></html>", "ventas.xls", "unsupported_format"),
        (biff7_xls(REPORT)[:700], "ventas.xls", "unreadable_file"),
        (xlsx(REPORT)[:400], "ventas.xlsx", "unreadable_file"),
    ],
)
def test_corrupt_or_unsupported_files_fail_and_keep_the_original(
    db: Session, operator: User, settings: Settings, data: bytes, name: str, reason: str
) -> None:
    job = upload(db, operator, settings, data, name)
    assert (job.status, job.failure_reason) == ("failed", reason)
    file = db.get_one(ImportFile, job.file_id)
    assert service.stored_file_path(settings, file).read_bytes() == data


def test_misleading_extension_is_read_by_signature(db: Session, operator: User, settings: Settings) -> None:
    job = upload(db, operator, settings, biff7_xls(REPORT), "items.csv")
    assert job.status == "pending"
    assert db.get_one(ImportFile, job.file_id).detected_format == "XLS"
    assert job.job_metadata["extension_mismatch"] is True


def test_extension_and_size_limits(db: Session, operator: User, settings: Settings) -> None:
    with pytest.raises(AppError) as error:
        upload(db, operator, settings, b"codigo\n1\n", "items.txt")
    assert (error.value.status_code, error.value.code) == (422, "invalid_extension")

    small = settings.model_copy(update={"upload_max_size_mb": 1})
    with pytest.raises(AppError) as error:
        upload(db, operator, small, b"x" * (1024 * 1024 + 1), "items.csv")
    assert (error.value.status_code, error.value.code) == (413, "request_too_large")
    assert db.scalar(select(ImportFile.id)) is None
    assert not [p for p in settings.upload_dir.iterdir() if p.name.startswith(".incoming-")]


def test_originals_are_stored_read_only_and_never_change(db: Session, operator: User, settings: Settings) -> None:
    data = biff7_xls(REPORT)
    job = upload(db, operator, settings, data)
    service.confirm(db, operator, settings, job.id)
    file = db.get_one(ImportFile, job.file_id)
    path = service.stored_file_path(settings, file)
    assert path.parent == settings.upload_dir
    assert path.read_bytes() == data
    assert file.sha256 == hashlib.sha256(data).hexdigest()
    assert path.stat().st_mode & 0o777 == 0o440


# --- confirmation, idempotency, rollback --------------------------------------------------------------------------


def test_confirm_writes_valid_rows_and_records_rejections(db: Session, operator: User, settings: Settings) -> None:
    job = upload(db, operator, settings, biff7_xls(REPORT))
    job = service.confirm(db, operator, settings, job.id)
    assert job.status == "completed_with_warnings"
    assert (job.rows_inserted, job.rows_updated, job.rows_unchanged) == (3, 0, 0)
    assert items(db) == {
        "1001": (SECRET_NAME, Decimal(6), date(2026, 10, 23)),
        "1002": ("Caña Ñandú", Decimal("1.5"), date(2026, 10, 24)),
        "1004": (None, Decimal(2), None),
    }
    outcomes = {n: o for n, _, o in statuses(db, job.id)}
    assert (outcomes[3], outcomes[4], outcomes[9], outcomes[6]) == ("inserted", "inserted", "inserted", None)
    issues = db.scalars(
        select(DataQualityIssue)
        .where(DataQualityIssue.code == "platform.import.invalid_row")
        .order_by(DataQualityIssue.id)
    ).all()
    assert [i.details["codes"] for i in issues] == [["missing_code"], ["invalid_quantity"]]


def test_clean_file_completes(db: Session, operator: User, settings: Settings) -> None:
    job = upload(db, operator, settings, xlsx([["codigo", "nombre", "cantidad"], [1, "a", 2]]), "i.xlsx")
    assert service.confirm(db, operator, settings, job.id).status == "completed"


def test_same_file_twice_is_a_duplicate_and_counts_do_not_change(
    db: Session, operator: User, settings: Settings
) -> None:
    data = biff7_xls(REPORT)
    first = service.confirm(db, operator, settings, upload(db, operator, settings, data).id)
    before = items(db)
    rows_before = db.scalar(text("SELECT count(*) FROM import_rows"))

    second = upload(db, operator, settings, data, "otro-nombre.xls")
    assert second.status == "duplicate"
    assert second.duplicate_of_job_id == first.id
    assert items(db) == before
    assert db.scalar(text("SELECT count(*) FROM import_rows")) == rows_before
    issue = db.scalars(select(DataQualityIssue).where(DataQualityIssue.code == "platform.import.duplicate_file")).one()
    assert issue.job_id == second.id
    with pytest.raises(AppError) as error:
        service.confirm(db, operator, settings, second.id)
    assert error.value.code == "import_job_not_pending"


def test_same_content_reuploaded_after_a_change_is_idempotent_per_row(
    db: Session, operator: User, settings: Settings
) -> None:
    service.confirm(db, operator, settings, upload(db, operator, settings, biff7_xls(REPORT)).id)
    changed = [*REPORT[:3], [1002, "Caña Ñandú", 7, date(2026, 10, 24)], [1005, "nuevo", 1, None]]
    job = service.confirm(db, operator, settings, upload(db, operator, settings, xlsx(changed), "i.xlsx").id)
    assert (job.rows_inserted, job.rows_updated, job.rows_unchanged) == (1, 1, 1)
    assert items(db)["1002"][1] == Decimal(7)
    assert len(items(db)) == 4


def test_failed_or_cancelled_twin_is_not_a_duplicate(db: Session, operator: User, settings: Settings) -> None:
    data = biff7_xls(REPORT)
    service.cancel(db, operator, upload(db, operator, settings, data).id)
    assert upload(db, operator, settings, data).status == "pending"


def test_a_failure_in_the_middle_rolls_back_everything(
    db: Session, operator: User, settings: Settings, synthetic_types: SyntheticTypes
) -> None:
    synthetic_types.items.knobs.fail_after_writes = 2
    job = upload(db, operator, settings, biff7_xls(REPORT))
    job = service.confirm(db, operator, settings, job.id)
    assert (job.status, job.failure_reason) == ("failed", "unexpected_error")
    assert items(db) == {}
    assert all(outcome is None for _, _, outcome in statuses(db, job.id))
    assert (job.rows_inserted, job.rows_updated) == (0, 0)
    assert not db.scalars(select(DataQualityIssue).where(DataQualityIssue.code == "platform.import.invalid_row")).all()
    events = db.scalars(select(AuditEvent.event_type).where(AuditEvent.entity_id == str(job.id))).all()
    assert "platform.import.confirmed" not in events
    assert events[-1] == "platform.import.failed"

    # the failure left nothing behind: the same file can be imported again
    synthetic_types.items.knobs.fail_after_writes = None
    again = service.confirm(db, operator, settings, upload(db, operator, settings, biff7_xls(REPORT)).id)
    assert again.status == "completed_with_warnings"


def test_structural_error_on_write_rolls_back(
    db: Session, operator: User, settings: Settings, synthetic_types: SyntheticTypes
) -> None:
    synthetic_types.items.knobs.structural_error_on_write = True
    job = service.confirm(db, operator, settings, upload(db, operator, settings, biff7_xls(REPORT)).id)
    assert (job.status, job.failure_reason) == ("failed", "source_conflict")
    assert items(db) == {}


THREE_ROWS = [["codigo", "nombre", "cantidad"], [1, "a", 1], [2, "b", 2], [3, "c", 3]]


@pytest.mark.parametrize("reported", [2, 0])
def test_missing_outcomes_fail_the_confirmation_and_roll_back(
    db: Session,
    operator: User,
    settings: Settings,
    synthetic_types: SyntheticTypes,
    caplog: pytest.LogCaptureFixture,
    reported: int,
) -> None:
    """The adapter writes all 3 writable rows but reports outcomes for fewer: the platform refuses the contract."""
    service.confirm(
        db, operator, settings, upload(db, operator, settings, xlsx([THREE_ROWS[0], [9, "z", 9]]), "s.xlsx").id
    )
    before = items(db)
    job = upload(db, operator, settings, xlsx(THREE_ROWS), "t.xlsx")
    assert (job.status, job.rows_valid) == ("pending", 3)
    synthetic_types.items.knobs.report_only = reported
    caplog.set_level(logging.INFO)

    job = service.confirm(db, operator, settings, job.id)

    assert (job.status, job.failure_reason) == ("failed", "adapter_contract_violation")
    assert items(db) == before  # the 3 writes were rolled back
    assert [outcome for _, _, outcome in statuses(db, job.id)] == [None, None, None, None]
    assert (job.rows_inserted, job.rows_updated, job.rows_unchanged, job.rows_superseded) == (0, 0, 0, 0)
    events = db.scalars(select(AuditEvent.event_type).where(AuditEvent.entity_id == str(job.id))).all()
    assert "platform.import.confirmed" not in events
    assert events[-1] == "platform.import.failed"
    logged = [r for r in caplog.records if r.getMessage() == "import_adapter_contract_violation"]
    assert len(logged) == 1
    assert logged[0].__dict__["error"] == f"Adapter {TYPE_KEY} returned {reported} outcomes for 3 writable rows"


def test_only_pending_jobs_confirm_or_cancel(db: Session, operator: User, settings: Settings) -> None:
    job = upload(db, operator, settings, biff7_xls(REPORT))
    assert service.cancel(db, operator, job.id).status == "cancelled"
    for action in (
        lambda: service.confirm(db, operator, settings, job.id),
        lambda: service.cancel(db, operator, job.id),
    ):
        with pytest.raises(AppError) as error:
            action()
        assert error.value.code == "import_job_not_pending"
    assert items(db) == {}


def test_reprocess_reads_the_stored_file_again(db: Session, operator: User, admin: User, settings: Settings) -> None:
    original = service.confirm(db, operator, settings, upload(db, operator, settings, biff7_xls(REPORT)).id)
    with pytest.raises(AppError) as error:
        service.reprocess(db, operator, settings, original.id)
    assert error.value.status_code == 403
    job = service.reprocess(db, admin, settings, original.id)
    assert (job.status, job.reprocess_of_job_id, job.file_id) == ("pending", original.id, original.file_id)
    job = service.confirm(db, admin, settings, job.id)
    assert (job.rows_inserted, job.rows_unchanged) == (0, 3)


# --- the per-type lock --------------------------------------------------------------------------------------------


def _confirm_in_thread(job_id: int, user_id: int, settings: Settings, results: dict[int, Any]) -> threading.Thread:
    def run() -> None:
        session = next(get_db())
        try:
            user = session.get_one(User, user_id)
            results[job_id] = service.confirm(session, user, settings, job_id).status
        except AppError as exc:
            results[job_id] = exc.code
        finally:
            session.close()

    thread = threading.Thread(target=run)
    thread.start()
    return thread


def _waiting_for_advisory_lock(db: Session) -> int:
    count = db.scalar(text("SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND NOT granted")) or 0
    db.rollback()
    return count


def _wait_until(condition: Any, timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.05)


def test_confirmations_of_one_type_are_serialized(
    db: Session, operator: User, settings: Settings, synthetic_types: SyntheticTypes
) -> None:
    knobs = synthetic_types.items.knobs
    knobs.release = threading.Event()
    first = upload(db, operator, settings, csv_bytes([["codigo", "nombre", "cantidad"], ["1", "a", "1"]]), "a.csv")
    second = upload(db, operator, settings, csv_bytes([["codigo", "nombre", "cantidad"], ["1", "b", "2"]]), "b.csv")
    results: dict[int, Any] = {}

    holder = _confirm_in_thread(first.id, operator.id, settings, results)
    assert knobs.entered.wait(10)
    knobs.entered.clear()
    waiter = _confirm_in_thread(second.id, operator.id, settings, results)
    _wait_until(lambda: _waiting_for_advisory_lock(db) == 1)
    assert not knobs.entered.is_set()  # the second never reached the adapter while the first held the lock
    assert items(db) == {}

    knobs.release.set()
    holder.join(15)
    waiter.join(15)
    assert results == {first.id: "completed", second.id: "completed"}
    assert items(db) == {"1": ("b", Decimal(2), None)}  # applied in order: the second one wins


def test_the_lock_is_per_type(db: Session, operator: User, settings: Settings, synthetic_types: SyntheticTypes) -> None:
    knobs = synthetic_types.items.knobs
    knobs.release = threading.Event()
    held = upload(db, operator, settings, csv_bytes([["codigo", "nombre", "cantidad"], ["1", "a", "1"]]), "a.csv")
    other = upload(db, operator, settings, csv_bytes([["codigo", "motivo"], ["1", "b"]]), "o.csv")
    assert other.import_type == OTHER_TYPE_KEY
    results: dict[int, Any] = {}
    holder = _confirm_in_thread(held.id, operator.id, settings, results)
    assert knobs.entered.wait(10)
    try:
        assert service.confirm(db, operator, settings, other.id).status == "completed"
    finally:
        knobs.release.set()
        holder.join(15)
    assert results[held.id] == "completed"


def test_a_confirmation_that_cannot_get_the_lock_goes_back_to_pending(
    db: Session, operator: User, settings: Settings, synthetic_types: SyntheticTypes, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.platform.imports.service.LOCK_WAIT_MS", 300)
    knobs = synthetic_types.items.knobs
    knobs.release = threading.Event()
    first = upload(db, operator, settings, csv_bytes([["codigo", "nombre", "cantidad"], ["1", "a", "1"]]), "a.csv")
    second = upload(db, operator, settings, csv_bytes([["codigo", "nombre", "cantidad"], ["2", "b", "2"]]), "b.csv")
    results: dict[int, Any] = {}
    holder = _confirm_in_thread(first.id, operator.id, settings, results)
    assert knobs.entered.wait(10)
    try:
        with pytest.raises(AppError) as error:
            service.confirm(db, operator, settings, second.id)
        assert (error.value.status_code, error.value.code) == (409, "import_in_progress")
        db.expire_all()
        job = db.get_one(ImportJob, second.id)
        assert (job.status, job.confirmed_by) == ("pending", None)
    finally:
        knobs.release.set()
        holder.join(15)
    assert service.confirm(db, operator, settings, second.id).status == "completed"


def test_two_pending_jobs_of_the_same_file_confirm_once(db: Session, operator: User, settings: Settings) -> None:
    data = biff7_xls(REPORT)
    first, second = upload(db, operator, settings, data), upload(db, operator, settings, data)
    assert (first.status, second.status) == ("pending", "pending")  # neither was completed yet
    assert service.confirm(db, operator, settings, first.id).status == "completed_with_warnings"
    job = service.confirm(db, operator, settings, second.id)  # checked again under the lock
    assert (job.status, job.duplicate_of_job_id) == ("duplicate", first.id)
    assert job.rows_inserted == 0


# --- recovery -----------------------------------------------------------------------------------------------------


def _age(db: Session, job_id: int) -> None:
    db.execute(
        update(ImportJob)
        .where(ImportJob.id == job_id)
        .values(status="processing", updated_at=ImportJob.updated_at - timedelta(hours=1))
    )
    db.commit()


def test_recover_fails_interrupted_jobs_only(db: Session, operator: User, settings: Settings) -> None:
    stale = upload(db, operator, settings, biff7_xls(REPORT))
    fresh = upload(db, operator, settings, xlsx(REPORT), "i.xlsx")
    _age(db, stale.id)
    db.execute(update(ImportJob).where(ImportJob.id == fresh.id).values(status="processing"))
    db.commit()
    assert service.recover(db, timedelta(minutes=30)) == [stale.id]
    db.expire_all()
    assert db.get_one(ImportJob, stale.id).failure_reason == "interrupted"
    assert db.get_one(ImportJob, fresh.id).status == "processing"


def test_recover_leaves_a_job_whose_type_is_locked(db: Session, operator: User, settings: Settings) -> None:
    stale = upload(db, operator, settings, biff7_xls(REPORT))
    _age(db, stale.id)
    holder = next(get_db())
    try:
        holder.execute(
            text("SELECT pg_advisory_xact_lock(:ns, hashtext(:key))"), {"ns": service.LOCK_NAMESPACE, "key": TYPE_KEY}
        )
        assert service.recover(db, timedelta(minutes=30)) == []
    finally:
        holder.rollback()
        holder.close()
    assert service.recover(db, timedelta(minutes=30)) == [stale.id]


# --- permissions and privacy --------------------------------------------------------------------------------------


def test_permissions(db: Session, make_user, grant, settings: Settings) -> None:  # type: ignore[no-untyped-def]
    viewer = make_user(email="viewer@districom.test", role="viewer")
    with pytest.raises(AppError) as error:
        upload(db, viewer, settings, biff7_xls(REPORT))
    assert error.value.status_code == 403
    grant("viewer", "commercial", "viewer")
    db.refresh(viewer)
    with pytest.raises(AppError) as error:
        upload(db, viewer, settings, biff7_xls(REPORT), import_type=TYPE_KEY)
    assert error.value.status_code == 403
    stranger = make_user(email="settlements@districom.test", role="viewer")
    grant("settlements", "settlements", "admin")
    db.refresh(stranger)
    with pytest.raises(AppError) as error:
        upload(db, stranger, settings, biff7_xls(REPORT), import_type=TYPE_KEY)
    assert (error.value.status_code, error.value.code) == (404, "import_type_not_found")


def test_no_row_values_in_logs_audit_or_issues(
    db: Session, operator: User, settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    job = upload(db, operator, settings, biff7_xls(REPORT), "ALMACEN DOÑA ROSA.xls")
    service.confirm(db, operator, settings, job.id)
    upload(db, operator, settings, biff7_xls(REPORT))  # duplicate
    upload(db, operator, settings, b"\x89PNG\x00", "x.csv")  # failed

    logged = "\n".join(f"{r.getMessage()} {json.dumps(r.__dict__, default=str)}" for r in caplog.records)
    audited = json.dumps([e.payload for e in db.scalars(select(AuditEvent)).all()], default=str)
    issues = json.dumps([i.details for i in db.scalars(select(DataQualityIssue)).all()], default=str)
    for value in (SECRET_NAME, "Caña Ñandú", "DOÑA"):
        assert value not in logged
        assert value not in audited
        assert value not in issues
