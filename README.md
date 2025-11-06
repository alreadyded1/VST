# Vehicle Maintenance Tracker

A comprehensive web-based vehicle maintenance tracking application designed for easy installation in Proxmox LXC containers.

**GitHub Repository:** https://github.com/alreadyded1/VST

## Features

✅ **Multiple Vehicle Management**
- Manage unlimited vehicles in one application
- Store manufacturer, model, year, engine, VIN for each vehicle
- Add custom nickname and vehicle photo
- Switch between vehicles using header dropdown
- Edit or delete vehicles anytime
- All data automatically filtered by selected vehicle

✅ **Service Record Tracking**
- Track date, cost, service provider
- Edit existing service records
- Record repairs completed and comments
- Upload and store service receipts (PDF/images)
- Link supplies used to service records
- Automatic cost calculation including supplies
- Sortable table columns
- Search functionality

✅ **Supplies Inventory**
- Track inventory items with cost and quantity
- Support for various unit types (quarts, gallons, liters, etc.)
- Automatic inventory deduction when used in services
- Edit and update supply quantities

✅ **Fuel Tracking**
- Record fill-ups with location
- Automatic MPG calculation with visual popup display
- Track total fuel purchased and spent
- View best MPG and average MPG statistics
- Sortable fuel history table
- Beautiful MPG popup shows fuel economy after each fill-up

✅ **Service Reminders**
- Set reminders by date and/or mileage
- Automatic overdue detection
- Mark reminders as complete/incomplete
- Custom notes for each reminder

✅ **Responsive Design**
- Desktop and mobile view toggle
- Consistent font styling across all pages
- Clean, modern interface
- Optimized for both touch and mouse input

## Technology Stack

- **Backend**: Python Flask
- **Database**: SQLite (no separate DB server needed)
- **Frontend**: HTML5, CSS3, Vanilla JavaScript
- **File Storage**: Local filesystem

## Installation on Proxmox LXC Container

### Prerequisites

- Proxmox VE server
- Access to create LXC containers

### Step 1: Create LXC Container

1. In Proxmox web interface, click **Create CT**
2. Configure the container:
   - **Hostname**: vehicle-tracker (or your choice)
   - **Template**: Ubuntu 22.04 or Debian 12
   - **Root Password**: Set a secure password
   - **Disk**: 8 GB minimum (recommended 16 GB for uploads)
   - **CPU**: 1 core minimum (2 recommended)
   - **Memory**: 512 MB minimum (1 GB recommended)
   - **Network**: Bridge mode with DHCP or static IP

3. Start the container

### Step 2: Access Container Console

In Proxmox, select your container and click **Console**

### Step 3: Install the Application

```bash
# Update system
apt-get update && apt-get upgrade -y

# Install git
apt-get install -y git

# Clone the repository from GitHub
git clone https://github.com/alreadyded1/VST.git /tmp/vehicle-tracker
cd /tmp/vehicle-tracker

# Run the installation script
chmod +x install.sh
sudo bash install.sh
```

The installation script will:
- Install Python 3 and dependencies
- Create a dedicated system user
- Set up a Python virtual environment
- Install required packages
- Configure proper permissions
- Create and start a systemd service
- Display the access URL

### Step 4: Access the Application

After installation completes, open your web browser and navigate to:

```
http://<container-ip>:5000
```

The installation script will display the exact URL.

## Manual Installation (Alternative)

If you prefer manual installation without using the install script:

```bash
# Install dependencies
apt-get update
apt-get install -y python3 python3-pip python3-venv git

# Clone the repository
git clone https://github.com/alreadyded1/VST.git /opt/vehicle-tracker
cd /opt/vehicle-tracker

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install Python packages
pip install -r requirements.txt

# Create upload directories
mkdir -p static/uploads/receipts
mkdir -p static/uploads/vehicles
mkdir -p instance

# Run the application
python app.py
```

The application will be available at `http://<your-ip>:5000`

**Note:** For production use, it's recommended to use the automated `install.sh` script which sets up proper systemd service, permissions, and auto-start on boot.

## Usage Guide

### First-Time Setup

1. **Add Your First Vehicle**
   - Navigate to "Vehicles" page
   - Click "Add Vehicle"
   - Fill in your vehicle information (manufacturer, model, year, etc.)
   - Optionally upload a vehicle photo
   - Click "Save Vehicle"
   - Your new vehicle will be automatically selected

2. **Add Supplies (Optional)**
   - Go to "Supplies" page
   - Add maintenance supplies you keep in stock
   - These can be linked to service records for automatic cost calculation

3. **Record Your First Service**
   - Navigate to "Service Records"
   - Click "Add Service Record"
   - Fill in service details
   - Optionally upload a receipt
   - Select supplies used (if any)
   - Click "Save"

4. **Track Fuel**
   - Go to "Fuel Tracker"
   - Click "Add Fuel Record"
   - Enter fill-up details including location
   - MPG is calculated automatically (after 2nd fill-up)
   - A popup will display your fuel economy!

5. **Set Reminders**
   - Navigate to "Reminders"
   - Click "Add Reminder"
   - Set service type and due date/mileage
   - Reminders will show on dashboard

### Managing Multiple Vehicles

- **Add More Vehicles**: Go to "Vehicles" → "Add Vehicle"
- **Switch Between Vehicles**: Use the dropdown in the header
- **Edit Vehicle Details**: Go to "Vehicles" → Click "Edit" on any vehicle
- **Delete a Vehicle**: Go to "Vehicles" → Click "Delete" (removes all associated data)
- **Selected Vehicle**: All data (services, supplies, fuel, reminders) automatically filters to your selected vehicle

### Desktop vs Mobile Mode

Click the "Switch to Mobile/Desktop" button in the header to toggle between view modes:
- **Desktop Mode**: Full-width tables, multi-column layouts
- **Mobile Mode**: Optimized for smaller screens, single-column layouts

The selected mode is saved in your browser.

## System Management

### Updating the Application

When a new version is available, use the automated update script to upgrade your installation. The script downloads the latest code directly from GitHub:

```bash
# Option 1: Download and run update script directly from GitHub
curl -o /tmp/update.sh https://raw.githubusercontent.com/alreadyded1/VST/main/update.sh
chmod +x /tmp/update.sh
sudo bash /tmp/update.sh
```

Or if you still have the original installation directory:

```bash
# Option 2: Use existing update script and it will pull latest from GitHub
cd /opt/vehicle-tracker
sudo bash update.sh
```

The update script will:
1. Stop the running service
2. **Automatically backup your database and uploads** to `/root/vehicle-tracker-backup-[timestamp]/`
3. **Download latest code from GitHub** (https://github.com/alreadyded1/VST.git)
4. Update application files (app.py, templates, static files)
5. Update Python dependencies
6. Set proper permissions
7. Restart the service
8. Verify successful startup

**Important Notes:**
- Your data (database and uploads) is **always preserved** during updates
- A backup is automatically created before any changes
- The script pulls the latest code from GitHub automatically
- If the update fails, rollback instructions are provided
- Git is automatically installed if not present
- Check the logs if you encounter issues: `journalctl -u vehicle-tracker -xe`

**What Gets Updated:**
- ✅ Application code (app.py)
- ✅ Templates (HTML files)
- ✅ Static files (CSS, JavaScript)
- ✅ Python dependencies
- ❌ Database (preserved)
- ❌ Uploaded files (receipts, vehicle photos - preserved)

### Service Control

```bash
# Check service status
systemctl status vehicle-tracker

# Stop the service
systemctl stop vehicle-tracker

# Start the service
systemctl start vehicle-tracker

# Restart the service
systemctl restart vehicle-tracker

# View logs
journalctl -u vehicle-tracker -f
```

### Backup

To backup your data:

```bash
# Backup database
cp /opt/vehicle-tracker/instance/vehicle_tracker.db ~/backup/

# Backup uploads
tar -czf ~/backup/uploads.tar.gz /opt/vehicle-tracker/static/uploads/
```

### Restore

To restore from backup:

```bash
# Stop the service
systemctl stop vehicle-tracker

# Restore database
cp ~/backup/vehicle_tracker.db /opt/vehicle-tracker/instance/

# Restore uploads
tar -xzf ~/backup/uploads.tar.gz -C /

# Fix permissions
chown -R vtracker:vtracker /opt/vehicle-tracker

# Start the service
systemctl start vehicle-tracker
```

## File Locations

- **Application**: `/opt/vehicle-tracker/`
- **Database**: `/opt/vehicle-tracker/instance/vehicle_tracker.db`
- **Receipts**: `/opt/vehicle-tracker/static/uploads/receipts/`
- **Vehicle Photos**: `/opt/vehicle-tracker/static/uploads/vehicles/`
- **Service File**: `/etc/systemd/system/vehicle-tracker.service`
- **Logs**: `journalctl -u vehicle-tracker`

## Customization

### Change Port

Edit `/etc/systemd/system/vehicle-tracker.service` and modify the ExecStart line:

```bash
# Change app.py to include port parameter
# Then edit app.py and change the last line to:
# app.run(host='0.0.0.0', port=YOUR_PORT, debug=False)
```

### Change Secret Key

Edit `/opt/vehicle-tracker/app.py` and modify:

```python
app.config['SECRET_KEY'] = 'your-new-secret-key-here'
```

Then restart the service:

```bash
systemctl restart vehicle-tracker
```

## Troubleshooting

### Service Won't Start

```bash
# Check logs
journalctl -u vehicle-tracker -xe

# Common issues:
# - Port 5000 already in use: Change port in app.py
# - Permission errors: Run chown -R vtracker:vtracker /opt/vehicle-tracker
# - Missing dependencies: Reinstall with pip install -r requirements.txt
```

### Can't Access from Browser

1. Check if service is running: `systemctl status vehicle-tracker`
2. Verify container IP: `ip addr show`
3. Check firewall: `ufw status` (if enabled)
4. Try accessing from container: `curl http://localhost:5000`

### Database Errors

```bash
# Recreate database
systemctl stop vehicle-tracker
rm /opt/vehicle-tracker/instance/vehicle_tracker.db
systemctl start vehicle-tracker
# Database will be recreated automatically
```

### Upload Issues

```bash
# Fix permissions
chown -R vtracker:vtracker /opt/vehicle-tracker/static/uploads/
chmod -R 755 /opt/vehicle-tracker/static/uploads/
```

## Security Considerations

- This application is designed for use on a private network
- For internet access, use a reverse proxy (nginx/Apache) with HTTPS
- Change the default SECRET_KEY in production
- Consider implementing authentication for multi-user environments
- Regular backups are recommended

## Uninstallation

```bash
# Stop and disable service
systemctl stop vehicle-tracker
systemctl disable vehicle-tracker

# Remove service file
rm /etc/systemd/system/vehicle-tracker.service
systemctl daemon-reload

# Remove application (WARNING: This deletes all data!)
rm -rf /opt/vehicle-tracker

# Remove user
userdel vtracker
```

## Support

For issues, questions, or feature requests:
- **GitHub Issues:** https://github.com/alreadyded1/VST/issues
- **Application logs:** `journalctl -u vehicle-tracker -f`
- **System logs:** `tail -f /var/log/syslog`

## License

This project is open source and available for personal and commercial use.

## Changelog

### Version 2.0.0 (Latest)
- **Multiple vehicle support** - Manage unlimited vehicles in one application
- **Vehicle selector** in header for easy switching between vehicles
- **Edit service records** - Update existing service entries
- **MPG popup modal** - Beautiful visual display after fuel fill-ups
- **Improved supplies integration** - Fixed display issues in service records
- **Automated update script** - Easy updates directly from GitHub
- **Enhanced UI** - Removed emoji, cleaner professional appearance
- **Better mobile toggle** - Fixed mobile/desktop view switching
- All data automatically filtered by selected vehicle
- Vehicle CRUD operations (Create, Read, Update, Delete)
- Vehicles management page with card-based layout

### Version 1.0.0
- Initial release
- Full vehicle maintenance tracking
- Service records with receipt uploads
- Supplies inventory management
- Fuel tracking with MPG calculation
- Service reminders
- Responsive design with mobile/desktop modes
- Sortable tables
- Search functionality
