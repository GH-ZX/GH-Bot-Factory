import pytest

from apps.api.main import health


@pytest.mark.asyncio
async def test_health_check():
    result = await health()
    assert result == {"status": "ok"}
