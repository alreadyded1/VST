# Vehicle Maintenance Tracker

A self-hosted web app for keeping track of your vehicles: service history, a maintenance schedule with automatic reminders, parts and warranties, fuel economy, tires and documents. It installs natively in a Proxmox LXC container (no Docker) and keeps everything in one SQLite file.

**GitHub Repository:** https://github.com/alreadyded1/VST

See [FEATURES.md](FEATURES.md) for a page-by-page reference and [QUICKSTART.md](QUICKSTART.md) for the short version of the install.

## Features

✅ **Vehicles**
- Any number of vehicles, each with make, model, year, engine, VIN, nickname and photo
- Decode a VIN to fill in the details, and check for open recalls (both use the public NHTSA service)
- Turn recalls into reminders with one click
- Pick the active vehicle from the sidebar; every page follows it

✅ **Service Records**
- Date, total bill, service provider (or mark it as done yourself) and odometer
- List the work done as line items picked from a list of service types you can add to; a price per line is optional
- Attach several receipts or photos to one record
- Use parts from the shelf; their cost is added to the bill automatically
- Filter by type of work, provider and date range, with a running total
- Duplicate a record to log routine work quickly
- Warns when an odometer reading is out of step with earlier or later records
- CSV import (skips rows already imported) and export

✅ **Maintenance Schedule**
- Give any type of work an interval per vehicle: miles, months or both
- A reminder is kept up to date automatically and moves forward each time that work is logged
- See, per type of work: last done, miles and days since, next due, times done and what it has cost
- Reminders you add by hand work alongside and are never changed

✅ **Parts & Warranties**
- One parts shelf shared by every vehicle
- Each purchase records quantity, cost per unit, supplier, receipt and warranty terms (months, miles or lifetime)
- Stock is worked out for you: what you bought minus what you installed, oldest stock used first
- Using a part on a service record installs it on that vehicle and starts its warranty (or the warranty can start at purchase)
- Warranties page lists what is covered, expiring soon or expired, per vehicle or across all of them
- Optional re-order reminder when stock falls to a level you choose

✅ **Fuel Tracker**
- Record fill-ups with station, optional grade and notes; enter any two of gallons, price per gallon and total
- MPG measured from full tank to full tank, so partial fill-ups don't distort it
- Statistics for the last 30 days, this year, 12 months or all time: average and best MPG, cost per mile, miles driven, spend
- Charts for MPG per tank with a rolling average, price per gallon, and spend and miles per month
- Flags a tank that comes in well below your recent average
- Station comparison, sortable, by fuel grade when you record it
- CSV import (skips rows already imported) and export

✅ **Reminders**
- Due by date, by mileage or both; overdue ones are highlighted
- Scheduled reminders come from the maintenance schedule; manual ones are yours to add and tick off
- Driving pace (miles per year, set by you or estimated from your records) gives mileage-based reminders an expected date

✅ **Tires**
- Tire sets with brand, model, size, season, DOT code, purchase date and price
- Install and uninstall log, rotation log and tread depth readings

✅ **Documents**
- Manuals, insurance, registration, title, warranty and inspection papers, with notes and an attached PDF or image

✅ **Everything else**
- Dashboard with totals, recent services and upcoming reminders
- Search across services, fuel, parts, reminders, documents and tires
- Printable vehicle report with CSV downloads
- User accounts with an admin panel; uploads are only served to signed-in users
- Layout adapts to phones automatically, with a bottom navigation bar

## Technology Stack

- **Backend**: Python, Flask, served by gunicorn
- **Database**: SQLite (no separate database server)
- **Frontend**: HTML, CSS and plain JavaScript; Chart.js is bundled for the fuel charts
- **File storage**: local filesystem
- **Outside services**: none required. VIN decoding and recall checks call NHTSA's public service when you use them; the page font loads from Google Fonts

## Installation on Proxmox LXC Container

### Step 1: Create the container

1. In the Proxmox web interface, click **Create CT**
2. Configure the container:
   - **Hostname**: vehicle-tracker (or your choice)
   - **Template**: Debian 12 or 13, or Ubuntu 22.04 or later
   - **Disk**: 8 GB minimum (16 GB recommended if you upload many receipts)
   - **CPU**: 1 core minimum (2 recommended)
   - **Memory**: 512 MB minimum (1 GB recommended)
   - **Network**: bridge mode with DHCP or a static IP
3. Start the container

### Step 2: Install

Open the container's **Console** in Proxmox and run:

```bash
apt-get update && apt-get install -y git
```

```bash
git clone https://github.com/alreadyded1/VST.git /tmp/vehicle-tracker
```

```bash
bash /tmp/vehicle-tracker/install.sh
```

The installation script will:
- Install Python 3
- Create a dedicated `vtracker` system user
- Copy the app to `/opt/vehicle-tracker` and set up a Python virtual environment
- Create and start a systemd service that runs on boot
- Print the address to open

### Step 3: Sign in

Open `http://<container-ip>:5000` in a browser and sign in with:

- **Username:** `admin`
- **Password:** `admin`

You are taken straight to the profile page to **change the password**. Until you do, every page shows a reminder.

## Manual Installation (Alternative)

```bash
apt-get update && apt-get install -y python3 python3-pip python3-venv git
```

```bash
git clone https://github.com/alreadyded1/VST.git /opt/vehicle-tracker
```

```bash
cd /opt/vehicle-tracker && python3 -m venv venv && venv/bin/pip install -r requirements.txt
```

```bash
cd /opt/vehicle-tracker && venv/bin/gunicorn wsgi:app
```

`gunicorn` reads its settings from `gunicorn.conf.py`. For a quick local try-out you can run `venv/bin/python app.py` instead, which starts Flask's development server.

The install script is the better choice for real use: it sets up the service user, permissions and start on boot.

## Usage Guide

### First-time setup

1. **Add a vehicle**: go to **Vehicles**, click **Add Vehicle**. Typing the VIN and clicking **Decode VIN** fills in the rest.
2. **Log your first fill-up**: **Fuel Tracker**, **Add Fuel Record**. MPG appears from the second full tank.
3. **Record a service**: **Service Records**, **Add Service Record**. Enter the total bill and pick the work done from the list.
4. **Set up the schedule**: on **Service Records**, open the **Maintenance Schedule** tab and click **Set Interval** on the work you want reminding about. The reminder appears once that work has been logged at least once.
5. **Add parts you keep in stock** (optional): **Parts & Warranties**, **Add Part**, with the purchase details and any warranty.

### Service records and the maintenance schedule

- **The bill and the line items are separate.** Enter what you paid as the total. Line items say what was done; a price on a line only breaks the bill down and is not added to it.
- **Parts from your shelf are added on top** of the bill, at the price you paid for them.
- **To start the clock on a car you already own**, log the last time each scheduled job was done, even roughly. The schedule counts from the most recent record.
- **Driving pace** (on the Maintenance Schedule tab) is the miles per year you drive. Set it, or leave the estimate from your records. It turns "due at 59,500 miles" into an expected date.

### Parts and warranties

- **A part** is the thing itself. **A purchase** is one time you bought it, with its price and warranty. **An install** is some of a purchase going onto a vehicle.
- **Stock goes down when you use a part** on a service record, or with **Install on Vehicle** for jobs you don't log as a service.
- **Warranties start when the part is installed**, unless the purchase is set to start at purchase.
- **When a part comes off the car**, edit its install and set the removed date. It stays in the history but drops off the warranty list.

### Fuel

- **Untick "Filled the tank"** for a top-up. It gets no MPG of its own; its gallons count toward the next full tank.
- **Tick "Missed logging a fill-up"** if you forgot one, so the miles in between don't produce a false MPG.
- **The period buttons** at the top change the statistics, charts and station table together.

### Managing vehicles

- **Switch vehicle**: use the selector at the top of the sidebar
- **Edit or delete**: on the **Vehicles** page
- **Deleting a vehicle** removes its services, fuel records, reminders, tires and documents. Parts stay on the shelf, and what the vehicle used stays used.

## System Management

### Updating

The update script downloads the latest code from GitHub and keeps your data:

```bash
cd /opt/vehicle-tracker && sudo bash update.sh
```

If you no longer have that copy of the script:

```bash
curl -o /tmp/update.sh https://raw.githubusercontent.com/alreadyded1/VST/claude/fix-fuel-up-date-display-0128p7VwVdzrdnkLi2zFC7wJ/update.sh
```

```bash
sudo bash /tmp/update.sh
```

The script will:
1. Stop the service
2. **Back up your database and uploads** to `/root/vehicle-tracker-backup-<timestamp>/`
3. Download the latest code
4. Replace the application files and update Python packages
5. Restart the service and check that it started

Any changes the new version needs to make to your database happen automatically the first time it starts. If the service fails to start, the script prints how to restore from the backup it just made.

**What is replaced:** `app.py`, `wsgi.py`, `gunicorn.conf.py`, `requirements.txt`, the templates and the CSS and JavaScript.
**What is kept:** the database, the signing keys, and all uploaded receipts, documents and photos.

### Service control

```bash
systemctl status vehicle-tracker
```

```bash
systemctl restart vehicle-tracker
```

```bash
journalctl -u vehicle-tracker -f
```

### Backup

Everything worth keeping is in two folders: `instance/` (the database and signing keys) and `static/uploads/` (receipts, documents and photos).

```bash
tar -czf ~/vehicle-tracker-backup.tar.gz -C /opt/vehicle-tracker instance static/uploads
```

### Restore

```bash
systemctl stop vehicle-tracker
```

```bash
tar -xzf ~/vehicle-tracker-backup.tar.gz -C /opt/vehicle-tracker
```

```bash
chown -R vtracker:vtracker /opt/vehicle-tracker && systemctl start vehicle-tracker
```

## File Locations

- **Application**: `/opt/vehicle-tracker/`
- **Database**: `/opt/vehicle-tracker/instance/vehicle_tracker.db`
- **Signing keys**: `/opt/vehicle-tracker/instance/secret_key` and `jwt_secret_key`
- **Receipts and photos**: `/opt/vehicle-tracker/static/uploads/receipts/`
- **Documents**: `/opt/vehicle-tracker/static/uploads/documents/`
- **Vehicle photos**: `/opt/vehicle-tracker/static/uploads/vehicles/`
- **Server settings**: `/opt/vehicle-tracker/gunicorn.conf.py`
- **Service file**: `/etc/systemd/system/vehicle-tracker.service`
- **Logs**: `journalctl -u vehicle-tracker`

## Customization

### Change the port

Edit the `bind` line in `/opt/vehicle-tracker/gunicorn.conf.py`, for example `bind = '0.0.0.0:8080'`, then restart the service. The update script replaces this file, so make the change again after updating.

### Run behind a reverse proxy

Point the proxy (NGINX Proxy Manager, nginx, Caddy) at `http://<container-ip>:5000` and let it handle HTTPS. Uploads can be up to 16 MB, so raise the proxy's upload limit if it is lower.

### Manage the signing keys yourself

The keys that sign login sessions and API tokens are generated on first start and stored in `instance/`. To supply your own, set `SECRET_KEY` and `JWT_SECRET_KEY` as `Environment=` lines in the systemd service file. The update script rewrites that file, so add them again after updating.

## API

Every page is built on a JSON API under `/api/`, which other clients can use too. Sign in by posting a username and password to `/api/auth/login`; it returns an access token to send as `Authorization: Bearer <token>` (valid for one hour) and a refresh token for `/api/auth/refresh` (valid for 30 days). The routes are listed, with their fields, in `app.py`.

## Running Tests

```bash
venv/bin/pip install pytest
```

```bash
venv/bin/python -m pytest tests
```

Tests run against a throwaway SQLite database and upload directory, so they never touch your real data.

## Troubleshooting

### Service won't start

```bash
journalctl -u vehicle-tracker -xe
```

Common causes:
- **Port 5000 already in use**: change the port (see Customization)
- **Permission errors**: `chown -R vtracker:vtracker /opt/vehicle-tracker`
- **Missing packages**: `cd /opt/vehicle-tracker && venv/bin/pip install -r requirements.txt`

### Can't reach it from a browser

1. Check the service is running: `systemctl status vehicle-tracker`
2. Check the container's address: `ip addr show`
3. Try from inside the container: `curl -I http://localhost:5000/login`
4. Check any firewall on the container or the Proxmox host

### Locked out

Five failed sign-ins for one username from the same address block further attempts for up to 15 minutes; wait and try again. If an admin has forgotten their password, another admin can reset it from the Admin panel.

### Something looks wrong after an update

Restore the backup the update script made (its location is printed at the end of the update, under `/root/`), following the Restore steps above, and report the problem with the output of `journalctl -u vehicle-tracker -xe`.

### Starting over with an empty database

This deletes all your records. Back up first.

```bash
systemctl stop vehicle-tracker && rm /opt/vehicle-tracker/instance/vehicle_tracker.db && systemctl start vehicle-tracker
```

A fresh database is created on start, with the default `admin` / `admin` account.

### Uploads fail

```bash
chown -R vtracker:vtracker /opt/vehicle-tracker/static/uploads/
```

Accepted file types are PDF, PNG, JPG, GIF and WebP, up to 16 MB.

## Security Considerations

- This application is designed for use on a private network
- For access from the internet, put it behind a reverse proxy with HTTPS
- Change the default `admin`/`admin` password right after installing. Until you do, every page shows a reminder, and the login page shows the default credentials (the admin settings can hide that hint)
- Every signed-in user can see and edit every vehicle; accounts are for a household, not for keeping users' data apart
- Session and API-token signing keys are generated on first start and stored in `instance/secret_key` and `instance/jwt_secret_key`
- Uploaded receipts, documents and vehicle photos are only served to signed-in users
- After 5 failed logins for one username (or 20 for any usernames) from the same IP address within 15 minutes, further attempts from that IP are refused until the window passes. Behind a reverse proxy every client shares the proxy's address, so those limits apply to everyone together
- Regular backups are recommended (`instance/` and `static/uploads/`)

## Uninstallation

```bash
systemctl disable --now vehicle-tracker
```

```bash
rm /etc/systemd/system/vehicle-tracker.service && systemctl daemon-reload
```

This deletes the app and all its data:

```bash
rm -rf /opt/vehicle-tracker && userdel vtracker
```

## Support

- **GitHub Issues:** https://github.com/alreadyded1/VST/issues
- **Application logs:** `journalctl -u vehicle-tracker -f`

## License

This project is open source and available for personal and commercial use.

## Changelog

### Version 3.2.0
- **Partial fill-ups**: untick "Filled the tank" and the gallons count toward the next full tank's MPG. Existing fill-ups all count as full tanks, so current numbers don't change
- **Fuel insights by period**: cost per mile, miles driven and monthly spend, for 30 days, this year, 12 months or all time
- **Driving pace and projected dates**: set miles per year per vehicle (or use the estimate from your records) and mileage-based reminders and scheduled services show an expected date
- **Faster entry**: any two of gallons, price per gallon and total fill in the third; odometer check on save
- **Economy drop alert**, **fuel grade and notes**, rolling-average MPG chart, station comparison by grade
- Fuel statistics laid out in two rows of three; station comparison sorts when you click a header
- CSV import skips rows already there and carries the full-tank and missed-fill-up flags

### Version 3.1.0
- **Service line items**: list the work done on each record from a list of service types (a starter list is included; add your own). Line prices are optional and only break down the total bill
- **Maintenance schedule**: per-vehicle intervals for any type of work, with automatic reminders that follow the service history. Manual reminders are unchanged
- **History by type of work**: last done, next due, times done and spend
- **Do-it-yourself flag**, **multiple attachments** per record, **filters with totals**, **duplicate a record**, **odometer check**
- CSV import skips rows already there and reads a `service_types` column; missing fields now give a clear message
- Existing records are untouched: the old free-text "repairs completed" stays as the record's details, and each existing receipt becomes its first attachment

### Version 3.0.0
- **Parts & Warranties rebuilt** around three records: parts, purchases and installs. The "track individual units" switch is gone; stock, service cost and warranty status are all calculated
- Parts are now one shelf shared by all vehicles
- Existing supplies are converted automatically the first time the new version starts. The old `supplies`, `supply_units` and `service_supplies` tables are left in the database, unused, as a backup
- API: `/api/supplies` and `/api/supply-units` are replaced by `/api/parts`, `/api/part-purchases`, `/api/part-installs` and `/api/warranties`. Service records accept a `parts` list (the old `supplies` list of `{id, quantity}` still works)

### Between 2.0.0 and 3.0.0 (not released as numbered versions)
- User accounts, admin panel and API tokens
- Tire log, documents, global search and the printable report
- VIN decoding and recall checks
- Redesigned interface with a sidebar and automatic phone layout
- Runs under gunicorn

### Version 2.0.0
- **Multiple vehicle support** with a vehicle selector
- **Edit service records**
- **MPG popup** after fuel fill-ups
- **Automated update script**
- All data filtered by the selected vehicle

### Version 1.0.0
- Initial release
- Service records with receipt uploads
- Supplies inventory
- Fuel tracking with MPG calculation
- Service reminders
- Sortable tables and search
