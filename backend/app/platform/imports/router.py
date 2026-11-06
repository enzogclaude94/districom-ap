"""Generic import API (ADR 014 §2.12, docs/IMPORTS.md §8). Permissions come from the module that owns each import
type: `viewer` reads, `operator` uploads, confirms and cancels, `admin` reprocesses. A user only ever sees the types
and jobs of the modules they can open."""

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Body, File, Form, Query, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.platform.auth.access import has_module_access
from app.platform.auth.deps import AppSettings, CurrentUser, DbSession
from app.platform.auth.models import User
from app.platform.imports import service
from app.platform.imports.models import ImportFile, ImportJob
from app.platform.imports.registry import ROW_STATUSES, Decision, Declared, registry
from app.platform.imports.schemas import (
    ConfirmRequest,
    ImportFileOut,
    ImportJobDetail,
    ImportJobItem,
    ImportJobPage,
    ImportMessageSummary,
    ImportRowDetailOut,
    ImportRowDetailPage,
    ImportRowOut,
    ImportRowPage,
    ImportTypeOut,
    QualityIssueOut,
    RowCounts,
)
from app.platform.quality.service import issues_of_job
from app.platform.sources.models import SourceSystem

router = APIRouter(prefix="/imports", tags=["imports"])


def _names(db: Session, ids: set[int | None]) -> dict[int, str]:
    wanted = {i for i in ids if i is not None}
    if not wanted:
        return {}
    return {row.id: row.full_name for row in db.execute(select(User.id, User.full_name).where(User.id.in_(wanted)))}


def _type_name(key: str | None) -> str | None:
    entry = registry.get(key) if key else None
    return entry.spec.name if entry else None


def _detail(db: Session, user: User, job: ImportJob) -> ImportJobDetail:
    db.refresh(job)
    file = db.get_one(ImportFile, job.file_id)
    names = _names(db, {job.created_by, job.confirmed_by})
    entry = registry.get(job.import_type) if job.import_type else None
    source = db.get(SourceSystem, job.source_system_id) if job.source_system_id else None
    return ImportJobDetail(
        id=job.id,
        import_type=job.import_type,
        import_type_name=_type_name(job.import_type),
        source_system=source.code if source else None,
        status=job.status,
        parser_version=job.parser_version,
        file=ImportFileOut(
            id=file.id,
            original_filename=file.original_filename,
            size_bytes=file.size_bytes,
            detected_format=file.detected_format,
            sha256=file.sha256,
            uploaded_at=file.uploaded_at,
        ),
        created_by_name=names.get(job.created_by, "—"),
        confirmed_by_name=names.get(job.confirmed_by) if job.confirmed_by else None,
        created_at=job.created_at,
        analyzed_at=job.analyzed_at,
        confirmed_at=job.confirmed_at,
        finished_at=job.finished_at,
        counts=RowCounts(
            detected=job.rows_detected,
            processed=job.rows_processed,
            valid=job.rows_valid,
            rejected=job.rows_rejected,
            duplicate=job.rows_duplicate,
            skipped=job.rows_skipped,
            pending_resolution=job.rows_pending_resolution,
            inserted=job.rows_inserted,
            updated=job.rows_updated,
            unchanged=job.rows_unchanged,
            superseded=job.rows_superseded,
            warnings=job.warning_count,
            errors=job.error_count,
        ),
        snapshot_date=job.snapshot_date,
        period_from=job.period_from,
        period_to=job.period_to,
        scope=job.scope,
        metadata=job.job_metadata or {},
        failure_reason=job.failure_reason,
        failure_detail=job.failure_detail,
        reprocess_of_job_id=job.reprocess_of_job_id,
        duplicate_of_job_id=job.duplicate_of_job_id,
        can_confirm=job.status == "pending"
        and entry is not None
        and has_module_access(user, entry.module_key, "operator"),
        can_decide=job.status == "pending" and entry is not None and has_module_access(user, entry.module_key, "admin"),
        can_reprocess=entry is not None
        and job.status in ("completed", "completed_with_warnings", "failed", "cancelled")
        and has_module_access(user, entry.module_key, "admin"),
    )


@router.get("/types", response_model=list[ImportTypeOut], summary="Tipos de importación disponibles")
def list_types(user: CurrentUser) -> list[ImportTypeOut]:
    return [
        ImportTypeOut(
            key=entry.spec.key,
            name=entry.spec.name,
            module_key=entry.module_key,
            source_system=entry.spec.source_system,
            can_import=has_module_access(user, entry.module_key, "operator"),
        )
        for entry in service.types_for(user, "viewer")
    ]


@router.post("", response_model=ImportJobDetail, status_code=201, summary="Subir y analizar un archivo")
def upload(
    db: DbSession,
    user: CurrentUser,
    settings: AppSettings,
    file: Annotated[UploadFile, File(description="Archivo .xls, .xlsx o .csv tal como sale del sistema")],
    import_type: Annotated[str | None, Form(max_length=80)] = None,
    snapshot_date: Annotated[
        date | None, Form(description="Fecha de la foto de un maestro; sin ella, la fecha de negocio de hoy")
    ] = None,
    scope: Annotated[
        Literal["FULL", "FILTERED"] | None, Form(description="Alcance declarado del export: sin filtros o filtrado")
    ] = None,
    scope_note: Annotated[str | None, Form(max_length=300, description="Filtros usados, si es FILTERED")] = None,
) -> ImportJobDetail:
    job = service.upload(
        db,
        user,
        settings,
        service.Upload(stream=file.file, filename=file.filename or "", content_type=file.content_type),
        import_type or None,
        Declared(snapshot_date, scope, scope_note),
    )
    return _detail(db, user, job)


@router.get("", response_model=ImportJobPage, summary="Importaciones")
def list_jobs(
    db: DbSession,
    user: CurrentUser,
    status: Annotated[str | None, Query(max_length=30)] = None,
    import_type: Annotated[str | None, Query(max_length=80)] = None,
    module: Annotated[
        str | None, Query(max_length=40, description="Solo los tipos de ese módulo y los archivos sin tipo reconocido")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ImportJobPage:
    query = select(ImportJob).where(service.visible_jobs_filter(user))
    if module:
        keys = [entry.spec.key for entry in registry.all() if entry.module_key == module]
        query = query.where(ImportJob.import_type.in_(keys) | ImportJob.import_type.is_(None))
    if status:
        query = query.where(ImportJob.status == status)
    if import_type:
        query = query.where(ImportJob.import_type == import_type)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    jobs = db.scalars(query.order_by(ImportJob.id.desc()).offset(offset).limit(limit)).all()
    files = {f.id: f for f in db.scalars(select(ImportFile).where(ImportFile.id.in_({j.file_id for j in jobs})))}
    names = _names(db, {j.created_by for j in jobs})
    return ImportJobPage(
        items=[
            ImportJobItem(
                id=j.id,
                import_type=j.import_type,
                import_type_name=_type_name(j.import_type),
                status=j.status,
                original_filename=files[j.file_id].original_filename,
                created_by_name=names.get(j.created_by, "—"),
                created_at=j.created_at,
                finished_at=j.finished_at,
                rows_detected=j.rows_detected,
                rows_processed=j.rows_processed,
                rows_rejected=j.rows_rejected,
                failure_reason=j.failure_reason,
            )
            for j in jobs
        ],
        total=total,
    )


@router.get("/{job_id}", response_model=ImportJobDetail, summary="Importación con su resumen (preview)")
def get_job(job_id: int, db: DbSession, user: CurrentUser) -> ImportJobDetail:
    return _detail(db, user, service.get_job(db, user, job_id))


def _sees_raw_rows(user: User, job: ImportJob) -> bool:
    """Least privilege on the file's values: a module `viewer` gets the rows' state and messages; the raw cells and
    normalized values go only to an `operator` or `admin` of the type's module. A job whose type was not detected is
    visible only to its uploader and platform admins (`service.can_view`), who already hold the file."""
    if job.import_type is None:
        return True
    entry = registry.get(job.import_type)
    return entry is not None and has_module_access(user, entry.module_key, "operator")


@router.get(
    "/{job_id}/rows",
    response_model=ImportRowDetailPage | ImportRowPage,
    summary="Filas del archivo en staging",
    description="Un `viewer` recibe estado, clave y mensajes de cada fila; `raw` y `normalized` solo operator o admin.",
)
def list_rows(
    job_id: int,
    db: DbSession,
    user: CurrentUser,
    status: Annotated[str | None, Query(pattern="^(" + "|".join(ROW_STATUSES) + ")$")] = None,
    code: Annotated[str | None, Query(max_length=80, description="Solo filas con este código de mensaje")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ImportRowDetailPage | ImportRowPage:
    job = service.get_job(db, user, job_id)
    rows, total = service.staged_rows(db, job_id, status, offset, limit, code)
    if _sees_raw_rows(user, job):
        return ImportRowDetailPage(
            items=[
                ImportRowDetailOut(
                    id=r.id,
                    sheet=r.sheet,
                    row_number=r.row_number,
                    status=r.status,
                    outcome=r.outcome,
                    raw=r.raw,
                    normalized=r.normalized,
                    business_key=r.business_key,
                    entity_type=r.entity_type,
                    entity_id=r.entity_id,
                    messages=r.messages,
                )
                for r in rows
            ],
            total=total,
        )
    return ImportRowPage(
        items=[
            ImportRowOut(
                id=r.id,
                sheet=r.sheet,
                row_number=r.row_number,
                status=r.status,
                outcome=r.outcome,
                business_key=r.business_key,
                entity_type=r.entity_type,
                entity_id=r.entity_id,
                messages=r.messages,
            )
            for r in rows
        ],
        total=total,
    )


@router.get("/{job_id}/messages", response_model=list[ImportMessageSummary], summary="Mensajes del análisis, por tipo")
def list_messages(job_id: int, db: DbSession, user: CurrentUser) -> list[ImportMessageSummary]:
    service.get_job(db, user, job_id)
    return [
        ImportMessageSummary(code=m.code, severity=m.severity, field=m.field, message=m.message, rows=m.rows)
        for m in service.message_summary(db, job_id)
    ]


@router.get("/{job_id}/issues", response_model=list[QualityIssueOut], summary="Problemas de calidad de la importación")
def list_issues(job_id: int, db: DbSession, user: CurrentUser) -> list[QualityIssueOut]:
    service.get_job(db, user, job_id)
    return [
        QualityIssueOut(
            id=i.id,
            code=i.code,
            severity=i.severity,
            status=i.status,
            entity_type=i.entity_type,
            entity_id=i.entity_id,
            row_id=i.row_id,
            details=i.details,
            occurrences=i.occurrences,
            first_seen_at=i.first_seen_at,
            last_seen_at=i.last_seen_at,
        )
        for i in issues_of_job(db, job_id)
    ]


@router.post("/{job_id}/confirm", response_model=ImportJobDetail, summary="Confirmar y escribir la importación")
def confirm(
    job_id: int,
    db: DbSession,
    user: CurrentUser,
    settings: AppSettings,
    body: Annotated[ConfirmRequest | None, Body()] = None,
) -> ImportJobDetail:
    decision = Decision(body.decision, body.reason or "") if body is not None and body.decision else None
    return _detail(db, user, service.confirm(db, user, settings, job_id, decision))


@router.post("/{job_id}/cancel", response_model=ImportJobDetail, summary="Descartar el análisis")
def cancel(job_id: int, db: DbSession, user: CurrentUser) -> ImportJobDetail:
    return _detail(db, user, service.cancel(db, user, job_id))


@router.post("/{job_id}/reprocess", response_model=ImportJobDetail, status_code=201, summary="Reprocesar el archivo")
def reprocess(job_id: int, db: DbSession, user: CurrentUser, settings: AppSettings) -> ImportJobDetail:
    return _detail(db, user, service.reprocess(db, user, settings, job_id))
