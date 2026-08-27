import asyncio
import base64
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, TypedDict

import aiohttp
from dotenv import load_dotenv
from loguru import logger

from energyid_monitor import battery, common, logging_config, reading_store, token_store

load_dotenv(override=True)

SUCCESS_STATUS_CODES = {200, 201}

DEFAULT_UPLOAD_INTERVAL_SECONDS = 900
DEFAULT_READING_RETENTION_SECONDS = 604800
WebhookPayload = dict[str, Any] | list[dict[str, Any]]


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


def _parse_positive_int_env(name: str, default: int) -> int:
    """Parse a positive integer environment variable."""
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw.strip())
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc
    if value <= 0:
        raise ValueError(f"{name} must be > 0, got {value}")
    return value


def _parse_bool_env(name: str, default: bool = False) -> bool:
    """Parse a boolean environment variable (true/1/yes/on)."""
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("true", "1", "yes", "on")


def load_upload_interval_seconds() -> int:
    """Local minimum seconds between successful webhook POSTs."""
    return _parse_positive_int_env(
        "ENERGYID_UPLOAD_INTERVAL_SECONDS", DEFAULT_UPLOAD_INTERVAL_SECONDS
    )


def load_upload_interval_override() -> bool:
    """If true, ignore hello's uploadInterval and use the env value only."""
    return _parse_bool_env("ENERGYID_UPLOAD_INTERVAL_OVERRIDE", default=False)


def load_reading_retention_seconds() -> int:
    """How long to keep stored readings (by payload ts) before pruning."""
    return _parse_positive_int_env(
        "ENERGYID_READING_RETENTION_SECONDS", DEFAULT_READING_RETENTION_SECONDS
    )


def effective_upload_interval(
    env_seconds: int,
    hello_cached_seconds: int | None,
    *,
    override: bool,
) -> int:
    """Compute the enforced upload interval.

    Default: max(env, cached hello). With override: env only.
    """
    if override or hello_cached_seconds is None:
        return env_seconds
    return max(env_seconds, hello_cached_seconds)


class HelloTokens(TypedDict):
    """Tokens returned from the EnergyID hello endpoint."""

    bearer_token: str
    twin_id: str
    exp: int
    webhook_url: str
    upload_interval_seconds: int | None


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


async def _persist_hello_upload_interval(
    upload_interval_seconds: int | None,
    db_path: str | Path,
) -> None:
    """Cache hello uploadInterval in sync_state when present."""
    if upload_interval_seconds is None:
        return
    await reading_store.set_hello_upload_interval(upload_interval_seconds, db_path)
    logger.info(
        f"Cached EnergyID hello uploadInterval={upload_interval_seconds} seconds"
    )


async def call_hello(
    session: aiohttp.ClientSession,
    config: ProvisioningConfig,
    db_path: str | Path = token_store.DEFAULT_DB_PATH,
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
        raw_interval = policy.get("uploadInterval")
        upload_interval_seconds: int | None = None
        if raw_interval is not None:
            try:
                upload_interval_seconds = int(raw_interval)
            except (TypeError, ValueError):
                logger.warning(
                    f"Ignoring non-integer hello uploadInterval: {raw_interval!r}"
                )
            else:
                if upload_interval_seconds <= 0:
                    logger.warning(
                        f"Ignoring non-positive hello uploadInterval: {upload_interval_seconds}"
                    )
                    upload_interval_seconds = None
                else:
                    logger.info(
                        f"EnergyID uploadInterval is {upload_interval_seconds} seconds"
                    )

        await _persist_hello_upload_interval(upload_interval_seconds, db_path)

        masked_token = logging_config.mask_token(bearer_token)
        logger.debug(f"Extracted: bearer={masked_token}, twin={twin_id}, exp={exp}")
        return {
            "bearer_token": bearer_token,
            "twin_id": twin_id,
            "exp": exp,
            "webhook_url": webhook_url,
            "upload_interval_seconds": upload_interval_seconds,
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
    hello_response = await call_hello(session, config, db_path)
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
    payload: WebhookPayload,
    webhook_url: str,
) -> dict:
    """Send measurement payload (object or batch array) to webhook-in."""
    headers = {
        "Content-Type": "application/json",
        "authorization": bearer_token,
        "x-twin-id": twin_id,
    }

    async with session.post(webhook_url, json=payload, headers=headers) as resp:
        text = await resp.text()
        if resp.status == 401:
            raise PermissionError("Webhook-in returned 401; token must be refreshed")
        if resp.status == 429:
            retry_after = resp.headers.get("Retry-After")
            detail = f"Retry-After={retry_after}" if retry_after else text
            raise RuntimeError(f"Webhook-in rate limited (429): {detail}")
        if resp.status not in SUCCESS_STATUS_CODES:
            raise RuntimeError(f"Webhook-in failed ({resp.status}): {text}")

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"status": resp.status, "body": text}


async def _post_with_token_retry(
    session: aiohttp.ClientSession,
    config: ProvisioningConfig,
    payload: WebhookPayload,
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
        hello_response = await call_hello(session, config, db_path)
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


def _should_skip_upload(
    last_successful_upload_at: int | None,
    effective_interval: int,
    *,
    now_seconds: int | None = None,
) -> bool:
    """True when the last successful POST was within the effective interval."""
    if last_successful_upload_at is None:
        return False
    now = now_seconds if now_seconds is not None else int(time.time())
    return (now - last_successful_upload_at) < effective_interval


async def run_energyid_flow(
    db_path: str | Path = token_store.DEFAULT_DB_PATH,
) -> None:
    """Full flow: read Solarbank, enqueue reading, batch-flush pending webhooks."""
    battery_config = battery.load_battery_config()
    config = load_provisioning_config()
    env_interval = load_upload_interval_seconds()
    override = load_upload_interval_override()
    retention_seconds = load_reading_retention_seconds()

    snapshot = await battery.fetch_snapshot(battery_config)
    logger.info("Solarbank snapshot:\n{}", battery.format_snapshot(snapshot))

    webhook_keys = battery.load_energyid_webhook_keys()
    logger.info(f"EnergyID webhook keys: {', '.join(webhook_keys)}")

    timestamp = int(time.time())
    payload = battery.to_energyid_payload(
        snapshot, timestamp, webhook_keys=webhook_keys
    )
    logger.info(f"EnergyID payload: {payload}")

    await token_store.ensure_db(db_path)
    reading_id = await reading_store.enqueue(payload, db_path)
    logger.info(f"Enqueued reading id={reading_id} ts={payload['ts']}")

    deleted = await reading_store.prune_expired(retention_seconds, db_path)
    if deleted:
        logger.info(
            f"Pruned {deleted} reading(s) older than {retention_seconds} seconds"
        )

    sync_state = await reading_store.get_sync_state(db_path)

    async with aiohttp.ClientSession() as session:
        hello_cached = sync_state["hello_upload_interval_seconds"]
        if not override and hello_cached is None:
            # Token-cache hits skip /hello, so after upgrade (or a cold sync_state)
            # we may not know the plan limit yet. Refresh once before gating.
            try:
                logger.info(
                    "No cached hello uploadInterval; calling /hello before "
                    "rate-limit gating"
                )
                await call_hello(session, config, db_path)
                sync_state = await reading_store.get_sync_state(db_path)
                hello_cached = sync_state["hello_upload_interval_seconds"]
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Failed to refresh hello uploadInterval before gating "
                    "({!r}); falling back to ENERGYID_UPLOAD_INTERVAL_SECONDS={} "
                    "(may hit HTTP 429 if that undercuts the plan)",
                    exc,
                    env_interval,
                )

        interval = effective_upload_interval(
            env_interval,
            hello_cached,
            override=override,
        )
        logger.info(
            "Effective upload interval={}s (env={}s, hello_cached={}, override={})",
            interval,
            env_interval,
            hello_cached,
            override,
        )

        if _should_skip_upload(sync_state["last_successful_upload_at"], interval):
            elapsed = timestamp - int(sync_state["last_successful_upload_at"] or 0)
            logger.info(
                "Skipping webhook POST: last success {}s ago "
                "(effective interval {}s); reading retained as pending",
                elapsed,
                interval,
            )
            return

        pending = await reading_store.list_pending(db_path)
        if not pending:
            logger.info("No pending readings to upload")
            return

        batch = [row["payload"] for row in pending]
        reading_ids = [row["id"] for row in pending]
        logger.info(f"Uploading {len(batch)} pending reading(s) as one webhook batch")

        webhook_response = await _post_with_token_retry(
            session, config, batch, db_path=db_path
        )

    uploaded_at = int(time.time())
    await reading_store.mark_sent(reading_ids, db_path, sent_at=uploaded_at)
    await reading_store.set_last_successful_upload(uploaded_at, db_path)
    logger.info(
        f"Webhook-in response: {webhook_response}; "
        f"marked {len(reading_ids)} reading(s) sent"
    )


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
