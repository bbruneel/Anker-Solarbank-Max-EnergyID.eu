#!/bin/bash
# version.sh - Update project version in pyproject.toml and __init__.py
# Compatible with GitHub Actions and manual usage

set -e  # Exit on error

# Get the project root directory (parent of scripts/)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Function to extract version from git tag
get_version_from_git() {
    # Check if we're in a git repository
    if ! git -C "$PROJECT_ROOT" rev-parse --git-dir > /dev/null 2>&1; then
        return 1
    fi
    
    # Try to get version from GITHUB_REF (GitHub Actions)
    if [ -n "$GITHUB_REF" ]; then
        # GITHUB_REF format: refs/tags/v1.0.0 or refs/heads/main
        if [[ "$GITHUB_REF" =~ ^refs/tags/v?([0-9]+\.[0-9]+\.[0-9]+.*)$ ]]; then
            echo "${BASH_REMATCH[1]}"
            return 0
        fi
    fi
    
    # Try to get latest git tag
    local latest_tag=$(git -C "$PROJECT_ROOT" describe --tags --abbrev=0 2>/dev/null || echo "")
    if [ -n "$latest_tag" ]; then
        # Remove 'v' prefix if present
        echo "${latest_tag#v}"
        return 0
    fi
    
    return 1
}

# Function to validate version format (semver-like)
validate_version() {
    local version="$1"
    # Basic semver validation: x.y.z or x.y.z-suffix
    if [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[a-zA-Z0-9.-]+)?$ ]]; then
        return 0
    else
        return 1
    fi
}

# Determine version from various sources
# Priority 1: Command line argument
if [ -n "$1" ]; then
    VERSION="$1"
# Priority 2: VERSION environment variable (for CI/CD)
elif [ -n "${VERSION:-}" ]; then
    # VERSION already set from environment
    VERSION="${VERSION}"
# Priority 3: Git tag (for GitHub Actions or local git repos)
elif git_version=$(get_version_from_git); then
    VERSION="$git_version"
# Priority 4: Default fallback
else
    echo "❌ Error: No version specified and unable to determine from git tags"
    echo ""
    echo "Usage: $0 [VERSION]"
    echo ""
    echo "Examples:"
    echo "  $0 1.0.0                    # Set version to 1.0.0"
    echo "  VERSION=1.0.0 $0            # Use VERSION environment variable"
    echo "  $0                          # Auto-detect from git tag (requires git repo)"
    echo ""
    echo "For GitHub Actions, set VERSION environment variable or use git tags."
    exit 1
fi

# Validate version format
if ! validate_version "$VERSION"; then
    echo "❌ Error: Invalid version format: $VERSION"
    echo "Version must follow semver format: x.y.z or x.y.z-suffix (e.g., 1.0.0 or 1.0.0-beta.1)"
    exit 1
fi

PYPROJECT_TOML="$PROJECT_ROOT/pyproject.toml"
INIT_PY="$PROJECT_ROOT/src/energyid_monitor/__init__.py"

# Check if files exist
if [ ! -f "$PYPROJECT_TOML" ]; then
    echo "❌ Error: pyproject.toml not found at $PYPROJECT_TOML"
    exit 1
fi

if [ ! -f "$INIT_PY" ]; then
    echo "❌ Error: __init__.py not found at $INIT_PY"
    exit 1
fi

echo "========================================"
echo "EnergyID Monitor - Version Updater"
echo "========================================"
echo ""
echo "Updating version to: $VERSION"
echo ""

# Update pyproject.toml
echo "Updating pyproject.toml..."
if sed -i.bak "s/^version = \".*\"/version = \"$VERSION\"/" "$PYPROJECT_TOML"; then
    rm -f "$PYPROJECT_TOML.bak"
    echo "✓ Updated pyproject.toml"
else
    echo "❌ Error: Failed to update pyproject.toml"
    exit 1
fi

# Update __init__.py
echo "Updating __init__.py..."
if sed -i.bak "s/^__version__ = \".*\"/__version__ = \"$VERSION\"/" "$INIT_PY"; then
    rm -f "$INIT_PY.bak"
    echo "✓ Updated __init__.py"
else
    echo "❌ Error: Failed to update __init__.py"
    exit 1
fi

echo ""
echo "✓ Version updated successfully to $VERSION"
echo ""
echo "Files updated:"
echo "  - $PYPROJECT_TOML"
echo "  - $INIT_PY"
echo ""

