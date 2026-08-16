"""Tests for EnergyID hello / webhook helpers (no live network)."""

from __future__ import annotations

import base64
import json
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from energyid_monitor import token_store
from energyid_monitor.energyid import (
    ProvisioningConfig,
    _post_with_token_retry,
    call_hello,
    post_webhook_in,
)


def _encode_jwt(payload: dict[str, Any]) -> str:
    header = (
        base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').rstrip(b"=").decode()
    )
    body = (
        base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8"))
        .rstrip(b"=")
        .decode()
    )
    return f"Bearer {header}.{body}.sig"


@pytest.fixture
def mock_config() -> ProvisioningConfig:
    return {
        "provisioning_key": "test_key",
        "provisioning_secret": "test_secret",
        "device_id": "test_device",
        "device_name": "test_name",
        "hello_url": "https://test.example.com/hello",
        "webhook_url": "https://test.example.com/webhook",
    }


def _mock_response(
    *,
    status: int,
    json_body: dict[str, Any] | None = None,
    text: str = "",
) -> MagicMock:
    response = MagicMock()
    response.status = status
    response.text = AsyncMock(return_value=text)
    response.json = AsyncMock(return_value=json_body or {})
    response.__aenter__ = AsyncMock(return_value=response)
    response.__aexit__ = AsyncMock(return_value=None)
    return response


@pytest.mark.asyncio
async def test_call_hello_returns_tokens(mock_config: ProvisioningConfig) -> None:
    exp = int(time.time()) + 7200
    bearer = _encode_jwt({"exp": exp})
    response = _mock_response(
        status=200,
        json_body={
            "headers": {
                "authorization": bearer,
                "x-twin-id": "twin-123",
            },
            "webhookUrl": "https://hooks.example.com/tenant-webhook",
            "webhookPolicy": {"uploadInterval": 900},
        },
    )
    session = MagicMock()
    session.post = MagicMock(return_value=response)

    result = await call_hello(session, mock_config)

    assert result["bearer_token"] == bearer
    assert result["twin_id"] == "twin-123"
    assert result["exp"] == exp
    assert result["webhook_url"] == "https://hooks.example.com/tenant-webhook"
    session.post.assert_called_once()


@pytest.mark.asyncio
async def test_call_hello_claim_required(mock_config: ProvisioningConfig) -> None:
    response = _mock_response(
        status=200,
        json_body={
            "claimCode": "ABC123",
            "claimUrl": "https://app.energyid.eu/claim/ABC123",
        },
    )
    session = MagicMock()
    session.post = MagicMock(return_value=response)

    with pytest.raises(RuntimeError, match="not claimed yet"):
        await call_hello(session, mock_config)


@pytest.mark.asyncio
async def test_post_webhook_in_raises_on_401(mock_config: ProvisioningConfig) -> None:
    response = _mock_response(status=401, text="unauthorized")
    session = MagicMock()
    session.post = MagicMock(return_value=response)

    with pytest.raises(PermissionError, match="401"):
        await post_webhook_in(
            session,
            bearer_token="Bearer old",
            twin_id="twin-old",
            payload={"ts": 1},
            webhook_url=mock_config["webhook_url"],
        )


@pytest.mark.asyncio
async def test_post_with_token_retry_refreshes_on_401(
    mock_config: ProvisioningConfig, tmp_path
) -> None:
    db_path = tmp_path / "token.db"
    await token_store.ensure_db(db_path)

    cached_exp = int(time.time()) + 7200
    await token_store.store_token(
        {
            "bearer_token": "Bearer cached",
            "twin_id": "twin-cached",
            "exp": cached_exp,
        },
        db_path,
    )

    fresh_exp = int(time.time()) + 10800
    fresh_bearer = _encode_jwt({"exp": fresh_exp})
    hello_tokens = {
        "bearer_token": fresh_bearer,
        "twin_id": "twin-fresh",
        "exp": fresh_exp,
        "webhook_url": "https://hooks.example.com/from-hello",
    }

    session = MagicMock()
    with (
        patch(
            "energyid_monitor.energyid.post_webhook_in",
            new_callable=AsyncMock,
        ) as mock_post,
        patch(
            "energyid_monitor.energyid.call_hello",
            new_callable=AsyncMock,
            return_value=hello_tokens,
        ) as mock_hello,
    ):
        mock_post.side_effect = [
            PermissionError("Webhook-in returned 401; token must be refreshed"),
            {"ok": True},
        ]

        result = await _post_with_token_retry(
            session,
            mock_config,
            payload={"ts": 1, "pv": 1.0},
            db_path=db_path,
        )

    assert result == {"ok": True}
    mock_hello.assert_called_once()
    assert mock_post.call_count == 2
    first_kwargs = mock_post.call_args_list[0].kwargs
    second_kwargs = mock_post.call_args_list[1].kwargs
    assert first_kwargs["webhook_url"] == mock_config["webhook_url"]
    assert first_kwargs["bearer_token"] == "Bearer cached"
    assert second_kwargs["webhook_url"] == "https://hooks.example.com/from-hello"
    assert second_kwargs["bearer_token"] == fresh_bearer
    assert second_kwargs["twin_id"] == "twin-fresh"
