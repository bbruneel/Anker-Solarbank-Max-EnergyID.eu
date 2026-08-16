# Distribution Package Guide

This document explains how to package and transfer this application to another Linux system.

## Quick Start for Recipients

If you received this application package, follow these simple steps:

### 1. Extract the archive (if compressed)
```bash
# This will create a folder called 'energyid-monitor' with all files inside
tar -xzf energyid-monitor.tar.gz
cd energyid-monitor
```

### 2. Run the automated deployment script
```bash
# Use default directories
./scripts/deploy.sh

# Or specify custom directories if needed
./scripts/deploy.sh --install-dir /opt/energyid --log-dir /var/log/app

# View all options
./scripts/deploy.sh --help
```

### 3. Configure your credentials
```bash
# Edit the config file (adjust path if you used custom install directory)
nano /var/lib/energyid-monitor/.env
```

Fill in your EnergyID credentials and Solarbank IP address.

### 4. Test the installation
```bash
cd /var/lib/energyid-monitor
source .venv/bin/activate
python -m energyid_monitor
```

Or, if installed as a package:
```bash
energyid-monitor
```

### 5. Set up crontab
```bash
crontab -e
```

Add this line to run every 5 minutes:
```
*/5 * * * * /var/lib/energyid-monitor/run.sh
```

For more detailed instructions, see `DEPLOYMENT.md`.

---

## For Distributors: How to Package This Application

### Option 0: Automated Releases via GitHub Actions (Recommended for Maintainers)

This project includes a GitHub Actions workflow that automatically creates distribution packages and GitHub releases when you push a git tag.

**How it works:**

1. **Create and push a git tag:**
   ```bash
   git tag v1.0.0
   git push origin v1.0.0
   ```

2. **The workflow automatically:**
   - Extracts the version from the tag (removes 'v' prefix)
   - Runs all tests to ensure code quality
   - Updates version in `pyproject.toml` and `__init__.py`
   - Generates a changelog from git commits (between previous tag and current tag)
   - Creates a distribution package using `scripts/package.sh`
   - Creates a **draft** GitHub release with:
     - Generated changelog as release notes
     - Distribution package attached as release asset
     - Version as release name

3. **Review and publish:**
   - Go to the [GitHub Releases page](https://github.com/yourusername/energyid-monitor/releases)
   - Review the draft release
   - Edit release notes if needed
   - Publish the release when ready

**Benefits:**
- Automated testing before release
- Consistent version management
- Automatic changelog generation
- Distribution packages ready for download
- Draft releases allow manual review before publishing

**Note:** Releases are created as drafts to allow manual review before publishing. This ensures you can verify the changelog and package before making it public.

For more details about the release workflow, see `.github/workflows/release.yml`.

### Option 1: Create a tarball using package.sh (Manual)

Use the included automated packaging script:

```bash
# From the project directory
cd /path-to/AP-EasyPower-EnergieID

# Make the script executable (if needed)
chmod +x scripts/package.sh

# Create the distribution package
./scripts/package.sh 1.0.0
```

This will create `energyid-monitor-v1.0.0.tar.gz` with:
- All required files included
- Sensitive files automatically excluded (.env, .venv, etc.)
- Proper folder structure (files prefixed with 'energyid-monitor/')
- Version number in the filename
- Automatic verification and detailed output

**Manual alternative (if package.sh is not available):**
```bash
tar -czf energyid-monitor.tar.gz \
  --transform 's,^,energyid-monitor/,' \
  --exclude='.venv' --exclude='data' --exclude='__pycache__' \
  --exclude='.pytest_cache' --exclude='*.pyc' --exclude='.env' \
  --exclude='.git' --exclude='uv.lock' --exclude='dist' \
  src/ dbscripts/ scripts/ pyproject.toml env.example *.md
```

Transfer the created package to the target system via:
- SCP: `scp energyid-monitor.tar.gz user@target-host:/tmp/`
- USB drive
- Network share
- Email (if small enough)

### Option 2: Git Repository

If using version control:

```bash
# On source system
git init
git add .
git commit -m "Initial commit"

# Push to a repository (GitHub, GitLab, etc.)
git remote add origin https://github.com/yourusername/energyid-monitor.git
git push -u origin main

# On target system
git clone https://github.com/yourusername/energyid-monitor.git
cd energyid-monitor
./scripts/deploy.sh
```

### Option 3: Direct SCP Transfer

Transfer files directly without creating an archive:

```bash
# From source system
scp -r /home/bram/cursor-projects/AP-EasyPower-EnergieID user@target-host:/tmp/energyid-monitor

# On target system
cd /tmp/energyid-monitor
./scripts/deploy.sh
```

### Option 4: USB Drive or Network Share

1. Copy the entire project directory to USB drive or network share
2. On target system, copy from USB/share to a local directory
3. Run `./scripts/deploy.sh`

## Files to Include in Distribution

### Required Files (Must Include):
- `src/` - Source code directory containing the `energyid_monitor` package
- `dbscripts/` - Database migration scripts (contains `0001_create_tokens_table.sql`)
- `pyproject.toml` - Dependencies specification (includes version)
- `scripts/` - Deployment and packaging scripts (contains `deploy.sh`, `package.sh`, `version.sh`)
- `env.example` - Environment variable template
- `DEPLOYMENT.md` - Detailed deployment guide
- `README.md` - Project overview

### Optional Files:
- `scripts/package.sh` - Automated packaging script (for distributors)
- `scripts/version.sh` - Version management script (for distributors)
- `DISTRIBUTION.md` - This file (for distributors)
- `CRONTAB-SETUP.md` - Quick crontab setup guide
- `.python-version` - Python version specification
- `pytest.ini` - Test configuration
- `tests/` - Test directory containing unit tests

### Files to EXCLUDE:
- `.env` - Contains sensitive credentials! Never distribute!
- `.venv/` - Virtual environment (will be recreated)
- `data/` - Database directory (will be created automatically with cached tokens)
- `__pycache__/` - Python cache files
- `*.pyc`, `*.pyo` - Compiled Python files
- `.pytest_cache/` - Pytest cache files
- `.git/` - Git repository data
- `uv.lock` - Lock file (can be included but not required)

## Security Checklist Before Distribution

Before packaging for distribution, ensure:

- [ ] `.env` file is NOT included
- [ ] No hardcoded credentials in source code
- [ ] `env.example` contains only placeholder values
- [ ] No API keys or tokens in configuration files
- [ ] No personal IP addresses or hostnames in code

## Recipient Requirements

The target system must have:
- Python 3.11 or higher
- Internet access (for pip/uv and EnergyID API)
- Network access to the Solarbank (Modbus TCP port 502)
- sudo/root privileges for initial setup
- Basic Linux command-line knowledge

## Post-Distribution Support

After distributing, recipients may need help with:

1. **Configuration**: Obtaining EnergyID credentials
2. **Network**: Accessing the Solarbank on their LAN (Modbus TCP enabled in the Anker app)
3. **Troubleshooting**: Debugging connection issues
4. **Updates**: How to update the application

Refer them to:
- `README.md` for project overview
- `DEPLOYMENT.md` for detailed instructions
- Log files at `/var/log/energyid/energyid.log`

## Quick Verification Commands

Provide these to recipients for verification:

```bash
# Check Python version
python3 --version

# Test Modbus connectivity to the Solarbank
ENERGYID_CONSOLE_LOGGING=true python -m energyid_monitor.battery

# Test internet connectivity to EnergyID
curl -I https://hooks.energyid.eu

# Check if cron job is running
crontab -l

# View recent logs
tail -20 /var/log/energyid/energyid.log

# Manual test run
cd /var/lib/energyid-monitor && source .venv/bin/activate && python -m energyid_monitor
```

## Version Management

The project uses dynamic version management. Versions are automatically updated in:
- `pyproject.toml` - Package metadata
- `src/energyid_monitor/__init__.py` - Python module version

The `scripts/version.sh` script handles version updates and is automatically called by `package.sh`. See the "Package Script Details" section above for usage examples.

## Common Recipient Issues

### Issue: Permission Denied
**Solution**: Run `chmod +x scripts/deploy.sh` before executing

### Issue: Python version too old
**Solution**: Install Python 3.11+ or use pyenv/update system Python

### Issue: Can't reach Solarbank
**Solution**: Confirm Modbus TCP is enabled in the Anker app, the IP is correct, and port 502 is not blocked. ICMP ping may fail even when Modbus works.

### Issue: EnergyID API errors
**Solution**: Verify credentials in `.env` file, check internet connectivity

### Issue: Cron not running
**Solution**: Check cron service status: `systemctl status cron` or `systemctl status crond`

## Update/Upgrade Process

To update an existing installation:

1. Create new distribution package
2. On target system, backup current `.env`:
   ```bash
   cp /var/lib/energyid-monitor/.env ~/energyid-backup.env
   ```
3. Stop cron/systemd timer
4. Deploy new version
5. Restore `.env` file
6. Restart cron/systemd timer

## Package Script Details

The `scripts/package.sh` script usage:

```bash
./scripts/package.sh [version]
```

**Examples:**
- `./scripts/package.sh` - Auto-detects version from git tag, or uses default (1.0.0)
- `./scripts/package.sh 1.5.2` - Creates package with version 1.5.2
- `VERSION=2.0.0 ./scripts/package.sh` - Uses VERSION environment variable
- `./scripts/package.sh 2.0.0` - Creates package with version 2.0.0

The script automatically:
- Updates version in `pyproject.toml` and `src/energyid_monitor/__init__.py`
- Verifies it's in the correct directory
- Excludes sensitive and unnecessary files
- Creates proper folder structure
- Shows package size and contents
- Provides transfer instructions

### Version Management

The packaging script uses `scripts/version.sh` to manage project versions. The version is updated in:
- `pyproject.toml` - Package metadata
- `src/energyid_monitor/__init__.py` - Python module version

**Version Detection Priority:**
1. Command line argument: `./scripts/package.sh 1.0.0`
2. Environment variable: `VERSION=1.0.0 ./scripts/package.sh`
3. Git tag (auto-detect): `./scripts/package.sh` (reads from latest git tag)
4. Default fallback: `1.0.0`

**For GitHub Actions:**
The version script is compatible with GitHub Actions and will automatically detect versions from:
- `GITHUB_REF` environment variable (when triggered by tags)
- `VERSION` environment variable
- Git tags in the repository

The project includes a complete GitHub Actions workflow (`.github/workflows/release.yml`) that handles the entire release process automatically. When you push a git tag, it will:
- Extract the version from the tag
- Run tests
- Update version files
- Generate changelog
- Create distribution package
- Create a draft GitHub release

See "Option 0: Automated Releases via GitHub Actions" above for more information.

Or use the version script directly:
```bash
# Update version manually
./scripts/version.sh 1.0.0

# Or let it auto-detect from git
./scripts/version.sh
```
