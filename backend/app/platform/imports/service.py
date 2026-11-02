"""The import pipeline (ADR 014 §2.2, docs/IMPORTS.md §2): upload → detect → analyze → preview → confirm.

Transactions, in order:

1. **Upload:** the original is stored under UPLOAD_DIR and `import_files` + `import_jobs` (`processing`) are committed
   before anything is read, so every attempt is recorded.
2. **Detect:** format by signature, the reader, then the type by header signature; an identical file already imported
   as that type ends the job as `duplicate`.
3. **Analyze:** the adapter runs in a **read-only transaction** (it may look data up, PostgreSQL refuses any write);
   its rows are then staged in `import_rows` and the job becomes `pending` with its counters and preview.
4. **Confirm:** the job is claimed (`pending` → `processing`, committed); then one transaction takes the advisory lock
   of the import type, checks for a duplicate again under the lock, lets the adapter write, records outcomes, issues
   and the audit event, and commits. Any error rolls that transaction back entirely and the job ends `failed`.

Nothing here logs or audits row values: only ids, counters, codes and the import type.
"""

import hashlib
import json
import logging
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, BinaryIO

from sqlalchemy import func, insert, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError
from app.platform.audit.service import record_event
from app.platform.auth.access import has_module_access
from app.platform.auth.models import User
from app.platform.imports.formats import UnsupportedFormatError, detect_format, extension_format
from app.platform.imports.models import COMPLETED_STATUSES, ImportFile, ImportJob, ImportRow
from app.platform.imports.readers import read_workbook
from app.platform.imports.registry import (
    DETECTION_THRESHOLD,
    ROW_OUTCOMES,
    AnalyzeContext,
    ConfirmContext,
    RegisteredType,
    StagedRow,
    StructuralError,
    registry,
)
from app.platform.imports.storage import FileTooLargeError, display_filename, store_upload, stored_path
from app.platform.imports.workbook import Workbook, encode_cells
from app.platform.quality.service import record_issue
from app.platform.sources.models import SourceSystem

logger = logging.getLogger("app.platform.imports")

# First key of every import advisory lock (pg_advisory_xact_lock(int, int)): "IMPT". The second is the type's hash.
LOCK_NAMESPACE = 0x494D5054
# A confirmation waits this long for another one of the same type before giving up (nothing written, job pending).
LOCK_WAIT_MS = 60_000
STAGE_BATCH = 1000
# Rows the adapter may write: rejected, skipped and duplicate rows never reach it.
WRITABLE_STATUSES = ("valid", "warning", "pending_resolution")
# A `processing` job untouched this long, whose type's lock is free, was interrupted (`imports recover`).
RECOVER_AFTER = timedelta(minutes=30)


@dataclass(frozen=True)
class Upload:
    stream: BinaryIO
    filename: str
    content_type: str | None


# --- permissions ------------------------------------------------------------------------------------------------


def types_for(user: User, min_role: str) -> list[RegisteredType]:
    return [entry for entry in registry.all() if has_module_access(user, entry.module_key, min_role)]


def _require_type(user: User, import_type: str, min_role: str) -> RegisteredType:
    entry = registry.get(import_type)
    if entry is None or not has_module_access(user, entry.module_key, "viewer"):
        raise AppError(404, "import_type_not_found", "Ese tipo de importación no existe.")
    if not has_module_access(user, entry.module_key, min_role):
        raise AppError(403, "forbidden", "No tenés permisos para esta acción.")
    return entry


def can_view(user: User, job: ImportJob) -> bool:
    if job.import_type is None:  # detection failed: the uploader and platform admins
        return job.created_by == user.id or user.role == "admin"
    entry = registry.get(job.import_type)
    return entry is not None and has_module_access(user, entry.module_key, "viewer")


def get_job(db: Session, user: User, job_id: int) -> ImportJob:
    job = db.get(ImportJob, job_id)
    if job is None or not can_view(user, job):
        raise AppError(404, "import_job_not_found", "Esa importación no existe.")
    return job


def visible_jobs_filter(user: User) -> Any:
    keys = [entry.spec.key for entry in types_for(user, "viewer")]
    own_untyped = ImportJob.import_type.is_(None) & (
        ImportJob.created_by == user.id if user.role != "admin" else ImportJob.id.is_not(None)
    )
    return ImportJob.import_type.in_(keys) | own_untyped


# --- upload, detect, analyze ------------------------------------------------------------------------------------


def upload(db: Session, user: User, settings: Settings, upload: Upload, import_type: str | None) -> ImportJob:
    """Store the file and run detection and analysis. Returns the job: `pending` with its preview, or already
    `failed` / `duplicate`. Raises only for what is no attempt at all (permissions, extension, size)."""
    candidates = [_require_type(user, import_type, "operator")] if import_type else types_for(user, "operator")
    if not candidates:
        raise AppError(403, "forbidden", "No tenés permisos para importar archivos.")
    filename = display_filename(upload.filename)
    if extension_format(filename) is None:
        raise AppError(
            422, "invalid_extension", "Solo se pueden importar archivos .xls, .xlsx o .csv.", {"filename": filename}
        )
    try:
        stored = store_upload(upload.stream, settings.upload_dir, settings.upload_max_size_mb * 1024 * 1024)
    except FileTooLargeError as exc:
        raise AppError(
            413, "request_too_large", f"El archivo supera el máximo de {settings.upload_max_size_mb} MB."
        ) from exc
    file = ImportFile(
        original_filename=filename,
        storage_key=stored.storage_key,
        size_bytes=stored.size_bytes,
        content_type=(upload.content_type or "")[:120] or None,
        sha256=stored.sha256,
        uploaded_by=user.id,
    )
    db.add(file)
    db.flush()
    job = ImportJob(status="processing", file_id=file.id, created_by=user.id, started_at=func.now())
    db.add(job)
    db.flush()
    record_event(
        db,
        "platform.import.uploaded",
        user_id=user.id,
        entity_type="import_job",
        entity_id=str(job.id),
        payload={"file_id": file.id, "size_bytes": stored.size_bytes, "requested_type": import_type},
    )
    db.commit()
    return _detect_and_analyze(db, settings, job.id, candidates, forced=import_type is not None, check_duplicate=True)


def reprocess(db: Session, user: User, settings: Settings, job_id: int) -> ImportJob:
    """A new job over the same stored file, read again with the current reader and adapter (docs/IMPORTS.md §7):
    admin of the type's module only; the duplicate check does not apply, the original job stays as it is."""
    original = get_job(db, user, job_id)
    if original.import_type is None:
        raise AppError(409, "import_job_without_type", "Esa importación no tiene un tipo para reprocesar.")
    entry = _require_type(user, original.import_type, "admin")
    if original.status not in (*COMPLETED_STATUSES, "failed", "cancelled"):
        raise AppError(409, "import_job_not_final", "Solo se reprocesa una importación terminada.")
    job = ImportJob(
        status="processing",
        file_id=original.file_id,
        created_by=user.id,
        started_at=func.now(),
        reprocess_of_job_id=original.id,
    )
    db.add(job)
    db.flush()
    record_event(
        db,
        "platform.import.reprocessed",
        user_id=user.id,
        entity_type="import_job",
        entity_id=str(job.id),
        payload={"reprocess_of_job_id": original.id, "import_type": original.import_type},
    )
    db.commit()
    return _detect_and_analyze(db, settings, job.id, [entry], forced=True, check_duplicate=False)


def _detect_and_analyze(
    db: Session,
    settings: Settings,
    job_id: int,
    candidates: list[RegisteredType],
    *,
    forced: bool,
    check_duplicate: bool,
) -> ImportJob:
    job = db.get_one(ImportJob, job_id)
    file = db.get_one(ImportFile, job.file_id)
    path = stored_path(settings.upload_dir, file.storage_key)
    try:
        detected = detect_format(path, file.original_filename)
        file.detected_format = detected.format
        workbook = read_workbook(path, detected.format)
    except UnsupportedFormatError as exc:
        return _fail(db, job, exc.reason, exc.message)
    metadata: dict[str, Any] = {"reader": dict(workbook.metadata), "extension_mismatch": detected.extension_mismatch}

    scores = {entry.spec.key: _score(entry, workbook) for entry in candidates}
    metadata["detection"] = {key: round(score, 3) for key, score in scores.items()}
    matching = [entry for entry in candidates if scores[entry.spec.key] >= DETECTION_THRESHOLD]
    if forced and not matching:
        job.job_metadata = metadata
        return _fail(db, job, "header_mismatch", "El archivo no tiene los encabezados de ese tipo de importación.")
    if not matching:
        job.job_metadata = metadata
        return _fail(db, job, "type_not_detected", "No se reconoce el reporte. Elegí el tipo de importación.")
    if len(matching) > 1:
        metadata["candidates"] = [entry.spec.key for entry in matching]
        job.job_metadata = metadata
        return _fail(
            db, job, "ambiguous_type", "El archivo coincide con más de un reporte. Elegí el tipo de importación."
        )
    entry = matching[0]
    job.import_type = entry.spec.key
    job.source_system_id = _source_system_id(db, entry.spec.source_system)
    job.parser_version = entry.spec.adapter.version
    job.job_metadata = metadata
    db.commit()

    if check_duplicate and (original := _completed_twin(db, job, file.sha256)) is not None:
        return _mark_duplicate(db, job, original, file.sha256)
    return _analyze(db, job, entry, workbook)


def _score(entry: RegisteredType, workbook: Workbook) -> float:
    try:
        return float(entry.spec.adapter.score(workbook))
    except Exception:  # an adapter that cannot even score the file does not match it
        logger.exception("import_score_failed", extra={"import_type": entry.spec.key})
        return 0.0


def _analyze(db: Session, job: ImportJob, entry: RegisteredType, workbook: Workbook) -> ImportJob:
    job_id = job.id
    context = AnalyzeContext(job_id=job_id, import_type=entry.spec.key, source_system_id=job.source_system_id or 0)
    db.commit()
    try:
        db.execute(text("SET TRANSACTION READ ONLY"))
        analysis = entry.spec.adapter.analyze(db, workbook, context)
    except StructuralError as exc:
        db.rollback()
        return _fail(db, db.get_one(ImportJob, job_id), exc.reason, exc.message, details=exc.details)
    except Exception:
        db.rollback()
        logger.exception("import_analyze_failed", extra={"import_job_id": job_id, "import_type": entry.spec.key})
        return _fail(db, db.get_one(ImportJob, job_id), "unexpected_error", "Error inesperado al analizar el archivo.")
    db.rollback()  # ends the read-only transaction; the adapter could not have written anything

    job = db.get_one(ImportJob, job_id)
    statuses: Counter[str] = Counter()
    severities: Counter[str] = Counter()
    batch: list[dict[str, Any]] = []
    for row in analysis.rows:
        statuses[row.status] += 1
        severities.update(message.severity for message in row.messages)
        raw = encode_cells(row.row.cells)
        batch.append(
            {
                "job_id": job_id,
                "sheet": row.sheet.name,
                "row_number": row.row.number,
                "raw": raw,
                "normalized": row.normalized,
                "business_key": row.business_key,
                "row_hash": hashlib.sha256(json.dumps(raw, ensure_ascii=False).encode()).digest(),
                "status": row.status,
                "entity_type": row.entity_type,
                "messages": [
                    {"code": m.code, "severity": m.severity, "text": m.text, "column": m.column} for m in row.messages
                ],
            }
        )
        if len(batch) >= STAGE_BATCH:
            db.execute(insert(ImportRow), batch)
            batch = []
    if batch:
        db.execute(insert(ImportRow), batch)

    total = sum(statuses.values())
    job.rows_detected = total
    job.rows_processed = total - statuses["skipped"]
    job.rows_valid = statuses["valid"] + statuses["warning"]
    job.rows_rejected = statuses["rejected"]
    job.rows_duplicate = statuses["duplicate"]
    job.rows_skipped = statuses["skipped"]
    job.rows_pending_resolution = statuses["pending_resolution"]
    job.warning_count = severities["warning"]
    job.error_count = severities["error"]
    job.snapshot_date = analysis.snapshot_date
    job.period_from = analysis.period_from
    job.period_to = analysis.period_to
    job.scope = analysis.scope
    job.job_metadata = {**job.job_metadata, **analysis.metadata, "preview": analysis.preview}
    job.status = "pending"
    job.analyzed_at = func.now()
    record_event(
        db,
        "platform.import.analyzed",
        user_id=job.created_by,
        entity_type="import_job",
        entity_id=str(job_id),
        payload={"import_type": job.import_type, "rows": dict(statuses)},
    )
    db.commit()
    logger.info("import_analyzed", extra={"import_job_id": job_id, "import_type": job.import_type, "rows": total})
    return job


def _completed_twin(db: Session, job: ImportJob, sha256: str) -> ImportJob | None:
    """A completed job of the same type over a file with the same content; the file name never matters."""
    return db.scalars(
        select(ImportJob)
        .join(ImportFile, ImportFile.id == ImportJob.file_id)
        .where(
            ImportJob.import_type == job.import_type,
            ImportJob.status.in_(COMPLETED_STATUSES),
            ImportFile.sha256 == sha256,
            ImportJob.id != job.id,
        )
        .order_by(ImportJob.id)
        .limit(1)
    ).first()


def _mark_duplicate(db: Session, job: ImportJob, original: ImportJob, sha256: str) -> ImportJob:
    job.status = "duplicate"
    job.duplicate_of_job_id = original.id
    job.finished_at = func.now()
    record_issue(
        db,
        "platform.import.duplicate_file",
        f"{job.import_type}:{sha256}",
        job_id=job.id,
        details={"duplicate_of_job_id": original.id, "import_type": job.import_type},
    )
    record_event(
        db,
        "platform.import.duplicate",
        user_id=job.created_by,
        entity_type="import_job",
        entity_id=str(job.id),
        payload={"import_type": job.import_type, "duplicate_of_job_id": original.id},
    )
    db.commit()
    logger.info("import_duplicate", extra={"import_job_id": job.id, "duplicate_of_job_id": original.id})
    return job


def _fail(db: Session, job: ImportJob, reason: str, message: str, details: dict[str, Any] | None = None) -> ImportJob:
    job.status = "failed"
    job.failure_reason = reason
    job.failure_detail = message
    job.finished_at = func.now()
    if details:
        job.job_metadata = {**(job.job_metadata or {}), "failure": details}
    if reason == "missing_columns" and job.import_type is not None:
        record_issue(
            db,
            "platform.import.missing_columns",
            f"{job.import_type}:job:{job.id}",
            job_id=job.id,
            details=details or {},
        )
    record_event(
        db,
        "platform.import.failed",
        user_id=job.created_by,
        entity_type="import_job",
        entity_id=str(job.id),
        payload={"import_type": job.import_type, "failure_reason": reason},
    )
    db.commit()
    logger.info("import_failed", extra={"import_job_id": job.id, "failure_reason": reason})
    return job


def _source_system_id(db: Session, code: str) -> int:
    source_id = db.scalar(select(SourceSystem.id).where(SourceSystem.code == code))
    if source_id is None:
        raise ValueError(f"Unknown source system: {code}")
    return source_id


# --- confirm, cancel, recover -----------------------------------------------------------------------------------


def confirm(db: Session, user: User, settings: Settings, job_id: int) -> ImportJob:
    job = _claim(db, user, job_id)
    entry = registry.get(job.import_type or "")
    assert entry is not None  # _claim checked it
    file_sha = db.scalar(select(ImportFile.sha256).where(ImportFile.id == job.file_id)) or ""
    try:
        _lock_type(db, settings, entry.spec.key)
    except DBAPIError as exc:
        db.rollback()
        _release(db, job_id)
        if getattr(exc.orig, "sqlstate", None) in ("55P03", "57014"):
            raise AppError(
                409,
                "import_in_progress",
                "Hay otra importación del mismo tipo confirmándose. Probá de nuevo en un rato.",
            ) from exc
        raise
    job = db.get_one(ImportJob, job_id)
    # A reprocess is the explicit way to apply an already imported file again (docs/IMPORTS.md §7).
    if job.reprocess_of_job_id is None and (original := _completed_twin(db, job, file_sha)) is not None:
        return _mark_duplicate(db, job, original, file_sha)
    try:
        _write(db, user, job, entry)
    except StructuralError as exc:
        db.rollback()
        return _fail(db, db.get_one(ImportJob, job_id), exc.reason, exc.message, details=exc.details)
    except Exception:
        db.rollback()
        logger.exception("import_confirm_failed", extra={"import_job_id": job_id, "import_type": entry.spec.key})
        return _fail(
            db, db.get_one(ImportJob, job_id), "unexpected_error", "Error inesperado al confirmar; no se escribió nada."
        )
    return db.get_one(ImportJob, job_id)


def _claim(db: Session, user: User, job_id: int) -> ImportJob:
    job = db.scalars(select(ImportJob).where(ImportJob.id == job_id).with_for_update()).first()
    if job is None or not can_view(user, job):
        raise AppError(404, "import_job_not_found", "Esa importación no existe.")
    _require_type(user, job.import_type or "", "operator")
    if job.status != "pending":
        raise AppError(409, "import_job_not_pending", "Esa importación no espera confirmación.", {"status": job.status})
    job.status = "processing"
    job.confirmed_by = user.id
    job.confirmed_at = func.now()
    db.commit()
    return job


def _release(db: Session, job_id: int) -> None:
    """Back to `pending` after the lock could not be taken: nothing was written, the operator can confirm again."""
    db.execute(
        update(ImportJob).where(ImportJob.id == job_id).values(status="pending", confirmed_by=None, confirmed_at=None)
    )
    db.commit()


def _lock_type(db: Session, settings: Settings, import_type: str) -> None:
    """Serialize confirmations of one import type for the rest of this transaction. The wait gets its own, longer
    limits; the backend's usual ones apply again once the lock is held."""
    db.execute(text(f"SET LOCAL lock_timeout = {LOCK_WAIT_MS}"))
    db.execute(text(f"SET LOCAL statement_timeout = {LOCK_WAIT_MS + 5_000}"))
    db.execute(text("SELECT pg_advisory_xact_lock(:ns, hashtext(:key))"), {"ns": LOCK_NAMESPACE, "key": import_type})
    db.execute(text(f"SET LOCAL lock_timeout = {int(settings.db_lock_timeout_ms)}"))
    db.execute(text(f"SET LOCAL statement_timeout = {int(settings.db_statement_timeout_ms)}"))


def _write(db: Session, user: User, job: ImportJob, entry: RegisteredType) -> None:
    rows = db.scalars(
        select(ImportRow)
        .where(ImportRow.job_id == job.id, ImportRow.status.in_(WRITABLE_STATUSES))
        .order_by(ImportRow.id)
    ).all()
    staged = [
        StagedRow(r.id, r.sheet, r.row_number, r.status, r.normalized, r.business_key, r.entity_type) for r in rows
    ]
    context = ConfirmContext(job.id, entry.spec.key, job.source_system_id or 0, user.id)
    outcomes = entry.spec.adapter.write(db, context, staged)

    ids = {row.id for row in staged}
    counts: Counter[str] = Counter()
    seen: set[int] = set()
    for outcome in outcomes:
        if outcome.row_id not in ids or outcome.row_id in seen or outcome.outcome not in ROW_OUTCOMES:
            raise RuntimeError(f"Adapter {entry.spec.key} returned an invalid outcome")
        seen.add(outcome.row_id)
        counts[outcome.outcome] += 1
        values: dict[str, Any] = {"outcome": outcome.outcome}
        if outcome.entity_type is not None:
            values["entity_type"] = outcome.entity_type
        if outcome.entity_id is not None:
            values["entity_id"] = outcome.entity_id
        db.execute(update(ImportRow).where(ImportRow.id == outcome.row_id).values(**values))

    _record_row_issues(
        db,
        job,
        db.scalars(
            select(ImportRow).where(ImportRow.job_id == job.id, ImportRow.status == "rejected").order_by(ImportRow.id)
        ),
    )
    job.rows_inserted = counts["inserted"]
    job.rows_updated = counts["updated"]
    job.rows_unchanged = counts["unchanged"]
    job.rows_superseded = counts["superseded"]
    clean = not (job.rows_rejected or job.rows_duplicate or job.rows_pending_resolution or job.warning_count)
    job.status = "completed" if clean else "completed_with_warnings"
    job.finished_at = func.now()
    record_event(
        db,
        "platform.import.confirmed",
        user_id=user.id,
        entity_type="import_job",
        entity_id=str(job.id),
        payload={"import_type": job.import_type, "status": job.status, "outcomes": dict(counts)},
    )
    db.commit()
    logger.info("import_confirmed", extra={"import_job_id": job.id, "import_type": job.import_type, **counts})


def _record_row_issues(db: Session, job: ImportJob, rejected: Iterable[ImportRow]) -> None:
    for row in rejected:
        codes = sorted({m["code"] for m in row.messages if m.get("severity") == "error"})
        record_issue(
            db,
            "platform.import.invalid_row",
            f"job:{job.id}:{row.sheet}:{row.row_number}",
            job_id=job.id,
            row_id=row.id,
            details={"import_type": job.import_type, "row_number": row.row_number, "codes": codes},
        )


def cancel(db: Session, user: User, job_id: int) -> ImportJob:
    job = db.scalars(select(ImportJob).where(ImportJob.id == job_id).with_for_update()).first()
    if job is None or not can_view(user, job):
        raise AppError(404, "import_job_not_found", "Esa importación no existe.")
    _require_type(user, job.import_type or "", "operator")
    if job.status != "pending":
        raise AppError(409, "import_job_not_pending", "Esa importación no espera confirmación.", {"status": job.status})
    job.status = "cancelled"
    job.finished_at = func.now()
    record_event(
        db,
        "platform.import.cancelled",
        user_id=user.id,
        entity_type="import_job",
        entity_id=str(job.id),
        payload={"import_type": job.import_type},
    )
    db.commit()
    return job


def recover(db: Session, older_than: timedelta = RECOVER_AFTER) -> list[int]:
    """`processing` jobs left by a process that died (docs/IMPORTS.md §7) → `failed` (`interrupted`). A job is only
    touched when nothing holds its type's lock, so a confirmation that is still running is never failed; since it
    was one transaction, an interrupted one left no data behind."""
    recovered: list[int] = []
    stale = db.scalars(
        select(ImportJob.id).where(ImportJob.status == "processing", ImportJob.updated_at < func.now() - older_than)
    ).all()
    db.rollback()
    for job_id in stale:
        job = db.scalars(select(ImportJob).where(ImportJob.id == job_id).with_for_update(skip_locked=True)).first()
        if job is None or job.status != "processing":
            db.rollback()
            continue
        if job.import_type is not None:
            free = db.scalar(
                text("SELECT pg_try_advisory_xact_lock(:ns, hashtext(:key))"),
                {"ns": LOCK_NAMESPACE, "key": job.import_type},
            )
            if not free:
                db.rollback()
                continue
        _fail(db, job, "interrupted", "La importación se interrumpió antes de terminar; no se escribió nada.")
        recovered.append(job_id)
    return recovered


# --- reading ----------------------------------------------------------------------------------------------------


def stored_file_path(settings: Settings, file: ImportFile) -> Path:
    return stored_path(settings.upload_dir, file.storage_key)


def staged_rows(
    db: Session, job_id: int, status: str | None, offset: int, limit: int
) -> tuple[Sequence[ImportRow], int]:
    query = select(ImportRow).where(ImportRow.job_id == job_id)
    if status is not None:
        query = query.where(ImportRow.status == status)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(query.order_by(ImportRow.sheet, ImportRow.row_number).offset(offset).limit(limit)).all()
    return rows, total
