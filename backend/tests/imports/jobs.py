"""A bare import job for tests that write masters (products, customers, mappings need the job that saw them)."""

import uuid

from sqlalchemy.orm import Session

from app.platform.imports.models import ImportFile, ImportJob


def make_job(db: Session, user_id: int, *, status: str = "processing") -> int:
    file = ImportFile(
        original_filename="sintetico.xls",
        storage_key=uuid.uuid4().hex,
        size_bytes=1,
        sha256=uuid.uuid4().hex + uuid.uuid4().hex,
        uploaded_by=user_id,
    )
    db.add(file)
    db.flush()
    job = ImportJob(status=status, file_id=file.id, created_by=user_id)
    db.add(job)
    db.flush()
    return job.id
