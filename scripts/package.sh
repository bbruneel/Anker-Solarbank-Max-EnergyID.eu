#!/bin/bash
# package.sh - Automated packaging script for distribution

set -e  # Exit on error

# Get the project root directory (parent of scripts/)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Update version in project files before packaging
# Priority: command line arg > VERSION env var > auto-detect from git > default
echo "Updating project version..."
if [ -n "$1" ]; then
    # Version provided as command line argument
    "$SCRIPT_DIR/version.sh" "$1"
elif [ -n "${VERSION:-}" ]; then
    # Version provided as environment variable
    "$SCRIPT_DIR/version.sh" "$VERSION"
else
    # Try to auto-detect from git or use default
    if ! "$SCRIPT_DIR/version.sh" 2>/dev/null; then
        echo "⚠️  Warning: Could not auto-detect version, using default 1.0.0"
        "$SCRIPT_DIR/version.sh" "1.0.0"
    fi
fi

# Read the actual version from pyproject.toml to ensure consistency
VERSION=$(grep -E '^version = ' "$PROJECT_ROOT/pyproject.toml" | sed -E 's/^version = "([^"]+)"/\1/')

PACKAGE_NAME="energyid-monitor-v${VERSION}.tar.gz"

# Create dist directory if it doesn't exist
DIST_DIR="$PROJECT_ROOT/dist"
mkdir -p "$DIST_DIR"

PACKAGE_PATH="$DIST_DIR/$PACKAGE_NAME"

echo ""
echo "========================================"
echo "EnergyID Monitor - Distribution Packager"
echo "========================================"
echo ""
echo "Creating package: $PACKAGE_NAME"
echo "Version: $VERSION"
echo ""

# Check if we're in the right directory structure
if [ ! -f "$PROJECT_ROOT/src/energyid_monitor/energyid.py" ]; then
    echo "❌ Error: src/energyid_monitor/energyid.py not found. Please run this script from the project directory."
    exit 1
fi

# Change to project root for tar operations
cd "$PROJECT_ROOT"

# Create the tarball
tar -czf "$PACKAGE_PATH" \
  --exclude='.venv' \
  --exclude='data' \
  --exclude='__pycache__' \
  --exclude='.pytest_cache' \
  --exclude='*.pyc' \
  --exclude='*.pyo' \
  --exclude='.env' \
  --exclude='.git*' \
  --exclude='uv.lock' \
  --exclude='*.tar.gz' \
  --exclude='dist' \
  --exclude='tests' \
  --transform 's,^,energyid-monitor/,' \
  src/energyid_monitor \
  dbscripts \
  pyproject.toml \
  scripts/deploy.sh \
  scripts/version.sh \
  scripts/test_battery.py \
  env.example \
  DEPLOYMENT.md \
  CRONTAB-SETUP.md \
  DISTRIBUTION.md \
  README.md

if [ $? -eq 0 ]; then
    echo "✓ Package created successfully!"
    echo ""
    echo "Package details:"
    echo "  File: $PACKAGE_PATH"
    echo "  Size: $(du -h "$PACKAGE_PATH" | cut -f1)"
    echo ""
    echo "Contents:"
    tar -tzf "$PACKAGE_PATH"
    echo ""
    echo "Next steps:"
    echo "1. Transfer $PACKAGE_NAME to target system"
    echo "2. Extract: tar -xzf $PACKAGE_NAME"
    echo "3. Run: cd energyid-monitor && ./scripts/deploy.sh"
    echo ""
    echo "See DISTRIBUTION.md for more transfer options."
else
    echo "❌ Error creating package"
    exit 1
fi
