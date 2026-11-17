#!/bin/bash

# Vehicle Maintenance Tracker Update Script for Proxmox LXC Container
# This script updates an existing installation from GitHub.
#
# Your settings are kept: the port, listen address, worker count and
# signing keys live in /etc/vehicle-tracker.env, which this script never
# overwrites. Settings an older version kept elsewhere (a port edited into
# gunicorn.conf.py, Environment= lines added to the service file) are moved
# there before those files are replaced.

set -e

INSTALL_DIR="${INSTALL_DIR:-/opt/vehicle-tracker}"
SERVICE_USER="vtracker"
SERVICE_FILE="${SERVICE_FILE:-/etc/systemd/system/vehicle-tracker.service}"
ENV_FILE="${ENV_FILE:-/etc/vehicle-tracker.env}"
GITHUB_REPO="https://github.com/alreadyded1/VST.git"
GITHUB_BRANCH="claude/fix-fuel-up-date-display-0128p7VwVdzrdnkLi2zFC7wJ"

ENV_TEMPLATE='# Vehicle Maintenance Tracker settings.
# Updates never change this file. Remove the # from a line to use it, then
# run: systemctl restart vehicle-tracker
#
# Port and address to listen on, and how many worker processes to run
#VT_PORT=5000
#VT_HOST=0.0.0.0
#VT_WORKERS=2
#
# Keys that sign logins and API tokens. Normally generated on first start
# and kept in instance/; set them here only to manage them yourself.
#SECRET_KEY=
#JWT_SECRET_KEY='

# Is NAME set (uncommented) in the env file?
env_has() {
    grep -qE "^[[:space:]]*$1=" "$ENV_FILE" 2>/dev/null
}

# Create the env file if it doesn't exist yet. Readable by root only, as it
# may hold signing keys; systemd reads it before dropping privileges.
ensure_env_file() {
    if [ ! -f "$ENV_FILE" ]; then
        printf '%s\n' "$ENV_TEMPLATE" > "$ENV_FILE"
        chmod 600 "$ENV_FILE"
        echo "Created settings file: $ENV_FILE"
    fi
}

# Move settings an older version kept in files that updates replace into the
# env file. Anything already set in the env file wins. Safe to run every time.
carry_over_settings() {
    ensure_env_file
    local conf="$INSTALL_DIR/gunicorn.conf.py"

    # A port or address edited into gunicorn.conf.py, as the old README said
    if [ -f "$conf" ]; then
        local bind
        bind=$(sed -nE "s/^bind[[:space:]]*=[[:space:]]*['\"]([^'\"]+)['\"].*/\1/p" "$conf" | head -n 1)
        if [ -n "$bind" ] && [ "$bind" != "0.0.0.0:5000" ]; then
            local host="${bind%:*}" port="${bind##*:}"
            if [ "$port" != "$bind" ] && ! env_has VT_PORT && [ "$port" != "5000" ]; then
                echo "VT_PORT=$port" >> "$ENV_FILE"
                echo "Kept your port setting: VT_PORT=$port"
            fi
            if [ "$port" != "$bind" ] && ! env_has VT_HOST && [ "$host" != "0.0.0.0" ]; then
                echo "VT_HOST=$host" >> "$ENV_FILE"
                echo "Kept your address setting: VT_HOST=$host"
            fi
        fi
        local workers
        workers=$(sed -nE 's/^workers[[:space:]]*=[[:space:]]*([0-9]+).*/\1/p' "$conf" | head -n 1)
        if [ -n "$workers" ] && [ "$workers" != "2" ] && ! env_has VT_WORKERS; then
            echo "VT_WORKERS=$workers" >> "$ENV_FILE"
            echo "Kept your worker setting: VT_WORKERS=$workers"
        fi
    fi

    # Environment= lines added to the service file by hand (signing keys and
    # the like). PATH is the script's own and is left out.
    if [ -f "$SERVICE_FILE" ]; then
        local line setting name
        while IFS= read -r line; do
            setting="${line#Environment=}"
            setting="${setting#\"}"
            setting="${setting%\"}"
            name="${setting%%=*}"
            if [ -n "$name" ] && [ "$name" != "$setting" ] && [ "$name" != "PATH" ] && ! env_has "$name"; then
                echo "$setting" >> "$ENV_FILE"
                echo "Kept your service setting: $name"
            fi
        done < <(grep -E '^[[:space:]]*Environment=' "$SERVICE_FILE" || true)
    fi
}

write_service_file() {
    cat > "$SERVICE_FILE" << EOF
[Unit]
Description=Vehicle Maintenance Tracker
After=network.target

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$INSTALL_DIR
Environment="PATH=$INSTALL_DIR/venv/bin"
# Your settings (port, keys): kept across updates
EnvironmentFile=-$ENV_FILE
# Production WSGI server; settings in gunicorn.conf.py
ExecStart=$INSTALL_DIR/venv/bin/gunicorn wsgi:app
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
}

main() {
    echo "========================================"
    echo "Vehicle Maintenance Tracker Update"
    echo "========================================"
    echo ""

    if [ "$EUID" -ne 0 ]; then
        echo "ERROR: This script must be run as root"
        echo "Please run: sudo bash update.sh"
        exit 1
    fi

    if [ ! -d "$INSTALL_DIR" ]; then
        echo "ERROR: Installation not found at $INSTALL_DIR"
        echo "Please run install.sh first"
        exit 1
    fi

    # Download first, while the app keeps running. When this copy of the
    # script is older than the one just downloaded, hand over to the new one
    # so its steps are the ones that run.
    local temp_dir="${VT_UPDATE_SOURCE:-}"
    if [ -z "$temp_dir" ]; then
        echo "Step 1: Downloading latest code from GitHub..."
        if ! command -v git &> /dev/null; then
            echo "Git not found, installing..."
            apt-get update -qq
            apt-get install -y -qq git
        fi
        temp_dir="/tmp/vehicle-tracker-update-$(date +%Y%m%d%H%M%S)"
        if ! git clone -q --depth 1 --branch "$GITHUB_BRANCH" "$GITHUB_REPO" "$temp_dir"; then
            echo "ERROR: Failed to download updates from GitHub. Nothing was changed."
            exit 1
        fi
        if ! cmp -s "$temp_dir/update.sh" "${BASH_SOURCE[0]}"; then
            echo "Using the newer update script that came with this version..."
            VT_UPDATE_SOURCE="$temp_dir" exec bash "$temp_dir/update.sh"
        fi
    fi

    echo ""
    echo "Step 2: Stopping service..."
    systemctl stop vehicle-tracker.service

    echo ""
    echo "Step 3: Creating backup..."
    local backup_dir
    backup_dir="/root/vehicle-tracker-backup-$(date +%Y%m%d%H%M%S)"
    mkdir -p "$backup_dir"
    cp -r "$INSTALL_DIR/instance" "$backup_dir/" 2>/dev/null || true
    cp -r "$INSTALL_DIR/static/uploads" "$backup_dir/" 2>/dev/null || true
    cp "$INSTALL_DIR/gunicorn.conf.py" "$backup_dir/" 2>/dev/null || true
    cp "$SERVICE_FILE" "$backup_dir/" 2>/dev/null || true
    echo "Backup created at: $backup_dir"

    echo ""
    echo "Step 4: Keeping your settings..."
    carry_over_settings
    echo "Settings are in $ENV_FILE"

    echo ""
    echo "Step 5: Updating application files..."
    # Database, signing keys and uploads are left alone
    cp "$temp_dir/app.py" "$temp_dir/wsgi.py" "$temp_dir/gunicorn.conf.py" "$temp_dir/requirements.txt" "$INSTALL_DIR/"
    cp "$temp_dir/update.sh" "$temp_dir/install.sh" "$INSTALL_DIR/"
    cp "$temp_dir"/*.md "$INSTALL_DIR/" 2>/dev/null || true
    cp -r "$temp_dir/templates" "$INSTALL_DIR/"
    cp -r "$temp_dir/static/css" "$INSTALL_DIR/static/"
    cp -r "$temp_dir/static/js" "$INSTALL_DIR/static/"
    cp "$temp_dir/static/favicon.svg" "$INSTALL_DIR/static/"
    rm -rf "$temp_dir"

    echo ""
    echo "Step 6: Updating Python dependencies..."
    cd "$INSTALL_DIR"
    "$INSTALL_DIR/venv/bin/pip" install --quiet --upgrade pip
    "$INSTALL_DIR/venv/bin/pip" install --quiet -r requirements.txt

    echo ""
    echo "Step 7: Setting permissions..."
    mkdir -p "$INSTALL_DIR/static/uploads/receipts" "$INSTALL_DIR/static/uploads/vehicles" \
             "$INSTALL_DIR/static/uploads/documents"
    chown -R "$SERVICE_USER":"$SERVICE_USER" "$INSTALL_DIR"
    chmod -R 755 "$INSTALL_DIR"

    # The service file is rewritten every time to keep it current; anything
    # you need to set goes in the env file, which it loads. For other
    # systemd changes, use: systemctl edit vehicle-tracker (kept as well).
    write_service_file
    systemctl daemon-reload

    echo ""
    echo "Step 8: Starting service..."
    systemctl start vehicle-tracker.service
    sleep 2

    local port
    port=$(sed -nE 's/^[[:space:]]*VT_PORT=([0-9]+).*/\1/p' "$ENV_FILE" | tail -n 1)
    port="${port:-5000}"

    if systemctl is-active --quiet vehicle-tracker.service; then
        echo ""
        echo "========================================"
        echo "Update completed successfully!"
        echo "========================================"
        echo ""
        echo "Access your tracker at: http://$(hostname -I | awk '{print $1}'):$port"
        echo ""
        echo "Backup location: $backup_dir"
        echo "Your settings:   $ENV_FILE"
        echo ""
    else
        echo ""
        echo "ERROR: Service failed to start after update"
        echo "Check logs with: journalctl -u vehicle-tracker -xe"
        echo ""
        echo "To restore from backup:"
        echo "  systemctl stop vehicle-tracker"
        echo "  cp -r $backup_dir/instance/* $INSTALL_DIR/instance/"
        echo "  cp -r $backup_dir/uploads/* $INSTALL_DIR/static/uploads/"
        echo "  chown -R $SERVICE_USER:$SERVICE_USER $INSTALL_DIR"
        echo "  systemctl start vehicle-tracker"
        exit 1
    fi
}

# Run only when executed, so the functions above can be tested on their own
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi
