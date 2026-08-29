"""Async SQLAlchemy engine and session management with retry-safe startup."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from shared.logging import get_logger

if TYPE_CHECKING:
    from app.settings import Settings

logger = get_logger(__name__)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def create_engine(settings: Settings) -> AsyncEngine:
    """Build async engine with pool settings from configuration."""
    print("DATABASE_URL =", settings.database_url)
    return create_async_engine(
        settings.database_url,
        pool_pre_ping=settings.db_pool_pre_ping,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_recycle=settings.db_pool_recycle,
        echo=settings.app_env == "development",
    )


@lru_cache
def get_settings_cached() -> Settings:
    from app.settings import Settings

    return Settings()


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_engine(get_settings_cached())
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(),
            expire_on_commit=False,
            autoflush=False,
            autocommit=False,
        )
    return _session_factory


async def connect_with_retry(
    engine: AsyncEngine | None = None,
    *,
    max_retries: int | None = None,
    retry_seconds: float | None = None,
) -> bool:
    """
    Verify database connectivity with exponential backoff.

    Used during application startup so the API can start in degraded mode
    if PostgreSQL is temporarily unavailable.
    """
    settings = get_settings_cached()
    engine = engine or get_engine()
    attempts = max_retries or settings.db_connect_max_retries
    base_delay = retry_seconds or settings.db_connect_retry_seconds

    for attempt in range(1, attempts + 1):
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            logger.info("database_connected", attempt=attempt)
            return True
        except Exception as exc:
            delay = base_delay * (2 ** (attempt - 1))
            logger.warning(
                "database_connect_failed",
                attempt=attempt,
                max_retries=attempts,
                error=str(exc),
                retry_in_seconds=delay if attempt < attempts else 0,
            )
            if attempt >= attempts:
                return False
            await asyncio.sleep(delay)
    return False


async def check_db_connection() -> bool:
    """Lightweight health probe — SELECT 1."""
    try:
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        logger.debug("database_health_check_failed", error=str(exc))
        return False


@asynccontextmanager
async def session_scope() -> AsyncGenerator[AsyncSession, None]:
    """Transactional session scope for scripts and background tasks."""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — yields a session per request."""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_database() -> bool:
    """Initialize engine and verify connectivity (called from lifespan)."""
    ok = await connect_with_retry()
    return ok


async def close_database() -> None:
    """Dispose engine on shutdown."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        logger.info("database_engine_disposed")
    _engine = None
    _session_factory = None


def reset_database_singletons() -> None:
    """Test helper — clear cached engine/factory."""
    global _engine, _session_factory
    _engine = None
    _session_factory = None
    get_settings_cached.cache_clear()
