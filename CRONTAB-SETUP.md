# Quick Crontab Setup Guide

This is a simplified guide for setting up the EnergyID monitor to run every 5 minutes using crontab.

## Prerequisites

1. Application installed at `/var/lib/energyid-monitor`
2. Configuration file (`.env`) properly filled out
3. Application tested and working manually

## Step-by-Step Crontab Setup

### 1. Open crontab editor

```bash
crontab -e
```

If asked to choose an editor, select `nano` (easiest for beginners) or your preferred editor.

### 2. Add the cron job

Add this line at the end of the file:

```
*/5 * * * * /var/lib/energyid-monitor/run.sh
```

### 3. Save and exit

- In nano: Press `Ctrl+X`, then `Y`, then `Enter`
- In vim: Press `Esc`, type `:wq`, press `Enter`

### 4. Verify crontab is set

```bash
crontab -l
```

You should see your new line listed.

## Understanding the Cron Schedule

```
*/5 * * * * /var/lib/energyid-monitor/run.sh
│   │ │ │ │
│   │ │ │ └─── Day of week (0-7, both 0 and 7 are Sunday)
│   │ │ └───── Month (1-12)
│   │ └─────── Day of month (1-31)
│   └───────── Hour (0-23)
└─────────── Minute (0-59)
```

`*/5` in the minute field means "every 5 minutes"

## Alternative Schedules

If you want different intervals:

### Every 10 minutes:
```
*/10 * * * * /var/lib/energyid-monitor/run.sh
```

### Every 15 minutes:
```
*/15 * * * * /var/lib/energyid-monitor/run.sh
```

### Every hour at minute 0:
```
0 * * * * /var/lib/energyid-monitor/run.sh
```

### Every 30 minutes:
```
*/30 * * * * /var/lib/energyid-monitor/run.sh
```

### Only during daylight hours (6 AM to 8 PM, every 5 minutes):
```
*/5 6-20 * * * /var/lib/energyid-monitor/run.sh
```

## Monitoring and Troubleshooting

### Check if cron is running

```bash
systemctl status cron    # Ubuntu/Debian
systemctl status crond   # RHEL/CentOS
```

### View cron logs

```bash
# Application logs
tail -f /var/log/energyid/energyid.log

# System cron logs
grep CRON /var/log/syslog    # Ubuntu/Debian
grep CRON /var/log/cron      # RHEL/CentOS
```

### Test the cron script manually

```bash
/var/lib/energyid-monitor/run.sh
```

Then check the log:
```bash
tail /var/log/energyid/energyid.log
```

### Check cron execution history

```bash
grep energyid /var/log/syslog | tail -20    # Ubuntu/Debian
grep energyid /var/log/cron | tail -20      # RHEL/CentOS
```

## Common Issues

### Issue: Cron job not running

**Check 1**: Is cron service running?
```bash
systemctl status cron
```

**Check 2**: Is the script executable?
```bash
ls -l /var/lib/energyid-monitor/run.sh
# Should show: -rwxr-xr-x
```

**Fix**:
```bash
chmod +x /var/lib/energyid-monitor/run.sh
```

**Check 3**: View crontab for typos
```bash
crontab -l
```

### Issue: Script runs but produces errors

Check the application log:
```bash
tail -f /var/log/energyid/energyid.log
```

Common causes:
- Wrong `.env` configuration
- Network connectivity issues
- Inverter not reachable
- Python dependency issues

### Issue: Can't find output

Logs go to: `/var/log/energyid/energyid.log`

If this directory doesn't exist:
```bash
sudo mkdir -p /var/log/energyid
sudo chown $USER:$USER /var/log/energyid
```

### Issue: Database errors

The application uses SQLite to cache authentication tokens in `data/token.db`.

If you see database-related errors:
```bash
# Check if data directory exists and is writable
ls -ld /var/lib/energyid-monitor/data

# If needed, ensure proper permissions
chmod 755 /var/lib/energyid-monitor/data
```

The database and directory are created automatically on first run.

### Issue: Email notifications from cron

If cron sends error emails, add this at the top of your crontab:
```
MAILTO=""
```

Or set a specific email:
```
MAILTO="your.email@example.com"
```

## Stopping/Removing the Cron Job

### Temporarily disable

```bash
crontab -e
```

Comment out the line by adding `#` at the beginning:
```
# */5 * * * * /var/lib/energyid-monitor/run.sh
```

### Permanently remove

```bash
crontab -e
```

Delete the line entirely.

Or remove all cron jobs for current user:
```bash
crontab -r
```

## Advanced: Environment Variables in Cron

Cron runs with a minimal environment. If you need specific environment variables:

```bash
crontab -e
```

Add at the top:
```
PATH=/usr/local/bin:/usr/bin:/bin
SHELL=/bin/bash

*/5 * * * * /var/lib/energyid-monitor/run.sh
```

## Verification Checklist

After setup, verify:

- [ ] Crontab entry is correct: `crontab -l`
- [ ] Script is executable: `ls -l /var/lib/energyid-monitor/run.sh`
- [ ] Log directory exists: `ls -ld /var/log/energyid/`
- [ ] Manual run works: `/var/lib/energyid-monitor/run.sh`
- [ ] Wait 5 minutes and check logs: `tail /var/log/energyid/energyid.log`
- [ ] Verify new entries appear every 5 minutes

## Quick Reference Card

```bash
# Edit crontab
crontab -e

# List crontab
crontab -l

# Remove crontab
crontab -r

# Test manual run
/var/lib/energyid-monitor/run.sh

# View logs
tail -f /var/log/energyid/energyid.log

# Check cron service
systemctl status cron

# Search cron execution in system logs
grep CRON /var/log/syslog | grep energyid
```

## Need More Control?

If you need:
- Better logging and monitoring
- Failure notifications
- Service management
- Restart on failure

Consider using **systemd timers** instead. See `DEPLOYMENT.md` for instructions.
