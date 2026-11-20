"""The Calidad queue over `data_quality_issues` (F1.6, docs/IMPORTS.md §7.1): search, summary, detail and the two
manual decisions. One implementation for every module; the table stays the only source of truth.

An issue belongs to the module of its code (`commercial.*`); a platform code (`platform.import.*`) belongs to the
module of its import's type. Filters work on safe metadata only (code, severity, status, group, job, dates): no
name, address or code of a customer goes in a query string.

Manual decisions only change the issue: `resolved` (an admin says the condition is gone) or `ignored` (it needs no
action). Neither touches business data, both need a note, both are audited without the note, and a later occurrence
of the same condition opens a new issue (`record_issue` only reuses an open one): nothing is suppressed for good.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, cast

from sqlalchemy import ColumnElement, Select, and_, case, false, func, or_, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.platform.audit.models import AuditEvent
from app.platform.audit.service import record_event
from app.platform.auth.models import User
from app.platform.customers.models import Customer
from app.platform.imports.models import ImportJob, ImportRow
from app.platform.imports.registry import registry as import_types
from app.platform.mappings import resolvers as mapping_kinds
from app.platform.mappings.models import ExternalEntityMapping
from app.platform.mappings.service import code_key
from app.platform.quality.models import ISSUE_SEVERITIES, ISSUE_STATUSES, DataQualityIssue
from app.platform.quality.registry import QualityCodeSpec, QualitySubjectSpec, registry
from app.platform.sources.models import SourceSystem

DECISIONS = ("resolved", "ignored")
NOTE_LENGTH = 500
SEVERITY_RANK = case({"error": 0, "warning": 1, "info": 2}, value=DataQualityIssue.severity, else_=3)
SORTS = ("severity", "recent")

# What of `details` may leave the database: counts, row numbers, statuses and ids. Never a raw value or a name.
SAFE_DETAILS = (
    "lines",
    "row_number",
    "codes",
    "mapping_status",
    "match",
    "duplicate_of_job_id",
    "first_job_id",
    "rows_with_price",
    "missing",
)


def _customers(db: Session, ids: Sequence[int]) -> dict[int, str]:
    rows = db.execute(select(Customer.id, Customer.display_name).where(Customer.id.in_(ids)))
    return {cid: f"Cliente {name}" for cid, name in rows}


registry.register_subject(QualitySubjectSpec("customer", _customers))


# --- which issues a module sees -------------------------------------------------------------------------------------


def module_condition(module: str) -> ColumnElement[bool]:
    """The issues of `module`: its own codes, and the platform's raised by one of its imports."""
    own = DataQualityIssue.code.startswith(f"{module}.", autoescape=True)
    of_its_imports = and_(
        DataQualityIssue.code.startswith("platform.", autoescape=True),
        DataQualityIssue.job_id.in_(
            select(ImportJob.id).where(ImportJob.import_type.startswith(f"{module}.", autoescape=True))
        ),
    )
    return or_(own, of_its_imports)


def module_of(db: Session, issue: DataQualityIssue) -> str | None:
    prefix = issue.code.split(".", 1)[0]
    if prefix != "platform":
        return prefix
    if issue.job_id is None:
        return None
    import_type = db.scalar(select(ImportJob.import_type).where(ImportJob.id == issue.job_id))
    return import_type.split(".", 1)[0] if import_type else None


# --- search and summary ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class IssueFilters:
    status: str | None = None
    severity: str | None = None
    code: str | None = None
    group: str | None = None
    job_id: int | None = None
    seen_from: date | None = None  # last seen on or after
    seen_to: date | None = None  # first seen on or before
    q: str | None = None  # words of the code's label or description, never of the data


def _check(filters: IssueFilters) -> None:
    if filters.status is not None and filters.status not in ISSUE_STATUSES:
        raise AppError(422, "invalid_status", "Estado inválido.")
    if filters.severity is not None and filters.severity not in ISSUE_SEVERITIES:
        raise AppError(422, "invalid_severity", "Severidad inválida.")
    if filters.code is not None and registry.get(filters.code) is None:
        raise AppError(422, "invalid_code", "Ese tipo de problema no existe.")
    if filters.group is not None and filters.group not in {g.key for g in registry.groups()}:
        raise AppError(422, "invalid_group", "Ese grupo no existe.")
    if filters.seen_from and filters.seen_to and filters.seen_from > filters.seen_to:
        raise AppError(422, "invalid_period", "La fecha desde es posterior a la fecha hasta.")


def _matching_codes(filters: IssueFilters) -> list[str] | None:
    """The codes the group and the words allow (None: every code)."""
    if filters.group is None and not filters.q:
        return None
    words = [w for w in (filters.q or "").lower().split() if w]
    codes = []
    for spec in registry.all():
        if filters.group is not None and spec.group.key != filters.group:
            continue
        text = f"{spec.label} {spec.description} {spec.group.label} {spec.code}".lower()
        if all(word in text for word in words):
            codes.append(spec.code)
    return codes


def _filtered(query: Select[Any], module: str, filters: IssueFilters, *, with_status: bool = True) -> Select[Any]:
    _check(filters)
    query = query.where(module_condition(module))
    if with_status and filters.status is not None:
        query = query.where(DataQualityIssue.status == filters.status)
    if with_status and filters.severity is not None:
        query = query.where(DataQualityIssue.severity == filters.severity)
    if filters.code is not None:
        query = query.where(DataQualityIssue.code == filters.code)
    codes = _matching_codes(filters)
    if codes is not None:
        query = query.where(DataQualityIssue.code.in_(codes) if codes else false())
    if filters.job_id is not None:
        query = query.where(DataQualityIssue.job_id == filters.job_id)
    if filters.seen_from is not None:
        query = query.where(DataQualityIssue.last_seen_at >= filters.seen_from)
    if filters.seen_to is not None:
        query = query.where(DataQualityIssue.first_seen_at < filters.seen_to + timedelta(days=1))
    return query


def search(
    db: Session, module: str, filters: IssueFilters, *, sort: str = "severity", limit: int = 25, offset: int = 0
) -> tuple[list[DataQualityIssue], int]:
    """A page of the module's issues. `severity`: errors first, then warnings, then info, the oldest first inside
    each (accumulated debt shows first); `recent`: the last seen first."""
    if sort not in SORTS:
        raise AppError(422, "invalid_sort", "Orden inválido.")
    base = _filtered(select(DataQualityIssue), module, filters)
    total = int(db.scalar(select(func.count()).select_from(base.order_by(None).subquery())) or 0)
    order: tuple[Any, ...]
    if sort == "severity":
        order = (SEVERITY_RANK, DataQualityIssue.first_seen_at, DataQualityIssue.id)
    else:
        order = (DataQualityIssue.last_seen_at.desc(), DataQualityIssue.id.desc())
    rows = list(db.scalars(base.order_by(*order).limit(limit).offset(offset)))
    return rows, total


@dataclass
class Counted:
    issues: int = 0  # distinct problems
    occurrences: int = 0  # times they were seen


@dataclass
class Summary:
    open: dict[str, Counted] = field(default_factory=lambda: {s: Counted() for s in ISSUE_SEVERITIES})
    resolved: Counted = field(default_factory=Counted)
    ignored: Counted = field(default_factory=Counted)

    @property
    def open_total(self) -> Counted:
        return Counted(sum(c.issues for c in self.open.values()), sum(c.occurrences for c in self.open.values()))


def summary(db: Session, module: str, filters: IssueFilters) -> Summary:
    """Issues and occurrences per status (and per severity while open), with the filters other than status and
    severity: one grouped query, nothing counted in the browser."""
    grouped = cast(
        Select[Any],
        select(
            DataQualityIssue.status,
            DataQualityIssue.severity,
            func.count(),
            func.coalesce(func.sum(DataQualityIssue.occurrences), 0),
        ),
    )
    query = _filtered(grouped, module, filters, with_status=False).group_by(
        DataQualityIssue.status, DataQualityIssue.severity
    )
    result = Summary()
    for status, severity, issues, occurrences in db.execute(query).all():
        if status == "open":
            counted = result.open.setdefault(severity, Counted())
        else:
            counted = result.resolved if status == "resolved" else result.ignored
        counted.issues += int(issues)
        counted.occurrences += int(occurrences)
    return result


# --- what the screen shows of an issue -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Source:
    job_id: int | None
    import_type: str | None
    import_label: str | None
    row_id: int | None
    row_number: int | None
    sheet: str | None


@dataclass(frozen=True)
class IssueView:
    issue: DataQualityIssue
    spec: QualityCodeSpec | None
    subject: str | None  # «Cliente ALMACEN UNO», «Código STORE SYS 998»; never CUIT, phone or address
    source: Source
    mapping_id: int | None
    facts: dict[str, Any]
    resolved_by_name: str | None
    by_hand: bool  # closed by a person in Calidad, not because its condition went away


def _safe_details(details: dict[str, Any] | None) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key in SAFE_DETAILS:
        value = (details or {}).get(key)
        if isinstance(value, bool) or value is None:
            continue
        if isinstance(value, int | str):
            safe[key] = value
        elif isinstance(value, list) and all(isinstance(v, str | int) for v in value):
            safe[key] = value[:50]
    return safe


def _mapping_ids(db: Session, issues: Sequence[DataQualityIssue]) -> dict[int, int]:
    """The mapping behind each mapping issue: its stored id, or the mapping of exactly its external identity."""
    found: dict[int, int] = {}
    wanted: dict[tuple[str, str, str, str], list[int]] = {}
    for issue in issues:
        spec = registry.get(issue.code)
        if spec is None or spec.fix != "mapping":
            continue
        ref, details = issue.external_ref or {}, issue.details or {}
        stored = ref.get("mapping_id") or details.get("mapping_id")
        if isinstance(stored, int):
            found[issue.id] = stored
        elif spec.mapping_entity_type and all(
            isinstance(ref.get(k), str) for k in ("source_system", "namespace", "external_id")
        ):
            key = (spec.mapping_entity_type, ref["source_system"], ref["namespace"], code_key(ref["external_id"]))
            wanted.setdefault(key, []).append(issue.id)
    if wanted:
        rows = db.execute(
            select(
                ExternalEntityMapping.id,
                ExternalEntityMapping.entity_type,
                SourceSystem.code,
                ExternalEntityMapping.namespace,
                ExternalEntityMapping.external_id,
            )
            .join(SourceSystem, SourceSystem.id == ExternalEntityMapping.source_system_id)
            .where(
                or_(
                    *(
                        and_(
                            ExternalEntityMapping.entity_type == k[0],
                            SourceSystem.code == k[1],
                            ExternalEntityMapping.namespace == k[2],
                            ExternalEntityMapping.external_id == k[3],
                        )
                        for k in wanted
                    )
                )
            )
        ).all()
        for mapping_id, entity_type, system, namespace, external_id in rows:
            for issue_id in wanted.get((entity_type, system, namespace, external_id), []):
                found[issue_id] = int(mapping_id)
    return found


def _mapping_subjects(db: Session, mapping_ids: Sequence[int]) -> dict[int, str]:
    """A mapping named as Mapeos names it to anyone who can read it: its kind and its external id and label."""
    if not mapping_ids:
        return {}
    rows = db.execute(
        select(
            ExternalEntityMapping.id,
            ExternalEntityMapping.entity_type,
            SourceSystem.code,
            ExternalEntityMapping.namespace,
            ExternalEntityMapping.external_id,
            ExternalEntityMapping.external_label,
        )
        .join(SourceSystem, SourceSystem.id == ExternalEntityMapping.source_system_id)
        .where(ExternalEntityMapping.id.in_(mapping_ids))
    ).all()
    named = {}
    for mapping_id, entity_type, system, namespace, external_id, label in rows:
        kind = mapping_kinds.registry.label((entity_type, system, namespace)) or "Código"
        extra = f" · {label}" if label and label != external_id else ""
        named[int(mapping_id)] = f"{kind} {external_id}{extra}"
    return named


def _subjects(db: Session, issues: Sequence[DataQualityIssue], mapping_ids: dict[int, int]) -> dict[int, str]:
    found: dict[int, str] = {}
    by_type: dict[str, list[DataQualityIssue]] = {}
    for issue in issues:
        if issue.entity_type and issue.entity_id is not None:
            by_type.setdefault(issue.entity_type, []).append(issue)
    for entity_type, of_type in by_type.items():
        subject = registry.subject(entity_type)
        if subject is None:
            continue
        names = subject.describe(db, sorted({i.entity_id for i in of_type if i.entity_id is not None}))
        for issue in of_type:
            if issue.entity_id in names:
                found[issue.id] = names[issue.entity_id]
    mapped = _mapping_subjects(db, sorted({m for i, m in mapping_ids.items() if i not in found}))
    for issue in issues:
        if issue.id in found:
            continue
        if issue.id in mapping_ids and mapping_ids[issue.id] in mapped:
            found[issue.id] = mapped[mapping_ids[issue.id]]
            continue
        ref = issue.external_ref or {}
        if isinstance(ref.get("external_id"), str):
            system = str(ref.get("source_system", "")).replace("_", " ")
            found[issue.id] = f"Código {system} {code_key(ref['external_id'])}".replace("  ", " ")
    return found


def views(db: Session, issues: Sequence[DataQualityIssue]) -> list[IssueView]:
    """Everything the screen shows of a page of issues, with one query per kind of reference."""
    mapping_ids = _mapping_ids(db, issues)
    subjects = _subjects(db, issues, mapping_ids)
    job_ids = sorted({i.job_id for i in issues if i.job_id is not None})
    jobs = dict(db.execute(select(ImportJob.id, ImportJob.import_type).where(ImportJob.id.in_(job_ids))).all())
    row_ids = sorted({i.row_id for i in issues if i.row_id is not None})
    rows = {
        r.id: (r.row_number, r.sheet)
        for r in db.execute(
            select(ImportRow.id, ImportRow.row_number, ImportRow.sheet).where(ImportRow.id.in_(row_ids))
        )
    }
    decider_ids = sorted({i.resolved_by for i in issues if i.resolved_by is not None})
    deciders = dict(db.execute(select(User.id, User.full_name).where(User.id.in_(decider_ids))).all())
    closed = [str(i.id) for i in issues if i.status != "open"]
    by_hand = (
        set(
            db.scalars(
                select(AuditEvent.entity_id).where(
                    AuditEvent.event_type.in_([f"platform.quality.{d}" for d in DECISIONS]),
                    AuditEvent.entity_type == "data_quality_issue",
                    AuditEvent.entity_id.in_(closed),
                )
            )
        )
        if closed
        else set()
    )
    result = []
    for issue in issues:
        import_type = jobs.get(issue.job_id) if issue.job_id is not None else None
        registered = import_types.get(import_type) if import_type else None
        row_number, sheet = rows.get(issue.row_id, (None, None)) if issue.row_id is not None else (None, None)
        result.append(
            IssueView(
                issue=issue,
                spec=registry.get(issue.code),
                subject=subjects.get(issue.id),
                source=Source(
                    job_id=issue.job_id,
                    import_type=import_type,
                    import_label=registered.spec.name if registered else None,
                    row_id=issue.row_id,
                    row_number=row_number,
                    sheet=sheet,
                ),
                mapping_id=mapping_ids.get(issue.id),
                facts=_safe_details(issue.details),
                resolved_by_name=deciders.get(issue.resolved_by) if issue.resolved_by is not None else None,
                by_hand=str(issue.id) in by_hand,
            )
        )
    return result


def get(db: Session, issue_id: int) -> DataQualityIssue:
    issue = db.get(DataQualityIssue, issue_id)
    if issue is None:
        raise AppError(404, "issue_not_found", "Ese problema no existe.")
    return issue


# --- what a person decides -------------------------------------------------------------------------------------------


def _note(note: str | None) -> str:
    clean = " ".join(note.split()) if note else ""
    if not clean:
        raise AppError(422, "note_required", "Explicá por qué en una nota.")
    if len(clean) > NOTE_LENGTH:
        raise AppError(422, "note_too_long", f"La nota tiene como máximo {NOTE_LENGTH} caracteres.")
    return clean


def decide(db: Session, actor: User, issue_id: int, status: str, *, note: str | None) -> DataQualityIssue:
    """Close an open issue by hand, in the caller's transaction (the endpoint commits). The row is locked: if someone
    closed it in between, 409 with its current status and nothing is overwritten. Only the issue changes."""
    if status not in DECISIONS:
        raise ValueError(f"Not a manual decision: {status}")
    clean = _note(note)
    issue = db.scalars(select(DataQualityIssue).where(DataQualityIssue.id == issue_id).with_for_update()).first()
    if issue is None:
        raise AppError(404, "issue_not_found", "Ese problema no existe.")
    if issue.status != "open":
        raise AppError(
            409,
            "issue_not_open",
            "Este problema ya no está abierto: alguien lo cerró mientras lo revisabas.",
            {"issue_id": issue.id, "status": issue.status},
        )
    issue.status = status
    issue.resolved_by = actor.id
    issue.resolved_at = func.now()
    issue.resolution_note = clean
    # The note stays only in the issue: a person may write a name in it. Ids and the code only.
    record_event(
        db,
        f"platform.quality.{status}",
        user_id=actor.id,
        entity_type="data_quality_issue",
        entity_id=str(issue.id),
        payload={
            "issue_id": issue.id,
            "code": issue.code,
            "severity": issue.severity,
            "from_status": "open",
            "to_status": status,
        },
    )
    db.flush()
    db.refresh(issue)
    return issue
