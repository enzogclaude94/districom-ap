from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Identity, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.errors import AppError

# Deterministic constraint names keep Alembic autogenerate and downgrades stable.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class IdMixin:
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def ensure_unchanged(self, expected_updated_at: datetime | None) -> None:
        """Optimistic concurrency for an admin edit (F1.7): the caller locked the row (`FOR UPDATE`), and the edit
        was made on the version the screen read. If someone else saved in between, 409 instead of a silent
        overwrite. None: the client did not say which version it read (older clients); the lock alone applies."""
        if expected_updated_at is not None and self.updated_at != expected_updated_at:
            raise AppError(
                409,
                "changed_meanwhile",
                "Alguien modificó este dato mientras lo editabas. Recargá para ver la versión actual.",
                {"updated_at": self.updated_at.isoformat()},
            )
