# Quick Start Guide

## Proxmox LXC Container Installation

### 1. Create Container in Proxmox

1. In Proxmox web interface: **Create CT**
2. Settings:
   - **Template**: Ubuntu 22.04 or Debian 12
   - **Disk**: 8 GB minimum
   - **CPU**: 1-2 cores
   - **Memory**: 512 MB - 1 GB
   - **Network**: DHCP or Static IP

### 2. Install Application

Access container console and run:

```bash
apt-get update && apt-get install -y git
git clone <your-repo-url> /tmp/vehicle-tracker
cd /tmp/vehicle-tracker
chmod +x install.sh
sudo bash install.sh
```

### 3. Access Application

Open browser to: `http://<container-ip>:5000`

The installation script displays the exact URL.

## First Steps

1. **Vehicle Profile** → Add your vehicle information
2. **Supplies** → Add any supplies you stock (optional)
3. **Service Records** → Add maintenance records
4. **Fuel Tracker** → Log fill-ups
5. **Reminders** → Set service reminders

## Quick Commands

```bash
# Check status
systemctl status vehicle-tracker

# View logs
journalctl -u vehicle-tracker -f

# Restart
systemctl restart vehicle-tracker

# Backup database
cp /opt/vehicle-tracker/instance/vehicle_tracker.db ~/backup/
```

## Tips

- Use the **Desktop/Mobile toggle** in the header
- Click **column headers** to sort tables
- Use the **search box** to find specific service records
- Upload **receipts** as PDF or images
- **MPG** is auto-calculated after your 2nd fill-up

## Need Help?

See the full [README.md](README.md) for detailed documentation.
