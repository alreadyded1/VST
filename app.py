from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, make_response, g, session
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta, timezone
from functools import lru_cache, wraps
from contextlib import contextmanager
import calendar
import posixpath
import sqlite3
import os
import secrets
import jwt
from werkzeug.utils import secure_filename
import json
import csv
import io
import requests
from urllib.parse import quote

app = Flask(__name__)

DATABASE = 'instance/vehicle_tracker.db'


def _load_or_create_secret(name):
    """Return a random secret persisted under instance/, creating it on first
    use. A hardcoded fallback would let anyone forge session cookies; a
    per-process random one would log everyone out (and void every JWT) on
    each restart."""
    path = os.path.join(os.path.dirname(DATABASE) or '.', name)
    try:
        with open(path) as f:
            value = f.read().strip()
            if value:
                return value
    except OSError:
        pass
    value = secrets.token_hex(32)
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write(value)
    return value


app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY') or _load_or_create_secret('secret_key')
app.config['JWT_SECRET_KEY'] = os.environ.get('JWT_SECRET_KEY') or _load_or_create_secret('jwt_secret_key')
# Lax keeps the session cookie off cross-site POST/PUT/DELETE requests, which
# is the app's CSRF protection for the browser UI.
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = timedelta(hours=1)
app.config['JWT_REFRESH_TOKEN_EXPIRES'] = timedelta(days=30)
app.config['UPLOAD_FOLDER'] = os.path.join(app.static_folder, 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size
app.config['ALLOWED_EXTENSIONS'] = {'png', 'jpg', 'jpeg', 'gif', 'pdf', 'webp'}

# API clients (iOS) authenticate with Bearer tokens, so cross-origin requests
# never need cookies. Allowing credentials with a wildcard origin would let
# any website read the API with a logged-in user's session.
CORS(app, resources={r'/api/*': {'origins': '*'}}, supports_credentials=False)

@app.context_processor
def inject_static_version():
    """Version static asset URLs by file mtime so browsers cache them
    until the file actually changes."""
    def static_v(filename):
        try:
            return int(os.path.getmtime(os.path.join(app.static_folder, filename)))
        except OSError:
            return 0
    return {'static_v': static_v}

# Flask-Login setup
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Please log in to access this page.'

def get_db():
    """Create database connection"""
    # Wait for a lock rather than failing at once: under gunicorn several
    # worker processes share the database file
    conn = sqlite3.connect(DATABASE, timeout=15)
    conn.row_factory = sqlite3.Row
    return conn

# User class for Flask-Login
class User(UserMixin):
    def __init__(self, id, username, is_admin):
        self.id = id
        self.username = username
        self.is_admin = is_admin

@login_manager.user_loader
def load_user(user_id):
    """Load user for Flask-Login"""
    db = get_db()
    user = db.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    db.close()
    if user:
        return User(user['id'], user['username'], user['is_admin'])
    return None

def admin_required(f):
    """Decorator to require admin privileges"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            return jsonify({'error': 'Admin privileges required'}), 403
        return f(*args, **kwargs)
    return decorated_function


# ── JWT helpers ──────────────────────────────────────────────────────────────

def _generate_access_token(user_id, username, is_admin):
    payload = {
        # RFC 7519 requires a string subject; PyJWT >= 2.10 rejects ints
        'sub': str(user_id),
        'username': username,
        'is_admin': bool(is_admin),
        'iat': datetime.now(timezone.utc),
        'exp': datetime.now(timezone.utc) + app.config['JWT_ACCESS_TOKEN_EXPIRES'],
        'type': 'access',
    }
    return jwt.encode(payload, app.config['JWT_SECRET_KEY'], algorithm='HS256')


def _generate_refresh_token(user_id):
    token = secrets.token_urlsafe(48)
    expires_at = datetime.now(timezone.utc) + app.config['JWT_REFRESH_TOKEN_EXPIRES']
    db = get_db()
    db.execute(
        'INSERT INTO refresh_tokens (user_id, token, expires_at) VALUES (?, ?, ?)',
        (user_id, token, expires_at.isoformat())
    )
    db.commit()
    db.close()
    return token


def _get_jwt_user():
    """Return a User from a Bearer token, or None."""
    auth = request.headers.get('Authorization', '')
    if not auth.startswith('Bearer '):
        return None
    token = auth[7:]
    try:
        data = jwt.decode(token, app.config['JWT_SECRET_KEY'], algorithms=['HS256'])
        if data.get('type') != 'access':
            return None
        return User(int(data['sub']), data['username'], data['is_admin'])
    except (jwt.InvalidTokenError, KeyError, ValueError):
        return None


def _api_user():
    """The authenticated user for an API request, whether it came in with a
    JWT or a browser session. current_user alone is anonymous for JWT
    requests."""
    return g.get('jwt_user') or current_user


def _revoke_refresh_tokens(db, user_id):
    db.execute('DELETE FROM refresh_tokens WHERE user_id = ?', (user_id,))


def api_login_required(f):
    """Accepts either a Flask-Login session (web) or a JWT Bearer token (iOS)."""
    @wraps(f)
    def decorated(*args, **kwargs):
        jwt_user = _get_jwt_user()
        if jwt_user is not None:
            g.jwt_user = jwt_user
            return f(*args, **kwargs)
        # Fall back to session-based auth
        if not current_user.is_authenticated:
            return jsonify({'error': 'Authentication required'}), 401
        g.jwt_user = None
        return f(*args, **kwargs)
    return decorated


def api_admin_required(f):
    """Like api_login_required but also requires admin role."""
    @wraps(f)
    def decorated(*args, **kwargs):
        jwt_user = _get_jwt_user()
        if jwt_user is not None:
            if not jwt_user.is_admin:
                return jsonify({'error': 'Admin privileges required'}), 403
            g.jwt_user = jwt_user
            return f(*args, **kwargs)
        if not current_user.is_authenticated:
            return jsonify({'error': 'Authentication required'}), 401
        if not current_user.is_admin:
            return jsonify({'error': 'Admin privileges required'}), 403
        g.jwt_user = None
        return f(*args, **kwargs)
    return decorated

def _is_upload_request():
    """True if this request is for a file under static/uploads/. The path is
    normalized the way the static file handler will resolve it, so variants
    like /static/./uploads/x or /static//uploads/x are caught too."""
    if request.endpoint != 'static':
        return False
    filename = posixpath.normpath((request.view_args or {}).get('filename', '')).lstrip('/')
    return filename == 'uploads' or filename.startswith('uploads/')

@app.before_request
def protect_uploads():
    """Receipts, documents and vehicle photos are private: serve them only
    to a logged-in user (browser session or Bearer token)."""
    if _is_upload_request() and _get_jwt_user() is None and not current_user.is_authenticated:
        return jsonify({'error': 'Authentication required'}), 401

@app.after_request
def no_shared_caching_of_uploads(response):
    if _is_upload_request():
        # Browser may cache for this user; proxies must not share it
        response.headers['Cache-Control'] = 'private, no-cache'
        response.headers['X-Content-Type-Options'] = 'nosniff'
    return response

def init_db():
    """Initialize database with schema"""
    with app.app_context():
        db = get_db()
        # WAL lets readers proceed while another process writes; the mode is
        # stored in the database file, so this only has to run once
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                is_admin BOOLEAN DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS vehicle (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                manufacturer TEXT NOT NULL,
                model TEXT NOT NULL,
                year INTEGER NOT NULL,
                engine TEXT,
                vin TEXT,
                nickname TEXT,
                picture TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS service_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                vehicle_id INTEGER NOT NULL,
                date DATE NOT NULL,
                cost REAL NOT NULL,
                service_provider TEXT NOT NULL,
                odometer INTEGER,
                comments TEXT,
                repairs_completed TEXT,
                receipt_path TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (vehicle_id) REFERENCES vehicle (id)
            );

            CREATE TABLE IF NOT EXISTS supplies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                vehicle_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                part_number TEXT,
                brand TEXT,
                cost REAL NOT NULL,
                quantity INTEGER NOT NULL,
                warranty_start_date DATE,
                warranty_months INTEGER,
                warranty_lifetime BOOLEAN DEFAULT 0,
                receipt_path TEXT,
                remind_to_reorder BOOLEAN DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (vehicle_id) REFERENCES vehicle (id)
            );

            CREATE TABLE IF NOT EXISTS service_supplies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                service_id INTEGER NOT NULL,
                supply_id INTEGER NOT NULL,
                quantity_used REAL NOT NULL,
                FOREIGN KEY (service_id) REFERENCES service_records (id),
                FOREIGN KEY (supply_id) REFERENCES supplies (id)
            );

            CREATE TABLE IF NOT EXISTS fuel_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                vehicle_id INTEGER NOT NULL,
                date DATE NOT NULL,
                gallons REAL NOT NULL,
                cost REAL NOT NULL,
                odometer INTEGER NOT NULL,
                location TEXT NOT NULL,
                mpg REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (vehicle_id) REFERENCES vehicle (id)
            );

            CREATE TABLE IF NOT EXISTS service_reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                vehicle_id INTEGER NOT NULL,
                service_type TEXT NOT NULL,
                due_date DATE,
                due_mileage INTEGER,
                notes TEXT,
                completed BOOLEAN DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (vehicle_id) REFERENCES vehicle (id)
            );

            CREATE TABLE IF NOT EXISTS refresh_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                token TEXT UNIQUE NOT NULL,
                expires_at TIMESTAMP NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id)
            );

            CREATE TABLE IF NOT EXISTS login_failures (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ip TEXT NOT NULL,
                username TEXT NOT NULL,
                attempted_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_login_failures_ip ON login_failures(ip, attempted_at);
        ''')

        # Migration: Add brand column to supplies table if it doesn't exist
        try:
            db.execute('ALTER TABLE supplies ADD COLUMN brand TEXT')
            db.commit()
        except sqlite3.OperationalError:
            # Column already exists
            pass

        # Migration: Add odometer column to service_records table if it doesn't exist
        try:
            db.execute('ALTER TABLE service_records ADD COLUMN odometer INTEGER')
            db.commit()
        except sqlite3.OperationalError:
            # Column already exists
            pass

        # Migration: Add new columns to supplies table for Parts & Supplies functionality
        migrations = [
            'ALTER TABLE supplies ADD COLUMN part_number TEXT',
            'ALTER TABLE supplies ADD COLUMN manufacturer TEXT',
            'ALTER TABLE supplies ADD COLUMN warranty_start_date DATE',
            'ALTER TABLE supplies ADD COLUMN warranty_months INTEGER',
            'ALTER TABLE supplies ADD COLUMN receipt_path TEXT'
        ]

        for migration in migrations:
            try:
                db.execute(migration)
                db.commit()
            except sqlite3.OperationalError:
                # Column already exists
                pass

        # Migration: Add remind_to_reorder column to supplies table
        try:
            db.execute('ALTER TABLE supplies ADD COLUMN remind_to_reorder BOOLEAN DEFAULT 0')
            db.commit()
        except sqlite3.OperationalError:
            # Column already exists
            pass

        # Migration: Add mileage-based warranty tracking columns to supplies table
        mileage_warranty_migrations = [
            'ALTER TABLE supplies ADD COLUMN warranty_start_mileage INTEGER',
            'ALTER TABLE supplies ADD COLUMN warranty_mileage_limit INTEGER'
        ]

        for migration in mileage_warranty_migrations:
            try:
                db.execute(migration)
                db.commit()
            except sqlite3.OperationalError:
                # Column already exists
                pass

        # Migration: Add lifetime warranty flag to supplies table
        try:
            db.execute('ALTER TABLE supplies ADD COLUMN warranty_lifetime BOOLEAN DEFAULT 0')
            db.commit()
        except sqlite3.OperationalError:
            # Column already exists
            pass

        # Migration: Add parts tracking columns to supplies table
        parts_tracking_migrations = [
            'ALTER TABLE supplies ADD COLUMN category TEXT',
            'ALTER TABLE supplies ADD COLUMN location TEXT',
            "ALTER TABLE supplies ADD COLUMN condition TEXT DEFAULT 'New'",
            'ALTER TABLE supplies ADD COLUMN supplier TEXT',
            'ALTER TABLE supplies ADD COLUMN purchase_date DATE',
            'ALTER TABLE supplies ADD COLUMN installation_date DATE',
            'ALTER TABLE supplies ADD COLUMN notes TEXT'
        ]

        for migration in parts_tracking_migrations:
            try:
                db.execute(migration)
                db.commit()
            except sqlite3.OperationalError:
                # Column already exists
                pass

        # Migration: Add station column to fuel_records table
        try:
            db.execute('ALTER TABLE fuel_records ADD COLUMN station TEXT')
            db.commit()
        except sqlite3.OperationalError:
            # Column already exists
            pass

        # Migration: Copy location data to station field where station is empty
        try:
            db.execute("UPDATE fuel_records SET station = location WHERE station IS NULL OR station = ''")
            db.commit()
        except sqlite3.OperationalError:
            pass

        # Migration: Drop location column from fuel_records table.
        # Only rebuild while the old column still exists: the rebuilt table has
        # no missed_fillup column, so running this on every startup wiped the
        # missed_fillup flag on every fuel record.
        fuel_columns = [row['name'] for row in db.execute('PRAGMA table_info(fuel_records)')]
        if 'location' in fuel_columns:
            try:
                # SQLite doesn't support DROP COLUMN directly, so we need to recreate the table
                db.execute('''CREATE TABLE IF NOT EXISTS fuel_records_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    vehicle_id INTEGER NOT NULL,
                    date DATE NOT NULL,
                    gallons REAL NOT NULL,
                    cost REAL NOT NULL,
                    odometer INTEGER NOT NULL,
                    station TEXT,
                    mpg REAL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (vehicle_id) REFERENCES vehicle (id)
                )''')

                # Copy data from old table to new table
                db.execute('''INSERT INTO fuel_records_new (id, vehicle_id, date, gallons, cost, odometer, station, mpg, created_at)
                             SELECT id, vehicle_id, date, gallons, cost, odometer, station, mpg, created_at
                             FROM fuel_records''')

                # Drop old table and rename new table
                db.execute('DROP TABLE fuel_records')
                db.execute('ALTER TABLE fuel_records_new RENAME TO fuel_records')
                db.commit()
            except sqlite3.OperationalError as e:
                # Table already migrated or error
                pass

        # Migration: Add missed_fillup column to fuel_records
        try:
            db.execute('ALTER TABLE fuel_records ADD COLUMN missed_fillup INTEGER DEFAULT 0')
            db.commit()
        except sqlite3.OperationalError:
            # Column already exists
            pass

        # Migration: Add tire_id column to tire_rotations table
        try:
            db.execute('ALTER TABLE tire_rotations ADD COLUMN tire_id INTEGER REFERENCES tires(id)')
            db.commit()
        except sqlite3.OperationalError:
            pass

        # Create tire_install_log table if it doesn't exist
        db.execute('''
            CREATE TABLE IF NOT EXISTS tire_install_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tire_id INTEGER NOT NULL,
                vehicle_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                date TEXT NOT NULL,
                odometer INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (tire_id) REFERENCES tires(id),
                FOREIGN KEY (vehicle_id) REFERENCES vehicle(id)
            )
        ''')
        db.commit()

        # Create default admin user if no users exist
        admin_exists = db.execute('SELECT COUNT(*) as count FROM users').fetchone()
        if admin_exists['count'] == 0:
            # Default admin credentials: username=admin, password=admin
            admin_password_hash = generate_password_hash('admin')
            db.execute('INSERT INTO users (username, password_hash, is_admin) VALUES (?, ?, ?)',
                      ('admin', admin_password_hash, 1))
            db.commit()

        # Documents table
        db.execute('''
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                vehicle_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                category TEXT NOT NULL,
                notes TEXT,
                file_path TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (vehicle_id) REFERENCES vehicle (id)
            )
        ''')
        db.commit()

        # Create indexes for performance optimization
        indexes = [
            'CREATE INDEX IF NOT EXISTS idx_service_records_vehicle_id ON service_records(vehicle_id)',
            'CREATE INDEX IF NOT EXISTS idx_service_records_date ON service_records(date DESC)',
            'CREATE INDEX IF NOT EXISTS idx_supplies_vehicle_id ON supplies(vehicle_id)',
            'CREATE INDEX IF NOT EXISTS idx_fuel_records_vehicle_id ON fuel_records(vehicle_id)',
            'CREATE INDEX IF NOT EXISTS idx_fuel_records_date ON fuel_records(date DESC)',
            'CREATE INDEX IF NOT EXISTS idx_fuel_records_odometer ON fuel_records(odometer DESC)',
            'CREATE INDEX IF NOT EXISTS idx_service_reminders_vehicle_id ON service_reminders(vehicle_id)',
            'CREATE INDEX IF NOT EXISTS idx_service_reminders_completed ON service_reminders(completed)',
            'CREATE INDEX IF NOT EXISTS idx_service_reminders_due_date ON service_reminders(due_date)',
            'CREATE INDEX IF NOT EXISTS idx_service_supplies_service_id ON service_supplies(service_id)',
            'CREATE INDEX IF NOT EXISTS idx_service_supplies_supply_id ON service_supplies(supply_id)',
            'CREATE INDEX IF NOT EXISTS idx_documents_vehicle_id ON documents(vehicle_id)'
        ]

        for index_sql in indexes:
            try:
                db.execute(index_sql)
            except sqlite3.OperationalError:
                # Index already exists
                pass

        # Settings table
        db.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        ''')
        db.commit()

        # Tires table
        db.execute('''
            CREATE TABLE IF NOT EXISTS tires (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                vehicle_id INTEGER NOT NULL,
                brand TEXT NOT NULL,
                model TEXT,
                size TEXT,
                season TEXT DEFAULT 'All-Season',
                dot_code TEXT,
                purchase_date TEXT,
                purchase_price REAL,
                is_installed INTEGER DEFAULT 0,
                installed_date TEXT,
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (vehicle_id) REFERENCES vehicle(id)
            )
        ''')
        db.execute('''
            CREATE TABLE IF NOT EXISTS tire_rotations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                vehicle_id INTEGER NOT NULL,
                tire_id INTEGER,
                date TEXT NOT NULL,
                odometer INTEGER,
                pattern TEXT,
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (vehicle_id) REFERENCES vehicle(id),
                FOREIGN KEY (tire_id) REFERENCES tires(id)
            )
        ''')
        db.execute('''
            CREATE TABLE IF NOT EXISTS tread_readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                vehicle_id INTEGER NOT NULL,
                tire_id INTEGER,
                date TEXT NOT NULL,
                odometer INTEGER,
                fl_depth REAL,
                fr_depth REAL,
                rl_depth REAL,
                rr_depth REAL,
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (vehicle_id) REFERENCES vehicle(id),
                FOREIGN KEY (tire_id) REFERENCES tires(id)
            )
        ''')
        db.commit()

        # Migration: opt-in per-unit tracking for supplies
        try:
            db.execute('ALTER TABLE supplies ADD COLUMN tracked_individually BOOLEAN DEFAULT 0')
            db.commit()
        except sqlite3.OperationalError:
            pass

        # Individually-tracked physical units of a supply (catalog row = supplies,
        # physical units = supply_units; same pattern as tires/tire_install_log)
        db.execute('''
            CREATE TABLE IF NOT EXISTS supply_units (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                supply_id INTEGER NOT NULL,
                vehicle_id INTEGER NOT NULL,
                purchase_date DATE,
                cost REAL,
                status TEXT NOT NULL DEFAULT 'in_stock',
                installation_date DATE,
                installed_service_id INTEGER,
                warranty_start_date DATE,
                warranty_months INTEGER,
                warranty_lifetime BOOLEAN DEFAULT 0,
                warranty_start_mileage INTEGER,
                warranty_mileage_limit INTEGER,
                receipt_path TEXT,
                supplier TEXT,
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (supply_id) REFERENCES supplies (id),
                FOREIGN KEY (vehicle_id) REFERENCES vehicle (id),
                FOREIGN KEY (installed_service_id) REFERENCES service_records (id)
            )
        ''')
        db.commit()

        # Migration: per-unit supplier. When the column is first added to an
        # existing table, seed it from the parent supply so backfilled units
        # keep their provenance. The ALTER fails once the column exists, so
        # the copy runs exactly once.
        try:
            db.execute('ALTER TABLE supply_units ADD COLUMN supplier TEXT')
            db.execute('''UPDATE supply_units
                          SET supplier = (SELECT s.supplier FROM supplies s
                                          WHERE s.id = supply_units.supply_id)
                          WHERE supplier IS NULL''')
            db.commit()
        except sqlite3.OperationalError:
            pass

        # Migration: link service_supplies rows to a specific unit
        try:
            db.execute('ALTER TABLE service_supplies ADD COLUMN supply_unit_id INTEGER REFERENCES supply_units(id)')
            db.commit()
        except sqlite3.OperationalError:
            pass

        supply_unit_indexes = [
            'CREATE INDEX IF NOT EXISTS idx_supply_units_supply_id ON supply_units(supply_id)',
            'CREATE INDEX IF NOT EXISTS idx_supply_units_vehicle_id ON supply_units(vehicle_id)',
            'CREATE INDEX IF NOT EXISTS idx_supply_units_status ON supply_units(status)'
        ]
        for index_sql in supply_unit_indexes:
            try:
                db.execute(index_sql)
            except sqlite3.OperationalError:
                pass
        db.commit()

        # One-time backfill: supplies that already carry warranty data become
        # individually tracked, with their quantity expanded into unit rows so
        # existing warranty values aren't dropped. Lossy by design: old flat
        # rows can't distinguish units, so each unit gets a copy of the
        # supply-level dates/warranty. Guarded so restarts never re-run it.
        backfilled = db.execute(
            "SELECT value FROM settings WHERE key = 'supply_units_backfilled'").fetchone()
        units_exist = db.execute('SELECT COUNT(*) AS c FROM supply_units').fetchone()['c'] > 0
        if (backfilled is None or backfilled['value'] != '1') and not units_exist:
            warranty_supplies = db.execute('''
                SELECT * FROM supplies
                WHERE warranty_months IS NOT NULL
                   OR warranty_lifetime = 1
                   OR warranty_start_date IS NOT NULL
            ''').fetchall()
            for s in warranty_supplies:
                db.execute('UPDATE supplies SET tracked_individually = 1 WHERE id = ?', (s['id'],))
                unit_count = max(1, int(s['quantity'] or 0))
                status = 'installed' if s['installation_date'] else 'in_stock'
                for _ in range(unit_count):
                    db.execute('''INSERT INTO supply_units
                                  (supply_id, vehicle_id, purchase_date, cost, status,
                                   installation_date, warranty_start_date, warranty_months,
                                   warranty_lifetime, warranty_start_mileage,
                                   warranty_mileage_limit, receipt_path, supplier)
                                  VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                              (s['id'], s['vehicle_id'], s['purchase_date'], s['cost'],
                               status, s['installation_date'], s['warranty_start_date'],
                               s['warranty_months'], s['warranty_lifetime'] or 0,
                               s['warranty_start_mileage'], s['warranty_mileage_limit'],
                               s['receipt_path'], s['supplier']))
            db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('supply_units_backfilled', '1')")
            db.commit()

        # Parts, purchases and installs: the model that replaces supplies,
        # supply_units and service_supplies above (see the parts section
        # further down). The old tables are kept, unused, as a backup.
        db.executescript('''
            CREATE TABLE IF NOT EXISTS parts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                part_number TEXT,
                brand TEXT,
                category TEXT,
                location TEXT,
                notes TEXT,
                reorder_threshold REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS part_purchases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                part_id INTEGER NOT NULL,
                purchase_date DATE,
                supplier TEXT,
                condition TEXT DEFAULT 'New',
                quantity REAL NOT NULL,
                unit_cost REAL NOT NULL DEFAULT 0,
                receipt_path TEXT,
                warranty_months INTEGER,
                warranty_miles INTEGER,
                warranty_lifetime BOOLEAN DEFAULT 0,
                warranty_starts TEXT NOT NULL DEFAULT 'install',
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (part_id) REFERENCES parts (id)
            );

            CREATE TABLE IF NOT EXISTS part_installs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                purchase_id INTEGER NOT NULL,
                vehicle_id INTEGER,
                service_id INTEGER,
                quantity REAL NOT NULL,
                install_date DATE,
                odometer INTEGER,
                removed_date DATE,
                warranty_start_date DATE,
                warranty_start_mileage INTEGER,
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (purchase_id) REFERENCES part_purchases (id),
                FOREIGN KEY (vehicle_id) REFERENCES vehicle (id),
                FOREIGN KEY (service_id) REFERENCES service_records (id)
            );

            CREATE INDEX IF NOT EXISTS idx_part_purchases_part_id ON part_purchases(part_id);
            CREATE INDEX IF NOT EXISTS idx_part_installs_purchase_id ON part_installs(purchase_id);
            CREATE INDEX IF NOT EXISTS idx_part_installs_service_id ON part_installs(service_id);
            CREATE INDEX IF NOT EXISTS idx_part_installs_vehicle_id ON part_installs(vehicle_id);
        ''')

        # One-time copy of the old supplies data into the tables above.
        # Guarded by the setting and by the tables being empty, so a restart
        # can never run it twice.
        if not db.execute("SELECT 1 FROM settings WHERE key = 'parts_migrated'").fetchone():
            if not db.execute('SELECT 1 FROM parts LIMIT 1').fetchone():
                migrate_supplies_to_parts(db)
            db.execute("INSERT INTO settings (key, value) VALUES ('parts_migrated', '1')")
            db.commit()

        # Service line items, maintenance schedule and attachments (see the
        # service records section further down)
        db.executescript('''
            CREATE TABLE IF NOT EXISTS service_types (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS service_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                service_id INTEGER NOT NULL,
                service_type_id INTEGER NOT NULL,
                cost REAL,
                notes TEXT,
                FOREIGN KEY (service_id) REFERENCES service_records (id),
                FOREIGN KEY (service_type_id) REFERENCES service_types (id)
            );

            CREATE TABLE IF NOT EXISTS maintenance_schedules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                vehicle_id INTEGER NOT NULL,
                service_type_id INTEGER NOT NULL,
                interval_miles INTEGER,
                interval_months INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (vehicle_id, service_type_id),
                FOREIGN KEY (vehicle_id) REFERENCES vehicle (id),
                FOREIGN KEY (service_type_id) REFERENCES service_types (id)
            );

            CREATE TABLE IF NOT EXISTS service_attachments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                service_id INTEGER NOT NULL,
                file_path TEXT NOT NULL,
                original_name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (service_id) REFERENCES service_records (id)
            );

            CREATE INDEX IF NOT EXISTS idx_service_items_service_id ON service_items(service_id);
            CREATE INDEX IF NOT EXISTS idx_service_items_type_id ON service_items(service_type_id);
            CREATE INDEX IF NOT EXISTS idx_service_attachments_service_id ON service_attachments(service_id);
        ''')

        for migration in (
                # Done by the owner rather than a shop
                'ALTER TABLE service_records ADD COLUMN diy INTEGER DEFAULT 0',
                # Set on reminders kept up to date from a maintenance schedule;
                # NULL on ones created by hand
                'ALTER TABLE service_reminders ADD COLUMN schedule_id INTEGER'):
            try:
                db.execute(migration)
                db.commit()
            except sqlite3.OperationalError:
                # Column already exists
                pass

        # Starter list of service types, added once. Intervals are left for
        # the owner to set per vehicle, so nothing nags with guessed numbers.
        if not db.execute("SELECT 1 FROM settings WHERE key = 'service_types_seeded'").fetchone():
            for name in DEFAULT_SERVICE_TYPES:
                db.execute('INSERT OR IGNORE INTO service_types (name) VALUES (?)', (name,))
            db.execute("INSERT INTO settings (key, value) VALUES ('service_types_seeded', '1')")
            db.commit()

        # One-time: a service used to hold a single receipt in receipt_path;
        # each becomes the first attachment
        if not db.execute("SELECT 1 FROM settings WHERE key = 'service_attachments_migrated'").fetchone():
            db.execute('''INSERT INTO service_attachments (service_id, file_path)
                          SELECT id, receipt_path FROM service_records
                          WHERE receipt_path IS NOT NULL AND receipt_path != ''
                            AND id NOT IN (SELECT service_id FROM service_attachments)''')
            db.execute("INSERT INTO settings (key, value) VALUES ('service_attachments_migrated', '1')")
            db.commit()

        for migration in (
                # 0 for a fill-up that didn't fill the tank; its gallons count
                # toward the next full tank's MPG
                'ALTER TABLE fuel_records ADD COLUMN full_tank INTEGER DEFAULT 1',
                'ALTER TABLE fuel_records ADD COLUMN grade TEXT',
                'ALTER TABLE fuel_records ADD COLUMN notes TEXT',
                # Miles and gallons of the full-tank span an MPG covers
                'ALTER TABLE fuel_records ADD COLUMN trip_miles INTEGER',
                'ALTER TABLE fuel_records ADD COLUMN trip_gallons REAL',
                # Driving pace set by the owner, for projecting mileage due dates
                'ALTER TABLE vehicle ADD COLUMN miles_per_year INTEGER'):
            try:
                db.execute(migration)
                db.commit()
            except sqlite3.OperationalError:
                # Column already exists
                pass

        # One-time: fill in the span columns for existing fuel records
        # (every existing fill-up counts as a full tank, so MPG is unchanged)
        if not db.execute("SELECT 1 FROM settings WHERE key = 'fuel_trips_calculated'").fetchone():
            for v in db.execute('SELECT id FROM vehicle').fetchall():
                recalculate_mpg(db, v['id'])
            db.execute("INSERT INTO settings (key, value) VALUES ('fuel_trips_calculated', '1')")
            db.commit()

        # One-time repair: MPG used to be computed only at insert time, so
        # edits, deletions and backdated fill-ups left stale values behind.
        if not db.execute("SELECT 1 FROM settings WHERE key = 'mpg_recalculated'").fetchone():
            for v in db.execute('SELECT id FROM vehicle').fetchall():
                recalculate_mpg(db, v['id'])
            db.execute("INSERT INTO settings (key, value) VALUES ('mpg_recalculated', '1')")
            db.commit()

        db.close()

DEFAULT_SERVICE_TYPES = [
    'Oil change', 'Oil filter', 'Tire rotation', 'Wheel alignment', 'Brake pads', 'Brake rotors',
    'Brake fluid', 'Engine air filter', 'Cabin air filter', 'Coolant', 'Transmission fluid',
    'Spark plugs', 'Battery', 'Wiper blades', 'Serpentine belt', 'Inspection', 'Diagnostics',
]

def get_setting(key, default='1'):
    """Get a setting value from the database"""
    db = get_db()
    row = db.execute('SELECT value FROM settings WHERE key = ?', (key,)).fetchone()
    db.close()
    return row['value'] if row else default

def set_setting(key, value):
    """Set a setting value in the database"""
    db = get_db()
    db.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)', (key, value))
    db.commit()
    db.close()

def allowed_file(filename):
    """Check if file extension is allowed"""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

def save_upload(field, subdir):
    """Save the uploaded file in request.files[field] under uploads/<subdir>
    and return its static-relative path, or None if nothing valid was sent.
    The random suffix keeps two uploads of the same name in the same second
    from overwriting each other."""
    return _store_upload(request.files.get(field), subdir)

def _store_upload(file, subdir):
    if not (file and file.filename and allowed_file(file.filename)):
        return None
    filename = (f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{secrets.token_hex(4)}_"
                f"{secure_filename(file.filename)}")
    folder = os.path.join(app.config['UPLOAD_FOLDER'], subdir)
    os.makedirs(folder, exist_ok=True)
    file.save(os.path.join(folder, filename))
    return f'uploads/{subdir}/{filename}'

def save_uploads(field, subdir):
    """Save every valid file sent under request.files[field]. Returns a list
    of (static-relative path, original file name)."""
    saved = []
    for file in request.files.getlist(field):
        path = _store_upload(file, subdir)
        if path:
            saved.append((path, file.filename))
    return saved

def remove_upload(static_path):
    """Delete an uploaded file given its static-relative path (e.g.
    'uploads/receipts/x.pdf'). Missing files are ignored."""
    if not static_path:
        return
    full_path = os.path.join(app.static_folder, static_path)
    try:
        os.remove(full_path)
    except OSError:
        pass

# Failed-login throttling. Attempts are recorded in the database (not in
# memory) so the limits hold across gunicorn worker processes.
LOGIN_WINDOW = timedelta(minutes=15)
MAX_FAILURES_PER_ACCOUNT = 5    # per client IP + username
MAX_FAILURES_PER_IP = 20        # per client IP, any usernames
DEFAULT_ADMIN_PASSWORD = 'admin'


def _login_retry_after(db, ip, username):
    """Seconds until this IP may try this username again, or 0 if allowed"""
    now = datetime.now(timezone.utc)
    db.execute('DELETE FROM login_failures WHERE attempted_at < ?',
               ((now - LOGIN_WINDOW).isoformat(),))
    for where, params, limit in (('ip = ? AND username = ?', (ip, username), MAX_FAILURES_PER_ACCOUNT),
                                 ('ip = ?', (ip,), MAX_FAILURES_PER_IP)):
        rows = db.execute(f'''SELECT attempted_at FROM login_failures WHERE {where}
                              ORDER BY attempted_at DESC LIMIT ?''', params + (limit,)).fetchall()
        if len(rows) >= limit:
            # Sliding window: allowed again once the oldest of the last
            # `limit` failures is older than the window
            oldest = datetime.fromisoformat(rows[-1]['attempted_at'])
            return max(1, int((oldest + LOGIN_WINDOW - now).total_seconds()) + 1)
    return 0


def authenticate(username, password):
    """Check credentials, enforcing the failed-login limits.
    Returns (user_row or None, retry_after_seconds)."""
    ip = request.remote_addr or 'unknown'
    username = username or ''
    db = get_db()
    try:
        retry_after = _login_retry_after(db, ip, username)
        if retry_after:
            db.commit()
            return None, retry_after
        user = db.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        if user and password and check_password_hash(user['password_hash'], password):
            db.execute('DELETE FROM login_failures WHERE ip = ? AND username = ?', (ip, username))
            db.commit()
            return user, 0
        db.execute('INSERT INTO login_failures (ip, username, attempted_at) VALUES (?, ?, ?)',
                   (ip, username, datetime.now(timezone.utc).isoformat()))
        db.commit()
        return None, 0
    finally:
        db.close()


def _too_many_attempts_message(retry_after):
    minutes = -(-retry_after // 60)  # round up
    return f"Too many failed login attempts. Try again in {minutes} minute{'s' if minutes != 1 else ''}."


@lru_cache(maxsize=16)
def _is_default_password_hash(password_hash):
    # Keyed on the hash, so a password change naturally invalidates the cache
    return check_password_hash(password_hash, DEFAULT_ADMIN_PASSWORD)


def default_admin_password_active():
    """True while the built-in admin account still has its default password"""
    db = get_db()
    row = db.execute("SELECT password_hash FROM users WHERE username = 'admin'").fetchone()
    db.close()
    return bool(row) and _is_default_password_hash(row['password_hash'])


# Authentication Routes
@app.route('/login', methods=['GET', 'POST'])
def login():
    """User login"""
    if current_user.is_authenticated:
        return redirect(url_for('index'))

    status = 200
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user, retry_after = authenticate(username, password)

        if user:
            login_user(User(user['id'], user['username'], user['is_admin']))
            if password == DEFAULT_ADMIN_PASSWORD:
                # Nag on every page (see base.html) until it's changed
                session['default_password'] = True
                return redirect(url_for('profile_page'))
            next_page = request.args.get('next', '')
            # Only follow same-site paths; '//host' and '/\host' are
            # protocol-relative URLs that browsers send off-site
            if not next_page.startswith('/') or next_page.startswith(('//', '/\\')):
                next_page = url_for('index')
            return redirect(next_page)
        elif retry_after:
            flash(_too_many_attempts_message(retry_after), 'error')
            status = 429
        else:
            flash('Invalid username or password', 'error')

    # The hint is only useful (and only shown) while the default still works
    show_creds = (get_setting('show_default_credentials', '1') == '1'
                  and default_admin_password_active())
    return render_template('login.html', show_default_credentials=show_creds), status

@app.route('/logout')
@login_required
def logout():
    """User logout"""
    logout_user()
    session.pop('default_password', None)
    return redirect(url_for('login'))

@app.route('/profile')
@login_required
def profile_page():
    """User profile page"""
    return render_template('profile.html')

@app.route('/admin')
@login_required
@admin_required
def admin_page():
    """Admin panel page"""
    return render_template('admin.html')

# Routes
@app.route('/')
@login_required
def index():
    """Main dashboard"""
    return render_template('index.html')

@app.route('/vehicle')
@login_required
def vehicle_page():
    """Vehicle profile page (legacy - redirects to vehicles)"""
    return render_template('vehicles.html')

@app.route('/vehicles')
@login_required
def vehicles_page():
    """Vehicles management page"""
    return render_template('vehicles.html')

@app.route('/services')
@login_required
def services_page():
    """Service records page"""
    return render_template('services.html')

@app.route('/parts')
@login_required
def parts_page():
    """Parts shelf and warranties page"""
    return render_template('parts.html')

@app.route('/supplies')
@login_required
def supplies_page():
    """Old address of the parts page"""
    return redirect(url_for('parts_page'))

@app.route('/fuel')
@login_required
def fuel_page():
    """Fuel tracker page"""
    return render_template('fuel.html')

@app.route('/reminders')
@login_required
def reminders_page():
    """Service reminders page"""
    return render_template('reminders.html')

@app.route('/documents')
@login_required
def documents_page():
    """Documents and notes page"""
    return render_template('documents.html')

@app.route('/report')
@login_required
def report_page():
    """Printable vehicle report page"""
    return render_template('report.html')

@app.route('/tires')
@login_required
def tires_page():
    """Tire log page"""
    return render_template('tires.html')

# API Endpoints

# User Management APIs
@app.route('/api/users', methods=['GET'])
@api_admin_required
def get_users():
    """Get all users (admin only)"""
    db = get_db()
    users = db.execute('SELECT id, username, is_admin, created_at FROM users ORDER BY created_at DESC').fetchall()
    db.close()
    return jsonify([dict(row) for row in users])

@app.route('/api/users', methods=['POST'])
@api_admin_required
def create_user():
    """Create new user (admin only)"""
    data = request.json
    username = data.get('username')
    password = data.get('password')
    is_admin = data.get('is_admin', False)

    if not username or not password:
        return jsonify({'error': 'Username and password are required'}), 400

    db = get_db()
    # Check if username already exists
    existing = db.execute('SELECT id FROM users WHERE username = ?', (username,)).fetchone()
    if existing:
        db.close()
        return jsonify({'error': 'Username already exists'}), 400

    password_hash = generate_password_hash(password)
    cursor = db.execute('INSERT INTO users (username, password_hash, is_admin) VALUES (?, ?, ?)',
                       (username, password_hash, is_admin))
    user_id = cursor.lastrowid
    db.commit()
    db.close()
    return jsonify({'success': True, 'id': user_id})

@app.route('/api/users/<int:user_id>', methods=['DELETE'])
@api_admin_required
def delete_user(user_id):
    """Delete a user (admin only)"""
    if user_id == _api_user().id:
        return jsonify({'error': 'Cannot delete your own account'}), 400

    db = get_db()
    _revoke_refresh_tokens(db, user_id)
    db.execute('DELETE FROM users WHERE id = ?', (user_id,))
    db.commit()
    db.close()
    return jsonify({'success': True})

@app.route('/api/users/<int:user_id>/reset-password', methods=['POST'])
@api_admin_required
def reset_user_password(user_id):
    """Reset user password (admin only)"""
    data = request.json
    new_password = data.get('password')

    if not new_password:
        return jsonify({'error': 'New password is required'}), 400

    db = get_db()
    password_hash = generate_password_hash(new_password)
    db.execute('UPDATE users SET password_hash = ? WHERE id = ?', (password_hash, user_id))
    _revoke_refresh_tokens(db, user_id)
    db.commit()
    if user_id == _api_user().id:
        session.pop('default_password', None)
    db.close()
    return jsonify({'success': True})

@app.route('/api/change-password', methods=['POST'])
@api_login_required
def change_password():
    """Change own password"""
    data = request.json
    current_password = data.get('current_password')
    new_password = data.get('new_password')

    if not current_password or not new_password:
        return jsonify({'error': 'Current and new password are required'}), 400

    user_id = _api_user().id
    db = get_db()
    user = db.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()

    if not user or not check_password_hash(user['password_hash'], current_password):
        db.close()
        return jsonify({'error': 'Current password is incorrect'}), 400

    password_hash = generate_password_hash(new_password)
    db.execute('UPDATE users SET password_hash = ? WHERE id = ?', (password_hash, user_id))
    # Sign out other devices: refresh tokens issued under the old password
    _revoke_refresh_tokens(db, user_id)
    db.commit()
    session.pop('default_password', None)
    db.close()
    return jsonify({'success': True})

# ── Token-based auth endpoints (for iOS / API clients) ───────────────────────

@app.route('/api/auth/login', methods=['POST'])
def api_login():
    """Exchange credentials for access + refresh tokens."""
    data = request.get_json(silent=True) or {}
    username = str(data.get('username') or '').strip()
    password = str(data.get('password') or '')
    if not username or not password:
        return jsonify({'error': 'username and password required'}), 400

    user, retry_after = authenticate(username, password)
    if retry_after:
        response = jsonify({'error': _too_many_attempts_message(retry_after)})
        response.headers['Retry-After'] = str(retry_after)
        return response, 429
    if not user:
        return jsonify({'error': 'Invalid credentials'}), 401

    access_token = _generate_access_token(user['id'], user['username'], user['is_admin'])
    refresh_token = _generate_refresh_token(user['id'])
    return jsonify({
        'access_token': access_token,
        'refresh_token': refresh_token,
        'token_type': 'Bearer',
        'expires_in': int(app.config['JWT_ACCESS_TOKEN_EXPIRES'].total_seconds()),
        'user': {'id': user['id'], 'username': user['username'], 'is_admin': bool(user['is_admin'])},
    })


@app.route('/api/auth/refresh', methods=['POST'])
def api_refresh():
    """Exchange a refresh token for a new access token."""
    data = request.get_json(silent=True) or {}
    refresh_token = data.get('refresh_token', '')
    if not refresh_token:
        return jsonify({'error': 'refresh_token required'}), 400

    db = get_db()
    row = db.execute(
        'SELECT * FROM refresh_tokens WHERE token = ?', (refresh_token,)
    ).fetchone()

    if not row:
        db.close()
        return jsonify({'error': 'Invalid refresh token'}), 401

    expires_at = datetime.fromisoformat(row['expires_at'])
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) > expires_at:
        db.execute('DELETE FROM refresh_tokens WHERE token = ?', (refresh_token,))
        db.commit()
        db.close()
        return jsonify({'error': 'Refresh token expired'}), 401

    user = db.execute('SELECT * FROM users WHERE id = ?', (row['user_id'],)).fetchone()
    db.close()

    if not user:
        return jsonify({'error': 'User not found'}), 401

    access_token = _generate_access_token(user['id'], user['username'], user['is_admin'])
    return jsonify({
        'access_token': access_token,
        'token_type': 'Bearer',
        'expires_in': int(app.config['JWT_ACCESS_TOKEN_EXPIRES'].total_seconds()),
    })


@app.route('/api/auth/logout', methods=['POST'])
def api_logout():
    """Revoke a refresh token."""
    data = request.get_json(silent=True) or {}
    refresh_token = data.get('refresh_token', '')
    if refresh_token:
        db = get_db()
        db.execute('DELETE FROM refresh_tokens WHERE token = ?', (refresh_token,))
        db.commit()
        db.close()
    return jsonify({'success': True})


def weighted_avg_mpg(db, vehicle_id):
    """Average MPG as total miles / total gallons over the full-tank spans
    that have an MPG, or None. A plain average of per-tank MPG would let a
    short span count as much as a long one."""
    row = db.execute('''SELECT SUM(trip_miles) * 1.0 / SUM(trip_gallons) AS avg
                        FROM fuel_records
                        WHERE vehicle_id = ? AND mpg > 0 AND trip_gallons > 0''',
                     (vehicle_id,)).fetchone()
    return round(row['avg'], 2) if row['avg'] else None

# Vehicle endpoints
@app.route('/api/vehicles/summary', methods=['GET'])
@api_login_required
def get_vehicles_summary():
    """Per-vehicle totals for the Vehicles page, computed in SQL instead of
    shipping every service and fuel record to the browser. Optional `year`
    (defaults to the current year) selects the year-to-date window."""
    year = str(request.args.get('year', type=int) or datetime.now().year)
    db = get_db()
    rows = db.execute('''
        SELECT v.id,
               (SELECT COUNT(*) FROM service_records s WHERE s.vehicle_id = v.id) AS service_count,
               (SELECT MAX(date) FROM service_records s WHERE s.vehicle_id = v.id) AS last_service,
               (SELECT COUNT(*) FROM fuel_records f WHERE f.vehicle_id = v.id) AS fuel_record_count,
               (SELECT MAX(date) FROM fuel_records f WHERE f.vehicle_id = v.id) AS last_fuel,
               (SELECT COALESCE(SUM(cost), 0) FROM fuel_records f WHERE f.vehicle_id = v.id) AS total_fuel_cost,
               (SELECT COALESCE(SUM(cost), 0) FROM fuel_records f
                 WHERE f.vehicle_id = v.id AND strftime('%Y', f.date) = :year) AS ytd_fuel_cost
        FROM vehicle v
    ''', {'year': year}).fetchall()

    # Service cost = labor/shop cost + parts used, per service
    service_costs = db.execute(f'''
        SELECT s.vehicle_id,
               SUM(s.cost + {PARTS_COST_SQL}) AS total,
               SUM(CASE WHEN strftime('%Y', s.date) = :year
                        THEN s.cost + {PARTS_COST_SQL} ELSE 0 END) AS ytd
        FROM service_records s
        GROUP BY s.vehicle_id
    ''', {'year': year}).fetchall()
    service_costs = {r['vehicle_id']: r for r in service_costs}

    summary = {}
    for r in rows:
        sc = service_costs.get(r['id'])
        total_service = round(sc['total'], 2) if sc else 0
        ytd_service = round(sc['ytd'], 2) if sc else 0
        summary[r['id']] = {
            'service_count': r['service_count'],
            'fuel_record_count': r['fuel_record_count'],
            'last_service': r['last_service'],
            'last_fuel': r['last_fuel'],
            'current_odometer': latest_odometer(db, r['id']),
            'avg_mpg': weighted_avg_mpg(db, r['id']),
            'total_service_cost': total_service,
            'total_fuel_cost': round(r['total_fuel_cost'], 2),
            'total_all_time_cost': round(total_service + r['total_fuel_cost'], 2),
            'ytd_service_cost': ytd_service,
            'ytd_fuel_cost': round(r['ytd_fuel_cost'], 2),
            'ytd_total_cost': round(ytd_service + r['ytd_fuel_cost'], 2),
        }
    db.close()
    return jsonify(summary)

@app.route('/api/vehicles', methods=['GET'])
@api_login_required
def get_vehicles():
    """Get all vehicles"""
    db = get_db()
    vehicles = db.execute('SELECT * FROM vehicle ORDER BY created_at DESC').fetchall()
    db.close()
    return jsonify([dict(row) for row in vehicles])

@app.route('/api/vehicle/<int:vehicle_id>', methods=['GET'])
@api_login_required
def get_vehicle(vehicle_id):
    """Get specific vehicle profile"""
    db = get_db()
    vehicle = db.execute('SELECT * FROM vehicle WHERE id = ?', (vehicle_id,)).fetchone()
    db.close()
    if vehicle:
        return jsonify(dict(vehicle))
    return jsonify(None)

@app.route('/api/vehicle', methods=['POST'])
@api_login_required
def add_vehicle():
    """Add new vehicle"""
    data = request.form
    picture_path = save_upload('picture', 'vehicles')

    db = get_db()
    cursor = db.execute('''INSERT INTO vehicle (manufacturer, model, year, engine, vin, nickname, picture)
                 VALUES (?, ?, ?, ?, ?, ?, ?)''',
              (data['manufacturer'], data['model'], data['year'],
               data.get('engine', ''), data.get('vin', ''), data.get('nickname', ''),
               picture_path))
    vehicle_id = cursor.lastrowid
    db.commit()
    db.close()
    return jsonify({'success': True, 'id': vehicle_id})

@app.route('/api/vehicle/<int:vehicle_id>', methods=['PUT'])
@api_login_required
def update_vehicle(vehicle_id):
    """Update existing vehicle"""
    data = request.form
    picture_path = save_upload('picture', 'vehicles')

    db = get_db()
    old = db.execute('SELECT picture FROM vehicle WHERE id = ?', (vehicle_id,)).fetchone()
    # A new upload replaces the picture; otherwise keep the existing one
    db.execute('''UPDATE vehicle SET manufacturer=?, model=?, year=?,
                 engine=?, vin=?, nickname=?, picture=COALESCE(?, picture) WHERE id=?''',
              (data['manufacturer'], data['model'], data['year'],
               data.get('engine', ''), data.get('vin', ''), data.get('nickname', ''),
               picture_path, vehicle_id))
    db.commit()
    db.close()
    if picture_path and old:
        remove_upload(old['picture'])
    return jsonify({'success': True})

@app.route('/api/vehicle/<int:vehicle_id>', methods=['DELETE'])
@api_login_required
def delete_vehicle(vehicle_id):
    """Delete a vehicle, everything recorded against it, and its uploaded files"""
    db = get_db()
    vehicle = db.execute('SELECT picture FROM vehicle WHERE id = ?', (vehicle_id,)).fetchone()
    if not vehicle:
        db.close()
        return jsonify({'error': 'Vehicle not found'}), 404

    # Collect file paths before the rows go away
    files = [vehicle['picture']]
    for table, column in (('service_records', 'receipt_path'),
                          ('documents', 'file_path')):
        rows = db.execute(f'SELECT {column} FROM {table} WHERE vehicle_id = ? AND {column} IS NOT NULL',
                          (vehicle_id,)).fetchall()
        files.extend(r[column] for r in rows)
    its_services = '(SELECT id FROM service_records WHERE vehicle_id = ?)'
    files.extend(r['file_path'] for r in db.execute(
        f'SELECT file_path FROM service_attachments WHERE service_id IN {its_services}',
        (vehicle_id,)).fetchall())
    db.execute(f'DELETE FROM service_attachments WHERE service_id IN {its_services}', (vehicle_id,))
    db.execute(f'DELETE FROM service_items WHERE service_id IN {its_services}', (vehicle_id,))

    # Parts are a shared shelf, so they outlive the vehicle. What it used
    # stays used (deleting the installs would put it back in stock); the
    # installs just lose their link to the vehicle and its services.
    db.execute('UPDATE part_installs SET vehicle_id = NULL, service_id = NULL WHERE vehicle_id = ?',
               (vehicle_id,))
    for table in ('service_records', 'maintenance_schedules', 'fuel_records',
                  'service_reminders', 'documents', 'tire_install_log',
                  'tire_rotations', 'tread_readings', 'tires'):
        db.execute(f'DELETE FROM {table} WHERE vehicle_id = ?', (vehicle_id,))
    db.execute('DELETE FROM vehicle WHERE id = ?', (vehicle_id,))
    db.commit()
    db.close()

    for path in set(files):
        remove_upload(path)
    return jsonify({'success': True})

# NHTSA API Integration endpoints
NHTSA_HEADERS = {
    'User-Agent': 'VST-Vehicle-Tracker/1.0',
    'Accept': 'application/json'
}

@app.route('/api/vehicle/decode-vin/<vin>', methods=['GET'])
@api_login_required
def decode_vin(vin):
    """Decode VIN using NHTSA API"""
    try:
        # NHTSA VIN Decoder API
        url = f'https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVinValues/{quote(vin, safe="")}'
        response = requests.get(url, params={'format': 'json'}, headers=NHTSA_HEADERS, timeout=10)

        if response.status_code == 200:
            data = response.json()

            # Extract relevant information from the response
            if data.get('Results') and len(data['Results']) > 0:
                result = data['Results'][0]

                # Return simplified vehicle information
                return jsonify({
                    'success': True,
                    'data': {
                        'vin': result.get('VIN', ''),
                        'manufacturer': result.get('Make', ''),
                        'model': result.get('Model', ''),
                        'year': result.get('ModelYear', ''),
                        'engine': result.get('EngineModel', '') or result.get('EngineCylinders', ''),
                        'body_type': result.get('BodyClass', ''),
                        'vehicle_type': result.get('VehicleType', ''),
                        'plant_city': result.get('PlantCity', ''),
                        'plant_state': result.get('PlantState', ''),
                        'plant_country': result.get('PlantCountry', ''),
                        'error_codes': result.get('ErrorCode', ''),
                        'error_text': result.get('ErrorText', '')
                    }
                })
            else:
                return jsonify({'success': False, 'error': 'No data returned from NHTSA'}), 400
        else:
            error_msg = f'NHTSA API returned status {response.status_code}'
            if response.text:
                error_msg += f': {response.text[:100]}'
            return jsonify({'success': False, 'error': error_msg}), 500

    except requests.exceptions.Timeout:
        return jsonify({'success': False, 'error': 'Request to NHTSA API timed out. Please try again.'}), 504
    except requests.exceptions.RequestException as e:
        return jsonify({'success': False, 'error': f'Network error: {str(e)}'}), 500
    except Exception as e:
        return jsonify({'success': False, 'error': f'Error: {str(e)}'}), 500

def _fetch_recalls(params):
    """Query the NHTSA recalls API and return a JSON response for the client"""
    try:
        # params= URL-encodes values, so makes/models with spaces or '&' work
        response = requests.get('https://api.nhtsa.gov/recalls/recallsByVehicle',
                                params=params, headers=NHTSA_HEADERS, timeout=10)

        # NHTSA API quirk: returns 400 even for successful queries with no results
        # Check if we got valid JSON regardless of status code
        if response.status_code not in (200, 400):
            error_msg = f'NHTSA Recalls API returned status {response.status_code}'
            if response.text:
                error_msg += f': {response.text[:100]}'
            return jsonify({'success': False, 'error': error_msg}), 500

        try:
            data = response.json()
        except ValueError:
            return jsonify({'success': False, 'error': 'Invalid JSON response from NHTSA'}), 500

        if 'results' not in data and 'Results' not in data:
            return jsonify({'success': False, 'error': 'Invalid response format from NHTSA'}), 500

        # Handle both lowercase and uppercase keys
        results = data.get('results') or data.get('Results') or []
        recalls = [{
            'nhtsa_campaign_number': recall.get('NHTSACampaignNumber', ''),
            'manufacturer': recall.get('Manufacturer', ''),
            'subject': recall.get('Subject', ''),
            'summary': recall.get('Summary', ''),
            'consequence': recall.get('Consequence', ''),
            'remedy': recall.get('Remedy', ''),
            'report_date': recall.get('ReportReceivedDate', ''),
            'component': recall.get('Component', '')
        } for recall in results]
        return jsonify({'success': True, 'count': len(recalls), 'recalls': recalls})

    except requests.exceptions.Timeout:
        return jsonify({'success': False, 'error': 'Request to NHTSA API timed out. Please try again.'}), 504
    except requests.exceptions.RequestException as e:
        return jsonify({'success': False, 'error': f'Network error: {str(e)}'}), 500

@app.route('/api/vehicle/recalls', methods=['GET'])
@api_login_required
def check_recalls():
    """Check for recalls using NHTSA API"""
    make = request.args.get('make')
    model = request.args.get('model')
    year = request.args.get('year')

    if not make or not model or not year:
        return jsonify({'success': False, 'error': 'Make, model, and year are required'}), 400

    return _fetch_recalls({'make': make, 'model': model, 'modelYear': year})

@app.route('/api/vehicle/recalls-by-vin/<vin>', methods=['GET'])
@api_login_required
def check_recalls_by_vin(vin):
    """Check for recalls by VIN using NHTSA API"""
    return _fetch_recalls({'vin': vin})

@app.route('/api/vehicle/recalls/create-reminders', methods=['POST'])
@api_login_required
def create_recall_reminders():
    """Create reminders for vehicle recalls"""
    try:
        data = request.json
        vehicle_id = data.get('vehicle_id')
        recalls = data.get('recalls', [])

        if not vehicle_id or not recalls:
            return jsonify({'success': False, 'error': 'Vehicle ID and recalls are required'}), 400

        db = get_db()
        created_count = 0

        for recall in recalls:
            campaign_number = recall.get('nhtsa_campaign_number', '')
            subject = recall.get('subject', '')
            component = recall.get('component', '')
            service_type = f"Recall Repair: {campaign_number}"

            # Check if reminder already exists for this recall. Exact match:
            # a LIKE '%<number>%' with a blank number matched every reminder.
            existing = db.execute(
                'SELECT id FROM service_reminders WHERE vehicle_id = ? AND service_type = ? AND completed = 0',
                (vehicle_id, service_type)
            ).fetchone()

            if not existing:
                # Create reminder for this recall
                notes = f"Component: {component}\n\nSubject: {subject}\n\n"
                notes += f"Summary: {recall.get('summary', 'N/A')}\n\n"
                notes += f"Remedy: {recall.get('remedy', 'N/A')}"

                db.execute(
                    'INSERT INTO service_reminders (vehicle_id, service_type, notes, completed) VALUES (?, ?, ?, ?)',
                    (vehicle_id, service_type, notes, 0)
                )
                created_count += 1

        db.commit()
        db.close()

        return jsonify({
            'success': True,
            'created': created_count,
            'message': f'Created {created_count} recall reminder(s)'
        })

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# Service records endpoints
#
# A service record is one visit or job: a date, the total bill, who did it
# and the odometer. Hanging off it are
#   service_items        what was done, one row per service type, each with
#                        an optional price (the bill total is entered once
#                        on the record; item prices are a breakdown, not
#                        added to it)
#   part_installs        parts from the shelf used on the job
#   service_attachments  receipts and photos
# Service types can carry a per-vehicle interval (maintenance_schedules);
# logging a service then moves that type's reminder to the next due point.

# Cost of the parts used on a service: each install is priced at the unit
# cost of the purchase it was drawn from, so old services keep their real
# cost when prices change. The API still calls this `supplies_cost`.
PARTS_COST_SQL = '''(SELECT COALESCE(SUM(pi.quantity * pp.unit_cost), 0)
                     FROM part_installs pi
                     JOIN part_purchases pp ON pp.id = pi.purchase_id
                     WHERE pi.service_id = s.id)'''
SERVICE_SELECT = f'''
    SELECT s.*, {PARTS_COST_SQL} AS supplies_cost,
           (SELECT GROUP_CONCAT(st.name, ', ') FROM service_items si
            JOIN service_types st ON st.id = si.service_type_id
            WHERE si.service_id = s.id) AS service_types,
           (SELECT GROUP_CONCAT(si.service_type_id) FROM service_items si
            WHERE si.service_id = s.id) AS service_type_ids,
           (SELECT COUNT(*) FROM service_attachments a WHERE a.service_id = s.id) AS attachment_count
    FROM service_records s'''

# Matches the free-text fields of a service or the names of its service types
SERVICE_TEXT_MATCH = '''(LOWER(s.service_provider) LIKE :q OR LOWER(s.comments) LIKE :q
                         OR LOWER(s.repairs_completed) LIKE :q
                         OR s.id IN (SELECT si.service_id FROM service_items si
                                     JOIN service_types st ON st.id = si.service_type_id
                                     WHERE LOWER(st.name) LIKE :q))'''

# A scheduled service is flagged as due soon once it is this close
MAINTENANCE_DUE_SOON_DAYS = 30
MAINTENANCE_DUE_SOON_MILES = 500


def _service_dict(row):
    service = dict(row)
    ids = service.get('service_type_ids')
    service['service_type_ids'] = [int(i) for i in ids.split(',')] if ids else []
    service['diy'] = bool(service.get('diy'))
    return service


@app.route('/api/services', methods=['GET'])
@api_login_required
def get_services():
    """Get all service records for a vehicle with optional pagination"""
    vehicle_id = request.args.get('vehicle_id')
    page = request.args.get('page', type=int)
    per_page = request.args.get('per_page', 20, type=int)

    db = get_db()
    if vehicle_id:
        where_clause = ' WHERE s.vehicle_id = ?'
        params = (vehicle_id,)
    else:
        where_clause = ''
        params = ()
    query = SERVICE_SELECT + where_clause + ' ORDER BY s.date DESC, s.id DESC'

    if page is not None:
        total_count = db.execute('SELECT COUNT(*) AS total FROM service_records s' + where_clause,
                                 params).fetchone()['total']
        offset = (page - 1) * per_page
        services = db.execute(query + ' LIMIT ? OFFSET ?', params + (per_page, offset)).fetchall()
        db.close()
        return jsonify({
            'data': [_service_dict(row) for row in services],
            'total': total_count,
            'page': page,
            'per_page': per_page,
            'total_pages': (total_count + per_page - 1) // per_page
        })

    # Return all results (backwards compatibility)
    services = db.execute(query, params).fetchall()
    db.close()
    return jsonify([_service_dict(row) for row in services])

@app.route('/api/services/<int:service_id>', methods=['GET'])
@api_login_required
def get_service(service_id):
    """Get a specific service record with its line items, parts and attachments"""
    db = get_db()
    service = db.execute(SERVICE_SELECT + ' WHERE s.id = ?', (service_id,)).fetchone()
    if not service:
        db.close()
        return jsonify(None), 404

    installs = load_installs(db, 'pi.service_id = ?', (service_id,))
    items = db.execute('''SELECT si.id, si.service_type_id AS type_id, st.name, si.cost, si.notes
                          FROM service_items si
                          JOIN service_types st ON st.id = si.service_type_id
                          WHERE si.service_id = ? ORDER BY si.id''', (service_id,)).fetchall()
    attachments = db.execute('''SELECT id, file_path, original_name FROM service_attachments
                                WHERE service_id = ? ORDER BY id''', (service_id,)).fetchall()
    db.close()

    result = _service_dict(service)
    result['items'] = [dict(row) for row in items]
    result['attachments'] = [dict(row) for row in attachments]
    result['parts'] = installs
    # Older API clients read `supplies` as [{id, name, cost, quantity}]
    result['supplies'] = [{'id': i['part_id'], 'name': i['part_name'],
                           'cost': i['unit_cost'], 'quantity': i['quantity']} for i in installs]
    return jsonify(result)

@app.route('/api/services/search', methods=['GET'])
@api_login_required
def search_services():
    """Search service records by provider, notes or type of work"""
    params = {'q': f"%{request.args.get('q', '').lower()}%"}
    where = ' WHERE ' + SERVICE_TEXT_MATCH
    vehicle_id = request.args.get('vehicle_id')
    if vehicle_id:
        where += ' AND s.vehicle_id = :vehicle_id'
        params['vehicle_id'] = vehicle_id

    db = get_db()
    services = db.execute(SERVICE_SELECT + where + ' ORDER BY s.date DESC, s.id DESC', params).fetchall()
    db.close()
    return jsonify([_service_dict(row) for row in services])

def _json_list(data, *keys):
    """A JSON list sent as one form field (or as real JSON), looked up under
    the first of `keys` that is present; None if the request has none of them."""
    raw = None
    for key in keys:
        raw = data.get(key)
        if raw is not None:
            break
    if raw is None:
        return None
    try:
        entries = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        raise ApiError(f'{keys[0]} is not valid JSON')
    if not isinstance(entries, list):
        raise ApiError(f'{keys[0]} must be a list')
    return entries

def _service_fields(data):
    """Validated core fields of a service record from a form"""
    service_date = _date(data, 'date', 'Date')
    if not service_date:
        raise ApiError('Date is required')
    cost = _number(data, 'cost', 'Total bill')
    if cost is None:
        raise ApiError('Total bill is required (enter 0 if it cost nothing)')
    diy = _flag(data, 'diy')
    provider = _text(data, 'service_provider') or ('Self' if diy else None)
    if not provider:
        raise ApiError('Service provider is required')
    return {
        'date': service_date,
        'cost': cost,
        'service_provider': provider,
        'diy': 1 if diy else 0,
        'odometer': _number(data, 'odometer', 'Odometer', integer=True),
        'comments': data.get('comments', ''),
        'repairs_completed': data.get('repairs_completed', ''),
    }

def get_or_create_service_type(db, name):
    name = (name or '').strip()
    if not name:
        raise ApiError('Service type name is required')
    row = db.execute('SELECT id FROM service_types WHERE name = ? COLLATE NOCASE', (name,)).fetchone()
    if row:
        return row['id']
    return db.execute('INSERT INTO service_types (name) VALUES (?)', (name,)).lastrowid

def service_type_ids(db, service_id):
    return {row['service_type_id'] for row in db.execute(
        'SELECT service_type_id FROM service_items WHERE service_id = ?', (service_id,)).fetchall()}

def sync_service_items(db, service_id, entries):
    """Replace a service's line items with `entries`: each is {type_id} or
    {type_name} (created if new), with an optional `cost` and `notes`."""
    db.execute('DELETE FROM service_items WHERE service_id = ?', (service_id,))
    for entry in entries:
        if not isinstance(entry, dict):
            raise ApiError('Each line item must be an object')
        type_id = entry.get('type_id')
        if type_id:
            if not db.execute('SELECT 1 FROM service_types WHERE id = ?', (type_id,)).fetchone():
                raise ApiError('Unknown service type; reload and try again')
        else:
            type_id = get_or_create_service_type(db, entry.get('type_name') or entry.get('name'))
        cost = _number(entry, 'cost', 'Line item price')
        db.execute('INSERT INTO service_items (service_id, service_type_id, cost, notes) VALUES (?, ?, ?, ?)',
                   (service_id, type_id, cost, _text(entry, 'notes')))

def sync_service_parts(db, service_id, vehicle_id, service_date, odometer, entries):
    """Make the installs linked to a service match `entries`.

    Each entry is {install_id, quantity} for a part already on the service,
    or {part_id, quantity[, purchase_id]} for one being added (older clients
    send the part as `id`). Installs left out of the list are deleted, which
    returns their stock to the shelf. Installs that stay keep their warranty
    overrides and notes; only quantity, date and odometer follow the service."""
    existing = {row['id']: row for row in db.execute(
        'SELECT * FROM part_installs WHERE service_id = ?', (service_id,)).fetchall()}
    keep = {}
    additions = []
    for entry in entries:
        try:
            quantity = float(entry.get('quantity') or 0)
        except (TypeError, ValueError, AttributeError):
            raise ApiError('Part quantity must be a number')
        if quantity <= 0:
            raise ApiError('Part quantity must be greater than 0')
        install_id = entry.get('install_id')
        if install_id:
            if int(install_id) not in existing:
                raise ApiError('A part on this service no longer exists; reload and try again')
            keep[int(install_id)] = quantity
        else:
            part_id = entry.get('part_id') or entry.get('id')
            if not part_id:
                raise ApiError('Each part needs a part_id')
            additions.append((int(part_id), quantity, entry.get('purchase_id')))

    # Free stock first so a reduced or removed line can cover an added one
    for install_id in existing:
        if install_id not in keep:
            db.execute('DELETE FROM part_installs WHERE id = ?', (install_id,))
    for install_id, quantity in keep.items():
        old = existing[install_id]
        if abs(quantity - old['quantity']) > 1e-9:
            lot = purchase_with_remaining(db, old['purchase_id'])
            available = lot['remaining'] + old['quantity']
            if quantity > available + 1e-9:
                raise ApiError(f'Only {available:g} available from that purchase')
            db.execute('UPDATE part_installs SET quantity = ? WHERE id = ?', (quantity, install_id))
    db.execute('UPDATE part_installs SET install_date = ?, odometer = ? WHERE service_id = ?',
               (service_date, odometer, service_id))
    for part_id, quantity, purchase_id in additions:
        allocate_install(db, part_id, quantity, vehicle_id=vehicle_id, service_id=service_id,
                         install_date=service_date, odometer=odometer, purchase_id=purchase_id)

def _add_attachments(db, service_id, uploads):
    for path, name in uploads:
        db.execute('INSERT INTO service_attachments (service_id, file_path, original_name) VALUES (?, ?, ?)',
                   (service_id, path, name))

def _sync_receipt_path(db, service_id):
    """service_records.receipt_path mirrors the first attachment, for the
    list views and API clients that only know about one receipt"""
    db.execute('''UPDATE service_records
                  SET receipt_path = (SELECT file_path FROM service_attachments
                                      WHERE service_id = ? ORDER BY id LIMIT 1)
                  WHERE id = ?''', (service_id, service_id))

def odometer_warning(db, vehicle_id, record_date, odometer, service_id=None, fuel_id=None):
    """A message if this reading is out of step with the vehicle's other
    service and fuel records, else None. Readings feed MPG, mileage
    warranties and maintenance intervals, so a typo here matters. Pass the
    id of the record being saved so it isn't compared with itself."""
    if odometer is None:
        return None
    readings = '''SELECT date, odometer FROM service_records
                  WHERE vehicle_id = :v AND id != :s AND odometer IS NOT NULL
                  UNION ALL
                  SELECT date, odometer FROM fuel_records WHERE vehicle_id = :v AND id != :f'''
    params = {'v': vehicle_id, 's': service_id or 0, 'f': fuel_id or 0, 'd': record_date}
    earlier = db.execute(f'SELECT MAX(odometer) AS o FROM ({readings}) WHERE date < :d', params).fetchone()['o']
    later = db.execute(f'SELECT MIN(odometer) AS o FROM ({readings}) WHERE date > :d', params).fetchone()['o']
    if earlier is not None and odometer < earlier:
        return (f'Saved, but check the odometer: {odometer:,} is lower than an earlier '
                f'reading of {earlier:,}.')
    if later is not None and odometer > later:
        return (f'Saved, but check the odometer: {odometer:,} is higher than a later '
                f'reading of {later:,}.')
    return None

@app.route('/api/services', methods=['POST'])
@api_login_required
def add_service():
    """Add new service record.

    Form fields: vehicle_id, date, cost (the total bill), service_provider
    (optional when diy is set), odometer, repairs_completed, comments, and
    JSON lists `items` (line items) and `parts`. Files: any number under
    `attachments` (the older single `receipt` field also works)."""
    data = request.form
    vehicle_id = _number(data, 'vehicle_id', 'Vehicle', integer=True)
    if not vehicle_id:
        raise ApiError('Vehicle ID is required')
    fields = _service_fields(data)
    items = _json_list(data, 'items')
    parts = _json_list(data, 'parts', 'supplies')

    uploads = save_uploads('receipt', 'receipts') + save_uploads('attachments', 'receipts')
    try:
        with db_session() as db:
            cursor = db.execute('''INSERT INTO service_records
                                  (vehicle_id, date, cost, service_provider, diy, odometer, comments,
                                   repairs_completed)
                                  VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                               (vehicle_id, fields['date'], fields['cost'], fields['service_provider'],
                                fields['diy'], fields['odometer'], fields['comments'],
                                fields['repairs_completed']))
            service_id = cursor.lastrowid
            if items:
                sync_service_items(db, service_id, items)
            if parts:
                sync_service_parts(db, service_id, vehicle_id, fields['date'], fields['odometer'], parts)
            _add_attachments(db, service_id, uploads)
            _sync_receipt_path(db, service_id)
            for type_id in service_type_ids(db, service_id):
                refresh_schedule_reminder(db, vehicle_id, type_id)
            warning = odometer_warning(db, vehicle_id, fields['date'], fields['odometer'], service_id=service_id)
    except Exception:
        for path, _ in uploads:
            remove_upload(path)
        raise
    return jsonify({'success': True, 'id': service_id, 'warning': warning})

@app.route('/api/services/<int:service_id>', methods=['PUT'])
@api_login_required
def update_service(service_id):
    """Update existing service record. `items` and `parts` are left alone
    when the form doesn't send them. A file under `receipt` replaces the
    first attachment (the old single-receipt behaviour); files under
    `attachments` are added; `remove_attachments` is a JSON list of
    attachment ids to delete."""
    data = request.form
    fields = _service_fields(data)
    items = _json_list(data, 'items')
    parts = _json_list(data, 'parts', 'supplies')
    remove_ids = _json_list(data, 'remove_attachments') or []

    replacement = save_uploads('receipt', 'receipts')
    uploads = replacement + save_uploads('attachments', 'receipts')
    removed_files = []
    try:
        with db_session() as db:
            old = db.execute('SELECT vehicle_id FROM service_records WHERE id = ?', (service_id,)).fetchone()
            if not old:
                raise ApiError('Service record not found', 404)
            vehicle_id = old['vehicle_id']
            affected_types = service_type_ids(db, service_id)

            db.execute('''UPDATE service_records
                         SET date=?, cost=?, service_provider=?, diy=?, odometer=?, comments=?,
                             repairs_completed=?
                         WHERE id=?''',
                      (fields['date'], fields['cost'], fields['service_provider'], fields['diy'],
                       fields['odometer'], fields['comments'], fields['repairs_completed'], service_id))

            if items is not None:
                sync_service_items(db, service_id, items)
            if parts is None:
                # Form didn't mention parts: leave them on the service, but
                # keep their install date and odometer in step with it
                db.execute('UPDATE part_installs SET install_date = ?, odometer = ? WHERE service_id = ?',
                           (fields['date'], fields['odometer'], service_id))
            else:
                sync_service_parts(db, service_id, vehicle_id, fields['date'], fields['odometer'], parts)

            doomed = [int(i) for i in remove_ids]
            if replacement:
                first = db.execute('SELECT id FROM service_attachments WHERE service_id = ? ORDER BY id LIMIT 1',
                                   (service_id,)).fetchone()
                if first:
                    doomed.append(first['id'])
            for attachment_id in doomed:
                row = db.execute('SELECT file_path FROM service_attachments WHERE id = ? AND service_id = ?',
                                 (attachment_id, service_id)).fetchone()
                if row:
                    removed_files.append(row['file_path'])
                    db.execute('DELETE FROM service_attachments WHERE id = ?', (attachment_id,))
            _add_attachments(db, service_id, uploads)
            _sync_receipt_path(db, service_id)

            for type_id in affected_types | service_type_ids(db, service_id):
                refresh_schedule_reminder(db, vehicle_id, type_id)
            warning = odometer_warning(db, vehicle_id, fields['date'], fields['odometer'], service_id=service_id)
    except Exception:
        for path, _ in uploads:
            remove_upload(path)
        raise
    for path in removed_files:
        remove_upload(path)
    return jsonify({'success': True, 'warning': warning})

@app.route('/api/services/<int:service_id>', methods=['DELETE'])
@api_login_required
def delete_service(service_id):
    """Delete a service record; the parts used on it go back on the shelf
    and any scheduled reminders fall back to the previous time it was done"""
    with db_session() as db:
        service = db.execute('SELECT vehicle_id FROM service_records WHERE id = ?', (service_id,)).fetchone()
        files = [r['file_path'] for r in db.execute(
            'SELECT file_path FROM service_attachments WHERE service_id = ?', (service_id,)).fetchall()]
        affected_types = service_type_ids(db, service_id)
        db.execute('DELETE FROM service_items WHERE service_id = ?', (service_id,))
        db.execute('DELETE FROM service_attachments WHERE service_id = ?', (service_id,))
        db.execute('DELETE FROM part_installs WHERE service_id = ?', (service_id,))
        db.execute('DELETE FROM service_records WHERE id = ?', (service_id,))
        if service:
            for type_id in affected_types:
                refresh_schedule_reminder(db, service['vehicle_id'], type_id)
    for path in files:
        remove_upload(path)
    return jsonify({'success': True})

# ── Service types and maintenance schedule ──────────────────────────────────

def _interval_text(miles, months):
    parts = []
    if miles:
        parts.append(f'{miles:,} miles')
    if months:
        parts.append(f"{months} month{'s' if months != 1 else ''}")
    return ' or '.join(parts)

def last_service_of_type(db, vehicle_id, type_id):
    """The most recent service on a vehicle that included a service type"""
    return db.execute('''SELECT s.id, s.date, s.odometer
                         FROM service_items si
                         JOIN service_records s ON s.id = si.service_id
                         WHERE s.vehicle_id = ? AND si.service_type_id = ?
                         ORDER BY s.date DESC, COALESCE(s.odometer, 0) DESC, s.id DESC
                         LIMIT 1''', (vehicle_id, type_id)).fetchone()

def next_due(last_date, last_odometer, interval_miles, interval_months):
    """(due_date, due_mileage) counted from the last time a service was done"""
    start = _parse_date(last_date)
    due_date = add_months(start, int(interval_months)).isoformat() if interval_months and start else None
    due_mileage = last_odometer + interval_miles if interval_miles and last_odometer is not None else None
    return due_date, due_mileage

def refresh_schedule_reminder(db, vehicle_id, type_id):
    """Bring the automatic reminder for one vehicle's scheduled service type
    in line with the service history: due one interval after the last time
    it was done. Safe to call any number of times. Called whenever a service
    with that type is added, changed or deleted, or the schedule changes.

    Reminders people create by hand have no schedule_id and are never touched."""
    schedule = db.execute('''SELECT ms.*, st.name FROM maintenance_schedules ms
                             JOIN service_types st ON st.id = ms.service_type_id
                             WHERE ms.vehicle_id = ? AND ms.service_type_id = ?''',
                          (vehicle_id, type_id)).fetchone()
    if not schedule:
        return
    open_ids = [r['id'] for r in db.execute(
        'SELECT id FROM service_reminders WHERE schedule_id = ? AND completed = 0 ORDER BY id',
        (schedule['id'],)).fetchall()]

    last = last_service_of_type(db, vehicle_id, type_id)
    due_date, due_mileage = (None, None)
    if last:
        due_date, due_mileage = next_due(last['date'], last['odometer'],
                                         schedule['interval_miles'], schedule['interval_months'])
    if due_date is None and due_mileage is None:
        # Never logged (or nothing to count from): no baseline, no reminder
        for reminder_id in open_ids:
            db.execute('DELETE FROM service_reminders WHERE id = ?', (reminder_id,))
        return

    notes = (f"Scheduled every {_interval_text(schedule['interval_miles'], schedule['interval_months'])}. "
             f"Last done {last['date']}"
             + (f" at {last['odometer']:,} miles." if last['odometer'] is not None else '.'))
    if open_ids:
        db.execute('''UPDATE service_reminders SET service_type = ?, due_date = ?, due_mileage = ?, notes = ?
                      WHERE id = ?''', (schedule['name'], due_date, due_mileage, notes, open_ids[0]))
        for reminder_id in open_ids[1:]:
            db.execute('DELETE FROM service_reminders WHERE id = ?', (reminder_id,))
        return
    # Don't bring back one the user already ticked off for this same due point
    done = db.execute('''SELECT 1 FROM service_reminders
                         WHERE schedule_id = ? AND completed = 1 AND due_date IS ? AND due_mileage IS ?''',
                      (schedule['id'], due_date, due_mileage)).fetchone()
    if not done:
        db.execute('''INSERT INTO service_reminders
                      (vehicle_id, service_type, due_date, due_mileage, notes, completed, schedule_id)
                      VALUES (?, ?, ?, ?, ?, 0, ?)''',
                   (vehicle_id, schedule['name'], due_date, due_mileage, notes, schedule['id']))

@app.route('/api/service-types', methods=['GET'])
@api_login_required
def get_service_types():
    """The list of service types, with how many service records use each"""
    db = get_db()
    rows = db.execute('''SELECT st.id, st.name,
                                (SELECT COUNT(*) FROM service_items si WHERE si.service_type_id = st.id) AS uses
                         FROM service_types st ORDER BY st.name COLLATE NOCASE''').fetchall()
    db.close()
    return jsonify([dict(row) for row in rows])

@app.route('/api/service-types', methods=['POST'])
@api_login_required
def add_service_type():
    data = _payload()
    with db_session() as db:
        type_id = get_or_create_service_type(db, data.get('name'))
    return jsonify({'success': True, 'id': type_id})

@app.route('/api/service-types/<int:type_id>', methods=['PUT'])
@api_login_required
def rename_service_type(type_id):
    name = _text(_payload(), 'name')
    if not name:
        raise ApiError('Service type name is required')
    with db_session() as db:
        if not db.execute('SELECT 1 FROM service_types WHERE id = ?', (type_id,)).fetchone():
            raise ApiError('Service type not found', 404)
        clash = db.execute('SELECT 1 FROM service_types WHERE name = ? COLLATE NOCASE AND id != ?',
                           (name, type_id)).fetchone()
        if clash:
            raise ApiError(f'There is already a service type called {name}')
        db.execute('UPDATE service_types SET name = ? WHERE id = ?', (name, type_id))
        # Scheduled reminders carry the name
        for row in db.execute('SELECT vehicle_id FROM maintenance_schedules WHERE service_type_id = ?',
                              (type_id,)).fetchall():
            refresh_schedule_reminder(db, row['vehicle_id'], type_id)
    return jsonify({'success': True})

@app.route('/api/service-types/<int:type_id>', methods=['DELETE'])
@api_login_required
def delete_service_type(type_id):
    """Delete a service type that no service record uses"""
    with db_session() as db:
        uses = db.execute('SELECT COUNT(*) AS c FROM service_items WHERE service_type_id = ?',
                          (type_id,)).fetchone()['c']
        if uses:
            raise ApiError(f"This type is on {uses} service record{'s' if uses != 1 else ''}. "
                           'Rename it instead, or remove it from those records first.')
        db.execute('''DELETE FROM service_reminders WHERE completed = 0 AND schedule_id IN
                      (SELECT id FROM maintenance_schedules WHERE service_type_id = ?)''', (type_id,))
        db.execute('DELETE FROM maintenance_schedules WHERE service_type_id = ?', (type_id,))
        db.execute('DELETE FROM service_types WHERE id = ?', (type_id,))
    return jsonify({'success': True})

@app.route('/api/maintenance', methods=['GET'])
@api_login_required
def get_maintenance():
    """One row per service type for a vehicle: its interval (if scheduled),
    when it was last done, when it is next due and what it has cost."""
    vehicle_id = request.args.get('vehicle_id', type=int)
    if not vehicle_id:
        raise ApiError('vehicle_id is required')
    db = get_db()
    types = db.execute('''SELECT st.id, st.name, ms.id AS schedule_id, ms.interval_miles, ms.interval_months,
                                 (SELECT COUNT(*) FROM service_items si WHERE si.service_type_id = st.id) AS uses
                          FROM service_types st
                          LEFT JOIN maintenance_schedules ms
                                 ON ms.service_type_id = st.id AND ms.vehicle_id = ?
                          ORDER BY st.name COLLATE NOCASE''', (vehicle_id,)).fetchall()
    items = db.execute(f'''SELECT si.service_type_id, si.cost AS item_cost, s.id AS service_id, s.date,
                                  s.odometer, s.cost AS service_cost, {PARTS_COST_SQL} AS parts_cost,
                                  (SELECT COUNT(*) FROM service_items x WHERE x.service_id = s.id) AS item_count
                           FROM service_items si
                           JOIN service_records s ON s.id = si.service_id
                           WHERE s.vehicle_id = ?''', (vehicle_id,)).fetchall()
    odometer = latest_odometer(db, vehicle_id)
    pace = driving_pace(db, vehicle_id)
    db.close()

    today = datetime.now().date()
    result = []
    for t in types:
        mine = [i for i in items if i['service_type_id'] == t['id']]
        services = {i['service_id']: i for i in mine}.values()
        last = max(services, key=lambda i: (i['date'] or '', i['odometer'] or 0, i['service_id']), default=None)

        # What this type has cost: the line item's own price where one was
        # entered; the whole bill (plus parts) when it was the only thing
        # done; otherwise unknown, and the total is flagged as partial
        spent = 0
        partial = False
        for i in mine:
            if i['item_cost'] is not None:
                spent += i['item_cost']
            elif i['item_count'] == 1:
                spent += (i['service_cost'] or 0) + (i['parts_cost'] or 0)
            else:
                partial = True

        row = {
            'type_id': t['id'], 'name': t['name'], 'uses': t['uses'],
            'schedule_id': t['schedule_id'],
            'interval_miles': t['interval_miles'], 'interval_months': t['interval_months'],
            'times_done': len(services),
            'spent': round(spent, 2), 'spent_partial': partial,
            'last_date': last['date'] if last else None,
            'last_odometer': last['odometer'] if last else None,
            'days_since': None, 'miles_since': None,
            'next_due_date': None, 'next_due_mileage': None,
            'days_remaining': None, 'miles_remaining': None, 'status': None,
            'projected_date': None,
        }
        if last:
            last_date = _parse_date(last['date'])
            if last_date:
                row['days_since'] = (today - last_date).days
            if last['odometer'] is not None and odometer is not None:
                row['miles_since'] = odometer - last['odometer']
            if t['schedule_id']:
                due_date, due_mileage = next_due(last['date'], last['odometer'],
                                                 t['interval_miles'], t['interval_months'])
                row['next_due_date'], row['next_due_mileage'] = due_date, due_mileage
                row['projected_date'] = projected_date(due_mileage, odometer, pace['effective_miles_per_year'])
                overdue = soon = False
                if due_date:
                    row['days_remaining'] = (_parse_date(due_date) - today).days
                    overdue = overdue or row['days_remaining'] < 0
                    soon = soon or row['days_remaining'] <= MAINTENANCE_DUE_SOON_DAYS
                if due_mileage is not None and odometer is not None:
                    row['miles_remaining'] = due_mileage - odometer
                    overdue = overdue or row['miles_remaining'] < 0
                    soon = soon or row['miles_remaining'] <= MAINTENANCE_DUE_SOON_MILES
                if due_date or due_mileage is not None:
                    row['status'] = 'overdue' if overdue else 'due_soon' if soon else 'ok'
        result.append(row)
    return jsonify({'current_odometer': odometer, 'pace': pace, 'types': result})

@app.route('/api/maintenance/<int:type_id>', methods=['PUT'])
@api_login_required
def set_maintenance_interval(type_id):
    """Set how often a service type is due on one vehicle: `interval_miles`
    and/or `interval_months`. Leaving both blank removes the schedule (and
    its automatic reminder)."""
    data = _payload()
    vehicle_id = _number(data, 'vehicle_id', 'Vehicle', integer=True)
    if not vehicle_id:
        raise ApiError('vehicle_id is required')
    miles = _number(data, 'interval_miles', 'Miles', integer=True) or None
    months = _number(data, 'interval_months', 'Months', integer=True) or None
    with db_session() as db:
        if not db.execute('SELECT 1 FROM service_types WHERE id = ?', (type_id,)).fetchone():
            raise ApiError('Service type not found', 404)
        if not db.execute('SELECT 1 FROM vehicle WHERE id = ?', (vehicle_id,)).fetchone():
            raise ApiError('Vehicle not found', 404)
        existing = db.execute('SELECT id FROM maintenance_schedules WHERE vehicle_id = ? AND service_type_id = ?',
                              (vehicle_id, type_id)).fetchone()
        if not miles and not months:
            if existing:
                db.execute('DELETE FROM service_reminders WHERE schedule_id = ? AND completed = 0',
                           (existing['id'],))
                db.execute('DELETE FROM maintenance_schedules WHERE id = ?', (existing['id'],))
        else:
            if existing:
                db.execute('UPDATE maintenance_schedules SET interval_miles = ?, interval_months = ? WHERE id = ?',
                           (miles, months, existing['id']))
            else:
                db.execute('''INSERT INTO maintenance_schedules
                              (vehicle_id, service_type_id, interval_miles, interval_months)
                              VALUES (?, ?, ?, ?)''', (vehicle_id, type_id, miles, months))
            refresh_schedule_reminder(db, vehicle_id, type_id)
    return jsonify({'success': True})

# CSV Export/Import endpoints for services
SERVICE_CSV_COLUMNS = ['date', 'service_provider', 'odometer', 'cost', 'service_types', 'diy',
                       'repairs_completed', 'comments']

@app.route('/api/services/export-csv', methods=['GET'])
@api_login_required
def export_services_csv():
    """Export service records to CSV. The file can be imported back in;
    `supplies_cost` is informational and ignored on import."""
    vehicle_id = request.args.get('vehicle_id')
    query = SERVICE_SELECT
    params = ()
    if vehicle_id:
        query += ' WHERE s.vehicle_id = ?'
        params = (vehicle_id,)
    db = get_db()
    services = db.execute(query + ' ORDER BY s.date DESC, s.id DESC', params).fetchall()
    db.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(SERVICE_CSV_COLUMNS + ['supplies_cost'])
    for service in services:
        writer.writerow([
            service['date'] or '',
            service['service_provider'] or '',
            service['odometer'] or '',
            service['cost'] or 0,
            (service['service_types'] or '').replace(', ', '; '),
            'yes' if service['diy'] else '',
            service['repairs_completed'] or '',
            service['comments'] or '',
            service['supplies_cost'] or 0
        ])

    response = make_response(output.getvalue())
    response.headers['Content-Type'] = 'text/csv; charset=utf-8'
    response.headers['Content-Disposition'] = 'attachment; filename=service_records.csv'
    return response

@app.route('/api/services/template-csv', methods=['GET'])
@api_login_required
def download_services_template():
    """Download a CSV template for importing service records"""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(SERVICE_CSV_COLUMNS)
    # service_types: separate several with semicolons; new names are added
    writer.writerow(['2024-01-15', 'Auto Shop', '50000', '150.00', 'Oil change; Tire rotation', '',
                     'Synthetic 5W-30', 'Regular maintenance'])
    writer.writerow(['2024-03-02', '', '52100', '0', 'Wiper blades', 'yes', '', 'Did it myself'])

    output.seek(0)
    response = make_response(output.getvalue())
    response.headers['Content-Type'] = 'text/csv; charset=utf-8'
    response.headers['Content-Disposition'] = 'attachment; filename=service_records_template.csv'
    return response

@app.route('/api/services/import-csv', methods=['POST'])
@api_login_required
def import_services_csv():
    """Import service records from CSV. Rows that match a record already
    there (same date, provider, cost and odometer) are skipped, so importing
    the same file twice doesn't duplicate anything."""
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400

    vehicle_id = request.form.get('vehicle_id')
    if not vehicle_id:
        return jsonify({'error': 'Vehicle ID is required'}), 400

    try:
        # utf-8-sig drops the byte-order mark Excel puts at the start
        stream = io.StringIO(file.stream.read().decode('utf-8-sig'), newline=None)
        csv_reader = csv.DictReader(stream)

        db = get_db()
        imported_count = 0
        skipped_count = 0
        errors = []
        affected_types = set()

        for row_num, row in enumerate(csv_reader, start=2):  # Start at 2 because of header
            try:
                row = {(k or '').strip(): (v or '').strip() for k, v in row.items()}
                diy = row.get('diy', '').lower() in ('yes', 'y', 'true', '1')
                provider = row.get('service_provider') or ('Self' if diy else '')
                if not row.get('date') or not provider:
                    errors.append(f"Row {row_num}: Missing required fields (date or service_provider)")
                    continue
                if _parse_date(row['date']) is None:
                    errors.append(f"Row {row_num}: Invalid date format (use YYYY-MM-DD)")
                    continue
                odometer = int(float(row['odometer'])) if row.get('odometer') else None
                cost = float(row.get('cost') or 0)

                duplicate = db.execute('''SELECT 1 FROM service_records
                                          WHERE vehicle_id = ? AND date = ? AND service_provider = ?
                                            AND ABS(cost - ?) < 0.005 AND odometer IS ?''',
                                       (vehicle_id, row['date'], provider, cost, odometer)).fetchone()
                if duplicate:
                    skipped_count += 1
                    continue

                cursor = db.execute('''
                    INSERT INTO service_records
                    (vehicle_id, date, service_provider, diy, odometer, cost, repairs_completed, comments)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (vehicle_id, row['date'], provider, 1 if diy else 0, odometer, cost,
                      row.get('repairs_completed', ''), row.get('comments', '')))
                for name in filter(None, (n.strip() for n in row.get('service_types', '').split(';'))):
                    type_id = get_or_create_service_type(db, name)
                    db.execute('INSERT INTO service_items (service_id, service_type_id) VALUES (?, ?)',
                               (cursor.lastrowid, type_id))
                    affected_types.add(type_id)
                imported_count += 1

            except Exception as e:
                errors.append(f"Row {row_num}: {str(e)}")

        for type_id in affected_types:
            refresh_schedule_reminder(db, int(vehicle_id), type_id)
        db.commit()
        db.close()

        return jsonify({
            'success': True,
            'imported': imported_count,
            'skipped': skipped_count,
            'errors': errors
        })

    except Exception as e:
        return jsonify({'error': f'Failed to import CSV: {str(e)}'}), 400

# Parts endpoints
def latest_odometer(db, vehicle_id):
    """Latest known odometer for a vehicle, from service or fuel records"""
    row = db.execute('''
        SELECT MAX(odo) AS odo FROM (
            SELECT MAX(odometer) AS odo FROM service_records WHERE vehicle_id = ? AND odometer IS NOT NULL
            UNION ALL
            SELECT MAX(odometer) AS odo FROM fuel_records WHERE vehicle_id = ?
        )
    ''', (vehicle_id, vehicle_id)).fetchone()
    return row['odo'] if row else None


# ── Parts, purchases and installs ────────────────────────────────────────────
#
# Three tables, nothing derived is stored:
#   parts           what a part is (name, part number, brand, ...). Shared by
#                   every vehicle: one shelf for the whole garage.
#   part_purchases  one line on a receipt: how many, what each cost, and the
#                   warranty terms they were sold with.
#   part_installs   some quantity from one purchase put on one vehicle,
#                   usually as part of a service record.
#
# Stock is purchased minus installed. A warranty belongs to an install: the
# terms come from its purchase and the clock starts when it was installed
# (or at purchase, for purchases sold that way).

class ApiError(Exception):
    """Raised anywhere inside a request to answer with a JSON error"""
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


@app.errorhandler(ApiError)
def handle_api_error(error):
    return jsonify({'error': error.message}), error.status


@contextmanager
def db_session():
    """A connection that commits on success and rolls back on any error"""
    db = get_db()
    try:
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def _payload():
    """Request fields from either a form post (needed for uploads) or JSON"""
    if request.form or request.files:
        return request.form
    return request.get_json(silent=True) or {}


def _text(data, key):
    value = data.get(key)
    if value is None or str(value).strip() == '':
        return None
    return str(value).strip()


def _number(data, key, label, integer=False, minimum=0):
    """An optional number from the request, or None when blank"""
    raw = _text(data, key)
    if raw is None:
        return None
    try:
        value = float(raw)
    except ValueError:
        raise ApiError(f'{label} must be a number')
    if value != value or value in (float('inf'), float('-inf')):
        raise ApiError(f'{label} must be a number')
    if minimum is not None and value < minimum:
        raise ApiError(f'{label} cannot be less than {minimum:g}')
    return int(value) if integer else value


def _flag(data, key):
    return str(data.get(key, '')).lower() in ('on', '1', 'true', 'yes')


def _parse_date(text):
    try:
        return datetime.strptime(text, '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return None


def _date(data, key, label):
    """An optional YYYY-MM-DD date from the request, or None when blank"""
    raw = _text(data, key)
    if raw is None:
        return None
    if _parse_date(raw) is None:
        raise ApiError(f'{label} must be a date (YYYY-MM-DD)')
    return raw


# A warranty is flagged as expiring once it is this close to running out
WARRANTY_EXPIRING_DAYS = 30
WARRANTY_EXPIRING_MILES = 1000


def add_months(start, months):
    """`start` moved forward by whole months, clamped to the month's last day"""
    year = start.year + (start.month - 1 + months) // 12
    month = (start.month - 1 + months) % 12 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return start.replace(year=year, month=month, day=day)


def warranty_info(terms, install_date=None, odometer=None, start_date_override=None,
                  start_mileage_override=None, current_odometer=None, today=None):
    """Work out where a warranty stands, or None if the purchase has none.

    `terms` is a part_purchases row. The clock starts on the install date
    (and install odometer) unless the purchase says it starts at purchase,
    or the install carries an explicit override. A warranty with both a time
    and a mileage limit ends at whichever comes first.

    status is 'lifetime', 'active', 'expiring' or 'expired'."""
    lifetime = bool(terms['warranty_lifetime'])
    months = terms['warranty_months']
    miles = terms['warranty_miles']
    if not lifetime and not months and not miles:
        return None

    at_purchase = terms['warranty_starts'] == 'purchase'
    start_text = (start_date_override
                  or (terms['purchase_date'] if at_purchase else None)
                  or install_date)
    start_mileage = start_mileage_override if start_mileage_override is not None else odometer
    info = {
        'status': 'lifetime' if lifetime else 'active',
        'lifetime': lifetime,
        'months': None if lifetime else months,
        'miles': None if lifetime else miles,
        'starts': 'purchase' if at_purchase else 'install',
        'start_date': start_text,
        'expires_on': None,
        'days_remaining': None,
        'start_mileage': start_mileage,
        'expires_at_mileage': None,
        'miles_remaining': None,
    }
    if lifetime:
        return info

    expired = expiring = False
    start = _parse_date(start_text)
    if months and start:
        expires = add_months(start, int(months))
        days = (expires - (today or datetime.now().date())).days
        info['expires_on'] = expires.isoformat()
        info['days_remaining'] = days
        expired = expired or days < 0
        expiring = expiring or days <= WARRANTY_EXPIRING_DAYS
    if miles and start_mileage is not None:
        info['expires_at_mileage'] = start_mileage + miles
        if current_odometer is not None:
            remaining = start_mileage + miles - current_odometer
            info['miles_remaining'] = remaining
            expired = expired or remaining < 0
            expiring = expiring or remaining <= WARRANTY_EXPIRING_MILES
    info['status'] = 'expired' if expired else 'expiring' if expiring else 'active'
    return info


PURCHASE_SELECT = '''
    SELECT pp.*,
           COALESCE((SELECT SUM(quantity) FROM part_installs WHERE purchase_id = pp.id), 0) AS installed
    FROM part_purchases pp
'''

INSTALL_SELECT = '''
    SELECT pi.*, pp.part_id, pp.unit_cost, pp.purchase_date, pp.supplier, pp.receipt_path,
           pp.warranty_months, pp.warranty_miles, pp.warranty_lifetime, pp.warranty_starts,
           p.name AS part_name, p.part_number, p.brand,
           v.nickname AS vehicle_nickname, v.year AS vehicle_year,
           v.manufacturer AS vehicle_manufacturer, v.model AS vehicle_model
    FROM part_installs pi
    JOIN part_purchases pp ON pp.id = pi.purchase_id
    JOIN parts p ON p.id = pp.part_id
    LEFT JOIN vehicle v ON v.id = pi.vehicle_id
'''


def _purchase_dict(row):
    purchase = dict(row)
    purchase['remaining'] = round(purchase['quantity'] - purchase['installed'], 4)
    # Stock still on the shelf only has a running warranty when the clock
    # started at purchase; otherwise it starts when the part is installed
    purchase['shelf_warranty'] = (warranty_info(row) if row['warranty_starts'] == 'purchase' else None)
    return purchase


def load_purchases(db, where='', params=()):
    """Purchases with their remaining stock, oldest first (the order stock is
    drawn in). Undated purchases count as oldest."""
    rows = db.execute(f"{PURCHASE_SELECT} {('WHERE ' + where) if where else ''} "
                      "ORDER BY COALESCE(pp.purchase_date, ''), pp.id", params).fetchall()
    return [_purchase_dict(row) for row in rows]


def purchase_with_remaining(db, purchase_id):
    purchases = load_purchases(db, 'pp.id = ?', (purchase_id,))
    if not purchases:
        raise ApiError('Purchase not found', 404)
    return purchases[0]


def load_installs(db, where='', params=()):
    """Installs with part, vehicle and warranty details, newest first"""
    rows = db.execute(f"{INSTALL_SELECT} {('WHERE ' + where) if where else ''} "
                      "ORDER BY COALESCE(pi.install_date, '') DESC, pi.id DESC", params).fetchall()
    odometers = {}
    installs = []
    for row in rows:
        vehicle_id = row['vehicle_id']
        if vehicle_id is not None and vehicle_id not in odometers:
            odometers[vehicle_id] = latest_odometer(db, vehicle_id)
        if row['vehicle_manufacturer'] is None:
            vehicle_name = 'Deleted vehicle'
        else:
            vehicle_name = row['vehicle_nickname'] or \
                f"{row['vehicle_year']} {row['vehicle_manufacturer']} {row['vehicle_model']}"
        installs.append({
            'id': row['id'],
            'install_id': row['id'],
            'purchase_id': row['purchase_id'],
            'part_id': row['part_id'],
            'part_name': row['part_name'],
            'part_number': row['part_number'],
            'brand': row['brand'],
            'vehicle_id': vehicle_id,
            'vehicle_name': vehicle_name,
            'service_id': row['service_id'],
            'quantity': row['quantity'],
            'unit_cost': row['unit_cost'],
            'install_date': row['install_date'],
            'odometer': row['odometer'],
            'removed_date': row['removed_date'],
            'notes': row['notes'],
            'warranty_start_date': row['warranty_start_date'],
            'warranty_start_mileage': row['warranty_start_mileage'],
            'purchase_date': row['purchase_date'],
            'supplier': row['supplier'],
            'receipt_path': row['receipt_path'],
            'warranty': warranty_info(row, row['install_date'], row['odometer'],
                                      row['warranty_start_date'], row['warranty_start_mileage'],
                                      odometers.get(vehicle_id)),
        })
    return installs


def build_parts(db, part_id=None):
    """Every part (or one) with its purchases, installs and derived totals"""
    if part_id is None:
        parts = db.execute('SELECT * FROM parts ORDER BY name COLLATE NOCASE, id').fetchall()
        purchases = load_purchases(db)
        installs = load_installs(db)
    else:
        parts = db.execute('SELECT * FROM parts WHERE id = ?', (part_id,)).fetchall()
        purchases = load_purchases(db, 'pp.part_id = ?', (part_id,))
        installs = load_installs(db, 'pp.part_id = ?', (part_id,))

    result = []
    for row in parts:
        part = dict(row)
        lots = [p for p in purchases if p['part_id'] == part['id']]
        fitted = [i for i in installs if i['part_id'] == part['id']]
        current = [i for i in fitted if not i['removed_date']]
        in_stock = round(sum(p['remaining'] for p in lots), 4)
        next_lot = next((p for p in lots if p['remaining'] > 1e-9), None)

        warranty = {'lifetime': 0, 'active': 0, 'expiring': 0, 'expired': 0, 'next_expiry': None}
        for install in current:
            w = install['warranty']
            if not w:
                continue
            warranty[w['status']] += 1
            if w['status'] in ('active', 'expiring') and w['expires_on'] and \
                    (warranty['next_expiry'] is None or w['expires_on'] < warranty['next_expiry']):
                warranty['next_expiry'] = w['expires_on']

        part.update({
            'purchases': lots,
            'installs': fitted,
            'in_stock': in_stock,
            # `quantity` and `cost` keep the names older API clients read
            'quantity': in_stock,
            'cost': (next_lot or (lots[-1] if lots else {})).get('unit_cost', 0),
            'stock_value': round(sum(p['remaining'] * p['unit_cost'] for p in lots), 2),
            'installed': round(sum(i['quantity'] for i in current), 4),
            'low_stock': part['reorder_threshold'] is not None and in_stock <= part['reorder_threshold'],
            'warranty': warranty,
        })
        result.append(part)
    return result


def add_reorder_reminder(db, vehicle_id, name, part_number, in_stock):
    """Add a "Re-order <part>" reminder unless an open one already exists, so
    repeated use of a part that is already low doesn't pile up duplicates."""
    service_type = f"Re-order {name}" + (f" ({part_number})" if part_number else '')
    exists = db.execute('''SELECT 1 FROM service_reminders
                           WHERE vehicle_id = ? AND service_type = ? AND completed = 0''',
                        (vehicle_id, service_type)).fetchone()
    if not exists:
        db.execute('''INSERT INTO service_reminders (vehicle_id, service_type, notes, completed)
                      VALUES (?, ?, ?, 0)''',
                   (vehicle_id, service_type,
                    f'Stock is down to {in_stock:g}. Consider re-ordering soon.'))


def allocate_install(db, part_id, quantity, vehicle_id, install_date, service_id=None, odometer=None,
                     purchase_id=None, notes=None, warranty_start_date=None, warranty_start_mileage=None):
    """Take `quantity` of a part off the shelf and record it as installed.

    Stock is drawn from the oldest purchase first, so one request can become
    several installs when it spans purchases (each keeps its own cost and
    warranty terms). Pass `purchase_id` to draw from one purchase only.
    Returns the new install ids."""
    part = db.execute('SELECT * FROM parts WHERE id = ?', (part_id,)).fetchone()
    if not part:
        raise ApiError('Part not found', 404)
    lots = [p for p in load_purchases(db, 'pp.part_id = ?', (part_id,)) if p['remaining'] > 1e-9]
    in_stock = sum(p['remaining'] for p in lots)
    if purchase_id:
        lots = [p for p in lots if p['id'] == int(purchase_id)]
    available = sum(p['remaining'] for p in lots)
    if quantity > available + 1e-9:
        where = ' from that purchase' if purchase_id else ' in stock'
        raise ApiError(f"Only {available:g} of {part['name']}{where}")

    install_ids = []
    left = quantity
    for lot in lots:
        if left <= 1e-9:
            break
        take = min(lot['remaining'], left)
        cursor = db.execute('''INSERT INTO part_installs
                               (purchase_id, vehicle_id, service_id, quantity, install_date, odometer,
                                warranty_start_date, warranty_start_mileage, notes)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                            (lot['id'], vehicle_id, service_id, take, install_date, odometer,
                             warranty_start_date, warranty_start_mileage, notes))
        install_ids.append(cursor.lastrowid)
        left -= take

    remaining = round(in_stock - quantity, 4)
    if part['reorder_threshold'] is not None and remaining <= part['reorder_threshold'] and vehicle_id:
        add_reorder_reminder(db, vehicle_id, part['name'], part['part_number'], remaining)
    return install_ids


def _part_fields(data):
    name = _text(data, 'name')
    if not name:
        raise ApiError('Part name is required')
    return (name, _text(data, 'part_number'), _text(data, 'brand'), _text(data, 'category'),
            _text(data, 'location'), _text(data, 'notes'),
            _number(data, 'reorder_threshold', 'Re-order level'))


def _purchase_fields(data, notes_key='notes'):
    quantity = _number(data, 'quantity', 'Quantity')
    if not quantity:
        raise ApiError('Quantity must be greater than 0')
    unit_cost = _number(data, 'unit_cost', 'Cost per unit')
    if unit_cost is None:
        unit_cost = _number(data, 'cost', 'Cost per unit') or 0
    lifetime = _flag(data, 'warranty_lifetime')
    starts = _text(data, 'warranty_starts') or 'install'
    if starts not in ('install', 'purchase'):
        raise ApiError('Warranty must start at install or at purchase')
    return {
        'purchase_date': _date(data, 'purchase_date', 'Purchase date'),
        'supplier': _text(data, 'supplier'),
        'condition': _text(data, 'condition') or 'New',
        'quantity': quantity,
        'unit_cost': unit_cost,
        'warranty_months': None if lifetime else (_number(data, 'warranty_months', 'Warranty months', integer=True) or None),
        'warranty_miles': None if lifetime else (_number(data, 'warranty_miles', 'Warranty miles', integer=True) or None),
        'warranty_lifetime': 1 if lifetime else 0,
        'warranty_starts': starts,
        'notes': _text(data, notes_key),
    }


def _insert_purchase(db, part_id, fields, receipt_path):
    cursor = db.execute('''INSERT INTO part_purchases
                           (part_id, purchase_date, supplier, condition, quantity, unit_cost, receipt_path,
                            warranty_months, warranty_miles, warranty_lifetime, warranty_starts, notes)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                        (part_id, fields['purchase_date'], fields['supplier'], fields['condition'],
                         fields['quantity'], fields['unit_cost'], receipt_path,
                         fields['warranty_months'], fields['warranty_miles'], fields['warranty_lifetime'],
                         fields['warranty_starts'], fields['notes']))
    return cursor.lastrowid


def _unreferenced_receipts(db, paths):
    """Of `paths`, those no purchase points at any more. Purchases migrated
    from one old supply can share a receipt file, so it is only deleted once
    the last of them lets go of it."""
    return [path for path in set(paths) if path and not db.execute(
        'SELECT 1 FROM part_purchases WHERE receipt_path = ?', (path,)).fetchone()]


@app.route('/api/parts', methods=['GET'])
@api_login_required
def get_parts():
    """Every part on the shelf with its purchases, installs and totals"""
    db = get_db()
    parts = build_parts(db)
    db.close()
    return jsonify(parts)

@app.route('/api/parts/<int:part_id>', methods=['GET'])
@api_login_required
def get_part(part_id):
    db = get_db()
    parts = build_parts(db, part_id)
    db.close()
    if not parts:
        return jsonify({'error': 'Part not found'}), 404
    return jsonify(parts[0])

@app.route('/api/parts', methods=['POST'])
@api_login_required
def add_part():
    """Add a part. If the form also carries a quantity, the first purchase is
    recorded with it (same fields as POST /api/parts/<id>/purchases, except
    its notes field is `purchase_notes`)."""
    data = _payload()
    part = _part_fields(data)
    purchase = _purchase_fields(data, 'purchase_notes') if _text(data, 'quantity') else None
    receipt_path = save_upload('receipt', 'receipts') if purchase else None
    try:
        with db_session() as db:
            cursor = db.execute('''INSERT INTO parts (name, part_number, brand, category, location, notes,
                                                      reorder_threshold)
                                   VALUES (?, ?, ?, ?, ?, ?, ?)''', part)
            part_id = cursor.lastrowid
            purchase_id = _insert_purchase(db, part_id, purchase, receipt_path) if purchase else None
    except Exception:
        remove_upload(receipt_path)
        raise
    return jsonify({'success': True, 'id': part_id, 'purchase_id': purchase_id})

@app.route('/api/parts/<int:part_id>', methods=['PUT'])
@api_login_required
def update_part(part_id):
    """Edit what a part is. Stock and warranty live on its purchases."""
    part = _part_fields(_payload())
    with db_session() as db:
        cursor = db.execute('''UPDATE parts SET name=?, part_number=?, brand=?, category=?, location=?,
                                                notes=?, reorder_threshold=?
                               WHERE id=?''', part + (part_id,))
        if cursor.rowcount == 0:
            raise ApiError('Part not found', 404)
    return jsonify({'success': True})

@app.route('/api/parts/<int:part_id>', methods=['DELETE'])
@api_login_required
def delete_part(part_id):
    """Delete a part and its purchases; blocked while any of it is installed,
    since service history and warranties would point at a missing part"""
    with db_session() as db:
        if not db.execute('SELECT 1 FROM parts WHERE id = ?', (part_id,)).fetchone():
            raise ApiError('Part not found', 404)
        used = db.execute('''SELECT COUNT(*) AS c FROM part_installs pi
                             JOIN part_purchases pp ON pp.id = pi.purchase_id
                             WHERE pp.part_id = ?''', (part_id,)).fetchone()['c']
        if used:
            raise ApiError('This part has been installed on a vehicle. Remove those installs '
                           '(or take it off the service records) before deleting it.')
        receipts = [r['receipt_path'] for r in db.execute(
            'SELECT receipt_path FROM part_purchases WHERE part_id = ?', (part_id,)).fetchall()]
        db.execute('DELETE FROM part_purchases WHERE part_id = ?', (part_id,))
        db.execute('DELETE FROM parts WHERE id = ?', (part_id,))
        orphaned = _unreferenced_receipts(db, receipts)
    for path in orphaned:
        remove_upload(path)
    return jsonify({'success': True})

@app.route('/api/parts/<int:part_id>/purchases', methods=['POST'])
@api_login_required
def add_part_purchase(part_id):
    """Record buying more of a part. `unit_cost` is per unit; warranty terms
    are `warranty_months`, `warranty_miles` or `warranty_lifetime`, and
    `warranty_starts` is 'install' (default) or 'purchase'."""
    fields = _purchase_fields(_payload())
    receipt_path = save_upload('receipt', 'receipts')
    try:
        with db_session() as db:
            if not db.execute('SELECT 1 FROM parts WHERE id = ?', (part_id,)).fetchone():
                raise ApiError('Part not found', 404)
            purchase_id = _insert_purchase(db, part_id, fields, receipt_path)
    except Exception:
        remove_upload(receipt_path)
        raise
    return jsonify({'success': True, 'id': purchase_id})

@app.route('/api/part-purchases/<int:purchase_id>', methods=['PUT'])
@api_login_required
def update_part_purchase(purchase_id):
    fields = _purchase_fields(_payload())
    receipt_path = save_upload('receipt', 'receipts')
    try:
        with db_session() as db:
            old = purchase_with_remaining(db, purchase_id)
            if fields['quantity'] < old['installed'] - 1e-9:
                raise ApiError(f"{old['installed']:g} from this purchase are already installed; "
                               'the quantity cannot be lower than that')
            # A new upload replaces the receipt; otherwise keep the current one
            db.execute('''UPDATE part_purchases
                          SET purchase_date=?, supplier=?, condition=?, quantity=?, unit_cost=?,
                              receipt_path=COALESCE(?, receipt_path), warranty_months=?, warranty_miles=?,
                              warranty_lifetime=?, warranty_starts=?, notes=?
                          WHERE id=?''',
                       (fields['purchase_date'], fields['supplier'], fields['condition'], fields['quantity'],
                        fields['unit_cost'], receipt_path, fields['warranty_months'], fields['warranty_miles'],
                        fields['warranty_lifetime'], fields['warranty_starts'], fields['notes'], purchase_id))
            orphaned = _unreferenced_receipts(db, [old['receipt_path']]) if receipt_path else []
    except Exception:
        remove_upload(receipt_path)
        raise
    for path in orphaned:
        remove_upload(path)
    return jsonify({'success': True})

@app.route('/api/part-purchases/<int:purchase_id>', methods=['DELETE'])
@api_login_required
def delete_part_purchase(purchase_id):
    """Delete a purchase; blocked once any of it has been installed"""
    with db_session() as db:
        old = purchase_with_remaining(db, purchase_id)
        if old['installed'] > 1e-9:
            raise ApiError('Some of this purchase has been installed. Remove those installs first.')
        db.execute('DELETE FROM part_purchases WHERE id = ?', (purchase_id,))
        orphaned = _unreferenced_receipts(db, [old['receipt_path']])
    for path in orphaned:
        remove_upload(path)
    return jsonify({'success': True})

@app.route('/api/parts/<int:part_id>/installs', methods=['POST'])
@api_login_required
def add_part_install(part_id):
    """Install a part on a vehicle without a service record (parts used on a
    service are sent with the service instead)"""
    data = _payload()
    vehicle_id = _number(data, 'vehicle_id', 'Vehicle', integer=True)
    quantity = _number(data, 'quantity', 'Quantity')
    install_date = _date(data, 'install_date', 'Install date')
    if not vehicle_id:
        raise ApiError('Choose the vehicle this was installed on')
    if not quantity:
        raise ApiError('Quantity must be greater than 0')
    if not install_date:
        raise ApiError('Install date is required')
    with db_session() as db:
        if not db.execute('SELECT 1 FROM vehicle WHERE id = ?', (vehicle_id,)).fetchone():
            raise ApiError('Vehicle not found', 404)
        install_ids = allocate_install(
            db, part_id, quantity, vehicle_id=vehicle_id, install_date=install_date,
            odometer=_number(data, 'odometer', 'Odometer', integer=True),
            purchase_id=_number(data, 'purchase_id', 'Purchase', integer=True),
            notes=_text(data, 'notes'),
            warranty_start_date=_date(data, 'warranty_start_date', 'Warranty start date'),
            warranty_start_mileage=_number(data, 'warranty_start_mileage', 'Warranty start mileage', integer=True))
    return jsonify({'success': True, 'ids': install_ids})

@app.route('/api/part-installs/<int:install_id>', methods=['PUT'])
@api_login_required
def update_part_install(install_id):
    """Edit an install: mark it removed, add notes, or override when its
    warranty started. Quantity, date and odometer can only be changed here
    for installs that aren't on a service record; otherwise they follow it."""
    data = _payload()
    with db_session() as db:
        old = db.execute('SELECT * FROM part_installs WHERE id = ?', (install_id,)).fetchone()
        if not old:
            raise ApiError('Install not found', 404)

        quantity, install_date, odometer = old['quantity'], old['install_date'], old['odometer']
        if not old['service_id']:
            if 'quantity' in data:
                quantity = _number(data, 'quantity', 'Quantity')
                if not quantity:
                    raise ApiError('Quantity must be greater than 0')
                available = purchase_with_remaining(db, old['purchase_id'])['remaining'] + old['quantity']
                if quantity > available + 1e-9:
                    raise ApiError(f'Only {available:g} available from that purchase')
            if 'install_date' in data:
                install_date = _date(data, 'install_date', 'Install date')
            if 'odometer' in data:
                odometer = _number(data, 'odometer', 'Odometer', integer=True)

        db.execute('''UPDATE part_installs
                      SET quantity=?, install_date=?, odometer=?, removed_date=?, notes=?,
                          warranty_start_date=?, warranty_start_mileage=?
                      WHERE id=?''',
                   (quantity, install_date, odometer,
                    _date(data, 'removed_date', 'Removed date'), _text(data, 'notes'),
                    _date(data, 'warranty_start_date', 'Warranty start date'),
                    _number(data, 'warranty_start_mileage', 'Warranty start mileage', integer=True),
                    install_id))
    return jsonify({'success': True})

@app.route('/api/part-installs/<int:install_id>', methods=['DELETE'])
@api_login_required
def delete_part_install(install_id):
    """Undo an install, returning its quantity to the shelf. To record that a
    part came off the vehicle, set its removed date instead."""
    with db_session() as db:
        old = db.execute('SELECT service_id FROM part_installs WHERE id = ?', (install_id,)).fetchone()
        if not old:
            raise ApiError('Install not found', 404)
        if old['service_id']:
            raise ApiError('This part was used on a service record. Edit that service to remove it.')
        db.execute('DELETE FROM part_installs WHERE id = ?', (install_id,))
    return jsonify({'success': True})

WARRANTY_STATUS_ORDER = {'expiring': 0, 'active': 1, 'lifetime': 2, 'expired': 3}

@app.route('/api/warranties', methods=['GET'])
@api_login_required
def get_warranties():
    """Warranties on parts currently installed, soonest to expire first.
    Optional `vehicle_id` limits the list to one vehicle."""
    vehicle_id = request.args.get('vehicle_id', type=int)
    where = 'pi.removed_date IS NULL'
    params = ()
    if vehicle_id:
        where += ' AND pi.vehicle_id = ?'
        params = (vehicle_id,)
    db = get_db()
    installs = [i for i in load_installs(db, where, params) if i['warranty']]
    db.close()
    installs.sort(key=lambda i: (WARRANTY_STATUS_ORDER[i['warranty']['status']],
                                 i['warranty']['expires_on'] or '9999',
                                 i['warranty']['miles_remaining'] if i['warranty']['miles_remaining'] is not None else float('inf')))
    return jsonify(installs)

@app.route('/api/parts/export-csv', methods=['GET'])
@api_login_required
def export_parts_csv():
    """Export the parts shelf to CSV"""
    db = get_db()
    parts = build_parts(db)
    db.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['name', 'part_number', 'brand', 'category', 'location', 'in_stock',
                     'stock_value', 'installed', 'reorder_threshold', 'notes'])
    for part in parts:
        writer.writerow([
            part['name'],
            part['part_number'] or '',
            part['brand'] or '',
            part['category'] or '',
            part['location'] or '',
            part['in_stock'],
            part['stock_value'],
            part['installed'],
            part['reorder_threshold'] if part['reorder_threshold'] is not None else '',
            part['notes'] or ''
        ])

    response = make_response(output.getvalue())
    response.headers['Content-Type'] = 'text/csv; charset=utf-8'
    response.headers['Content-Disposition'] = 'attachment; filename=parts.csv'
    return response


def migrate_supplies_to_parts(db):
    """One-time copy of the old supplies / supply_units / service_supplies
    data into parts, part_purchases and part_installs. The old tables are
    left untouched as a backup. Part ids match the old supply ids.

    Plain supplies become one purchase: what is on hand plus what services
    already used, each use becoming an install. Individually-tracked units
    are grouped into purchases by matching date, cost, supplier, receipt and
    warranty terms; installed or used units become installs."""
    def service(service_id):
        if not service_id:
            return None
        return db.execute('SELECT vehicle_id, date, odometer FROM service_records WHERE id = ?',
                          (service_id,)).fetchone()

    def insert_install(purchase_id, vehicle_id, service_id, quantity, install_date, odometer,
                       start_date, start_mileage, notes=None):
        db.execute('''INSERT INTO part_installs
                      (purchase_id, vehicle_id, service_id, quantity, install_date, odometer,
                       warranty_start_date, warranty_start_mileage, notes)
                      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                   (purchase_id, vehicle_id, service_id, quantity, install_date, odometer,
                    start_date, start_mileage, notes))

    for s in db.execute('SELECT * FROM supplies ORDER BY id').fetchall():
        tracked = bool(s['tracked_individually'])
        db.execute('''INSERT INTO parts (id, name, part_number, brand, category, location, notes,
                                         reorder_threshold, created_at)
                      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                   (s['id'], s['name'], s['part_number'] or None, s['brand'] or None,
                    s['category'] or None, s['location'] or None, s['notes'] or None,
                    1 if s['remind_to_reorder'] else None, s['created_at']))

        # Uses recorded as a plain quantity (not as a specific unit)
        uses = db.execute('''SELECT ss.service_id, ss.quantity_used, sr.vehicle_id, sr.date, sr.odometer
                             FROM service_supplies ss
                             JOIN service_records sr ON sr.id = ss.service_id
                             WHERE ss.supply_id = ? AND ss.supply_unit_id IS NULL''', (s['id'],)).fetchall()
        used = sum(u['quantity_used'] or 0 for u in uses)
        on_hand = 0 if tracked else max(s['quantity'] or 0, 0)
        has_warranty = bool(s['warranty_months'] or s['warranty_lifetime'] or s['warranty_mileage_limit'])

        if not tracked or uses:
            at_purchase = bool(s['warranty_start_date'] and s['warranty_start_date'] == s['purchase_date'])
            start_override = None if at_purchase else s['warranty_start_date']
            # An install date with nothing linking it to a service: the old
            # flat row described a part already on the vehicle. Only treated
            # that way when it carries a warranty; otherwise it stays in
            # stock and the date is kept as a note.
            fitted = bool(not tracked and s['installation_date'] and not uses and on_hand)
            notes = None
            if s['installation_date'] and not (fitted and has_warranty):
                notes = f"Installation date recorded before the parts rebuild: {s['installation_date']}"
            cursor = db.execute('''INSERT INTO part_purchases
                                   (part_id, purchase_date, supplier, condition, quantity, unit_cost,
                                    receipt_path, warranty_months, warranty_miles, warranty_lifetime,
                                    warranty_starts, notes)
                                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                                (s['id'], s['purchase_date'] or None, s['supplier'] or None,
                                 s['condition'] or 'New', on_hand + used, s['cost'] or 0,
                                 None if tracked else s['receipt_path'],
                                 s['warranty_months'], s['warranty_mileage_limit'],
                                 1 if s['warranty_lifetime'] else 0,
                                 'purchase' if at_purchase else 'install', notes))
            purchase_id = cursor.lastrowid
            for u in uses:
                insert_install(purchase_id, u['vehicle_id'], u['service_id'], u['quantity_used'],
                               u['date'], u['odometer'], start_override, s['warranty_start_mileage'])
            if fitted and has_warranty:
                insert_install(purchase_id, s['vehicle_id'], None, on_hand, s['installation_date'],
                               None, start_override, s['warranty_start_mileage'])

        if not tracked:
            continue

        groups = {}
        for u in db.execute('SELECT * FROM supply_units WHERE supply_id = ? ORDER BY id', (s['id'],)).fetchall():
            at_purchase = bool(u['warranty_start_date'] and u['warranty_start_date'] == u['purchase_date'])
            key = (u['purchase_date'], u['cost'] if u['cost'] is not None else (s['cost'] or 0),
                   u['supplier'] or None, u['receipt_path'] or s['receipt_path'], u['warranty_months'],
                   u['warranty_mileage_limit'], 1 if u['warranty_lifetime'] else 0, at_purchase)
            groups.setdefault(key, []).append(u)

        for key, units in groups.items():
            purchase_date, unit_cost, supplier, receipt, months, miles, lifetime, at_purchase = key
            kept = [u for u in units if u['status'] != 'returned']
            notes = []
            for u in units:
                if u['notes'] and u['notes'] not in notes:
                    notes.append(u['notes'])
            returned = len(units) - len(kept)
            if returned:
                notes.append(f"{returned} unit{'s' if returned != 1 else ''} returned to the supplier "
                             'before the parts rebuild (not counted in the quantity)')
            for u in kept:
                if u['status'] == 'in_stock' and u['warranty_start_date'] and not at_purchase:
                    notes.append(f"Unit #{u['id']} had a warranty start date of {u['warranty_start_date']} "
                                 'while still in stock')
            cursor = db.execute('''INSERT INTO part_purchases
                                   (part_id, purchase_date, supplier, quantity, unit_cost, receipt_path,
                                    warranty_months, warranty_miles, warranty_lifetime, warranty_starts, notes)
                                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                                (s['id'], purchase_date, supplier, len(kept), unit_cost, receipt, months,
                                 miles, lifetime, 'purchase' if at_purchase else 'install',
                                 '\n'.join(notes) or None))
            purchase_id = cursor.lastrowid

            # Units put on the vehicle together become one install
            installs = {}
            for u in kept:
                if u['status'] not in ('installed', 'used'):
                    continue
                linked = service(u['installed_service_id'])
                install_date = u['installation_date'] or (linked['date'] if linked else None)
                odometer = linked['odometer'] if linked else None
                default_start = purchase_date if at_purchase else install_date
                start_date = u['warranty_start_date'] if u['warranty_start_date'] != default_start else None
                start_mileage = u['warranty_start_mileage'] if u['warranty_start_mileage'] != odometer else None
                install_key = (linked['vehicle_id'] if linked else u['vehicle_id'],
                               u['installed_service_id'] if linked else None,
                               install_date, odometer, start_date, start_mileage,
                               'Marked as used before the parts rebuild' if u['status'] == 'used' else None)
                installs[install_key] = installs.get(install_key, 0) + 1
            for install_key, count in installs.items():
                vehicle_id, service_id, install_date, odometer, start_date, start_mileage, note = install_key
                insert_install(purchase_id, vehicle_id, service_id, count, install_date, odometer,
                               start_date, start_mileage, note)

# Fuel endpoints
#
# Fuel economy is measured from one full tank to the next. A fill-up that
# didn't fill the tank can't say how much fuel the miles before it used, so
# its gallons are carried forward to the next full tank. Each full tank that
# closes such a span stores the span as trip_miles / trip_gallons, and
# mpg = trip_miles / trip_gallons.

FUEL_GRADES = ('Regular', 'Mid-grade', 'Premium', 'Diesel', 'E85')

# A tank is flagged when it comes in this far below the tanks before it
ECONOMY_DROP_RATIO = 0.85
ECONOMY_BASELINE_TANKS = 5
ECONOMY_MIN_TANKS = 3


def recalculate_mpg(db, vehicle_id):
    """Recompute MPG for every fuel record of a vehicle, in odometer order.

    Only a full tank gets an MPG: the miles since the previous full tank
    divided by all the fuel added since then (this fill-up plus any partial
    ones in between). The first record, or any full tank after a gap, is
    just the starting point for the next span. A record flagged
    missed_fillup breaks the chain, since fuel went in that was never
    logged: it gets no MPG and, if it filled the tank, becomes the new
    starting point. Called after any insert/update/delete so out-of-order
    entries and deletions can't leave stale values behind."""
    records = db.execute('''SELECT id, odometer, gallons, missed_fillup, full_tank FROM fuel_records
                            WHERE vehicle_id = ? ORDER BY odometer, date, id''',
                         (vehicle_id,)).fetchall()
    anchor = None        # odometer at the last full tank, or None if unknown
    pending = 0.0        # gallons added since then
    for rec in records:
        full = rec['full_tank'] is None or bool(rec['full_tank'])
        gallons = rec['gallons'] or 0
        mpg = trip_miles = trip_gallons = None
        if rec['missed_fillup']:
            anchor = rec['odometer'] if full else None
            pending = 0.0
        elif anchor is None:
            if full:
                anchor = rec['odometer']
                pending = 0.0
        else:
            pending += gallons
            if full:
                miles = rec['odometer'] - anchor
                if miles > 0 and pending > 0:
                    mpg = round(miles / pending, 2)
                    trip_miles, trip_gallons = miles, round(pending, 3)
                anchor = rec['odometer']
                pending = 0.0
        db.execute('UPDATE fuel_records SET mpg = ?, trip_miles = ?, trip_gallons = ? WHERE id = ?',
                   (mpg, trip_miles, trip_gallons, rec['id']))


def annotate_fuel_records(db, vehicle_id):
    """Per-record figures that depend on a vehicle's other fill-ups, keyed by
    record id:
      distance        miles since the previous fill-up of any kind
      recent_avg_mpg  economy over the few tanks before this one
      low_economy     True when this tank is well below that"""
    rows = db.execute('''SELECT id, odometer, mpg, trip_miles, trip_gallons FROM fuel_records
                         WHERE vehicle_id = ? ORDER BY odometer, date, id''', (vehicle_id,)).fetchall()
    notes = {}
    previous_odometer = None
    tanks = []   # (trip_miles, trip_gallons) of earlier tanks with an MPG
    for row in rows:
        distance = row['odometer'] - previous_odometer if previous_odometer is not None else None
        previous_odometer = row['odometer']
        recent = None
        low = False
        if row['mpg']:
            baseline = tanks[-ECONOMY_BASELINE_TANKS:]
            if len(baseline) >= ECONOMY_MIN_TANKS:
                recent = round(sum(m for m, _ in baseline) / sum(g for _, g in baseline), 2)
                low = row['mpg'] < recent * ECONOMY_DROP_RATIO
            tanks.append((row['trip_miles'], row['trip_gallons']))
        notes[row['id']] = {'distance': distance if distance and distance > 0 else None,
                            'recent_avg_mpg': recent, 'low_economy': low}
    return notes


def _fuel_dict(row, notes=None):
    record = dict(row)
    record['full_tank'] = record.get('full_tank') is None or bool(record['full_tank'])
    record['missed_fillup'] = bool(record.get('missed_fillup'))
    record.update((notes or {}).get(record['id'], {}))
    return record


def _fuel_fields(data):
    """Validated fields of a fuel record. Any two of gallons, cost and
    price_per_gallon are enough; the third is worked out."""
    fuel_date = _date(data, 'date', 'Date')
    if not fuel_date:
        raise ApiError('Date is required')
    odometer = _number(data, 'odometer', 'Odometer', integer=True)
    if odometer is None:
        raise ApiError('Odometer is required')
    gallons = _number(data, 'gallons', 'Gallons')
    cost = _number(data, 'cost', 'Total cost')
    price = _number(data, 'price_per_gallon', 'Price per gallon')
    if gallons is None and cost is not None and price:
        gallons = round(cost / price, 3)
    if cost is None and gallons is not None and price is not None:
        cost = round(gallons * price, 2)
    if not gallons:
        raise ApiError('Gallons is required (or give the total cost and price per gallon)')
    if cost is None:
        raise ApiError('Total cost is required (or give the gallons and price per gallon)')
    # The form requires a station; the API stays lenient for imports and
    # older clients that never sent one
    station = _text(data, 'station') or ''
    full_tank = data.get('full_tank')
    return {
        'date': fuel_date,
        'odometer': odometer,
        'gallons': gallons,
        'cost': cost,
        'station': station,
        # Older clients don't send full_tank: every fill-up was a full tank
        'full_tank': 1 if full_tank is None or full_tank is True or _flag(data, 'full_tank') else 0,
        'missed_fillup': 1 if data.get('missed_fillup') is True or _flag(data, 'missed_fillup') else 0,
        'grade': _text(data, 'grade'),
        'notes': _text(data, 'notes'),
    }


def _fuel_save_result(db, vehicle_id, fuel_id, fields):
    """What a fill-up save reports back: its MPG and anything worth a look"""
    record = db.execute('SELECT mpg FROM fuel_records WHERE id = ?', (fuel_id,)).fetchone()
    note = annotate_fuel_records(db, vehicle_id).get(fuel_id, {})
    alert = None
    if note.get('low_economy'):
        drop = round((1 - record['mpg'] / note['recent_avg_mpg']) * 100)
        alert = (f"This tank came in at {record['mpg']:g} MPG, {drop}% below your recent "
                 f"{note['recent_avg_mpg']:g}. One low tank can be weather or short trips; if it "
                 'keeps up, check tire pressure and whether a service is due.')
    return {
        'success': True, 'id': fuel_id, 'mpg': record['mpg'],
        'warning': odometer_warning(db, vehicle_id, fields['date'], fields['odometer'], fuel_id=fuel_id),
        'economy_alert': alert,
    }


@app.route('/api/fuel', methods=['GET'])
@api_login_required
def get_fuel_records():
    """Get all fuel records for a vehicle with optional pagination"""
    vehicle_id = request.args.get('vehicle_id')
    page = request.args.get('page', type=int)
    per_page = request.args.get('per_page', 20, type=int)

    db = get_db()

    # Base query and count query
    if vehicle_id:
        base_query = 'SELECT * FROM fuel_records WHERE vehicle_id = ? ORDER BY date DESC, odometer DESC'
        count_query = 'SELECT COUNT(*) as total FROM fuel_records WHERE vehicle_id = ?'
        params = (vehicle_id,)
        notes = annotate_fuel_records(db, vehicle_id)
    else:
        base_query = 'SELECT * FROM fuel_records ORDER BY date DESC, odometer DESC'
        count_query = 'SELECT COUNT(*) as total FROM fuel_records'
        params = ()
        notes = {}

    # If pagination requested, add LIMIT and OFFSET
    if page is not None:
        total_count = db.execute(count_query, params).fetchone()['total']
        offset = (page - 1) * per_page
        query = base_query + ' LIMIT ? OFFSET ?'
        records = db.execute(query, params + (per_page, offset)).fetchall()

        db.close()
        return jsonify({
            'data': [_fuel_dict(row, notes) for row in records],
            'total': total_count,
            'page': page,
            'per_page': per_page,
            'total_pages': (total_count + per_page - 1) // per_page
        })

    # Return all results (backwards compatibility)
    records = db.execute(base_query, params).fetchall()
    db.close()
    return jsonify([_fuel_dict(row, notes) for row in records])

@app.route('/api/fuel', methods=['POST'])
@api_login_required
def add_fuel_record():
    """Add new fuel record and calculate MPG.

    JSON: vehicle_id, date, odometer, station, and any two of gallons, cost
    and price_per_gallon. Optional: full_tank (default true), missed_fillup,
    grade, notes."""
    data = _payload()
    vehicle_id = _number(data, 'vehicle_id', 'Vehicle', integer=True)
    if not vehicle_id:
        raise ApiError('Vehicle ID is required')
    fields = _fuel_fields(data)

    with db_session() as db:
        cursor = db.execute('''INSERT INTO fuel_records
                              (vehicle_id, date, gallons, cost, odometer, station, missed_fillup,
                               full_tank, grade, notes)
                              VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                           (vehicle_id, fields['date'], fields['gallons'], fields['cost'],
                            fields['odometer'], fields['station'], fields['missed_fillup'],
                            fields['full_tank'], fields['grade'], fields['notes']))
        recalculate_mpg(db, vehicle_id)
        result = _fuel_save_result(db, vehicle_id, cursor.lastrowid, fields)
    return jsonify(result)

@app.route('/api/fuel/<int:fuel_id>', methods=['GET'])
@api_login_required
def get_fuel_record(fuel_id):
    """Get a specific fuel record"""
    db = get_db()
    record = db.execute('SELECT * FROM fuel_records WHERE id = ?', (fuel_id,)).fetchone()
    db.close()

    if record:
        return jsonify(_fuel_dict(record))
    return jsonify({'error': 'Fuel record not found'}), 404

@app.route('/api/fuel/<int:fuel_id>', methods=['PUT'])
@api_login_required
def update_fuel_record(fuel_id):
    """Update an existing fuel record"""
    fields = _fuel_fields(_payload())
    with db_session() as db:
        record = db.execute('SELECT vehicle_id FROM fuel_records WHERE id = ?', (fuel_id,)).fetchone()
        if not record:
            raise ApiError('Fuel record not found', 404)
        db.execute('''UPDATE fuel_records
                     SET date=?, gallons=?, cost=?, odometer=?, station=?, missed_fillup=?,
                         full_tank=?, grade=?, notes=?
                     WHERE id=?''',
                  (fields['date'], fields['gallons'], fields['cost'], fields['odometer'],
                   fields['station'], fields['missed_fillup'], fields['full_tank'],
                   fields['grade'], fields['notes'], fuel_id))
        # Recalculate the whole chain: this edit may have shifted the neighbours' MPG too
        recalculate_mpg(db, record['vehicle_id'])
        result = _fuel_save_result(db, record['vehicle_id'], fuel_id, fields)
    return jsonify(result)

@app.route('/api/fuel/<int:fuel_id>', methods=['DELETE'])
@api_login_required
def delete_fuel_record(fuel_id):
    """Delete a fuel record and fix up the MPG of the record after it"""
    db = get_db()
    record = db.execute('SELECT vehicle_id FROM fuel_records WHERE id = ?', (fuel_id,)).fetchone()
    db.execute('DELETE FROM fuel_records WHERE id = ?', (fuel_id,))
    if record:
        recalculate_mpg(db, record['vehicle_id'])
    db.commit()
    db.close()
    return jsonify({'success': True})

FUEL_PERIODS = ('30d', 'ytd', '12m', 'all')

@app.route('/api/fuel/insights', methods=['GET'])
@api_login_required
def get_fuel_insights():
    """Fuel totals for a vehicle over a period: `period` is 30d, ytd, 12m or
    all (default). Cost per mile only counts fill-ups whose miles are fully
    accounted for, so a missed fill-up doesn't make driving look cheaper."""
    vehicle_id = request.args.get('vehicle_id', type=int)
    if not vehicle_id:
        raise ApiError('vehicle_id is required')
    period = request.args.get('period', 'all')
    if period not in FUEL_PERIODS:
        raise ApiError('period must be one of ' + ', '.join(FUEL_PERIODS))
    today = datetime.now().date()
    start = {'30d': today - timedelta(days=30), '12m': today - timedelta(days=365),
             'ytd': today.replace(month=1, day=1), 'all': None}[period]
    start_date = start.isoformat() if start else None

    db = get_db()
    notes = annotate_fuel_records(db, vehicle_id)
    rows = db.execute('SELECT * FROM fuel_records WHERE vehicle_id = ? ORDER BY date, odometer, id',
                      (vehicle_id,)).fetchall()
    pace = driving_pace(db, vehicle_id)
    db.close()

    records = [_fuel_dict(r, notes) for r in rows if not start_date or r['date'] >= start_date]
    gallons = sum(r['gallons'] or 0 for r in records)
    spent = sum(r['cost'] or 0 for r in records)
    miles = sum(r['distance'] or 0 for r in records)
    tanks = [r for r in records if r['mpg']]
    trip_miles = sum(r['trip_miles'] for r in tanks)
    trip_gallons = sum(r['trip_gallons'] for r in tanks)
    accounted = [r for r in records if r['distance'] and not r['missed_fillup']]
    accounted_miles = sum(r['distance'] for r in accounted)

    months = {}
    for r in records:
        month = months.setdefault(r['date'][:7], {'month': r['date'][:7], 'spent': 0, 'gallons': 0, 'miles': 0})
        month['spent'] = round(month['spent'] + (r['cost'] or 0), 2)
        month['gallons'] = round(month['gallons'] + (r['gallons'] or 0), 3)
        month['miles'] += r['distance'] or 0

    return jsonify({
        'period': period,
        'start_date': start_date,
        'fill_ups': len(records),
        'gallons': round(gallons, 3),
        'spent': round(spent, 2),
        'miles': miles,
        'avg_mpg': round(trip_miles / trip_gallons, 2) if trip_gallons else None,
        'best_mpg': max((r['mpg'] for r in tanks), default=None),
        'avg_price_per_gallon': round(spent / gallons, 3) if gallons else None,
        'cost_per_mile': round(sum(r['cost'] or 0 for r in accounted) / accounted_miles, 3)
                         if accounted_miles else None,
        'low_economy_tanks': sum(1 for r in tanks if r['low_economy']),
        'monthly': [months[m] for m in sorted(months)],
        'pace': pace,
    })

# CSV Export/Import endpoints for fuel records
FUEL_CSV_COLUMNS = ['date', 'odometer', 'gallons', 'cost', 'station', 'full_tank', 'missed_fillup',
                    'grade', 'notes']

def _csv_flag(value, default):
    value = (value or '').strip().lower()
    if not value:
        return default
    return value in ('yes', 'y', 'true', '1')

@app.route('/api/fuel/export-csv', methods=['GET'])
@api_login_required
def export_fuel_csv():
    """Export fuel records to CSV. The file can be imported back in; `mpg`
    is informational and recalculated on import."""
    vehicle_id = request.args.get('vehicle_id')
    query = 'SELECT * FROM fuel_records'
    params = ()
    if vehicle_id:
        query += ' WHERE vehicle_id = ?'
        params = (vehicle_id,)
    db = get_db()
    records = [_fuel_dict(r) for r in db.execute(query + ' ORDER BY date DESC, odometer DESC', params).fetchall()]
    db.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(FUEL_CSV_COLUMNS + ['mpg'])
    for record in records:
        writer.writerow([
            record['date'] or '',
            record['odometer'],
            record['gallons'],
            record['cost'],
            record['station'] or '',
            'yes' if record['full_tank'] else 'no',
            'yes' if record['missed_fillup'] else '',
            record['grade'] or '',
            record['notes'] or '',
            record['mpg'] or ''
        ])

    response = make_response(output.getvalue())
    response.headers['Content-Type'] = 'text/csv; charset=utf-8'
    response.headers['Content-Disposition'] = 'attachment; filename=fuel_records.csv'
    return response

@app.route('/api/fuel/template-csv', methods=['GET'])
@api_login_required
def download_fuel_template():
    """Download a CSV template for importing fuel records"""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(FUEL_CSV_COLUMNS)
    # full_tank: yes (default when blank) or no. missed_fillup: yes or blank.
    writer.writerow(['2024-01-15', '50000', '12.5', '45.00', 'Shell', 'yes', '', 'Regular', ''])
    writer.writerow(['2024-01-22', '50350', '4.0', '14.80', 'BP', 'no', '', 'Regular', 'Topped up before a trip'])

    output.seek(0)
    response = make_response(output.getvalue())
    response.headers['Content-Type'] = 'text/csv; charset=utf-8'
    response.headers['Content-Disposition'] = 'attachment; filename=fuel_records_template.csv'
    return response

@app.route('/api/fuel/import-csv', methods=['POST'])
@api_login_required
def import_fuel_csv():
    """Import fuel records from CSV. Rows that match a record already there
    (same date, odometer and gallons) are skipped, so importing the same
    file twice doesn't duplicate anything."""
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400

    vehicle_id = request.form.get('vehicle_id')
    if not vehicle_id:
        return jsonify({'error': 'Vehicle ID is required'}), 400

    try:
        # utf-8-sig drops the byte-order mark Excel puts at the start
        stream = io.StringIO(file.stream.read().decode('utf-8-sig'), newline=None)
        csv_reader = csv.DictReader(stream)

        db = get_db()
        imported_count = 0
        skipped_count = 0
        errors = []

        for row_num, row in enumerate(csv_reader, start=2):  # Start at 2 because of header
            try:
                row = {(k or '').strip(): (v or '').strip() for k, v in row.items()}
                if not row.get('date') or not row.get('odometer') or not row.get('gallons') or not row.get('cost'):
                    errors.append(f"Row {row_num}: Missing required fields")
                    continue
                if _parse_date(row['date']) is None:
                    errors.append(f"Row {row_num}: Invalid date format (use YYYY-MM-DD)")
                    continue
                try:
                    odometer = int(float(row['odometer']))
                    gallons = float(row['gallons'])
                    cost = float(row['cost'])
                except ValueError:
                    errors.append(f"Row {row_num}: Invalid numeric value")
                    continue

                duplicate = db.execute('''SELECT 1 FROM fuel_records
                                          WHERE vehicle_id = ? AND date = ? AND odometer = ?
                                            AND ABS(gallons - ?) < 0.0005''',
                                       (vehicle_id, row['date'], odometer, gallons)).fetchone()
                if duplicate:
                    skipped_count += 1
                    continue

                # MPG is computed for the whole chain below
                db.execute('''
                    INSERT INTO fuel_records
                    (vehicle_id, date, odometer, gallons, cost, station, full_tank, missed_fillup, grade, notes)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (vehicle_id, row['date'], odometer, gallons, cost, row.get('station', ''),
                      1 if _csv_flag(row.get('full_tank'), True) else 0,
                      1 if _csv_flag(row.get('missed_fillup'), False) else 0,
                      row.get('grade') or None, row.get('notes') or None))
                imported_count += 1

            except Exception as e:
                errors.append(f"Row {row_num}: {str(e)}")

        if imported_count:
            recalculate_mpg(db, vehicle_id)
        db.commit()
        db.close()

        return jsonify({
            'success': True,
            'imported': imported_count,
            'skipped': skipped_count,
            'errors': errors
        })

    except Exception as e:
        return jsonify({'error': f'Failed to import CSV: {str(e)}'}), 400


# ── Driving pace and projected due dates ────────────────────────────────────
#
# A reminder due at a mileage says nothing about when. Each vehicle has a
# driving pace in miles per year: the figure its owner sets, or failing
# that an estimate from the odometer readings on its fuel and service
# records. The pace turns "due at 59,500 mi" into "around mid-March".

PACE_MIN_DAYS = 30

def driving_pace(db, vehicle_id):
    """A vehicle's miles per year: what was set, what the records suggest,
    and which of the two is in effect (set wins)."""
    vehicle = db.execute('SELECT miles_per_year FROM vehicle WHERE id = ?', (vehicle_id,)).fetchone()
    chosen = vehicle['miles_per_year'] if vehicle else None

    readings = db.execute('''SELECT date, odometer FROM service_records
                             WHERE vehicle_id = :v AND odometer IS NOT NULL
                             UNION ALL
                             SELECT date, odometer FROM fuel_records WHERE vehicle_id = :v
                             ORDER BY date, odometer''', {'v': vehicle_id}).fetchall()
    readings = [(d, r['odometer']) for r in readings for d in [_parse_date(r['date'])] if d]
    estimate = None
    if readings:
        latest = readings[-1][0]
        # The last year of driving if there's enough of it, else everything
        recent = [r for r in readings if (latest - r[0]).days <= 365]
        for window in (recent, readings):
            days = (window[-1][0] - window[0][0]).days
            miles = window[-1][1] - window[0][1]
            if days >= PACE_MIN_DAYS and miles > 0:
                estimate = round(miles / days * 365)
                break

    return {
        'miles_per_year': chosen,
        'estimated_miles_per_year': estimate,
        'effective_miles_per_year': chosen or estimate,
        'source': 'set' if chosen else 'estimated' if estimate else None,
    }

def projected_date(due_mileage, current_odometer, miles_per_year, today=None):
    """When the odometer should reach `due_mileage` at the vehicle's pace, or
    None if that can't be said (no pace, no reading, or already past it)."""
    if due_mileage is None or current_odometer is None or not miles_per_year:
        return None
    remaining = due_mileage - current_odometer
    if remaining <= 0:
        return None
    days = remaining / (miles_per_year / 365)
    if days > 3650:
        return None
    return ((today or datetime.now().date()) + timedelta(days=round(days))).isoformat()

@app.route('/api/vehicle/<int:vehicle_id>/driving-pace', methods=['GET'])
@api_login_required
def get_driving_pace(vehicle_id):
    db = get_db()
    pace = driving_pace(db, vehicle_id)
    pace['current_odometer'] = latest_odometer(db, vehicle_id)
    db.close()
    return jsonify(pace)

@app.route('/api/vehicle/<int:vehicle_id>/driving-pace', methods=['PUT'])
@api_login_required
def set_driving_pace(vehicle_id):
    """Set how many miles a year this vehicle is driven, used to project
    when mileage-based reminders will come due. Blank goes back to the
    estimate from the vehicle's records."""
    miles = _number(_payload(), 'miles_per_year', 'Miles per year', integer=True) or None
    with db_session() as db:
        cursor = db.execute('UPDATE vehicle SET miles_per_year = ? WHERE id = ?', (miles, vehicle_id))
        if cursor.rowcount == 0:
            raise ApiError('Vehicle not found', 404)
        pace = driving_pace(db, vehicle_id)
    return jsonify({'success': True, **pace})

# Service reminders endpoints
@app.route('/api/reminders', methods=['GET'])
@api_login_required
def get_reminders():
    """Get all service reminders for a vehicle. Open reminders with a due
    mileage also carry `miles_remaining` and, where the vehicle's driving
    pace is known, the `projected_date` it should reach that mileage."""
    vehicle_id = request.args.get('vehicle_id')
    db = get_db()

    if vehicle_id:
        reminders = db.execute('SELECT * FROM service_reminders WHERE vehicle_id = ? ORDER BY completed, due_date', (vehicle_id,)).fetchall()
    else:
        reminders = db.execute('SELECT * FROM service_reminders ORDER BY completed, due_date').fetchall()

    vehicles = {}
    result = []
    for row in reminders:
        reminder = dict(row)
        reminder['miles_remaining'] = reminder['projected_date'] = None
        if reminder['due_mileage'] is not None and not reminder['completed']:
            if reminder['vehicle_id'] not in vehicles:
                vehicles[reminder['vehicle_id']] = (
                    latest_odometer(db, reminder['vehicle_id']),
                    driving_pace(db, reminder['vehicle_id'])['effective_miles_per_year'])
            odometer, pace = vehicles[reminder['vehicle_id']]
            if odometer is not None:
                reminder['miles_remaining'] = reminder['due_mileage'] - odometer
            reminder['projected_date'] = projected_date(reminder['due_mileage'], odometer, pace)
        result.append(reminder)
    db.close()
    return jsonify(result)

@app.route('/api/reminders', methods=['POST'])
@api_login_required
def add_reminder():
    """Add new service reminder"""
    data = request.json
    vehicle_id = data.get('vehicle_id')

    if not vehicle_id:
        return jsonify({'error': 'Vehicle ID is required'}), 400

    db = get_db()
    cursor = db.execute('''INSERT INTO service_reminders
                          (vehicle_id, service_type, due_date, due_mileage, notes)
                          VALUES (?, ?, ?, ?, ?)''',
                       (vehicle_id, data['service_type'],
                        data.get('due_date'), data.get('due_mileage'), data.get('notes', '')))

    db.commit()
    reminder_id = cursor.lastrowid
    db.close()
    return jsonify({'success': True, 'id': reminder_id})

@app.route('/api/reminders/<int:reminder_id>', methods=['PUT'])
@api_login_required
def update_reminder(reminder_id):
    """Update reminder completion status"""
    data = request.json
    db = get_db()
    db.execute('UPDATE service_reminders SET completed=? WHERE id=?',
              (data['completed'], reminder_id))
    db.commit()
    db.close()
    return jsonify({'success': True})

@app.route('/api/reminders/<int:reminder_id>', methods=['DELETE'])
@api_login_required
def delete_reminder(reminder_id):
    """Delete a reminder"""
    db = get_db()
    db.execute('DELETE FROM service_reminders WHERE id = ?', (reminder_id,))
    db.commit()
    db.close()
    return jsonify({'success': True})

# Dashboard stats
@app.route('/api/stats', methods=['GET'])
@api_login_required
def get_stats():
    """Get dashboard statistics for a vehicle"""
    vehicle_id = request.args.get('vehicle_id')
    db = get_db()

    if not vehicle_id:
        # Get the first vehicle if none specified
        vehicle = db.execute('SELECT id FROM vehicle ORDER BY created_at ASC LIMIT 1').fetchone()
        if not vehicle:
            db.close()
            return jsonify({
                'total_services': 0,
                'total_spent': 0,
                'avg_mpg': 0,
                'pending_reminders': 0
            })
        vehicle_id = vehicle['id']

    stats = {}

    # Total services
    result = db.execute('SELECT COUNT(*) as count FROM service_records WHERE vehicle_id = ?',
                       (vehicle_id,)).fetchone()
    stats['total_services'] = result['count']

    # Total spent on services, including parts used (matches the
    # per-service totals shown on the Services page)
    result = db.execute(f'''SELECT COALESCE(SUM(s.cost + {PARTS_COST_SQL}), 0) AS total
                            FROM service_records s WHERE s.vehicle_id = ?''', (vehicle_id,)).fetchone()
    stats['total_spent'] = round(result['total'], 2)

    stats['avg_mpg'] = weighted_avg_mpg(db, vehicle_id) or 0

    # Pending reminders
    result = db.execute('SELECT COUNT(*) as count FROM service_reminders WHERE vehicle_id = ? AND completed = 0',
                       (vehicle_id,)).fetchone()
    stats['pending_reminders'] = result['count']

    db.close()
    return jsonify(stats)

# Settings API
@app.route('/api/settings', methods=['GET'])
@api_admin_required
def get_settings():
    """Get all settings"""
    return jsonify({
        'show_default_credentials': get_setting('show_default_credentials', '1') == '1'
    })

@app.route('/api/settings', methods=['POST'])
@api_admin_required
def update_settings():
    """Update settings"""
    data = request.json
    if 'show_default_credentials' in data:
        set_setting('show_default_credentials', '1' if data['show_default_credentials'] else '0')
    return jsonify({'success': True})

# Documents API
@app.route('/api/documents', methods=['GET'])
@api_login_required
def get_documents():
    """Get all documents for a vehicle"""
    vehicle_id = request.args.get('vehicle_id')
    if not vehicle_id:
        return jsonify({'error': 'vehicle_id is required'}), 400
    db = get_db()
    docs = db.execute(
        'SELECT * FROM documents WHERE vehicle_id = ? ORDER BY created_at DESC',
        (vehicle_id,)
    ).fetchall()
    db.close()
    return jsonify([dict(row) for row in docs])

@app.route('/api/documents', methods=['POST'])
@api_login_required
def add_document():
    """Add a new document"""
    data = request.form
    vehicle_id = data.get('vehicle_id')
    title = data.get('title')
    category = data.get('category')

    if not vehicle_id or not title or not category:
        return jsonify({'error': 'vehicle_id, title, and category are required'}), 400

    file_path = save_upload('file', 'documents')

    db = get_db()
    cursor = db.execute(
        'INSERT INTO documents (vehicle_id, title, category, notes, file_path) VALUES (?, ?, ?, ?, ?)',
        (vehicle_id, title, category, data.get('notes', ''), file_path)
    )
    doc_id = cursor.lastrowid
    db.commit()
    db.close()
    return jsonify({'success': True, 'id': doc_id})

@app.route('/api/documents/<int:doc_id>', methods=['PUT'])
@api_login_required
def update_document(doc_id):
    """Update a document's title, category, and notes"""
    data = request.json
    db = get_db()
    db.execute(
        'UPDATE documents SET title=?, category=?, notes=? WHERE id=?',
        (data['title'], data['category'], data.get('notes', ''), doc_id)
    )
    db.commit()
    db.close()
    return jsonify({'success': True})

@app.route('/api/documents/<int:doc_id>', methods=['DELETE'])
@api_login_required
def delete_document(doc_id):
    """Delete a document and its file"""
    db = get_db()
    doc = db.execute('SELECT file_path FROM documents WHERE id = ?', (doc_id,)).fetchone()
    db.execute('DELETE FROM documents WHERE id = ?', (doc_id,))
    db.commit()
    db.close()
    if doc:
        remove_upload(doc['file_path'])
    return jsonify({'success': True})

# Global search endpoint
@app.route('/api/search', methods=['GET'])
@api_login_required
def global_search():
    """Search across all sections for the selected vehicle"""
    query = request.args.get('q', '').strip()
    vehicle_id = request.args.get('vehicle_id')

    if not query or len(query) < 2:
        return jsonify({'results': {}})

    like = f'%{query}%'
    db = get_db()
    results = {}

    # Service records
    where = 'WHERE ' + SERVICE_TEXT_MATCH
    service_params = {'q': like.lower()}
    if vehicle_id:
        where += ' AND s.vehicle_id = :vehicle_id'
        service_params['vehicle_id'] = vehicle_id
    rows = db.execute(f'{SERVICE_SELECT} {where} ORDER BY s.date DESC LIMIT 10', service_params).fetchall()
    if rows:
        results['services'] = [{'id': r['id'], 'date': r['date'], 'label': r['service_provider'],
                                 'sub': r['service_types'] or r['repairs_completed'] or '',
                                 'cost': r['cost']} for r in rows]

    # Fuel records
    base = 'FROM fuel_records WHERE LOWER(station) LIKE ?'
    params = (like,)
    if vehicle_id:
        base += ' AND vehicle_id = ?'
        params += (vehicle_id,)
    rows = db.execute(f'SELECT id, date, station, gallons, cost {base} ORDER BY date DESC LIMIT 10', params).fetchall()
    if rows:
        results['fuel'] = [{'id': r['id'], 'date': r['date'], 'label': r['station'],
                             'sub': f"{r['gallons']} gal — ${r['cost']}"} for r in rows]

    # Parts (one shared shelf, so not filtered by vehicle)
    rows = db.execute('''
        SELECT p.id, p.name, p.brand, p.part_number,
               COALESCE((SELECT SUM(quantity) FROM part_purchases WHERE part_id = p.id), 0)
             - COALESCE((SELECT SUM(pi.quantity) FROM part_installs pi
                         JOIN part_purchases pp ON pp.id = pi.purchase_id
                         WHERE pp.part_id = p.id), 0) AS in_stock
        FROM parts p
        WHERE LOWER(p.name) LIKE ? OR LOWER(p.part_number) LIKE ? OR LOWER(p.brand) LIKE ?
           OR LOWER(p.notes) LIKE ? OR LOWER(p.category) LIKE ?
        ORDER BY p.name LIMIT 10''', (like, like, like, like, like)).fetchall()
    if rows:
        results['parts'] = [{'id': r['id'], 'label': r['name'],
                             'sub': ' '.join(filter(None, [r['brand'], r['part_number']])),
                             'qty': round(r['in_stock'], 4)} for r in rows]

    # Reminders
    base = 'FROM service_reminders WHERE (LOWER(service_type) LIKE ? OR LOWER(notes) LIKE ?)'
    params = (like, like)
    if vehicle_id:
        base += ' AND vehicle_id = ?'
        params += (vehicle_id,)
    rows = db.execute(f'SELECT id, service_type, due_date, notes, completed {base} ORDER BY completed, due_date LIMIT 10', params).fetchall()
    if rows:
        results['reminders'] = [{'id': r['id'], 'label': r['service_type'],
                                  'sub': r['due_date'] or '', 'completed': r['completed']} for r in rows]

    # Documents
    base = 'FROM documents WHERE (LOWER(title) LIKE ? OR LOWER(category) LIKE ? OR LOWER(notes) LIKE ?)'
    params = (like, like, like)
    if vehicle_id:
        base += ' AND vehicle_id = ?'
        params += (vehicle_id,)
    rows = db.execute(f'SELECT id, title, category, notes {base} ORDER BY created_at DESC LIMIT 10', params).fetchall()
    if rows:
        results['documents'] = [{'id': r['id'], 'label': r['title'],
                                  'sub': r['category']} for r in rows]

    # Tires
    base = 'FROM tires WHERE (LOWER(brand) LIKE ? OR LOWER(model) LIKE ? OR LOWER(size) LIKE ? OR LOWER(notes) LIKE ?)'
    params = (like, like, like, like)
    if vehicle_id:
        base += ' AND vehicle_id = ?'
        params += (vehicle_id,)
    rows = db.execute(f'SELECT id, brand, model, size, season {base} ORDER BY is_installed DESC LIMIT 10', params).fetchall()
    if rows:
        results['tires'] = [{'id': r['id'], 'label': f"{r['brand']} {r['model'] or ''}".strip(),
                              'sub': ' '.join(filter(None, [r['size'], r['season']]))} for r in rows]

    db.close()
    return jsonify({'results': results, 'query': query})


# Tire log endpoints
@app.route('/api/tires', methods=['GET'])
@api_login_required
def get_tires():
    vehicle_id = request.args.get('vehicle_id')
    db = get_db()
    if vehicle_id:
        tires = db.execute('SELECT * FROM tires WHERE vehicle_id = ? ORDER BY is_installed DESC, created_at DESC', (vehicle_id,)).fetchall()
    else:
        tires = db.execute('SELECT * FROM tires ORDER BY is_installed DESC, created_at DESC').fetchall()
    db.close()
    return jsonify([dict(t) for t in tires])

@app.route('/api/tires', methods=['POST'])
@api_login_required
def add_tire():
    data = request.json
    vehicle_id = data.get('vehicle_id')
    if not vehicle_id or not data.get('brand'):
        return jsonify({'error': 'vehicle_id and brand are required'}), 400
    db = get_db()
    cursor = db.execute(
        '''INSERT INTO tires (vehicle_id, brand, model, size, season, dot_code,
           purchase_date, purchase_price, is_installed, installed_date, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (vehicle_id, data['brand'], data.get('model', ''), data.get('size', ''),
         data.get('season', 'All-Season'), data.get('dot_code', ''),
         data.get('purchase_date'), data.get('purchase_price'),
         1 if data.get('is_installed') else 0,
         data.get('installed_date'), data.get('notes', ''))
    )
    db.commit()
    db.close()
    return jsonify({'success': True, 'id': cursor.lastrowid})

@app.route('/api/tires/<int:tire_id>', methods=['PUT'])
@api_login_required
def update_tire(tire_id):
    data = request.json
    db = get_db()
    db.execute(
        '''UPDATE tires SET brand=?, model=?, size=?, season=?, dot_code=?,
           purchase_date=?, purchase_price=?, installed_date=?, notes=? WHERE id=?''',
        (data['brand'], data.get('model', ''), data.get('size', ''),
         data.get('season', 'All-Season'), data.get('dot_code', ''),
         data.get('purchase_date'), data.get('purchase_price'),
         data.get('installed_date'), data.get('notes', ''), tire_id)
    )
    db.commit()
    db.close()
    return jsonify({'success': True})

@app.route('/api/tires/<int:tire_id>/install', methods=['POST'])
@api_login_required
def install_tire(tire_id):
    """Mark a tire set as currently installed (uninstalls others for the same vehicle)"""
    data = request.json or {}
    date = data.get('date')
    odometer = data.get('odometer')
    if not date:
        return jsonify({'error': 'date is required'}), 400
    db = get_db()
    tire = db.execute('SELECT vehicle_id FROM tires WHERE id = ?', (tire_id,)).fetchone()
    if not tire:
        db.close()
        return jsonify({'error': 'Tire not found'}), 404
    db.execute('UPDATE tires SET is_installed = 0 WHERE vehicle_id = ?', (tire['vehicle_id'],))
    db.execute('UPDATE tires SET is_installed = 1, installed_date = ? WHERE id = ?', (date, tire_id))
    db.execute('INSERT INTO tire_install_log (tire_id, vehicle_id, event_type, date, odometer) VALUES (?, ?, ?, ?, ?)',
               (tire_id, tire['vehicle_id'], 'install', date, odometer))
    db.commit()
    db.close()
    return jsonify({'success': True})

@app.route('/api/tires/<int:tire_id>/uninstall', methods=['POST'])
@api_login_required
def uninstall_tire(tire_id):
    data = request.json or {}
    date = data.get('date')
    odometer = data.get('odometer')
    if not date:
        return jsonify({'error': 'date is required'}), 400
    db = get_db()
    tire = db.execute('SELECT vehicle_id FROM tires WHERE id = ?', (tire_id,)).fetchone()
    if not tire:
        db.close()
        return jsonify({'error': 'Tire not found'}), 404
    db.execute('UPDATE tires SET is_installed = 0 WHERE id = ?', (tire_id,))
    db.execute('INSERT INTO tire_install_log (tire_id, vehicle_id, event_type, date, odometer) VALUES (?, ?, ?, ?, ?)',
               (tire_id, tire['vehicle_id'], 'uninstall', date, odometer))
    db.commit()
    db.close()
    return jsonify({'success': True})

@app.route('/api/tire-install-log', methods=['GET'])
@api_login_required
def get_tire_install_log():
    vehicle_id = request.args.get('vehicle_id')
    tire_id = request.args.get('tire_id')
    db = get_db()
    query = '''
        SELECT l.*, t.brand || COALESCE(' ' || NULLIF(t.model, ''), '') AS tire_name
        FROM tire_install_log l
        JOIN tires t ON t.id = l.tire_id
    '''
    conditions = []
    params = []
    if vehicle_id:
        conditions.append('l.vehicle_id = ?')
        params.append(vehicle_id)
    if tire_id:
        conditions.append('l.tire_id = ?')
        params.append(tire_id)
    if conditions:
        query += ' WHERE ' + ' AND '.join(conditions)
    query += ' ORDER BY l.date DESC, l.created_at DESC'
    rows = db.execute(query, params).fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/tires/<int:tire_id>', methods=['DELETE'])
@api_login_required
def delete_tire(tire_id):
    db = get_db()
    db.execute('DELETE FROM tread_readings WHERE tire_id = ?', (tire_id,))
    db.execute('DELETE FROM tire_install_log WHERE tire_id = ?', (tire_id,))
    # Rotations are vehicle history worth keeping; just unlink the tire set
    db.execute('UPDATE tire_rotations SET tire_id = NULL WHERE tire_id = ?', (tire_id,))
    db.execute('DELETE FROM tires WHERE id = ?', (tire_id,))
    db.commit()
    db.close()
    return jsonify({'success': True})

@app.route('/api/tire-rotations', methods=['GET'])
@api_login_required
def get_tire_rotations():
    vehicle_id = request.args.get('vehicle_id')
    db = get_db()
    query = '''
        SELECT tr.*, t.brand || COALESCE(' ' || NULLIF(t.model, ''), '') AS tire_name
        FROM tire_rotations tr
        LEFT JOIN tires t ON t.id = tr.tire_id
    '''
    if vehicle_id:
        rows = db.execute(query + ' WHERE tr.vehicle_id = ? ORDER BY tr.date DESC', (vehicle_id,)).fetchall()
    else:
        rows = db.execute(query + ' ORDER BY tr.date DESC').fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/tire-rotations', methods=['POST'])
@api_login_required
def add_tire_rotation():
    data = request.json
    vehicle_id = data.get('vehicle_id')
    if not vehicle_id or not data.get('date'):
        return jsonify({'error': 'vehicle_id and date are required'}), 400
    db = get_db()
    cursor = db.execute(
        'INSERT INTO tire_rotations (vehicle_id, date, odometer, pattern, notes, tire_id) VALUES (?, ?, ?, ?, ?, ?)',
        (vehicle_id, data['date'], data.get('odometer'), data.get('pattern', ''), data.get('notes', ''), data.get('tire_id'))
    )
    db.commit()
    db.close()
    return jsonify({'success': True, 'id': cursor.lastrowid})

@app.route('/api/tire-rotations/<int:rotation_id>', methods=['DELETE'])
@api_login_required
def delete_tire_rotation(rotation_id):
    db = get_db()
    db.execute('DELETE FROM tire_rotations WHERE id = ?', (rotation_id,))
    db.commit()
    db.close()
    return jsonify({'success': True})

@app.route('/api/tread-readings', methods=['GET'])
@api_login_required
def get_tread_readings():
    vehicle_id = request.args.get('vehicle_id')
    db = get_db()
    if vehicle_id:
        rows = db.execute(
            '''SELECT tr.*, t.brand || COALESCE(' ' || NULLIF(t.model, ''), '') AS tire_name
               FROM tread_readings tr
               LEFT JOIN tires t ON tr.tire_id = t.id
               WHERE tr.vehicle_id = ? ORDER BY tr.date DESC''', (vehicle_id,)
        ).fetchall()
    else:
        rows = db.execute('SELECT * FROM tread_readings ORDER BY date DESC').fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/tread-readings', methods=['POST'])
@api_login_required
def add_tread_reading():
    data = request.json
    vehicle_id = data.get('vehicle_id')
    if not vehicle_id or not data.get('date'):
        return jsonify({'error': 'vehicle_id and date are required'}), 400
    db = get_db()
    cursor = db.execute(
        '''INSERT INTO tread_readings (vehicle_id, tire_id, date, odometer,
           fl_depth, fr_depth, rl_depth, rr_depth, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (vehicle_id, data.get('tire_id'), data['date'], data.get('odometer'),
         data.get('fl_depth'), data.get('fr_depth'),
         data.get('rl_depth'), data.get('rr_depth'), data.get('notes', ''))
    )
    db.commit()
    db.close()
    return jsonify({'success': True, 'id': cursor.lastrowid})

@app.route('/api/tread-readings/<int:reading_id>', methods=['DELETE'])
@api_login_required
def delete_tread_reading(reading_id):
    db = get_db()
    db.execute('DELETE FROM tread_readings WHERE id = ?', (reading_id,))
    db.commit()
    db.close()
    return jsonify({'success': True})

def setup():
    """Create runtime directories and bring the database schema up to date.
    Run once per server start, before serving requests (wsgi.py does this for
    gunicorn, in the master process before workers fork)."""
    os.makedirs(os.path.dirname(DATABASE) or '.', exist_ok=True)
    for sub in ('receipts', 'vehicles', 'documents'):
        os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], sub), exist_ok=True)
    init_db()

if __name__ == '__main__':
    # Development server; production runs gunicorn via wsgi.py
    setup()
    app.run(host='0.0.0.0', port=5000, debug=False)
