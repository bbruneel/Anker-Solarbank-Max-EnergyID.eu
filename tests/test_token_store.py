import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from energyid_monitor import token_store
from energyid_monitor.energyid import ProvisioningConfig, get_or_refresh_token


@pytest.fixture
def mock_config() -> ProvisioningConfig:
    """Fixture providing a test provisioning config."""
    return {
        "provisioning_key": "test_key",
        "provisioning_secret": "test_secret",
        "device_id": "test_device",
        "device_name": "test_name",
        "hello_url": "https://test.example.com/hello",
        "webhook_url": "https://test.example.com/webhook",
    }


@pytest.fixture
def in_memory_db() -> str:
    """Fixture providing a temporary SQLite database file."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as handle:
        db_path = handle.name

    yield db_path
    Path(db_path).unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_ensure_db_creates_schema(in_memory_db: str) -> None:
    """Test that ensure_db runs migrations and creates the tokens table."""
    await token_store.ensure_db(in_memory_db)
    latest = await token_store.get_latest_token(in_memory_db)
    assert latest is None


@pytest.mark.asyncio
async def test_store_and_retrieve_token(in_memory_db: str) -> None:
    """Test storing a token and retrieving it."""
    await token_store.ensure_db(in_memory_db)

    test_token: token_store.StoredToken = {
        "bearer_token": "test_bearer_123",
        "twin_id": "test_twin_456",
        "exp": int(time.time()) + 7200,
    }

    await token_store.store_token(test_token, in_memory_db)
    retrieved = await token_store.get_latest_token(in_memory_db)

    assert retrieved is not None
    assert retrieved["bearer_token"] == test_token["bearer_token"]
    assert retrieved["twin_id"] == test_token["twin_id"]
    assert retrieved["exp"] == test_token["exp"]


@pytest.mark.asyncio
async def test_get_latest_token_returns_most_recent(in_memory_db: str) -> None:
    """Test that get_latest_token returns the token with the latest expiration."""
    await token_store.ensure_db(in_memory_db)

    older_token: token_store.StoredToken = {
        "bearer_token": "old_bearer",
        "twin_id": "old_twin",
        "exp": int(time.time()) + 3600,
    }
    newer_token: token_store.StoredToken = {
        "bearer_token": "new_bearer",
        "twin_id": "new_twin",
        "exp": int(time.time()) + 7200,
    }

    await token_store.store_token(older_token, in_memory_db)
    await token_store.store_token(newer_token, in_memory_db)

    retrieved = await token_store.get_latest_token(in_memory_db)
    assert retrieved is not None
    assert retrieved["bearer_token"] == "new_bearer"


def test_is_token_valid_with_buffer() -> None:
    """Test token validation with 1-hour expiry buffer."""
    now = int(time.time())

    valid_token: token_store.StoredToken = {
        "bearer_token": "valid",
        "twin_id": "twin",
        "exp": now + 7200,
    }
    assert token_store.is_token_valid(valid_token, now) is True

    expiring_soon: token_store.StoredToken = {
        "bearer_token": "expiring",
        "twin_id": "twin",
        "exp": now + 1800,
    }
    assert token_store.is_token_valid(expiring_soon, now) is False

    expired: token_store.StoredToken = {
        "bearer_token": "expired",
        "twin_id": "twin",
        "exp": now - 100,
    }
    assert token_store.is_token_valid(expired, now) is False


@pytest.mark.asyncio
async def test_get_or_refresh_uses_cached_valid_token(
    in_memory_db: str, mock_config: ProvisioningConfig
) -> None:
    """Test that get_or_refresh_token uses a cached valid token without calling hello."""
    await token_store.ensure_db(in_memory_db)

    cached_token: token_store.StoredToken = {
        "bearer_token": "cached_bearer",
        "twin_id": "cached_twin",
        "exp": int(time.time()) + 7200,
    }
    await token_store.store_token(cached_token, in_memory_db)

    mock_session = AsyncMock()

    with patch("energyid_monitor.energyid.call_hello") as mock_hello:
        result = await get_or_refresh_token(mock_session, mock_config, in_memory_db)
        mock_hello.assert_not_called()
        assert result["bearer_token"] == "cached_bearer"
        assert result["twin_id"] == "cached_twin"


@pytest.mark.asyncio
async def test_get_or_refresh_fetches_new_when_expired(
    in_memory_db: str, mock_config: ProvisioningConfig
) -> None:
    """Test that get_or_refresh_token fetches a new token when cached one is expired."""
    await token_store.ensure_db(in_memory_db)

    expired_token: token_store.StoredToken = {
        "bearer_token": "expired_bearer",
        "twin_id": "expired_twin",
        "exp": int(time.time()) - 100,
    }
    await token_store.store_token(expired_token, in_memory_db)

    mock_session = AsyncMock()
    new_exp = int(time.time()) + 7200

    with patch("energyid_monitor.energyid.call_hello") as mock_hello:
        mock_hello.return_value = {
            "bearer_token": "new_bearer",
            "twin_id": "new_twin",
            "exp": new_exp,
            "webhook_url": "https://hooks.energyid.eu/webhook-in",
        }

        result = await get_or_refresh_token(mock_session, mock_config, in_memory_db)
        mock_hello.assert_called_once_with(mock_session, mock_config)
        assert result["bearer_token"] == "new_bearer"
        assert result["twin_id"] == "new_twin"
        assert result["exp"] == new_exp

        stored = await token_store.get_latest_token(in_memory_db)
        assert stored is not None
        assert stored["bearer_token"] == "new_bearer"


@pytest.mark.asyncio
async def test_get_or_refresh_fetches_new_when_no_cache(
    in_memory_db: str, mock_config: ProvisioningConfig
) -> None:
    """Test that get_or_refresh_token fetches a new token when cache is empty."""
    await token_store.ensure_db(in_memory_db)

    mock_session = AsyncMock()
    new_exp = int(time.time()) + 7200

    with patch("energyid_monitor.energyid.call_hello") as mock_hello:
        mock_hello.return_value = {
            "bearer_token": "first_bearer",
            "twin_id": "first_twin",
            "exp": new_exp,
            "webhook_url": "https://hooks.energyid.eu/webhook-in",
        }

        result = await get_or_refresh_token(mock_session, mock_config, in_memory_db)
        mock_hello.assert_called_once()
        assert result["bearer_token"] == "first_bearer"

        stored = await token_store.get_latest_token(in_memory_db)
        assert stored is not None
        assert stored["bearer_token"] == "first_bearer"
