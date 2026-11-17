# Vehicle Maintenance Tracker - Feature Reference

What each page of the app does. For installation and upkeep see [README.md](README.md).

## Pages at a glance

| Page | Address | What it is for |
|---|---|---|
| Dashboard | `/` | What needs attention across all vehicles, spending and recent activity |
| Vehicles | `/vehicles` | Add, edit and delete vehicles; VIN decode and recall check |
| Service Records | `/services` | Service history and the maintenance schedule |
| Parts & Warranties | `/parts` | Parts shelf, purchases, installs and warranty status |
| Fuel Tracker | `/fuel` | Fill-ups, fuel economy and cost |
| Reminders | `/reminders` | Manual and scheduled reminders |
| Tires | `/tires` | Tire sets, rotations and tread depth |
| Documents | `/documents` | Papers and notes for the vehicle |
| Export / Report | `/report` | Printable summary and CSV downloads |
| Profile | `/profile` | Change your password |
| Admin | `/admin` | Users and settings (admins only) |

The active vehicle is chosen in the sidebar and every page follows it. Search (top right) looks across services, fuel, parts, reminders, documents and tires.

## Dashboard

Covers every vehicle at once, not just the active one.

- **Needs attention**, worst first:
  - reminders that are overdue, or due within 30 days or 500 miles (this includes scheduled maintenance)
  - warranties about to run out on installed parts
  - parts at or below their re-order level
  - installed tires with tread at 4/32" or less
  - a latest tank well below the vehicle's recent MPG
- Clicking an item opens the right page with that vehicle selected
- **Spending** this month and this year, split into fuel, service and parts, and tires. Parts still on the shelf are not counted until they are used
- **A card per vehicle:** odometer, average MPG, last service, spend this year, and whether anything is overdue. Clicking a card makes it the active vehicle
- **Recent activity:** the latest services and fill-ups across all vehicles

## Vehicles

- Make, model, year, engine, VIN, nickname and a photo
- **Decode VIN** fills in the details from the VIN
- **Check Recalls** lists open recalls and can create a reminder for each
- Per-vehicle summary: service and fuel counts, last service and fill-up, current odometer, average MPG, and spend this year and all time
- Deleting a vehicle removes its services, fuel records, reminders, tires, documents and their files. Parts stay on the shelf

## Service Records

### Service History tab

- **Each record:** date, total bill, service provider, odometer, details and comments
- **Work done:** line items picked from the list of service types. New types can be added from the form. A price per line is optional and only breaks the bill down
- **I did this myself:** marks the record as DIY and makes the provider optional
- **Receipts and photos:** any number of PDF or image files per record
- **Parts used:** taken from the parts shelf, oldest stock first, and added to the record's cost
- **Total cost** shown everywhere is the bill plus the parts used
- **Filters:** search text, type of work, provider and date range, with a count and total for what is shown
- **Row menu:** View, Edit, Duplicate, Delete
- **Odometer check:** a reading lower than an earlier record, or higher than a later one, is saved with a warning
- **CSV:** download a template, import (rows already present are skipped) and export

### Maintenance Schedule tab

- One row per type of work, with **Set Interval** to schedule it for the active vehicle in miles, months or both
- **Last done**, miles and days since, **next due** with an on track, due soon or overdue status, **times done** and **spent**
- **Driving pace:** miles per year for the vehicle, set by you or estimated from its fuel and service records. It gives each mileage due point an expected date
- **Add, rename and delete service types.** A type in use on a record can be renamed but not deleted
- A starter list of 17 common types is included, with no intervals set

### How scheduled reminders behave

- A reminder appears once the scheduled work has been logged at least once, due one interval after the most recent time
- Logging the work again moves the same reminder forward
- Editing or deleting a service recalculates it
- Removing the interval removes the reminder
- Reminders added by hand are never changed by the schedule

### How spend per type is worked out

- A line with a price counts that price
- Work that was the only thing on a record counts the whole bill plus parts
- An unpriced line on a shared bill shows as "Not priced"

## Parts & Warranties

### Parts Shelf tab

- **A part** has a name, part number, brand, category, storage location, notes and an optional re-order level
- **A purchase** records quantity, cost per unit, date, supplier, condition, receipt, notes and warranty terms
- **An install** is a quantity from one purchase put on one vehicle, with its date and odometer
- **In stock** is purchased minus installed. **Stock value** uses the price paid for what is left
- Parts are shared by all vehicles; the install decides which vehicle they went on
- **Row menu:** Details, Add Purchase, Install on Vehicle, Edit, Delete
- A part cannot be deleted while any of it is installed; a purchase cannot be deleted once some of it is used
- A re-order reminder is added when stock falls to the re-order level

### Warranty terms

- Months, miles, both, or lifetime
- Starts when the part is installed (the default) or on the purchase date
- With both a time and a mileage limit, it ends at whichever comes first
- The start date and mileage can be overridden on an individual install
- Setting a removed date on an install keeps it in the history but ends its place on the warranty list

### Warranties tab

- Every installed part that was sold with a warranty, soonest to expire first
- Status: covered, expiring soon (within 30 days or 1,000 miles), expired, or lifetime
- For the active vehicle, or all vehicles
- Link to the purchase receipt

## Fuel Tracker

### Fill-ups

- Date, odometer, station, and any two of gallons, price per gallon and total cost
- Optional fuel grade and notes
- **Filled the tank:** untick for a partial fill
- **Missed logging a fill-up:** tick when one was forgotten
- Odometer check, as on service records
- CSV template, import (rows already present are skipped) and export

### How MPG is worked out

- From one full tank to the next: the miles between them divided by all the fuel added in between
- A partial fill has no MPG of its own
- The first fill-up, and the first full tank after a missed one, are starting points with no MPG
- Editing, deleting or back-dating a fill-up recalculates the whole chain
- Averages are total miles over total gallons, not an average of the per-tank figures

### Statistics, charts and stations

- **Period:** 30 days, this year, 12 months or all time
- **Statistics:** average MPG, best MPG, cost per mile, miles driven, total spent and total fuel
- **Cost per mile** leaves out any stretch with a missed fill-up
- **Charts:** MPG per full tank with a five-tank average, price per gallon, and spend and miles per month
- **Low economy:** a tank more than 15% below the few before it is flagged when saved and marked in the list and chart
- **Station comparison:** visits, gallons, average price and average MPG per station. Click a header to sort; filter by grade when more than one is recorded

## Reminders

- Service type, due date, due mileage (either or both) and notes
- Mark complete or incomplete, or delete
- Overdue when past the date or past the mileage
- Mileage reminders show miles to go and, when the driving pace is known, an expected date
- Reminders from the maintenance schedule are tagged **Scheduled**
- Recall and re-order reminders are created for you from the Vehicles and Parts pages

## Tires

- **Tire sets:** brand, model, size, season, DOT code, purchase date, price and notes
- **Install and uninstall** a set, with date and odometer; installing one set uninstalls the other
- **Miles on each set**, worked out from its install history and the vehicle's latest odometer
- **Rotation log:** date, odometer, pattern and notes. A rotation logged here counts toward the tire rotation interval on the maintenance schedule, and a tire rotation on a service record is listed here too
- **Tread depth log:** a reading for each corner, in 32nds of an inch. 4/32" or less is marked marginal and 2/32" or less as replace now, with a warning when you save the reading and an item on the dashboard
- **Purchase price** counts toward the vehicle's spend, in the year of the purchase date
- An out-of-step odometer on an install or rotation is saved with a warning

## Documents

- Title, category (manual, insurance, registration, title, warranty, inspection, other), notes and an attached PDF or image
- Filter by category

## Export / Report

- A printable summary of the vehicle: services, fuel records, parts installed with their warranty status, and reminders
- CSV downloads for services, fuel and parts

## Accounts and Admin

- Sign in with a username and password; change your own password on the Profile page
- Admins can add and remove users, reset passwords, and hide the default-credentials hint on the login page
- **Backups** (admins only):
  - **Download Full Backup:** one zip with the database, signing keys and every upload, laid out so it unzips straight over the install folder
  - **Automatic daily backup:** a database-only copy kept on the server, made on the first visit after 24 hours, with a number to keep
  - **Back Up Now**, download and delete for the copies kept on the server
- Every signed-in user sees every vehicle
- Uploaded files are only served to signed-in users
- Repeated failed sign-ins from one address are blocked for up to 15 minutes

## On phones

- The layout switches automatically on narrow screens: tables become cards, the sidebar becomes a slide-out menu, and a bottom bar links to the main pages
- CSV import and export buttons are hidden on phones

## Under the hood

- Python and Flask, served by gunicorn under systemd; no Docker
- One SQLite database file; uploads stored on disk
- Money is stored as whole cents and added up as integers; the API and CSV files use dollars
- Database changes for a new version are applied automatically at startup
- A JSON API under `/api/` backs every page and accepts session cookies or bearer tokens
- Automated tests cover the calculations and the upgrade steps (`pytest tests`)
