"""Tests for EnergyID hello / webhook helpers (no live network)."""

from __future__ import annotations

import base64
import json
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from energyid_monitor import reading_store, token_store
from energyid_monitor.energyid import (
    ProvisioningConfig,
    _post_with_token_retry,
    _should_skip_upload,
    call_hello,
    effective_upload_interval,
    load_reading_retention_seconds,
    load_upload_interval_override,
    load_upload_interval_seconds,
    post_webhook_in,
    run_energyid_flow,
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
    headers: dict[str, str] | None = None,
) -> MagicMock:
    response = MagicMock()
    response.status = status
    response.text = AsyncMock(return_value=text)
    response.json = AsyncMock(return_value=json_body or {})
    response.headers = headers or {}
    response.__aenter__ = AsyncMock(return_value=response)
    response.__aexit__ = AsyncMock(return_value=None)
    return response


@pytest.mark.asyncio
async def test_call_hello_returns_tokens_and_caches_interval(
    mock_config: ProvisioningConfig, tmp_path
) -> None:
    db_path = tmp_path / "token.db"
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

    result = await call_hello(session, mock_config, db_path)

    assert result["bearer_token"] == bearer
    assert result["twin_id"] == "twin-123"
    assert result["exp"] == exp
    assert result["webhook_url"] == "https://hooks.example.com/tenant-webhook"
    assert result["upload_interval_seconds"] == 900
    session.post.assert_called_once()

    state = await reading_store.get_sync_state(db_path)
    assert state["hello_upload_interval_seconds"] == 900


@pytest.mark.asyncio
async def test_call_hello_claim_required(
    mock_config: ProvisioningConfig, tmp_path
) -> None:
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
        await call_hello(session, mock_config, tmp_path / "token.db")


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
async def test_post_webhook_in_accepts_batch_array(
    mock_config: ProvisioningConfig,
) -> None:
    response = _mock_response(status=200, text='{"ok":true}')
    session = MagicMock()
    session.post = MagicMock(return_value=response)

    batch = [{"ts": 1, "pv": 1.0}, {"ts": 2, "pv": 2.0}]
    result = await post_webhook_in(
        session,
        bearer_token="Bearer tok",
        twin_id="twin",
        payload=batch,
        webhook_url=mock_config["webhook_url"],
    )
    assert result == {"ok": True}
    assert session.post.call_args.kwargs["json"] == batch


@pytest.mark.asyncio
async def test_post_webhook_in_logs_retry_after_on_429(
    mock_config: ProvisioningConfig,
) -> None:
    response = _mock_response(
        status=429, text="slow down", headers={"Retry-After": "60"}
    )
    session = MagicMock()
    session.post = MagicMock(return_value=response)

    with pytest.raises(RuntimeError, match="429.*Retry-After=60"):
        await post_webhook_in(
            session,
            bearer_token="Bearer tok",
            twin_id="twin",
            payload=[{"ts": 1}],
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
        "upload_interval_seconds": 900,
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


def test_effective_upload_interval_max_and_override() -> None:
    assert effective_upload_interval(300, 900, override=False) == 900
    assert effective_upload_interval(900, 300, override=False) == 900
    assert effective_upload_interval(300, 900, override=True) == 300
    assert effective_upload_interval(300, None, override=False) == 300


def test_should_skip_upload() -> None:
    now = 1_700_000_000
    assert _should_skip_upload(None, 900, now_seconds=now) is False
    assert _should_skip_upload(now - 100, 900, now_seconds=now) is True
    assert _should_skip_upload(now - 900, 900, now_seconds=now) is False
    assert _should_skip_upload(now - 901, 900, now_seconds=now) is False


def test_load_queue_env_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENERGYID_UPLOAD_INTERVAL_SECONDS", raising=False)
    monkeypatch.delenv("ENERGYID_UPLOAD_INTERVAL_OVERRIDE", raising=False)
    monkeypatch.delenv("ENERGYID_READING_RETENTION_SECONDS", raising=False)

    assert load_upload_interval_seconds() == 900
    assert load_upload_interval_override() is False
    assert load_reading_retention_seconds() == 604800


def test_load_queue_env_custom(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENERGYID_UPLOAD_INTERVAL_SECONDS", "300")
    monkeypatch.setenv("ENERGYID_UPLOAD_INTERVAL_OVERRIDE", "true")
    monkeypatch.setenv("ENERGYID_READING_RETENTION_SECONDS", "86400")

    assert load_upload_interval_seconds() == 300
    assert load_upload_interval_override() is True
    assert load_reading_retention_seconds() == 86400


@pytest.mark.asyncio
async def test_run_energyid_flow_batches_pending_and_marks_sent(
    mock_config: ProvisioningConfig, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "token.db"
    await token_store.ensure_db(db_path)
    await reading_store.set_hello_upload_interval(60, db_path)

    monkeypatch.setenv("ENERGYID_UPLOAD_INTERVAL_SECONDS", "60")
    monkeypatch.setenv("ENERGYID_UPLOAD_INTERVAL_OVERRIDE", "true")
    monkeypatch.setenv("ENERGYID_READING_RETENTION_SECONDS", "604800")

    now = int(time.time())
    await reading_store.enqueue({"ts": now - 60, "pv": 1.0}, db_path)

    snapshot = {
        "pv_total_generation": 2.0,
        "cumulative_charge_energy": 1.0,
        "cumulative_discharge_energy": 0.5,
        "battery_soc": 50.0,
        "grid_import_power": 0.0,
        "grid_export_power": 100.0,
    }

    with (
        patch(
            "energyid_monitor.energyid.battery.load_battery_config",
            return_value={"ip": "x"},
        ),
        patch(
            "energyid_monitor.energyid.battery.fetch_snapshot",
            new_callable=AsyncMock,
            return_value=snapshot,
        ),
        patch(
            "energyid_monitor.energyid.battery.load_energyid_webhook_keys",
            return_value=["pv"],
        ),
        patch(
            "energyid_monitor.energyid.battery.to_energyid_payload",
            return_value={"ts": now, "pv": 2.0},
        ),
        patch(
            "energyid_monitor.energyid.battery.format_snapshot",
            return_value="snap",
        ),
        patch(
            "energyid_monitor.energyid.load_provisioning_config",
            return_value=mock_config,
        ),
        patch(
            "energyid_monitor.energyid._post_with_token_retry",
            new_callable=AsyncMock,
            return_value={"ok": True},
        ) as mock_post,
    ):
        await run_energyid_flow(db_path)

    mock_post.assert_called_once()
    batch = mock_post.call_args.args[2]
    assert batch == [{"ts": now - 60, "pv": 1.0}, {"ts": now, "pv": 2.0}]

    pending = await reading_store.list_pending(db_path)
    assert pending == []
    state = await reading_store.get_sync_state(db_path)
    assert state["last_successful_upload_at"] is not None


@pytest.mark.asyncio
async def test_run_energyid_flow_skips_within_interval(
    mock_config: ProvisioningConfig, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "token.db"
    await token_store.ensure_db(db_path)
    now = int(time.time())
    await reading_store.set_last_successful_upload(now - 10, db_path)

    monkeypatch.setenv("ENERGYID_UPLOAD_INTERVAL_SECONDS", "900")
    monkeypatch.setenv("ENERGYID_UPLOAD_INTERVAL_OVERRIDE", "true")
    monkeypatch.setenv("ENERGYID_READING_RETENTION_SECONDS", "604800")

    snapshot = {
        "pv_total_generation": 2.0,
        "cumulative_charge_energy": 1.0,
        "cumulative_discharge_energy": 0.5,
        "battery_soc": 50.0,
        "grid_import_power": 0.0,
        "grid_export_power": 0.0,
    }

    with (
        patch(
            "energyid_monitor.energyid.battery.load_battery_config",
            return_value={"ip": "x"},
        ),
        patch(
            "energyid_monitor.energyid.battery.fetch_snapshot",
            new_callable=AsyncMock,
            return_value=snapshot,
        ),
        patch(
            "energyid_monitor.energyid.battery.load_energyid_webhook_keys",
            return_value=["pv"],
        ),
        patch(
            "energyid_monitor.energyid.battery.to_energyid_payload",
            return_value={"ts": now, "pv": 2.0},
        ),
        patch(
            "energyid_monitor.energyid.battery.format_snapshot",
            return_value="snap",
        ),
        patch(
            "energyid_monitor.energyid.load_provisioning_config",
            return_value=mock_config,
        ),
        patch(
            "energyid_monitor.energyid._post_with_token_retry",
            new_callable=AsyncMock,
        ) as mock_post,
    ):
        await run_energyid_flow(db_path)

    mock_post.assert_not_called()
    pending = await reading_store.list_pending(db_path)
    assert len(pending) == 1
    assert pending[0]["payload"]["pv"] == 2.0


@pytest.mark.asyncio
async def test_run_energyid_flow_failed_post_leaves_pending(
    mock_config: ProvisioningConfig, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "token.db"
    await token_store.ensure_db(db_path)

    monkeypatch.setenv("ENERGYID_UPLOAD_INTERVAL_SECONDS", "60")
    monkeypatch.setenv("ENERGYID_UPLOAD_INTERVAL_OVERRIDE", "true")
    monkeypatch.setenv("ENERGYID_READING_RETENTION_SECONDS", "604800")

    snapshot = {
        "pv_total_generation": 2.0,
        "cumulative_charge_energy": 1.0,
        "cumulative_discharge_energy": 0.5,
        "battery_soc": 50.0,
        "grid_import_power": 0.0,
        "grid_export_power": 0.0,
    }

    with (
        patch(
            "energyid_monitor.energyid.battery.load_battery_config",
            return_value={"ip": "x"},
        ),
        patch(
            "energyid_monitor.energyid.battery.fetch_snapshot",
            new_callable=AsyncMock,
            return_value=snapshot,
        ),
        patch(
            "energyid_monitor.energyid.battery.load_energyid_webhook_keys",
            return_value=["pv"],
        ),
        patch(
            "energyid_monitor.energyid.battery.to_energyid_payload",
            return_value={"ts": int(time.time()), "pv": 3.0},
        ),
        patch(
            "energyid_monitor.energyid.battery.format_snapshot",
            return_value="snap",
        ),
        patch(
            "energyid_monitor.energyid.load_provisioning_config",
            return_value=mock_config,
        ),
        patch(
            "energyid_monitor.energyid._post_with_token_retry",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Webhook-in failed (503): down"),
        ),
    ):
        with pytest.raises(RuntimeError, match="503"):
            await run_energyid_flow(db_path)

    pending = await reading_store.list_pending(db_path)
    assert len(pending) == 1
    assert pending[0]["sent_at"] is None
    state = await reading_store.get_sync_state(db_path)
    assert state["last_successful_upload_at"] is None


@pytest.mark.asyncio
async def test_run_energyid_flow_uses_stricter_hello_interval(
    mock_config: ProvisioningConfig, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Env 300s + cached hello 900s => skip when last success was 400s ago."""
    db_path = tmp_path / "token.db"
    await token_store.ensure_db(db_path)
    now = int(time.time())
    await reading_store.set_hello_upload_interval(900, db_path)
    await reading_store.set_last_successful_upload(now - 400, db_path)

    monkeypatch.setenv("ENERGYID_UPLOAD_INTERVAL_SECONDS", "300")
    monkeypatch.setenv("ENERGYID_UPLOAD_INTERVAL_OVERRIDE", "false")
    monkeypatch.setenv("ENERGYID_READING_RETENTION_SECONDS", "604800")

    with (
        patch(
            "energyid_monitor.energyid.battery.load_battery_config",
            return_value={"ip": "x"},
        ),
        patch(
            "energyid_monitor.energyid.battery.fetch_snapshot",
            new_callable=AsyncMock,
            return_value={
                "pv_total_generation": 1.0,
                "cumulative_charge_energy": 1.0,
                "cumulative_discharge_energy": 1.0,
                "battery_soc": 1.0,
                "grid_import_power": 0.0,
                "grid_export_power": 0.0,
            },
        ),
        patch(
            "energyid_monitor.energyid.battery.load_energyid_webhook_keys",
            return_value=["pv"],
        ),
        patch(
            "energyid_monitor.energyid.battery.to_energyid_payload",
            return_value={"ts": now, "pv": 1.0},
        ),
        patch(
            "energyid_monitor.energyid.battery.format_snapshot",
            return_value="snap",
        ),
        patch(
            "energyid_monitor.energyid.load_provisioning_config",
            return_value=mock_config,
        ),
        patch(
            "energyid_monitor.energyid._post_with_token_retry",
            new_callable=AsyncMock,
        ) as mock_post,
    ):
        await run_energyid_flow(db_path)

    mock_post.assert_not_called()
    assert len(await reading_store.list_pending(db_path)) == 1


@pytest.mark.asyncio
async def test_run_energyid_flow_fetches_hello_when_interval_uncached(
    mock_config: ProvisioningConfig, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing hello cache + override false => /hello before gating; use plan limit."""
    db_path = tmp_path / "token.db"
    await token_store.ensure_db(db_path)
    now = int(time.time())
    await reading_store.set_last_successful_upload(now - 400, db_path)

    monkeypatch.setenv("ENERGYID_UPLOAD_INTERVAL_SECONDS", "300")
    monkeypatch.setenv("ENERGYID_UPLOAD_INTERVAL_OVERRIDE", "false")
    monkeypatch.setenv("ENERGYID_READING_RETENTION_SECONDS", "604800")

    async def _hello_side_effect(session, config, path):
        await reading_store.set_hello_upload_interval(900, path)
        return {
            "bearer_token": "Bearer x",
            "twin_id": "twin",
            "exp": now + 7200,
            "webhook_url": mock_config["webhook_url"],
            "upload_interval_seconds": 900,
        }

    with (
        patch(
            "energyid_monitor.energyid.battery.load_battery_config",
            return_value={"ip": "x"},
        ),
        patch(
            "energyid_monitor.energyid.battery.fetch_snapshot",
            new_callable=AsyncMock,
            return_value={
                "pv_total_generation": 1.0,
                "cumulative_charge_energy": 1.0,
                "cumulative_discharge_energy": 1.0,
                "battery_soc": 1.0,
                "grid_import_power": 0.0,
                "grid_export_power": 0.0,
            },
        ),
        patch(
            "energyid_monitor.energyid.battery.load_energyid_webhook_keys",
            return_value=["pv"],
        ),
        patch(
            "energyid_monitor.energyid.battery.to_energyid_payload",
            return_value={"ts": now, "pv": 1.0},
        ),
        patch(
            "energyid_monitor.energyid.battery.format_snapshot",
            return_value="snap",
        ),
        patch(
            "energyid_monitor.energyid.load_provisioning_config",
            return_value=mock_config,
        ),
        patch(
            "energyid_monitor.energyid.call_hello",
            new_callable=AsyncMock,
            side_effect=_hello_side_effect,
        ) as mock_hello,
        patch(
            "energyid_monitor.energyid._post_with_token_retry",
            new_callable=AsyncMock,
        ) as mock_post,
    ):
        await run_energyid_flow(db_path)

    mock_hello.assert_called_once()
    mock_post.assert_not_called()
    state = await reading_store.get_sync_state(db_path)
    assert state["hello_upload_interval_seconds"] == 900
    assert len(await reading_store.list_pending(db_path)) == 1


@pytest.mark.asyncio
async def test_run_energyid_flow_falls_back_when_hello_refresh_fails(
    mock_config: ProvisioningConfig, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the pre-gating /hello fails, continue with env interval and still upload."""
    db_path = tmp_path / "token.db"
    await token_store.ensure_db(db_path)
    now = int(time.time())

    monkeypatch.setenv("ENERGYID_UPLOAD_INTERVAL_SECONDS", "60")
    monkeypatch.setenv("ENERGYID_UPLOAD_INTERVAL_OVERRIDE", "false")
    monkeypatch.setenv("ENERGYID_READING_RETENTION_SECONDS", "604800")

    with (
        patch(
            "energyid_monitor.energyid.battery.load_battery_config",
            return_value={"ip": "x"},
        ),
        patch(
            "energyid_monitor.energyid.battery.fetch_snapshot",
            new_callable=AsyncMock,
            return_value={
                "pv_total_generation": 1.0,
                "cumulative_charge_energy": 1.0,
                "cumulative_discharge_energy": 1.0,
                "battery_soc": 1.0,
                "grid_import_power": 0.0,
                "grid_export_power": 0.0,
            },
        ),
        patch(
            "energyid_monitor.energyid.battery.load_energyid_webhook_keys",
            return_value=["pv"],
        ),
        patch(
            "energyid_monitor.energyid.battery.to_energyid_payload",
            return_value={"ts": now, "pv": 1.0},
        ),
        patch(
            "energyid_monitor.energyid.battery.format_snapshot",
            return_value="snap",
        ),
        patch(
            "energyid_monitor.energyid.load_provisioning_config",
            return_value=mock_config,
        ),
        patch(
            "energyid_monitor.energyid.call_hello",
            new_callable=AsyncMock,
            side_effect=aiohttp.ClientError("offline"),
        ) as mock_hello,
        patch(
            "energyid_monitor.energyid._post_with_token_retry",
            new_callable=AsyncMock,
            return_value={"ok": True},
        ) as mock_post,
    ):
        await run_energyid_flow(db_path)

    mock_hello.assert_called_once()
    mock_post.assert_called_once()
    assert mock_post.call_args.args[2] == [{"ts": now, "pv": 1.0}]
    pending = await reading_store.list_pending(db_path)
    assert pending == []
    state = await reading_store.get_sync_state(db_path)
    assert state["hello_upload_interval_seconds"] is None
    assert state["last_successful_upload_at"] is not None
