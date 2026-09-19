import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote_plus

from sqlalchemy import DateTime, func
from sqlalchemy.ext.asyncio import (
    AsyncAttrs,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from packages.core.config import settings


class Base(AsyncAttrs, DeclarativeBase):
    """Base declarative class for all models."""


class UUIDMixin:
    """UUID Primary Key mixin."""

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
        sort_order=-100,
    )


class TimestampMixin:
    """UTC Timestamp mixin for created_at and updated_at."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        server_default=func.now(),
        nullable=False,
    )


class SoftDeleteMixin:
    """Soft delete mixin adding deleted_at timestamp."""

    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def soft_delete(self) -> None:
        self.deleted_at = datetime.now(UTC)

    def restore(self) -> None:
        self.deleted_at = None

def is_supabase_database_url(url: str) -> bool:
    """Detect if the database URL points to a Supabase managed instance or transaction pooler."""
    lowered = (url or "").lower()
    return "supabase.co" in lowered or "pooler.supabase.com" in lowered or "statement_cache_size=0" in lowered


def format_supabase_connection_url(
    project_ref: str,
    db_password: str,
    *,
    pooler: bool = True,
    region: str = "eu-central-1",
    db_name: str = "postgres",
) -> str:
    """Construct an asyncpg-compatible PostgreSQL URL for a customer's Supabase project."""
    ref = project_ref.strip().replace("https://", "").replace(".supabase.co", "")
    pwd = quote_plus(db_password)
    if pooler:
        return f"postgresql+asyncpg://postgres.{ref}:{pwd}@aws-0-{region}.pooler.supabase.com:6543/{db_name}?statement_cache_size=0"
    return f"postgresql+asyncpg://postgres:{pwd}@db.{ref}.supabase.co:5432/{db_name}"


def get_async_engine_connect_args(db_url: str) -> dict[str, Any]:
    connect_args: dict[str, Any] = {}
    if is_supabase_database_url(db_url):
        connect_args["statement_cache_size"] = 0
    return connect_args


engine = create_async_engine(
    settings.database_url,
    echo=False,
    future=True,
    connect_args=get_async_engine_connect_args(settings.database_url),
)
async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Dependency generator yielding an async database session."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
