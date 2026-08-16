from unittest.mock import patch

import pytest

from energyid_monitor.battery import (
    _post_process,
    load_device_yaml,
    to_energyid_payload,
)
from energyid_monitor.modbus_client import (
    AnkerModbusClient,
    apply_gain,
    apply_power_split,
    decode_register_value,
    parse_batch_ranges,
)


def test_decode_uint16() -> None:
    assert decode_register_value(10014, "UINT16", [69]) == 69


def test_decode_int16_negative() -> None:
    assert decode_register_value(1, "INT16", [0xFFFF]) == -1


def test_decode_int32_negative_charge_power() -> None:
    # 65535, 65146 == -390 W (charging), as observed on Solarbank Max AC
    assert decode_register_value(10008, "INT32", [65535, 65146]) == -390


def test_decode_uint32_energy() -> None:
    # raw 61 with gain 10 => 6.1 kWh
    assert decode_register_value(10018, "UINT32", [0, 61]) == 61
    assert apply_gain(61, "UINT32", 10) == 6.1


def test_decode_string_pn() -> None:
    # A17E2 from live device PN registers
    value = decode_register_value(32768, "STRING", [16689, 14149, 12800, 0, 0])
    assert value.strip() == "A17E2"


def test_power_split_modes() -> None:
    assert apply_power_split(-390, "negative_only") == 390
    assert apply_power_split(-390, "positive_only") == 0
    assert apply_power_split(250, "positive_only") == 250
    assert apply_power_split(250, "negative_only") == 0


def test_parse_batch_ranges_typed() -> None:
    ranges = parse_batch_ranges(
        {
            "input": ["10000-10050", "32768-32774"],
            "holding": ["10060-10072"],
        }
    )
    assert (10000, 10050, "input") in ranges
    assert (32768, 32774, "input") in ranges
    assert (10060, 10072, "holding") in ranges


def test_post_process_pv_sum_and_status_label() -> None:
    data_points = {
        "pv_power": {
            "additional_sources": ["third_party_pv_power"],
        },
        "third_party_pv_power": {},
        "battery_charging_power": {"power_split_mode": "negative_only"},
        "battery_discharging_power": {"power_split_mode": "positive_only"},
        "battery_status": {
            "value_mapping": {0: "standby", 1: "charging", 2: "discharging"}
        },
    }
    raw = {
        "pv_power": 0,
        "third_party_pv_power": 390,
        "battery_charging_power": -390,
        "battery_discharging_power": -390,
        "battery_status": 1,
    }
    processed = _post_process(raw, data_points)
    assert processed["pv_power"] == 390
    assert processed["battery_charging_power"] == 390
    assert processed["battery_discharging_power"] == 0
    assert processed["battery_status_label"] == "charging"


def test_energyid_payload_mapping() -> None:
    snapshot = {
        "pv_total_generation": 6.1,
        "cumulative_charge_energy": 5.4,
        "cumulative_discharge_energy": 2.7,
        "battery_soc": 69,
        "grid_import_power": 0,
        "grid_export_power": 1500,
    }
    payload = to_energyid_payload(snapshot, timestamp=1733835004)
    assert payload == {
        "ts": 1733835004,
        "pv": 6.1,
        "bat": 5.4,
        "bat-i": 2.7,
        "bat-soc": 69,
        "pwr": 0.0,
        "pwr-i": 1.5,
    }


def test_energyid_payload_rejects_incomplete_snapshot() -> None:
    with pytest.raises(RuntimeError, match="Incomplete Solarbank snapshot"):
        to_energyid_payload(
            {
                "pv_total_generation": 6.1,
                "battery_soc": 69,
            },
            timestamp=1733835004,
        )


def test_bundled_yaml_loads() -> None:
    config = load_device_yaml()
    assert config["product_info"]["default_name"] == "Anker SOLIX Solarbank Max AC"
    assert "battery_soc" in config["read_quantities"]
    assert config["read_quantities"]["battery_soc"]["address"] == 10014


@pytest.mark.asyncio
async def test_read_all_decodes_from_batch_ranges() -> None:
    client = AnkerModbusClient(host="127.0.0.1")
    data_points = {
        "battery_soc": {
            "address": 10014,
            "data_type": "UINT16",
            "count": 1,
            "gain": 1,
        },
        "pv_total_generation": {
            "address": 10018,
            "data_type": "UINT32",
            "count": 2,
            "gain": 10,
        },
        "battery_charging_power": {
            "address": 10008,
            "data_type": "INT32",
            "count": 2,
            "gain": 1,
        },
    }
    batch_ranges = [(10000, 10050, "input")]

    async def fake_read(start: int, count: int, reg_type: str) -> list[int]:
        assert start == 10000
        assert count == 51
        assert reg_type == "input"
        registers = [0] * count
        registers[14] = 69  # battery_soc at 10014
        registers[18] = 0  # pv_total_generation high
        registers[19] = 61  # pv_total_generation low (raw 61 / gain 10)
        registers[8] = 65535  # battery_charging_power INT32 -390
        registers[9] = 65146
        return registers

    with patch.object(client, "_read_range", side_effect=fake_read):
        data = await client.read_all(data_points, batch_ranges)

    assert data["battery_soc"] == 69
    assert data["pv_total_generation"] == 6.1
    assert data["battery_charging_power"] == -390
