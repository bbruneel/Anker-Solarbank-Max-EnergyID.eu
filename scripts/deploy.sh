#!/bin/bash
# Deployment script for EnergyID Monitor
# This script automates the installation process

set -e  # Exit on error

# Parse command-line arguments
INSTALL_DIR="/var/lib/energyid-monitor"
LOG_DIR="/var/log/energyid"

# Show usage information
show_usage() {
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  -i, --install-dir DIR    Installation directory (default: /var/lib/energyid-monitor)"
    echo "  -l, --log-dir DIR        Log directory (default: /var/log/energyid)"
    echo "  -h, --help               Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0                                    # Use default directories"
    echo "  $0 -i /opt/energyid -l /var/log/app  # Custom directories"
    echo "  $0 --install-dir ~/energyid          # Custom install directory only"
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -i|--install-dir)
            INSTALL_DIR="$2"
            shift 2
            ;;
        -l|--log-dir)
            LOG_DIR="$2"
            shift 2
            ;;
        -h|--help)
            show_usage
            exit 0
            ;;
        *)
            echo "Error: Unknown option: $1"
            echo ""
            show_usage
            exit 1
            ;;
    esac
done

CURRENT_USER="${SUDO_USER:-$USER}"
IS_ROOT=false

# Check if running as root
if [ "$(id -u)" -eq 0 ]; then
    IS_ROOT=true
fi

echo "==================================="
echo "EnergyID Monitor Deployment Script"
echo "==================================="
echo ""
echo "Installation directory: $INSTALL_DIR"
echo "Log directory: $LOG_DIR"
echo ""

# Check if running with appropriate permissions
if [ "$IS_ROOT" = true ]; then
    echo "⚠️  Running as root. Will create directories and set proper ownership."
else
    echo "ℹ️  Running as user: $CURRENT_USER"
    echo "You may need sudo privileges for some operations."
fi

# Check Python version
echo "Checking Python version..."
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 is not installed. Please install Python 3.11 or higher."
    exit 1
fi

PYTHON_VERSION=$(python3 --version | cut -d' ' -f2)
echo "✓ Found Python $PYTHON_VERSION"

# Check if Python version is 3.11 or higher
REQUIRED_VERSION="3.11"
if [ "$(printf '%s\n' "$REQUIRED_VERSION" "$PYTHON_VERSION" | sort -V | head -n1)" != "$REQUIRED_VERSION" ]; then
    echo "❌ Python 3.11 or higher is required. Found: $PYTHON_VERSION"
    exit 1
fi

# Create installation directory
echo ""
echo "Creating installation directory: $INSTALL_DIR"
if [ ! -d "$INSTALL_DIR" ]; then
    if [ "$IS_ROOT" = true ]; then
        mkdir -p "$INSTALL_DIR"
        chown "$CURRENT_USER:$CURRENT_USER" "$INSTALL_DIR"
    else
        sudo mkdir -p "$INSTALL_DIR"
        sudo chown "$CURRENT_USER:$CURRENT_USER" "$INSTALL_DIR"
    fi
    echo "✓ Directory created"
else
    echo "✓ Directory already exists"
fi

# Copy application files
echo ""
echo "Copying application files..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Copy the entire src directory to maintain src-layout
# This ensures pyproject.toml package discovery works correctly
if [ -d "$PROJECT_ROOT/src" ]; then
    cp -r "$PROJECT_ROOT/src" "$INSTALL_DIR/"
    echo "✓ Source directory copied"
else
    echo "❌ Error: src directory not found"
    exit 1
fi

# Copy pyproject.toml
if [ -f "$PROJECT_ROOT/pyproject.toml" ]; then
    cp "$PROJECT_ROOT/pyproject.toml" "$INSTALL_DIR/"
    echo "✓ pyproject.toml copied"
else
    echo "⚠️  Warning: pyproject.toml not found"
fi

# Copy uv.lock if it exists (for uv to work properly)
if [ -f "$PROJECT_ROOT/uv.lock" ]; then
    cp "$PROJECT_ROOT/uv.lock" "$INSTALL_DIR/"
    echo "✓ uv.lock copied"
fi

# Copy database scripts directory
if [ -d "$PROJECT_ROOT/dbscripts" ]; then
    cp -r "$PROJECT_ROOT/dbscripts" "$INSTALL_DIR/"
    echo "✓ Database scripts copied"
fi

if [ -f "$PROJECT_ROOT/env.example" ]; then
    cp "$PROJECT_ROOT/env.example" "$INSTALL_DIR/.env.example"
    echo "✓ env.example copied"
fi

echo "✓ Files copied"

# Create virtual environment
echo ""
echo "Creating Python virtual environment..."
cd "$INSTALL_DIR"
if [ ! -d ".venv" ]; then
    if command -v uv &> /dev/null; then
        echo "Using uv to create virtual environment..."
        uv venv .venv
    else
        python3 -m venv .venv
    fi
    echo "✓ Virtual environment created"
else
    echo "✓ Virtual environment already exists"
fi

# Install package in editable mode
echo ""
echo "Installing package in editable mode..."
source .venv/bin/activate

# Check if uv is available
if command -v uv &> /dev/null; then
    echo "Using uv to install package..."
    uv pip install -e .
else
    echo "Using pip to install package..."
    pip install --upgrade pip
    pip install -e .
fi

echo "✓ Package installed (energyid-monitor command available)"

# Create .env file if it doesn't exist
echo ""
if [ ! -f "$INSTALL_DIR/.env" ]; then
    if [ -f "$INSTALL_DIR/.env.example" ]; then
        cp "$INSTALL_DIR/.env.example" "$INSTALL_DIR/.env"
        echo "⚠️  Created .env file from .env.example"
        echo "⚠️  IMPORTANT: Edit $INSTALL_DIR/.env with your actual credentials!"
    else
        echo "⚠️  No .env.example found. Please create .env manually."
    fi
else
    echo "✓ .env file already exists"
fi

# Create log directory
echo ""
echo "Creating log directory: $LOG_DIR"
if [ ! -d "$LOG_DIR" ]; then
    if [ "$IS_ROOT" = true ]; then
        mkdir -p "$LOG_DIR"
        chown "$CURRENT_USER:$CURRENT_USER" "$LOG_DIR"
    else
        sudo mkdir -p "$LOG_DIR"
        sudo chown "$CURRENT_USER:$CURRENT_USER" "$LOG_DIR"
    fi
    echo "✓ Log directory created"
else
    echo "✓ Log directory already exists"
fi

# Create run script
echo ""
echo "Creating run script..."
cat > "$INSTALL_DIR/run.sh" << EOF
#!/bin/bash
cd $INSTALL_DIR
source .venv/bin/activate
python -m energyid_monitor >> $LOG_DIR/energyid.log 2>&1
EOF

chmod +x "$INSTALL_DIR/run.sh"
echo "✓ Run script created: $INSTALL_DIR/run.sh"

# Set proper permissions
echo ""
echo "Setting file permissions..."
chmod 600 "$INSTALL_DIR/.env" 2>/dev/null || true
if [ "$IS_ROOT" = true ]; then
    chown -R "$CURRENT_USER:$CURRENT_USER" "$INSTALL_DIR"
else
    chown -R "$CURRENT_USER:$CURRENT_USER" "$INSTALL_DIR" 2>/dev/null || sudo chown -R "$CURRENT_USER:$CURRENT_USER" "$INSTALL_DIR"
fi
echo "✓ Permissions set"

# Offer to set up crontab
echo ""
echo "==================================="
echo "Installation complete!"
echo "==================================="
echo ""
echo "Next steps:"
echo ""
echo "1. Edit the configuration file:"
echo "   nano $INSTALL_DIR/.env"
echo ""
echo "2. Test the application:"
echo "   cd $INSTALL_DIR"
echo "   source .venv/bin/activate"
echo "   python -m energyid_monitor"
echo ""
echo "3. Set up automatic execution (choose one):"
echo ""
echo "   Option A - Using crontab (simple):"
echo "   crontab -e"
echo "   Add this line:"
echo "   */5 * * * * $INSTALL_DIR/run.sh"
echo ""
echo "   Option B - Using systemd timer (recommended):"
echo "   See DEPLOYMENT.md for detailed instructions"
echo ""
echo "4. Monitor logs:"
echo "   tail -f $LOG_DIR/energyid.log"
echo ""
echo "For detailed documentation, see DEPLOYMENT.md"
echo ""
