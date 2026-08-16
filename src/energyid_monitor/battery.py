"""Read Anker SOLIX Solarbank Max AC data over local Modbus TCP."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, TypedDict

import yaml
from dotenv import load_dotenv
from loguru import logger

from energyid_monitor import common
from energyid_monitor.modbus_client import (
    AnkerModbusClient,
    apply_power_split,
    parse_batch_ranges,
)

load_dotenv(override=True)

DEVICE_CONFIG_PATH = (
    Path(__file__).resolve().parent / "devices" / "solarbank_max_ac.yaml"
)

# EnergyID predefined keys:
# https://help.energyid.eu/en/developer/incoming-webhooks/
ENERGYID_CUMULATIVE_KWH = {
    "pv": "pv_total_generation",
    "bat": "cumulative_charge_energy",
    "bat-i": "cumulative_discharge_energy",
}
ENERGYID_SOC_KEY = "battery_soc"
ENERGYID_GRID_IMPORT_W = "grid_import_power"
ENERGYID_GRID_EXPORT_W = "grid_export_power"

# Snapshot keys required before posting to EnergyID. Incomplete Modbus reads
# that omit these would upload misleading partial payloads.
REQUIRED_SNAPSHOT_KEYS = (
    "pv_total_generation",
    "cumulative_charge_energy",
    "cumulative_discharge_energy",
    "battery_soc",
    "grid_import_power",
    "grid_export_power",
)


class BatteryConfig(TypedDict):
    """Configuration for the Solarbank Modbus TCP connection."""

    ip_address: str
    port: int
    device_id: int


def load_battery_config() -> BatteryConfig:
    """Load battery connection settings from environment variables."""
    return {
        "ip_address": common._require_env(
            "SOLARBANK_IP_ADDRESS", default="192.168.50.100"
        ),
        "port": int(common._require_env("SOLARBANK_MODBUS_PORT", default="502")),
        "device_id": int(common._require_env("SOLARBANK_DEVICE_ID", default="1")),
    }


def load_device_yaml(path: Path = DEVICE_CONFIG_PATH) -> dict[str, Any]:
    """Load the bundled Solarbank Max AC register map."""
    with path.open(encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    if not isinstance(config, dict):
        raise RuntimeError(f"Invalid device configuration: {path}")
    return config


def _post_process(raw: dict[str, Any], data_points: dict[str, Any]) -> dict[str, Any]:
    """Apply additional_sources, power_split_mode, and value_mapping."""
    processed = dict(raw)

    for key, config in data_points.items():
        extra_sources = config.get("additional_sources") or []
        if extra_sources and key in processed:
            total = processed[key] or 0
            for source in extra_sources:
                extra = processed.get(source)
                if extra is not None:
                    total += extra
            processed[key] = total

    for key, config in data_points.items():
        split_mode = config.get("power_split_mode")
        if split_mode and key in processed and isinstance(processed[key], (int, float)):
            processed[key] = apply_power_split(float(processed[key]), split_mode)

    for key, config in data_points.items():
        mapping = config.get("value_mapping")
        if mapping and key in processed:
            raw_value = processed[key]
            processed[f"{key}_label"] = mapping.get(
                raw_value, mapping.get(str(raw_value), raw_value)
            )

    return processed


def validate_snapshot(snapshot: dict[str, Any]) -> None:
    """Raise if required EnergyID source keys are missing or non-numeric."""
    missing: list[str] = []
    invalid: list[str] = []
    for key in REQUIRED_SNAPSHOT_KEYS:
        if key not in snapshot:
            missing.append(key)
            continue
        if not isinstance(snapshot[key], (int, float)):
            invalid.append(key)
    if missing or invalid:
        details: list[str] = []
        if missing:
            details.append(f"missing={missing}")
        if invalid:
            details.append(f"non-numeric={invalid}")
        raise RuntimeError(
            "Incomplete Solarbank snapshot; refusing EnergyID upload "
            f"({', '.join(details)})"
        )


def to_energyid_payload(snapshot: dict[str, Any], timestamp: int) -> dict[str, Any]:
    """Map a battery snapshot to EnergyID predefined webhook keys.

    Units follow https://help.energyid.eu/en/developer/incoming-webhooks/ :
    cumulative energy in kWh, grid power gauges in kW, SoC in %.
    """
    validate_snapshot(snapshot)
    payload: dict[str, Any] = {"ts": timestamp}

    for energyid_key, source_key in ENERGYID_CUMULATIVE_KWH.items():
        value = snapshot.get(source_key)
        if isinstance(value, (int, float)):
            payload[energyid_key] = round(float(value), 3)

    soc = snapshot.get(ENERGYID_SOC_KEY)
    if isinstance(soc, (int, float)):
        payload["bat-soc"] = round(float(soc), 1)

    import_w = snapshot.get(ENERGYID_GRID_IMPORT_W)
    if isinstance(import_w, (int, float)):
        payload["pwr"] = round(float(import_w) / 1000.0, 3)

    export_w = snapshot.get(ENERGYID_GRID_EXPORT_W)
    if isinstance(export_w, (int, float)):
        payload["pwr-i"] = round(float(export_w) / 1000.0, 3)

    return payload


async def read_snapshot(
    client: AnkerModbusClient,
    device_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Read and decode the configured Solarbank data points."""
    device_config = device_config or load_device_yaml()
    data_points = device_config.get("read_quantities") or {}
    batch_ranges = parse_batch_ranges(device_config.get("batch_read_ranges"))
    raw = await client.read_all(data_points, batch_ranges)
    return _post_process(raw, data_points)


def format_snapshot(snapshot: dict[str, Any]) -> str:
    """Human-readable summary for the live test script."""
    status = snapshot.get("battery_status_label") or snapshot.get("battery_status")
    lines = [
        f"model: {snapshot.get('device_model', '').strip() or '(unknown)'}",
        f"serial: {snapshot.get('device_sn', '').strip() or '(unknown)'}",
        f"firmware: {snapshot.get('device_sw_version', '').strip() or '(unknown)'}",
        f"status: {status}",
        f"soc: {snapshot.get('battery_soc')} %",
        f"pv power: {snapshot.get('pv_power')} W",
        f"battery charge: {snapshot.get('battery_charging_power')} W",
        f"battery discharge: {snapshot.get('battery_discharging_power')} W",
        f"load: {snapshot.get('load_power')} W",
        f"grid import: {snapshot.get('grid_import_power')} W",
        f"grid export: {snapshot.get('grid_export_power')} W",
        f"pv total: {snapshot.get('pv_total_generation')} kWh",
        f"charge total: {snapshot.get('cumulative_charge_energy')} kWh",
        f"discharge total: {snapshot.get('cumulative_discharge_energy')} kWh",
        f"rated energy: {snapshot.get('rated_energy')} kWh",
    ]
    return "\n".join(lines)


async def fetch_snapshot(config: BatteryConfig | None = None) -> dict[str, Any]:
    """Connect, read one snapshot, disconnect."""
    config = config or load_battery_config()
    client = AnkerModbusClient(
        host=config["ip_address"],
        port=config["port"],
        device_id=config["device_id"],
    )
    connected = await client.connect()
    if not connected:
        raise ConnectionError(
            f"Unable to connect to Solarbank at {config['ip_address']}:{config['port']}"
        )
    try:
        return await read_snapshot(client)
    finally:
        await client.close()


async def main() -> None:
    """Live connectivity test: print decoded Solarbank registers."""
    from energyid_monitor import logging_config

    logging_config.setup_logging()
    config = load_battery_config()
    logger.info(
        f"Connecting to Solarbank at {config['ip_address']}:{config['port']} "
        f"(device_id={config['device_id']})"
    )
    try:
        snapshot = await fetch_snapshot(config)
    except (asyncio.TimeoutError, OSError, ConnectionError) as exc:
        logger.error(f"Connection to {config['ip_address']} failed: {exc}")
        return

    logger.info("Solarbank snapshot:\n{}", format_snapshot(snapshot))
    payload = to_energyid_payload(snapshot, timestamp=0)
    payload.pop("ts", None)
    logger.info(f"EnergyID payload preview: {payload}")


if __name__ == "__main__":
    asyncio.run(main())
