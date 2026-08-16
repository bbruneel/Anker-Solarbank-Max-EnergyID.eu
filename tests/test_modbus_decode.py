from energyid_monitor.battery import (
    _post_process,
    load_device_yaml,
    to_energyid_payload,
)
from energyid_monitor.modbus_client import (
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


def test_bundled_yaml_loads() -> None:
    config = load_device_yaml()
    assert config["product_info"]["default_name"] == "Anker SOLIX Solarbank Max AC"
    assert "battery_soc" in config["read_quantities"]
    assert config["read_quantities"]["battery_soc"]["address"] == 10014
