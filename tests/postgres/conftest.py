import os
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from packages.core.models import Base


@pytest.fixture(scope="session")
def postgres_test_database_url() -> str:
    database_url = os.getenv("POSTGRES_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("POSTGRES_TEST_DATABASE_URL is required for PostgreSQL integration tests.")
    if not database_url.startswith("postgresql+asyncpg://"):
        pytest.fail("POSTGRES_TEST_DATABASE_URL must use the postgresql+asyncpg dialect.")
    return database_url


@pytest_asyncio.fixture
async def postgres_session_factory(
    postgres_test_database_url: str,
) -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    engine = create_async_engine(
        postgres_test_database_url,
        pool_size=10,
        max_overflow=10,
        pool_pre_ping=True,
    )

    table_names = [table.name for table in reversed(Base.metadata.sorted_tables)]
    if table_names:
        quoted = ", ".join(f'"{name}"' for name in table_names)
        async with engine.begin() as connection:
            await connection.execute(text(f"TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE"))

    factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )
    try:
        yield factory
    finally:
        await engine.dispose()
