# Anker Solarbank Max 2 EnergyID.eu

Python application that reads an [Anker SOLIX Solarbank Max AC](https://www.ankersolix.com/) over **local Modbus TCP** and posts the measurements to [EnergyID](https://www.energyid.eu/) every 5 minutes.

This is the Solarbank counterpart of [APsystems-EZ1-energyid.eu](https://github.com/bbruneel/APsystems-EZ1-energyid.eu). It does **not** require Home Assistant. The register map is taken from Anker's official integration, [ha-anker-solix-official](https://github.com/anker-charging/ha-anker-solix-official).

This requires:

- An Anker SOLIX Solarbank Max AC on your LAN with **Modbus TCP enabled** in the Anker app (see below).
- An account on https://www.energyid.eu/en
  - Check your account type to see if you are eligible to use webhooks and at which frequency.
  - An incoming webhook configured as described here: https://help.energyid.eu/en/developer/incoming-webhooks/
- A Linux runtime on your local network with internet access. A Raspberry Pi is fine.

## Enable Modbus TCP on the Solarbank

In the Anker app:

1. Open the **Devices** tab and select the Solarbank.
2. Tap **Settings** (gear icon).
3. Tap **Three-Party Control Settings**.
4. Enable the **Modbus TCP** toggle and note the IP address.

Default port is **502**. The device does not need to answer ICMP ping; a TCP connection to port 502 is enough.

## What is sent to EnergyID

Readings are mapped to EnergyID [predefined webhook keys](https://help.energyid.eu/en/developer/incoming-webhooks/):

| EnergyID key | Metric | Unit | Solarbank source |
| --- | --- | --- | --- |
| `pv` | Solar production | kWh (cumulative) | PV total generation |
| `bat` | Battery charging | kWh (cumulative) | Cumulative charge energy |
| `bat-i` | Battery discharging | kWh (cumulative) | Cumulative discharge energy |
| `bat-soc` | Battery state of charge | % | SoC register |
| `pwr` | Grid offtake power | kW | Grid import power |
| `pwr-i` | Grid injection power | kW | Grid export power |

## Quick start

### Test the battery connection first

From this repository, with the Solarbank reachable on the LAN:

```bash
cp env.example .env
# Edit SOLARBANK_IP_ADDRESS if needed (default: 192.168.50.100)

uv sync --extra dev
ENERGYID_CONSOLE_LOGGING=true uv run python scripts/test_battery.py
```

You should see model `A17E2`, firmware, SoC, live power, and lifetime energy totals. This does **not** call EnergyID.

### Deploy latest release

Download the latest release from GitHub Releases and extract the distribution package.

```bash
# This will create a folder called 'energyid-monitor' with all files inside
tar -xzf energyid-monitor-v1.0.0.tar.gz
cd energyid-monitor

# Use default directories
./scripts/deploy.sh

# Or specify custom directories
./scripts/deploy.sh --install-dir /opt/energyid --log-dir /var/log/app

# View all options
./scripts/deploy.sh --help
```

Then configure your credentials in the `.env` file (default: `/var/lib/energyid-monitor/.env`) and set up scheduled runs.

### EnergyID webhook setup

Configure an incoming webhook as described here: https://help.energyid.eu/en/developer/incoming-webhooks/

Step-by-step UI: https://app.energyid.eu/integrations/Webhook-In

Update your `.env` file with the provisioning key, secret, device id, and device name.

#### Hello endpoint

Use this to verify provisioning keys. If the device is not claimed yet, open the `claimUrl` from the response in a browser, then call `/hello` again to get the bearer token and twin id.

```bash
curl -X POST "https://hooks.energyid.eu/hello" \
  -H "Content-Type: application/json" \
  -H "X-Provisioning-Key: ENERGYID_KEY" \
  -H "X-Provisioning-Secret: ENERGYID_SECRET" \
  -d '{
    "deviceId": "ENERGYID_YOUR_DEVICE_ID",
    "deviceName": "ENERGYID_YOUR_DEVICE_NAME"
  }'
```

#### Webhook ingestion

Send a test payload using the `authorization` and `x-twin-id` headers from the hello response. `ts` is a Unix timestamp in seconds.

```bash
curl -w "%{response_code}" -X POST "https://hooks.energyid.eu/webhook-in" \
  -H "Content-Type: application/json" \
  -H "authorization: ENERGYID_BEARER_TOKEN" \
  -H "x-twin-id: ENERGYID_TWIN_ID" \
  -d '{
    "ts": 1764950877,
    "pv": 6.1,
    "bat": 5.4,
    "bat-i": 2.7,
    "bat-soc": 69
  }'
```

### Run the application

Once everything is set up:

```bash
/var/lib/energyid-monitor/run.sh
```

From a development checkout:

```bash
ENERGYID_CONSOLE_LOGGING=true uv run python -m energyid_monitor
```

### Configure scheduled runs

Either use crontab as described in [CRONTAB-SETUP.md](CRONTAB-SETUP.md) or systemd timers as explained in [DEPLOYMENT.md](DEPLOYMENT.md). Match the interval to your EnergyID plan (`uploadInterval` from `/hello`: 24 h free, 15 min Premium, 60 s real-time add-on).

## Other guides

- [DEPLOYMENT.md](DEPLOYMENT.md) — full deployment guide for Linux systems
- [CRONTAB-SETUP.md](CRONTAB-SETUP.md) — crontab configuration
- [DISTRIBUTION.md](DISTRIBUTION.md) — how to package and distribute this application
- [AGENTS.md](AGENTS.md) — project map for contributors and coding agents

## Token caching database

The application uses SQLite to cache EnergyID bearer tokens and avoid unnecessary API calls.

- Database location: `data/token.db` (created on first run)
- Schema migrations: SQL scripts in `dbscripts/` run automatically on first use
- Tokens are reused until they are within 1 hour of expiry
- HTTP 401 from the webhook triggers an immediate `/hello` refresh and one retry

View tokens:

```bash
sqlite3 data/token.db "SELECT twin_id, datetime(exp, 'unixepoch') as expires_at FROM tokens ORDER BY exp DESC LIMIT 5;"
```

Clear cache:

```bash
rm data/token.db
```

## Logging

The application uses loguru with daily rotation, gzip compression, and 30-day retention. Bearer tokens are masked in log output.

```bash
ENERGYID_LOG_LEVEL=DEBUG
ENERGYID_LOG_FILE=/path/to/your/logs/energyid.log
ENERGYID_CONSOLE_LOGGING=true
```

If the configured log directory is not writable, logs fall back to `~/.local/log/energyid/energyid.log`.

## Releases and versioning

Semantic versioning. Pushing a git tag `vX.Y.Z` creates a draft GitHub release with a distribution tarball.

```bash
git tag v1.0.0
git push origin v1.0.0
```

## License

MIT. Register map derived from [ha-anker-solix-official](https://github.com/anker-charging/ha-anker-solix-official) (also MIT).
