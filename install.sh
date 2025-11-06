#!/bin/bash

# Vehicle Maintenance Tracker Installation Script for Proxmox LXC Container
# This script installs and configures the Vehicle Maintenance Tracker

set -e

echo "=========================================="
echo "Vehicle Maintenance Tracker Installation"
echo "=========================================="
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "ERROR: This script must be run as root"
    echo "Please run: sudo bash install.sh"
    exit 1
fi

# Get the directory where the script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
INSTALL_DIR="/opt/vehicle-tracker"
SERVICE_USER="vtracker"

echo "Step 1: Updating system packages..."
apt-get update -qq
apt-get upgrade -y -qq

echo ""
echo "Step 2: Installing Python 3 and dependencies..."
apt-get install -y -qq python3 python3-pip python3-venv

echo ""
echo "Step 3: Creating application user and directories..."
# Create user if doesn't exist
if ! id "$SERVICE_USER" &>/dev/null; then
    useradd -r -s /bin/false "$SERVICE_USER"
    echo "Created user: $SERVICE_USER"
fi

# Create installation directory
mkdir -p "$INSTALL_DIR"

# Copy application files
echo ""
echo "Step 4: Copying application files..."
cp -r "$SCRIPT_DIR"/* "$INSTALL_DIR/"

# Create virtual environment
echo ""
echo "Step 5: Setting up Python virtual environment..."
cd "$INSTALL_DIR"
python3 -m venv venv
source venv/bin/activate

# Install Python dependencies
echo ""
echo "Step 6: Installing Python packages..."
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

# Create necessary directories
echo ""
echo "Step 7: Creating upload and data directories..."
mkdir -p "$INSTALL_DIR/static/uploads/receipts"
mkdir -p "$INSTALL_DIR/static/uploads/vehicles"
mkdir -p "$INSTALL_DIR/instance"

# Set permissions
echo ""
echo "Step 8: Setting permissions..."
chown -R "$SERVICE_USER":"$SERVICE_USER" "$INSTALL_DIR"
chmod -R 755 "$INSTALL_DIR"

# Create systemd service
echo ""
echo "Step 9: Creating systemd service..."
cat > /etc/systemd/system/vehicle-tracker.service << EOF
[Unit]
Description=Vehicle Maintenance Tracker
After=network.target

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$INSTALL_DIR
Environment="PATH=$INSTALL_DIR/venv/bin"
ExecStart=$INSTALL_DIR/venv/bin/python app.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# Reload systemd and enable service
echo ""
echo "Step 10: Enabling and starting service..."
systemctl daemon-reload
systemctl enable vehicle-tracker.service
systemctl start vehicle-tracker.service

# Wait a moment for service to start
sleep 2

# Check service status
if systemctl is-active --quiet vehicle-tracker.service; then
    echo ""
    echo "=========================================="
    echo "Installation completed successfully!"
    echo "=========================================="
    echo ""
    echo "The Vehicle Maintenance Tracker is now running on:"
    echo ""
    echo "  http://$(hostname -I | awk '{print $1}'):5000"
    echo ""
    echo "You can also access it from other devices on your network."
    echo ""
    echo "Useful commands:"
    echo "  - Check status:  systemctl status vehicle-tracker"
    echo "  - Stop service:  systemctl stop vehicle-tracker"
    echo "  - Start service: systemctl start vehicle-tracker"
    echo "  - View logs:     journalctl -u vehicle-tracker -f"
    echo ""
    echo "Data is stored in: $INSTALL_DIR/instance/vehicle_tracker.db"
    echo "Uploads are in:    $INSTALL_DIR/static/uploads/"
    echo ""
else
    echo ""
    echo "ERROR: Service failed to start"
    echo "Check logs with: journalctl -u vehicle-tracker -xe"
    exit 1
fi
