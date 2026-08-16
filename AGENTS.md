# AGENTS.md

Guidance for coding agents working in this repository.

## What this project is

A small Python app that:

1. Reads an **Anker SOLIX Solarbank Max AC** over **local Modbus TCP** (default `192.168.50.100:502`).
2. Maps those registers to EnergyID predefined webhook keys.
3. POSTs one JSON object to EnergyID incoming webhooks.

It is intentionally **not** a Home Assistant integration. The sibling project is [APsystems-EZ1-energyid.eu](https://github.com/bbruneel/APsystems-EZ1-energyid.eu) — follow the same layout, logging, token cache, deploy scripts, and CI style.

## Do not add

- Home Assistant as a dependency
- Anker cloud / app-login APIs
- Write/control commands unless the user explicitly asks (this app is read + report)
- Secrets, `.env`, serial numbers, or live tokens in git

## Architecture

```
scripts/test_battery.py          live Modbus probe (no EnergyID)
src/energyid_monitor/
  __main__.py                    python -m energyid_monitor
  battery.py                     env + YAML + snapshot + EnergyID payload mapping
  modbus_client.py               pymodbus 3.x async TCP client + decode helpers
  energyid.py                    /hello, token cache, webhook POST
  token_store.py                 SQLite token cache
  logging_config.py              loguru setup, token masking
  devices/solarbank_max_ac.yaml  register map (from official HA integration)
dbscripts/                       SQLite migrations
```

Cron/systemd runs `python -m energyid_monitor` as a **one-shot** every few minutes. Do not turn it into a long-running daemon unless asked.

## Modbus facts (verified live)

- Protocol: Modbus TCP, port **502**, `device_id=1`
- Input registers (FC 04) for sensors; holding registers (FC 03) for RW fields
- Values are **big-endian**; INT32/UINT32 use two registers; STRING is UTF-8 packed two bytes per register
- Energy registers use `gain: 10` (raw `61` → `6.1 kWh`)
- Signed battery/grid power: negative = charge / export (then split into two positive sensors)
- PV power = PCS PV (10002) + third-party PV (10004)
- Device PN register `0x8000` / 32768 decodes to `A17E2` for Solarbank Max AC
- ICMP ping may fail even when port 502 works

Register map source (keep YAML in sync if Anker updates it):

- https://github.com/anker-charging/ha-anker-solix-official
- `custom_components/anker_solix_official/config/*.yaml` (Solarbank Max AC file, hashed filename)

## EnergyID mapping

Docs: https://help.energyid.eu/en/developer/incoming-webhooks/

| Key | Type | Unit | Source |
| --- | --- | --- | --- |
| `ts` | unix seconds | — | `time.time()` |
| `pv` | cumulative | kWh | `pv_total_generation` |
| `bat` | cumulative | kWh | `cumulative_charge_energy` |
| `bat-i` | cumulative | kWh | `cumulative_discharge_energy` |
| `bat-soc` | gauge | % | `battery_soc` |
| `pwr` | gauge | kW | `grid_import_power / 1000` |
| `pwr-i` | gauge | kW | `grid_export_power / 1000` |

Webhook rules to preserve:

- Pass `authorization` and `x-twin-id` headers **exactly** as `/hello` returned them
- Accept HTTP 200 and 201 as success
- On 401, call `/hello` again and retry once
- If `/hello` returns `claimCode` / `claimUrl`, fail with a clear “claim this device” message
- Cache tokens in SQLite with a 1-hour expiry buffer
- Do not send more often than `webhookPolicy.uploadInterval`

## Commands

```bash
uv sync --extra dev
uv run pytest
uv run black .
uv run isort .
uv run flake8 . --max-line-length=127 --exclude=.venv,venv,__pycache__,.eggs,*.egg,dist,build

# Live battery only (no EnergyID credentials needed)
ENERGYID_CONSOLE_LOGGING=true uv run python scripts/test_battery.py

# Full EnergyID flow (needs a filled-in .env)
ENERGYID_CONSOLE_LOGGING=true uv run python -m energyid_monitor
```

Python: **3.11+** (local `.python-version` is 3.12). Package manager: **uv**.

## Conventions

- Match the APsystems repo: `src/` layout, `env.example`, `loguru`, `aiosqlite`, deploy/package/version scripts
- Keep functions typed; prefer small modules over a HA-style coordinator
- Never log raw bearer tokens (`logging_config.mask_token`)
- Decode/register tests belong in `tests/test_modbus_decode.py`; token cache tests in `tests/test_token_store.py`
- Do not hit the real battery or EnergyID from unit tests
- When adding registers, update the YAML, the EnergyID mapping (if a predefined key exists), tests, README, and this file
