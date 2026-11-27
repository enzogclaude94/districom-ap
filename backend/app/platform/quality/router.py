"""Calidad API (F1.6, docs/IMPORTS.md §7.1): the queue of data quality issues of a module.

Permissions come from the module that owns each issue: `viewer` reads, `operator` ignores with a note, `admin` also
resolves by hand with a note. An issue of a module the user cannot read is not discoverable (404).
"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query

from app.core.errors import AppError
from app.platform.auth.access import has_module_access
from app.platform.auth.deps import CurrentUser, DbSession
from app.platform.auth.models import User
from app.platform.modules import installed_modules
from app.platform.quality import review
from app.platform.quality.models import DataQualityIssue
from app.platform.quality.registry import QualityCodeSpec, registry
from app.platform.quality.schemas import (
    CountedOut,
    IssueDecisionIn,
    IssueDetailOut,
    IssueOut,
    IssuePage,
    IssueSourceOut,
    IssueSummaryOut,
    QualityCodeOut,
    QualityGroupOut,
)

router = APIRouter(prefix="/quality", tags=["quality"])

ModuleParam = Annotated[str, Query(max_length=40, description="Módulo dueño de los problemas, p. ej. commercial")]


def _module(user: User, module: str, role: str = "viewer") -> str:
    if module not in {m.key for m in installed_modules()}:
        raise AppError(422, "invalid_module", "Ese módulo no existe.")
    if not has_module_access(user, module, role):
        raise AppError(403, "forbidden", "No tenés permisos para esta acción.")
    return module


def _codes_of(module: str) -> list[QualityCodeSpec]:
    return [s for s in registry.all() if s.code.startswith(f"{module}.") or s.code.startswith("platform.")]


def _group(spec: QualityCodeSpec | None) -> QualityGroupOut:
    return QualityGroupOut(key=spec.group.key, label=spec.group.label) if spec else QualityGroupOut(key="", label="")


def _item(view: review.IssueView) -> IssueOut:
    issue, spec = view.issue, view.spec
    return IssueOut(
        id=issue.id,
        code=issue.code,
        label=spec.label if spec else issue.code,
        severity=issue.severity,
        status=issue.status,
        group=_group(spec),
        subject=view.subject,
        occurrences=issue.occurrences,
        first_seen_at=issue.first_seen_at,
        last_seen_at=issue.last_seen_at,
        source=IssueSourceOut(**vars(view.source)),
        mapping_id=view.mapping_id,
        fix=spec.fix if spec else None,
        fix_href=spec.fix_href(issue.entity_id, issue.dedupe_key) if spec else None,
        action=spec.action if spec else "",
        resolved_at=issue.resolved_at,
        resolved_by_name=view.resolved_by_name,
    )


def _detail(view: review.IssueView, *, technical: bool) -> IssueDetailOut:
    issue, spec = view.issue, view.spec
    return IssueDetailOut(
        **_item(view).model_dump(),
        description=spec.description if spec else "",
        closes_when=spec.closes_when if spec else "",
        facts=view.facts,
        resolution_note=issue.resolution_note,
        resolved_automatically=issue.status == "resolved" and not view.by_hand,
        dedupe_key=issue.dedupe_key if technical else None,
        entity_type=issue.entity_type if technical else None,
        entity_id=issue.entity_id if technical else None,
    )


def _readable(db: DbSession, user: User, issue_id: int) -> tuple[DataQualityIssue, str]:
    issue = review.get(db, issue_id)
    module = review.module_of(db, issue)
    if module is None or not has_module_access(user, module, "viewer"):
        raise AppError(404, "issue_not_found", "Ese problema no existe.")
    return issue, module


@router.get("/codes", response_model=list[QualityCodeOut], summary="Tipos de problema de un módulo")
def list_codes(user: CurrentUser, module: ModuleParam) -> list[QualityCodeOut]:
    _module(user, module)
    return [
        QualityCodeOut(
            code=s.code,
            label=s.label,
            severity=s.severity,
            group=_group(s),
            description=s.description,
            action=s.action,
            closes_when=s.closes_when,
            fix=s.fix,
        )
        for s in sorted(_codes_of(module), key=lambda s: (s.group.order, s.label))
    ]


def _filters(
    status: str | None,
    severity: str | None,
    code: str | None,
    group: str | None,
    job_id: int | None,
    seen_from: date | None,
    seen_to: date | None,
    q: str | None,
) -> review.IssueFilters:
    return review.IssueFilters(status, severity, code, group, job_id, seen_from, seen_to, (q or "").strip() or None)


Status = Annotated[str | None, Query(max_length=10)]
Severity = Annotated[str | None, Query(max_length=10)]
Code = Annotated[str | None, Query(max_length=80)]
Group = Annotated[str | None, Query(max_length=40)]
JobId = Annotated[int | None, Query(ge=1)]
Day = Annotated[date | None, Query()]
Words = Annotated[
    str | None, Query(max_length=100, description="Palabras del tipo de problema (no busca en los datos)")
]


@router.get("/issues", response_model=IssuePage, summary="Problemas de calidad de un módulo")
def list_issues(
    db: DbSession,
    user: CurrentUser,
    module: ModuleParam,
    status: Status = None,
    severity: Severity = None,
    code: Code = None,
    group: Group = None,
    job_id: JobId = None,
    seen_from: Day = None,
    seen_to: Day = None,
    q: Words = None,
    sort: Annotated[str, Query(max_length=10)] = "severity",
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> IssuePage:
    _module(user, module)
    filters = _filters(status, severity, code, group, job_id, seen_from, seen_to, q)
    rows, total = review.search(db, module, filters, sort=sort, limit=limit, offset=offset)
    return IssuePage(items=[_item(v) for v in review.views(db, rows)], total=total, limit=limit, offset=offset)


@router.get("/issues/summary", response_model=IssueSummaryOut, summary="Resumen: problemas y ocurrencias")
def issues_summary(
    db: DbSession,
    user: CurrentUser,
    module: ModuleParam,
    code: Code = None,
    group: Group = None,
    job_id: JobId = None,
    seen_from: Day = None,
    seen_to: Day = None,
    q: Words = None,
) -> IssueSummaryOut:
    _module(user, module)
    counted = review.summary(db, module, _filters(None, None, code, group, job_id, seen_from, seen_to, q))

    def out(c: review.Counted) -> CountedOut:
        return CountedOut(issues=c.issues, occurrences=c.occurrences)

    return IssueSummaryOut(
        open={severity: out(c) for severity, c in counted.open.items()},
        open_total=out(counted.open_total),
        resolved=out(counted.resolved),
        ignored=out(counted.ignored),
    )


@router.get("/issues/{issue_id}", response_model=IssueDetailOut, summary="Detalle de un problema")
def get_issue(issue_id: int, db: DbSession, user: CurrentUser) -> IssueDetailOut:
    issue, module = _readable(db, user, issue_id)
    [view] = review.views(db, [issue])
    return _detail(view, technical=has_module_access(user, module, "operator"))


def _decide(db: DbSession, user: User, issue_id: int, status: str, note: str, role: str) -> IssueDetailOut:
    issue, module = _readable(db, user, issue_id)
    if not has_module_access(user, module, role):
        raise AppError(403, "forbidden", "No tenés permisos para esta acción.")
    decided = review.decide(db, user, issue.id, status, note=note)
    db.commit()
    [view] = review.views(db, [decided])
    return _detail(view, technical=True)


@router.post("/issues/{issue_id}/ignore", response_model=IssueDetailOut, summary="Ignorar un problema (con nota)")
def ignore_issue(issue_id: int, body: IssueDecisionIn, db: DbSession, user: CurrentUser) -> IssueDetailOut:
    return _decide(db, user, issue_id, "ignored", body.note, "operator")


@router.post(
    "/issues/{issue_id}/resolve", response_model=IssueDetailOut, summary="Marcar resuelto a mano (admin, con nota)"
)
def resolve_issue(issue_id: int, body: IssueDecisionIn, db: DbSession, user: CurrentUser) -> IssueDetailOut:
    return _decide(db, user, issue_id, "resolved", body.note, "admin")
