# Deployment Guide

This guide explains how to deploy the EnergyID application on a Linux system and configure it to run automatically every 5 minutes.

## Prerequisites

- Linux system (Ubuntu, Debian, RHEL, etc.)
- Python 3.11 or higher
- Network access to your Anker Solarbank (Modbus TCP port 502)
- Internet access to EnergyID API
- Root or sudo access for initial setup

## Quick Start (Automated Deployment)

For a quick automated setup, use the provided `scripts/deploy.sh` script:

```bash
# Make the script executable
chmod +x scripts/deploy.sh

# Run the deployment script with default directories
./scripts/deploy.sh

# Or specify custom directories
./scripts/deploy.sh --install-dir /opt/energyid --log-dir /var/log/app

# View all options
./scripts/deploy.sh --help
```

### Command-Line Options

The `deploy.sh` script accepts the following options:
- `-i, --install-dir DIR` - Installation directory (default: `/var/lib/energyid-monitor`)
- `-l, --log-dir DIR` - Log directory (default: `/var/log/energyid`)
- `-h, --help` - Show help message

The script will automatically:
- ✅ Check Python version (requires 3.11+)
- ✅ Create `/var/lib/energyid-monitor` directory
- ✅ Copy all application files
- ✅ Set up Python virtual environment
- ✅ Install dependencies (using `uv` if available, otherwise `pip`)
- ✅ Create `.env` configuration file from template
- ✅ Create log directory at `/var/log/energyid`
- ✅ Create `run.sh` wrapper script
- ✅ Set proper file permissions

**After running the script, you still need to:**

1. **Edit the .env file with your actual credentials:**
   ```bash
   nano /var/lib/energyid-monitor/.env
   ```

2. **Test the application:**
   ```bash
   cd /var/lib/energyid-monitor
   source .venv/bin/activate
   python -m energyid_monitor
   ```
   Or, if installed as a package:
   ```bash
   energyid-monitor
   ```

3. **Set up automatic execution** (see sections below for crontab or systemd timer options)

**For detailed step-by-step instructions or if you prefer manual installation, see the Manual Installation Steps below.**

---

## Manual Installation Steps

If you prefer to install manually or want to understand each step in detail, follow these instructions.

**Note:** The automated `deploy.sh` script performs steps 3-8 automatically. These manual steps are provided for those who want full control or need to customize the installation.

### 1. Install Python, pip, and SQLite (if not already installed)

```bash
# Ubuntu/Debian
sudo apt update
sudo apt install python3 python3-pip python3-venv sqlite3 -y

# RHEL/CentOS/Rocky
sudo dnf install python3 python3-pip sqlite -y
```

**Note**: SQLite3 is typically pre-installed on most Linux systems, but we include it here for completeness. The application uses SQLite to cache authentication tokens.

### 2. Install uv (recommended) or use pip

Option A: Install uv (faster, better dependency management):
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.cargo/env
```

Option B: Use pip (traditional method):
```bash
# Will use pip to install dependencies in step 4
```

### 3. Create application directory and copy files

```bash
# Create application directory
sudo mkdir -p /var/lib/energyid-monitor
sudo chown $USER:$USER /var/lib/energyid-monitor

# Copy all application files to the deployment directory
cd /var/lib/energyid-monitor
# (Upload/copy your files here: src/, pyproject.toml, dbscripts/, scripts/, etc.)
```

### 4. Install dependencies

Using uv (recommended):
```bash
cd /var/lib/energyid-monitor
uv venv
source .venv/bin/activate
uv pip install -e .
```

This installs the package in editable mode, allowing it to be run as `python -m energyid_monitor` or `energyid-monitor`.

Using pip:
```bash
cd /var/lib/energyid-monitor
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

This installs the package in editable mode from `pyproject.toml`, which includes all dependencies and allows running as `python -m energyid_monitor` or `energyid-monitor`.

### 5. Configure environment variables

```bash
cd /var/lib/energyid-monitor
cp .env.example .env
nano .env  # or vim, vi, etc.
```

Fill in your actual values:
- `ENERGYID_KEY` - Your EnergyID provisioning key
- `ENERGYID_SECRET` - Your EnergyID provisioning secret
- `ENERGYID_YOUR_DEVICE_ID` - Your device ID
- `ENERGYID_YOUR_DEVICE_NAME` - Your device name
- `ENERGYID_HELLO_URL` - EnergyID hello endpoint URL
- `ENERGYID_WEBHOOK_URL` - EnergyID webhook endpoint URL
- `SOLARBANK_IP_ADDRESS` - Your Solarbank's LAN IP address
- `SOLARBANK_MODBUS_PORT` - Modbus TCP port (default `502`)
- `SOLARBANK_DEVICE_ID` - Modbus unit/device id (default `1`)

**Optional logging configuration:**
- `ENERGYID_LOG_LEVEL` - Log level (default: `INFO`, options: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`)
- `ENERGYID_LOG_FILE` - Log file path (default: `/var/log/energyid/energyid.log`)
- `ENERGYID_CONSOLE_LOGGING` - Enable console logging to stdout (default: `false`, set to `true` to enable)

### 6. Test the application

```bash
cd /var/lib/energyid-monitor
source .venv/bin/activate
python -m energyid_monitor
```

Or, if installed as a package:
```bash
energyid-monitor
```

You should see structured log output showing:
- Database initialization (first run only)
- Reading Solarbank Modbus registers (SoC, charge/discharge energy, PV total)
- Token caching information (cached or new token)
- Webhook response

The application uses loguru for structured logging with:
- Automatic log rotation (daily at midnight)
- Compression of rotated logs (gzip)
- 30-day retention (old logs automatically deleted)
- Bearer tokens are automatically masked in logs for security

The application will automatically create:
- A `data/` directory with a SQLite database (`token.db`) to cache authentication tokens
- The log directory if it doesn't exist (default: `/var/log/energyid/`)

If you see errors, check your configuration and network connectivity.

### 7. Create a wrapper script

Create a script that handles the virtual environment activation:

Create a script that handles the virtual environment activation:
```bash
cat > /var/lib/energyid-monitor/run.sh << 'EOF'
#!/bin/bash
cd /var/lib/energyid-monitor
source .venv/bin/activate
python -m energyid_monitor >> /var/log/energyid/energyid.log 2>&1
EOF

chmod +x /var/lib/energyid-monitor/run.sh
```

**Note:** The application uses loguru for logging, which automatically writes to the configured log file. The script above doesn't need to redirect output since loguru handles file logging internally. However, if you want to also capture stdout/stderr separately, you can still add `>> /var/log/energyid/energyid.log 2>&1` to the end.

### 8. Create log directory

```bash
sudo mkdir -p /var/log/energyid
sudo chown $USER:$USER /var/log/energyid
```

### 9. Set up crontab to run every 5 minutes

**Note:** This step is required whether you used the automated `deploy.sh` script or manual installation.

```bash
crontab -e
```

Add this line:
```
*/5 * * * * /var/lib/energyid-monitor/run.sh
```

This will run the script every 5 minutes.

### 10. Verify crontab is working

Wait 5 minutes and check the log:
```bash
tail -f /var/log/energyid/energyid.log
```

You should see new entries every 5 minutes.

## Alternative: Using systemd timer (recommended for better control)

**Note:** This is an alternative to the crontab method above (step 9). Choose either crontab OR systemd timer, not both. This works whether you used the automated `deploy.sh` script or manual installation.

Instead of cron, you can use systemd timers for better logging and control:

### Create systemd service file

```bash
sudo nano /etc/systemd/system/energyid.service
```

Content:
```ini
[Unit]
Description=EnergyID Data Collector
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=YOUR_USERNAME
WorkingDirectory=/var/lib/energyid-monitor
Environment="PATH=/var/lib/energyid-monitor/.venv/bin:/usr/local/bin:/usr/bin:/bin"
ExecStart=/var/lib/energyid-monitor/.venv/bin/python -m energyid_monitor
StandardOutput=append:/var/log/energyid/energyid.log
StandardError=append:/var/log/energyid/energyid.log

[Install]
WantedBy=multi-user.target
```

Replace `YOUR_USERNAME` with your actual username.

### Create systemd timer file

```bash
sudo nano /etc/systemd/system/energyid.timer
```

Content:
```ini
[Unit]
Description=Run EnergyID Data Collector every 5 minutes
Requires=energyid.service

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
AccuracySec=1s

[Install]
WantedBy=timers.target
```

### Enable and start the timer

```bash
sudo systemctl daemon-reload
sudo systemctl enable energyid.timer
sudo systemctl start energyid.timer
```

### Check timer status

```bash
sudo systemctl status energyid.timer
sudo systemctl list-timers energyid.timer
```

### View logs

```bash
# Real-time logs from systemd journal
sudo journalctl -u energyid.service -f

# Or check the log file (loguru writes directly to file)
tail -f /var/log/energyid/energyid.log

# View logs with log level filtering
grep "INFO" /var/log/energyid/energyid.log
```

## Troubleshooting

### Script doesn't run
- Check crontab syntax: `crontab -l`
- Check script permissions: `ls -la /var/lib/energyid-monitor/run.sh`
- Check log files: `tail -f /var/log/energyid/energyid.log`

### Network errors
- Verify Solarbank Modbus: `ENERGYID_CONSOLE_LOGGING=true python -m energyid_monitor.battery`
  (ICMP ping may fail even when port 502 is open)
- Check internet connectivity: `curl -I https://hooks.energyid.eu`
- Verify firewall rules

### Python errors
- Ensure virtual environment is activated in the wrapper script
- Check Python version: `python --version` (should be 3.11+)
- Reinstall dependencies if needed

### Permission errors
- Check log directory permissions: `ls -la /var/log/energyid/`
- Ensure the user in systemd service file has proper permissions

## Logging Configuration

The application uses loguru for structured logging with automatic rotation, compression, and retention.

### Log Features

- **Automatic rotation**: Logs rotate daily at midnight
- **Compression**: Rotated logs are automatically compressed with gzip
- **Retention**: Old logs are automatically deleted after 30 days
- **Structured format**: Logs include timestamps, log levels, module/function names, and line numbers
- **Security**: Bearer tokens are automatically masked in all log entries

### Configuring Log Level

Set the log level via environment variable or `.env` file:

```bash
# In .env file
ENERGYID_LOG_LEVEL=DEBUG

# Or as environment variable
export ENERGYID_LOG_LEVEL=DEBUG
python -m energyid_monitor
```

Available log levels (from most verbose to least):
- `DEBUG` - Detailed information for debugging (includes full token details, response bodies)
- `INFO` - General informational messages (default)
- `WARNING` - Warning messages (connection issues, missing data)
- `ERROR` - Error messages (exceptions, failures)
- `CRITICAL` - Critical errors only

### Configuring Log File Location

Change the log file location via environment variable or `.env` file:

```bash
# In .env file
ENERGYID_LOG_FILE=/path/to/your/logs/energyid.log

# Or as environment variable
export ENERGYID_LOG_FILE=/home/user/logs/energyid.log
python -m energyid_monitor
```

**Default location**: `/var/log/energyid/energyid.log`

The log directory will be created automatically if it doesn't exist.

**Permission fallback**: If the application doesn't have permission to create the log directory at the specified location (e.g., when running as a non-root user), it will automatically fallback to `~/.local/log/energyid/energyid.log` (user's home directory). A warning message will be printed to stderr indicating the fallback location is being used. This allows the application to run during development without requiring elevated permissions.

### Configuring Console Logging

By default, logs are only written to the log file. To also output logs to stdout (useful for development), enable console logging:

```bash
# In .env file
ENERGYID_CONSOLE_LOGGING=true

# Or as environment variable
export ENERGYID_CONSOLE_LOGGING=true
python -m energyid_monitor
```

**Default**: `false` (console logging disabled)

When enabled, logs will appear both in the log file and on stdout (with colors for better readability). In production environments, it's recommended to keep console logging disabled (`false`) to avoid duplicate log output.

### Viewing Logs

```bash
# View recent log entries
tail -f /var/log/energyid/energyid.log

# View with line numbers
tail -n 100 /var/log/energyid/energyid.log | cat -n

# Search for specific patterns
grep "ERROR" /var/log/energyid/energyid.log
grep "Webhook-in response" /var/log/energyid/energyid.log

# View rotated/compressed logs
zcat /var/log/energyid/energyid.log.2024-01-15.gz
```

**Note:** Log rotation is handled automatically by loguru, so you don't need to configure external log rotation tools like logrotate. However, if you prefer using logrotate for additional control, you can still set it up, but be aware that loguru's rotation will also be active.

## Monitoring and Maintenance

### Log rotation

**Note:** The application uses loguru which handles log rotation automatically (daily at midnight, with 30-day retention and gzip compression). You typically don't need to set up external log rotation.

However, if you want additional log management with logrotate, you can create this configuration:

```bash
sudo nano /etc/logrotate.d/energyid
```

Content:
```
/var/log/energyid/*.log {
    daily
    rotate 7
    compress
    delaycompress
    missingok
    notifempty
    create 0644 YOUR_USERNAME YOUR_USERNAME
}
```

**Warning:** Using both loguru's rotation and logrotate together may cause conflicts. It's recommended to use one or the other.

### Check if data is being sent

Monitor the logs for successful webhook responses:
```bash
grep "Webhook-in response" /var/log/energyid/energyid.log
```

### Update the application

```bash
cd /var/lib/energyid-monitor
git pull  # if using git
source .venv/bin/activate
uv pip install -e .  # or pip install -e . for editable install
```

If using systemd timer, restart it:
```bash
sudo systemctl restart energyid.timer
```

## Security Considerations

1. **Protect .env file**: 
   ```bash
   chmod 600 /var/lib/energyid-monitor/.env
   ```

2. **Run as non-root user**: The instructions above use a regular user account.

3. **Firewall**: Ensure only necessary ports are open.

4. **Regular updates**: Keep the system and Python packages updated.

## Uninstallation

If using cron:
```bash
crontab -e  # Remove the energyid line
```

If using systemd:
```bash
sudo systemctl stop energyid.timer
sudo systemctl disable energyid.timer
sudo rm /etc/systemd/system/energyid.service
sudo rm /etc/systemd/system/energyid.timer
sudo systemctl daemon-reload
```

Remove files:
```bash
sudo rm -rf /var/lib/energyid-monitor
sudo rm -rf /var/log/energyid
sudo rm /etc/logrotate.d/energyid
```
