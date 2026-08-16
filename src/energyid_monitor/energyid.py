import asyncio
import base64
import json
import sys
import time
from pathlib import Path
from typing import Any, TypedDict

import aiohttp
from dotenv import load_dotenv
from loguru import logger

from energyid_monitor import battery, common, logging_config, token_store

load_dotenv(override=True)

SUCCESS_STATUS_CODES = {200, 201}


def _decode_jwt_exp(bearer_token: str) -> int:
    """Extract exp claim from JWT bearer token without verification."""
    token = bearer_token.replace("Bearer ", "").strip()
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("Invalid JWT token format")

    payload_encoded = parts[1]
    padding = "=" * (4 - len(payload_encoded) % 4)
    payload_decoded = base64.urlsafe_b64decode(payload_encoded + padding)
    payload = json.loads(payload_decoded)

    exp = payload.get("exp")
    if not exp:
        raise ValueError("JWT token does not contain exp claim")
    return int(exp)


class HelloTokens(TypedDict):
    """Tokens returned from the EnergyID hello endpoint."""

    bearer_token: str
    twin_id: str
    exp: int
    webhook_url: str


class ProvisioningConfig(TypedDict):
    """Configuration for EnergyID device provisioning and API endpoints."""

    provisioning_key: str
    provisioning_secret: str
    device_id: str
    device_name: str
    hello_url: str
    webhook_url: str


def load_provisioning_config() -> ProvisioningConfig:
    """Load provisioning credentials and device metadata from the environment."""
    return {
        "provisioning_key": common._require_env("ENERGYID_KEY"),
        "provisioning_secret": common._require_env("ENERGYID_SECRET"),
        "device_id": common._require_env("ENERGYID_YOUR_DEVICE_ID"),
        "device_name": common._require_env("ENERGYID_YOUR_DEVICE_NAME"),
        "hello_url": common._require_env("ENERGYID_HELLO_URL"),
        "webhook_url": common._require_env("ENERGYID_WEBHOOK_URL"),
    }


async def call_hello(
    session: aiohttp.ClientSession, config: ProvisioningConfig
) -> HelloTokens:
    """Call the EnergyID hello endpoint and return bearer token + twin id + exp."""
    headers = {
        "Content-Type": "application/json",
        "X-Provisioning-Key": config["provisioning_key"],
        "X-Provisioning-Secret": config["provisioning_secret"],
    }
    payload = {"deviceId": config["device_id"], "deviceName": config["device_name"]}

    async with session.post(config["hello_url"], json=payload, headers=headers) as resp:
        if resp.status != 200:
            text = await resp.text()
            raise RuntimeError(f"Hello endpoint failed ({resp.status}): {text}")

        body = await resp.json()
        logger.debug(f"Hello response body: {body}")

        if body.get("claimCode") or body.get("claimUrl"):
            claim_url = body.get("claimUrl", "")
            claim_code = body.get("claimCode", "")
            raise RuntimeError(
                "EnergyID device is not claimed yet. Open the claim URL in a browser "
                f"and link it to your record, then retry. claimCode={claim_code} "
                f"claimUrl={claim_url}"
            )

        headers_dict = body.get("headers") or {}
        bearer_token = (
            headers_dict.get("authorization")
            or body.get("bearerToken")
            or body.get("ENERGYID_BEARER_TOKEN")
        )
        twin_id = (
            headers_dict.get("x-twin-id")
            or body.get("twinId")
            or body.get("ENERGYID_TWIN_ID")
        )
        webhook_url = body.get("webhookUrl") or config["webhook_url"]

        if not bearer_token or not twin_id:
            raise RuntimeError("Hello response missing bearer token or twin id")

        exp = _decode_jwt_exp(bearer_token)
        policy = body.get("webhookPolicy") or {}
        upload_interval = policy.get("uploadInterval")
        if upload_interval:
            logger.info(f"EnergyID uploadInterval is {upload_interval} seconds")

        masked_token = logging_config.mask_token(bearer_token)
        logger.debug(f"Extracted: bearer={masked_token}, twin={twin_id}, exp={exp}")
        return {
            "bearer_token": bearer_token,
            "twin_id": twin_id,
            "exp": exp,
            "webhook_url": webhook_url,
        }


class ActiveCredentials(TypedDict):
    """Bearer credentials plus the webhook URL to use for this post."""

    bearer_token: str
    twin_id: str
    exp: int
    webhook_url: str


async def get_or_refresh_token(
    session: aiohttp.ClientSession,
    config: ProvisioningConfig,
    db_path: str | Path = token_store.DEFAULT_DB_PATH,
) -> ActiveCredentials:
    """Get a valid token from cache or fetch a new one if missing/expired.

    When a fresh `/hello` response is used, the webhook URL from that response
    is preferred (falling back to ENERGYID_WEBHOOK_URL). Cached tokens reuse
    the configured webhook URL from the environment.
    """
    await token_store.ensure_db(db_path)
    cached = await token_store.get_latest_token(db_path)

    if cached and token_store.is_token_valid(cached):
        logger.info("Using cached token (valid)")
        return {
            "bearer_token": cached["bearer_token"],
            "twin_id": cached["twin_id"],
            "exp": cached["exp"],
            "webhook_url": config["webhook_url"],
        }

    logger.info(
        "Fetching new token from hello endpoint (cache miss or expired/expiring)"
    )
    hello_response = await call_hello(session, config)
    new_token: token_store.StoredToken = {
        "bearer_token": hello_response["bearer_token"],
        "twin_id": hello_response["twin_id"],
        "exp": hello_response["exp"],
    }
    await token_store.store_token(new_token, db_path)
    logger.info("New token stored in database")
    return {
        "bearer_token": hello_response["bearer_token"],
        "twin_id": hello_response["twin_id"],
        "exp": hello_response["exp"],
        "webhook_url": hello_response["webhook_url"],
    }


async def post_webhook_in(
    session: aiohttp.ClientSession,
    bearer_token: str,
    twin_id: str,
    payload: dict[str, Any],
    webhook_url: str,
) -> dict:
    """Send measurement payload to webhook-in using hello tokens."""
    headers = {
        "Content-Type": "application/json",
        "authorization": bearer_token,
        "x-twin-id": twin_id,
    }

    async with session.post(webhook_url, json=payload, headers=headers) as resp:
        text = await resp.text()
        if resp.status == 401:
            raise PermissionError("Webhook-in returned 401; token must be refreshed")
        if resp.status not in SUCCESS_STATUS_CODES:
            raise RuntimeError(f"Webhook-in failed ({resp.status}): {text}")

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"status": resp.status, "body": text}


async def _post_with_token_retry(
    session: aiohttp.ClientSession,
    config: ProvisioningConfig,
    payload: dict[str, Any],
    db_path: str | Path = token_store.DEFAULT_DB_PATH,
) -> dict:
    """Post webhook data, refreshing the token once on HTTP 401."""
    credentials = await get_or_refresh_token(session, config, db_path)
    try:
        return await post_webhook_in(
            session,
            bearer_token=credentials["bearer_token"],
            twin_id=credentials["twin_id"],
            payload=payload,
            webhook_url=credentials["webhook_url"],
        )
    except PermissionError:
        logger.warning("Webhook returned 401; refreshing EnergyID token and retrying")
        hello_response = await call_hello(session, config)
        new_token: token_store.StoredToken = {
            "bearer_token": hello_response["bearer_token"],
            "twin_id": hello_response["twin_id"],
            "exp": hello_response["exp"],
        }
        await token_store.store_token(new_token, db_path)
        return await post_webhook_in(
            session,
            bearer_token=new_token["bearer_token"],
            twin_id=new_token["twin_id"],
            payload=payload,
            webhook_url=hello_response["webhook_url"],
        )


async def run_energyid_flow() -> None:
    """Full flow: load env, read Solarbank, get tokens, post webhook-in."""
    battery_config = battery.load_battery_config()
    config = load_provisioning_config()

    snapshot = await battery.fetch_snapshot(battery_config)
    logger.info("Solarbank snapshot:\n{}", battery.format_snapshot(snapshot))

    timestamp = int(time.time())
    payload = battery.to_energyid_payload(snapshot, timestamp)
    logger.info(f"EnergyID payload: {payload}")

    async with aiohttp.ClientSession() as session:
        webhook_response = await _post_with_token_retry(session, config, payload)

    logger.info(f"Webhook-in response: {webhook_response}")


async def main() -> None:
    logging_config.setup_logging()
    try:
        await run_energyid_flow()
    except (
        asyncio.TimeoutError,
        aiohttp.ClientError,
        ConnectionError,
        OSError,
    ) as exc:
        logger.error(f"EnergyID flow failed: Connection error - {exc}")
        sys.exit(1)
    except Exception:  # noqa: BLE001
        logger.exception("EnergyID flow failed")
        sys.exit(1)
