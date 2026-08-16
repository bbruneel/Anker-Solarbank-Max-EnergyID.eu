"""Standalone Anker Solix Modbus TCP client (no Home Assistant dependency).

Decode rules and register types follow the official Anker Solix HA integration:
https://github.com/anker-charging/ha-anker-solix-official
"""

from __future__ import annotations

from typing import Any, Iterable

from loguru import logger
from pymodbus.client import AsyncModbusTcpClient
from pymodbus.exceptions import ModbusException

DEFAULT_TIMEOUT = 10.0
DEFAULT_RETRIES = 1
DEFAULT_DEVICE_ID = 1


class RegisterDecodeError(Exception):
    """Malformed register data."""


def default_value(data_type: str) -> Any:
    """Return default fallback value for a data type."""
    return "" if data_type in ("STRING", "VERSION") else 0


def decode_register_value(address: int, data_type: str, registers: list[int]) -> Any:
    """Decode a list of 16-bit registers into a Python value (big-endian)."""
    if not registers:
        raise RegisterDecodeError(f"Register {address} returned no data")
    if any(r is None for r in registers):
        raise RegisterDecodeError(
            f"Register {address} contains None values: {registers}"
        )

    if data_type == "UINT16":
        return registers[0] & 0xFFFF
    if data_type == "INT16":
        raw = registers[0] & 0xFFFF
        return raw if raw < 0x8000 else raw - 0x10000
    if data_type == "INT32":
        if len(registers) < 2:
            raise RegisterDecodeError(
                f"Register {address} requires 2 values for INT32, got {len(registers)}"
            )
        high = registers[0] & 0xFFFF
        low = registers[1] & 0xFFFF
        unsigned = (high << 16) | low
        if unsigned & 0x80000000:
            return -((~unsigned & 0xFFFFFFFF) + 1)
        return unsigned
    if data_type == "UINT32":
        if len(registers) < 2:
            raise RegisterDecodeError(
                f"Register {address} requires 2 values for UINT32, got {len(registers)}"
            )
        high = registers[0] & 0xFFFF
        low = registers[1] & 0xFFFF
        return (high << 16) | low
    if data_type == "VERSION":
        version_bytes = []
        for reg in registers[:2]:
            version_bytes.append((reg >> 8) & 0xFF)
            version_bytes.append(reg & 0xFF)
        if len(version_bytes) >= 4:
            return (
                f"{version_bytes[0]}.{version_bytes[1]}."
                f"{version_bytes[2]}.{version_bytes[3]}"
            )
        return ""
    if data_type == "STRING":
        string_bytes = []
        for reg in registers:
            string_bytes.append((reg >> 8) & 0xFF)
            string_bytes.append(reg & 0xFF)
        return bytes(string_bytes).decode("utf-8", errors="ignore").rstrip("\x00")
    return registers[0]


def apply_gain(value: Any, data_type: str, gain: Any) -> Any:
    """Apply YAML gain factor to a numeric value."""
    if data_type in ("STRING", "VERSION") or gain in (None, 1):
        return value
    return value / gain


def apply_power_split(value: float, mode: str | None) -> float:
    """Split a signed power register into charge/discharge or import/export."""
    if mode == "negative_only":
        return abs(value) if value < 0 else 0
    if mode == "positive_only":
        return value if value > 0 else 0
    return value


def parse_range_string(range_str: str) -> tuple[int, int] | None:
    """Parse a single range string like '10000-10050' into (start, end)."""
    parts = [p.strip() for p in range_str.replace(" ", "").split("-") if p.strip()]
    if len(parts) == 2 and all(part.isdigit() for part in parts):
        start, end = int(parts[0]), int(parts[1])
        if start > end:
            start, end = end, start
        return (start, end)
    return None


def parse_batch_ranges(raw_ranges: Any) -> list[tuple[int, int, str]]:
    """Parse batch_read_ranges from device YAML.

    Returns list of (start, end, register_type) where register_type is
    'holding' (FC 03) or 'input' (FC 04).
    """
    ranges: list[tuple[int, int, str]] = []
    if not raw_ranges:
        return ranges

    if isinstance(raw_ranges, dict):
        for reg_type in ("holding", "input"):
            type_ranges = raw_ranges.get(reg_type)
            if not type_ranges or not isinstance(type_ranges, list):
                continue
            for item in type_ranges:
                if isinstance(item, str):
                    parsed = parse_range_string(item)
                    if parsed:
                        ranges.append((parsed[0], parsed[1], reg_type))
        return ranges

    items: Iterable[Any]
    if isinstance(raw_ranges, str):
        items = [part.strip() for part in raw_ranges.split(",") if part.strip()]
    elif isinstance(raw_ranges, Iterable):
        items = raw_ranges
    else:
        return ranges

    for item in items:
        if isinstance(item, str):
            parsed = parse_range_string(item)
            if parsed:
                ranges.append((parsed[0], parsed[1], "input"))
    return ranges


class AnkerModbusClient:
    """Async Modbus TCP client for Anker SOLIX devices."""

    def __init__(
        self,
        host: str,
        port: int = 502,
        device_id: int = DEFAULT_DEVICE_ID,
        timeout: float = DEFAULT_TIMEOUT,
        retries: int = DEFAULT_RETRIES,
    ):
        self.host = host
        self.port = port
        self.device_id = device_id
        self.client = AsyncModbusTcpClient(
            host=host,
            port=port,
            timeout=timeout,
            retries=retries,
            reconnect_delay=0,
        )

    async def connect(self) -> bool:
        """Open the Modbus TCP connection."""
        connected = await self.client.connect()
        if connected:
            logger.debug(f"Connected to Modbus {self.host}:{self.port}")
        else:
            logger.error(f"Unable to connect to Modbus {self.host}:{self.port}")
        return bool(connected)

    async def close(self) -> None:
        """Close the Modbus TCP connection."""
        closer = getattr(self.client, "close", None)
        if closer is None:
            return
        result = closer()
        if hasattr(result, "__await__"):
            await result
        logger.debug(f"Disconnected from Modbus {self.host}:{self.port}")

    async def _read_range(
        self, start: int, count: int, reg_type: str
    ) -> list[int] | None:
        """Read a contiguous register range."""
        try:
            if reg_type == "holding":
                result = await self.client.read_holding_registers(
                    address=start, count=count, device_id=self.device_id
                )
            else:
                result = await self.client.read_input_registers(
                    address=start, count=count, device_id=self.device_id
                )
        except (OSError, TimeoutError, ModbusException) as exc:
            logger.warning(
                f"Exception reading {reg_type} range {start}-{start + count - 1}: {exc}"
            )
            return None

        if result is None or (hasattr(result, "isError") and result.isError()):
            logger.warning(
                f"Failed to read {reg_type} range {start}-{start + count - 1}: {result}"
            )
            return None

        registers = getattr(result, "registers", None)
        if not registers or len(registers) < count:
            logger.warning(
                f"{reg_type} range {start}-{start + count - 1} returned insufficient data"
            )
            return None
        return list(registers[:count])

    async def read_all(
        self,
        data_points: dict[str, Any],
        batch_ranges: list[tuple[int, int, str]],
    ) -> dict[str, Any]:
        """Batch-read configured ranges and decode data points."""
        range_data: dict[tuple[int, int], list[int]] = {}
        for start, end, reg_type in sorted(batch_ranges, key=lambda item: item[0]):
            count = end - start + 1
            if count <= 0:
                continue
            registers = await self._read_range(start, count, reg_type)
            if registers is not None:
                range_data[(start, end)] = registers

        data: dict[str, Any] = {}
        for key, config in data_points.items():
            try:
                address = int(config["address"])
                count = int(config.get("count", 1))
                data_type = config.get("data_type", "UINT16")
            except (KeyError, TypeError, ValueError):
                logger.debug(f"Invalid configuration for data point {key}: {config}")
                continue

            range_entry = None
            for (start, end), registers in range_data.items():
                if start <= address and address + count - 1 <= end:
                    range_entry = (start, registers)
                    break
            if range_entry is None:
                logger.debug(
                    f"Skipping {key}: address {address} outside configured batch ranges"
                )
                continue

            start, registers = range_entry
            offset = address - start
            slice_end = offset + count
            try:
                value = decode_register_value(
                    address, data_type, registers[offset:slice_end]
                )
            except RegisterDecodeError as exc:
                logger.debug(f"Failed to decode {key}: {exc}")
                data[key] = default_value(data_type)
                continue

            value = apply_gain(value, data_type, config.get("gain"))
            data[key] = value
            logger.debug(f"Data point {key}: address={address}, value={value}")

        return data
