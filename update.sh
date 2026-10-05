#!/bin/bash

# Vehicle Maintenance Tracker Update Script for Proxmox LXC Container
# This script updates an existing installation from GitHub

set -e

echo "========================================"
echo "Vehicle Maintenance Tracker Update"
echo "========================================"
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "ERROR: This script must be run as root"
    echo "Please run: sudo bash update.sh"
    exit 1
fi

INSTALL_DIR="/opt/vehicle-tracker"
SERVICE_USER="vtracker"
GITHUB_REPO="https://github.com/alreadyded1/VST.git"
TEMP_DIR="/tmp/vehicle-tracker-update-$(date +%Y%m%d%H%M%S)"

# Check if installation exists
if [ ! -d "$INSTALL_DIR" ]; then
    echo "ERROR: Installation not found at $INSTALL_DIR"
    echo "Please run install.sh first"
    exit 1
fi

echo "Step 1: Stopping service..."
systemctl stop vehicle-tracker.service

echo ""
echo "Step 2: Creating backup..."
BACKUP_DIR="/root/vehicle-tracker-backup-$(date +%Y%m%d%H%M%S)"
mkdir -p "$BACKUP_DIR"
cp -r "$INSTALL_DIR/instance" "$BACKUP_DIR/" 2>/dev/null || true
cp -r "$INSTALL_DIR/static/uploads" "$BACKUP_DIR/" 2>/dev/null || true
echo "Backup created at: $BACKUP_DIR"

echo ""
echo "Step 3: Downloading latest code from GitHub..."
# Install git if not present
if ! command -v git &> /dev/null; then
    echo "Git not found, installing..."
    apt-get update -qq
    apt-get install -y -qq git
fi

# Clone latest code to temp directory
git clone --depth 1 --branch claude/fix-fuel-up-date-display-0128p7VwVdzrdnkLi2zFC7wJ "$GITHUB_REPO" "$TEMP_DIR" 2>&1 | grep -v "Cloning into" || true

if [ ! -d "$TEMP_DIR" ]; then
    echo "ERROR: Failed to download updates from GitHub"
    systemctl start vehicle-tracker.service
    exit 1
fi

echo ""
echo "Step 4: Updating application files..."
# Copy new files, preserving database and uploads
cp "$TEMP_DIR/app.py" "$INSTALL_DIR/"
cp "$TEMP_DIR/wsgi.py" "$INSTALL_DIR/"
cp "$TEMP_DIR/gunicorn.conf.py" "$INSTALL_DIR/"
cp "$TEMP_DIR/requirements.txt" "$INSTALL_DIR/"
cp -r "$TEMP_DIR/templates" "$INSTALL_DIR/"
cp -r "$TEMP_DIR/static/css" "$INSTALL_DIR/static/"
cp -r "$TEMP_DIR/static/js" "$INSTALL_DIR/static/"
cp "$TEMP_DIR/static/favicon.svg" "$INSTALL_DIR/static/"

# Clean up temp directory
rm -rf "$TEMP_DIR"

echo ""
echo "Step 5: Updating Python dependencies..."
cd "$INSTALL_DIR"
source venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

echo ""
echo "Step 6: Setting permissions..."
chown -R "$SERVICE_USER":"$SERVICE_USER" "$INSTALL_DIR"
chmod -R 755 "$INSTALL_DIR"

# Ensure upload directories have correct permissions
chmod 755 "$INSTALL_DIR/static/uploads/receipts"
chmod 755 "$INSTALL_DIR/static/uploads/vehicles"
mkdir -p "$INSTALL_DIR/static/uploads/documents"
chown "$SERVICE_USER":"$SERVICE_USER" "$INSTALL_DIR/static/uploads/documents"
chmod 755 "$INSTALL_DIR/static/uploads/documents"

# Older installs ran Flask's development server (python app.py); switch the
# service to gunicorn. Rewriting the unit every time keeps it current.
cat > /etc/systemd/system/vehicle-tracker.service << EOF
[Unit]
Description=Vehicle Maintenance Tracker
After=network.target

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$INSTALL_DIR
Environment="PATH=$INSTALL_DIR/venv/bin"
# Production WSGI server; settings in gunicorn.conf.py
ExecStart=$INSTALL_DIR/venv/bin/gunicorn wsgi:app
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload

echo ""
echo "Step 7: Starting service..."
systemctl start vehicle-tracker.service

# Wait a moment for service to start
sleep 2

# Check service status
if systemctl is-active --quiet vehicle-tracker.service; then
    echo ""
    echo "========================================"
    echo "Update completed successfully!"
    echo "========================================"
    echo ""
    echo "The Vehicle Maintenance Tracker has been updated and is running."
    echo ""
    echo "Access your tracker at: http://$(hostname -I | awk '{print $1}'):5000"
    echo ""
    echo "Backup location: $BACKUP_DIR"
    echo ""
    echo "If you experience any issues, you can:"
    echo "  - Check logs: journalctl -u vehicle-tracker -f"
    echo "  - Restore from backup if needed"
    echo ""
else
    echo ""
    echo "ERROR: Service failed to start after update"
    echo "Check logs with: journalctl -u vehicle-tracker -xe"
    echo ""
    echo "To restore from backup:"
    echo "  systemctl stop vehicle-tracker"
    echo "  cp -r $BACKUP_DIR/instance/* $INSTALL_DIR/instance/"
    echo "  cp -r $BACKUP_DIR/uploads/* $INSTALL_DIR/static/uploads/"
    echo "  chown -R $SERVICE_USER:$SERVICE_USER $INSTALL_DIR"
    echo "  systemctl start vehicle-tracker"
    exit 1
fi
