# PROMPT:
# Database module tests — connect_with_retry, check_db_connection, session helpers.
#
# CHANGES MADE:
# - Mocked engine connect for retry success/failure paths
# - reset_database_singletons smoke test

"""Database utility tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.database import (
    check_db_connection,
    connect_with_retry,
    reset_database_singletons,
)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_connect_with_retry_success() -> None:
    engine = MagicMock()
    conn = AsyncMock()
    conn.execute = AsyncMock()
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=None)
    engine.connect = MagicMock(return_value=cm)
    with patch("app.database.get_settings_cached") as gs:
        gs.return_value = MagicMock(db_connect_max_retries=2, db_connect_retry_seconds=0.01)
        ok = await connect_with_retry(engine)
    assert ok is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_connect_with_retry_exhausted() -> None:
    engine = MagicMock()
    engine.connect = MagicMock(side_effect=RuntimeError("down"))
    with patch("app.database.get_settings_cached") as gs:
        gs.return_value = MagicMock(db_connect_max_retries=2, db_connect_retry_seconds=0.01)
        ok = await connect_with_retry(engine)
    assert ok is False


@pytest.mark.unit
def test_reset_singletons() -> None:
    reset_database_singletons()
