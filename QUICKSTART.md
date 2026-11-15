# Quick Start Guide

## Install in a Proxmox LXC container

### 1. Create the container

In the Proxmox web interface, click **Create CT** with:

- **Template**: Debian 12 or 13, or Ubuntu 22.04 or later
- **Disk**: 8 GB minimum
- **CPU**: 1-2 cores
- **Memory**: 512 MB - 1 GB
- **Network**: DHCP or a static IP

### 2. Install the app

Open the container's console and run:

```bash
apt-get update && apt-get install -y git
```

```bash
git clone https://github.com/alreadyded1/VST.git /tmp/vehicle-tracker
```

```bash
bash /tmp/vehicle-tracker/install.sh
```

### 3. Sign in

Open `http://<container-ip>:5000` (the script prints the exact address) and sign in as `admin` / `admin`. Change the password when prompted.

## First steps

1. **Vehicles** → add your vehicle. Enter the VIN and click **Decode VIN** to fill in the rest
2. **Fuel Tracker** → log a fill-up. MPG appears from the second full tank
3. **Service Records** → add a record: the total bill, plus the work done from the list
4. **Service Records → Maintenance Schedule** → set an interval on the jobs you want reminding about
5. **Parts & Warranties** → add parts you keep in stock, with any warranty (optional)

## Everyday commands

```bash
systemctl status vehicle-tracker
```

```bash
journalctl -u vehicle-tracker -f
```

```bash
systemctl restart vehicle-tracker
```

Update to the latest version (backs up your data first):

```bash
cd /opt/vehicle-tracker && sudo bash update.sh
```

Back up the database, keys and uploads:

```bash
tar -czf ~/vehicle-tracker-backup.tar.gz -C /opt/vehicle-tracker instance static/uploads
```

## Tips

- **Start the schedule on a car you already own** by logging the last time each job was done; reminders count from the most recent record
- **Untick "Filled the tank"** for a top-up so it doesn't distort your MPG
- **Set your driving pace** on the Maintenance Schedule tab to see expected dates for mileage-based reminders
- **Duplicate** a service record from its row menu to log routine work quickly
- **Click column headers** to sort tables
- **Search** (top right) looks across every section
- Attach **receipts and photos** as PDF or images, several per service record

## Need help?

See [README.md](README.md) for the full guide and troubleshooting, and [FEATURES.md](FEATURES.md) for what each page does.
