from flask import Flask, render_template, request, jsonify, send_from_directory, redirect, url_for, flash, make_response, g
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta, timezone
from functools import wraps
import sqlite3
import os
import secrets
import jwt
from werkzeug.utils import secure_filename
import json
import csv
import io
import requests

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'your-secret-key-change-in-production')
app.config['JWT_SECRET_KEY'] = os.environ.get('JWT_SECRET_KEY', secrets.token_hex(32))
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = timedelta(hours=1)
app.config['JWT_REFRESH_TOKEN_EXPIRES'] = timedelta(days=30)
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size
app.config['ALLOWED_EXTENSIONS'] = {'png', 'jpg', 'jpeg', 'gif', 'pdf', 'webp'}

CORS(app, resources={r'/api/*': {'origins': '*'}}, supports_credentials=True)

DATABASE = 'instance/vehicle_tracker.db'

# Flask-Login setup
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Please log in to access this page.'

def get_db():
    """Create database connection"""
    conn = sqlite3.connect(DATABASE)
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
        'sub': user_id,
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
        return User(data['sub'], data['username'], data['is_admin'])
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


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

def init_db():
    """Initialize database with schema"""
    with app.app_context():
        db = get_db()
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

        # Migration: Add parts tracking columns to supplies table
        parts_tracking_migrations = [
            'ALTER TABLE supplies ADD COLUMN category TEXT',
            'ALTER TABLE supplies ADD COLUMN location TEXT',
            'ALTER TABLE supplies ADD COLUMN condition TEXT DEFAULT "New"',
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

        # Migration: Drop location column from fuel_records table
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
                date TEXT NOT NULL,
                odometer INTEGER,
                pattern TEXT,
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (vehicle_id) REFERENCES vehicle(id)
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

        db.close()

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

# Authentication Routes
@app.route('/login', methods=['GET', 'POST'])
def login():
    """User login"""
    if current_user.is_authenticated:
        return redirect(url_for('index'))

    if request.method == 'POST':
        data = request.form
        username = data.get('username')
        password = data.get('password')

        db = get_db()
        user = db.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        db.close()

        if user and check_password_hash(user['password_hash'], password):
            user_obj = User(user['id'], user['username'], user['is_admin'])
            login_user(user_obj)
            next_page = request.args.get('next')
            return redirect(next_page if next_page else url_for('index'))
        else:
            flash('Invalid username or password', 'error')

    show_creds = get_setting('show_default_credentials', '1') == '1'
    return render_template('login.html', show_default_credentials=show_creds)

@app.route('/logout')
@login_required
def logout():
    """User logout"""
    logout_user()
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

@app.route('/supplies')
@login_required
def supplies_page():
    """Supplies inventory page"""
    return render_template('supplies.html')

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
    if user_id == current_user.id:
        return jsonify({'error': 'Cannot delete your own account'}), 400

    db = get_db()
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
    db.commit()
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

    db = get_db()
    user = db.execute('SELECT * FROM users WHERE id = ?', (current_user.id,)).fetchone()

    if not check_password_hash(user['password_hash'], current_password):
        db.close()
        return jsonify({'error': 'Current password is incorrect'}), 400

    password_hash = generate_password_hash(new_password)
    db.execute('UPDATE users SET password_hash = ? WHERE id = ?', (password_hash, current_user.id))
    db.commit()
    db.close()
    return jsonify({'success': True})

# ── Token-based auth endpoints (for iOS / API clients) ───────────────────────

@app.route('/api/auth/login', methods=['POST'])
def api_login():
    """Exchange credentials for access + refresh tokens."""
    data = request.get_json(silent=True) or {}
    username = data.get('username', '').strip()
    password = data.get('password', '')
    if not username or not password:
        return jsonify({'error': 'username and password required'}), 400

    db = get_db()
    user = db.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
    db.close()

    if not user or not check_password_hash(user['password_hash'], password):
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


# Vehicle endpoints
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
    picture_path = None

    if 'picture' in request.files:
        file = request.files['picture']
        if file and file.filename and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
            filename = f"{timestamp}_{filename}"
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], 'vehicles', filename)
            file.save(filepath)
            picture_path = f'uploads/vehicles/{filename}'

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
    picture_path = None

    if 'picture' in request.files:
        file = request.files['picture']
        if file and file.filename and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
            filename = f"{timestamp}_{filename}"
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], 'vehicles', filename)
            file.save(filepath)
            picture_path = f'uploads/vehicles/{filename}'

    db = get_db()
    if picture_path:
        db.execute('''UPDATE vehicle SET manufacturer=?, model=?, year=?,
                     engine=?, vin=?, nickname=?, picture=? WHERE id=?''',
                  (data['manufacturer'], data['model'], data['year'],
                   data.get('engine', ''), data.get('vin', ''), data.get('nickname', ''),
                   picture_path, vehicle_id))
    else:
        db.execute('''UPDATE vehicle SET manufacturer=?, model=?, year=?,
                     engine=?, vin=?, nickname=? WHERE id=?''',
                  (data['manufacturer'], data['model'], data['year'],
                   data.get('engine', ''), data.get('vin', ''), data.get('nickname', ''),
                   vehicle_id))
    db.commit()
    db.close()
    return jsonify({'success': True})

@app.route('/api/vehicle/<int:vehicle_id>', methods=['DELETE'])
@api_login_required
def delete_vehicle(vehicle_id):
    """Delete a vehicle"""
    db = get_db()
    db.execute('DELETE FROM service_supplies WHERE service_id IN (SELECT id FROM service_records WHERE vehicle_id = ?)', (vehicle_id,))
    db.execute('DELETE FROM service_records WHERE vehicle_id = ?', (vehicle_id,))
    db.execute('DELETE FROM supplies WHERE vehicle_id = ?', (vehicle_id,))
    db.execute('DELETE FROM fuel_records WHERE vehicle_id = ?', (vehicle_id,))
    db.execute('DELETE FROM service_reminders WHERE vehicle_id = ?', (vehicle_id,))
    db.execute('DELETE FROM vehicle WHERE id = ?', (vehicle_id,))
    db.commit()
    db.close()
    return jsonify({'success': True})

# NHTSA API Integration endpoints
@app.route('/api/vehicle/decode-vin/<vin>', methods=['GET'])
@api_login_required
def decode_vin(vin):
    """Decode VIN using NHTSA API"""
    try:
        # NHTSA VIN Decoder API
        url = f'https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVinValues/{vin}?format=json'
        headers = {
            'User-Agent': 'VST-Vehicle-Tracker/1.0',
            'Accept': 'application/json'
        }
        response = requests.get(url, headers=headers, timeout=10)

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

@app.route('/api/vehicle/recalls', methods=['GET'])
@api_login_required
def check_recalls():
    """Check for recalls using NHTSA API"""
    try:
        make = request.args.get('make')
        model = request.args.get('model')
        year = request.args.get('year')

        if not make or not model or not year:
            return jsonify({'success': False, 'error': 'Make, model, and year are required'}), 400

        # NHTSA Recalls API
        url = f'https://api.nhtsa.gov/recalls/recallsByVehicle?make={make}&model={model}&modelYear={year}'
        headers = {
            'User-Agent': 'VST-Vehicle-Tracker/1.0',
            'Accept': 'application/json'
        }
        response = requests.get(url, headers=headers, timeout=10)

        # NHTSA API quirk: returns 400 even for successful queries with no results
        # Check if we got valid JSON regardless of status code
        if response.status_code in [200, 400]:
            try:
                data = response.json()

                # Check if the API returned a valid response structure
                if 'results' in data or 'Results' in data:
                    # Extract recall information (handle both lowercase and uppercase keys)
                    recalls = []
                    results = data.get('results') or data.get('Results', [])

                    if results and len(results) > 0:
                        for recall in results:
                            recalls.append({
                                'nhtsa_campaign_number': recall.get('NHTSACampaignNumber', ''),
                                'manufacturer': recall.get('Manufacturer', ''),
                                'subject': recall.get('Subject', ''),
                                'summary': recall.get('Summary', ''),
                                'consequence': recall.get('Consequence', ''),
                                'remedy': recall.get('Remedy', ''),
                                'report_date': recall.get('ReportReceivedDate', ''),
                                'component': recall.get('Component', '')
                            })

                    return jsonify({
                        'success': True,
                        'count': len(recalls),
                        'recalls': recalls
                    })
                else:
                    return jsonify({'success': False, 'error': 'Invalid response format from NHTSA'}), 500
            except ValueError:
                return jsonify({'success': False, 'error': 'Invalid JSON response from NHTSA'}), 500
        else:
            error_msg = f'NHTSA Recalls API returned status {response.status_code}'
            if response.text:
                error_msg += f': {response.text[:100]}'
            return jsonify({'success': False, 'error': error_msg}), 500

    except requests.exceptions.Timeout:
        return jsonify({'success': False, 'error': 'Request to NHTSA API timed out. Please try again.'}), 504
    except requests.exceptions.RequestException as e:
        return jsonify({'success': False, 'error': f'Network error: {str(e)}'}), 500
    except Exception as e:
        return jsonify({'success': False, 'error': f'Error: {str(e)}'}), 500

@app.route('/api/vehicle/recalls-by-vin/<vin>', methods=['GET'])
@api_login_required
def check_recalls_by_vin(vin):
    """Check for recalls by VIN using NHTSA API"""
    try:
        # NHTSA Recalls by VIN API
        url = f'https://api.nhtsa.gov/recalls/recallsByVehicle?vin={vin}'
        headers = {
            'User-Agent': 'VST-Vehicle-Tracker/1.0',
            'Accept': 'application/json'
        }
        response = requests.get(url, headers=headers, timeout=10)

        # NHTSA API quirk: returns 400 even for successful queries with no results
        # Check if we got valid JSON regardless of status code
        if response.status_code in [200, 400]:
            try:
                data = response.json()

                # Check if the API returned a valid response structure
                if 'results' in data or 'Results' in data:
                    # Extract recall information (handle both lowercase and uppercase keys)
                    recalls = []
                    results = data.get('results') or data.get('Results', [])

                    if results and len(results) > 0:
                        for recall in results:
                            recalls.append({
                                'nhtsa_campaign_number': recall.get('NHTSACampaignNumber', ''),
                                'manufacturer': recall.get('Manufacturer', ''),
                                'subject': recall.get('Subject', ''),
                                'summary': recall.get('Summary', ''),
                                'consequence': recall.get('Consequence', ''),
                                'remedy': recall.get('Remedy', ''),
                                'report_date': recall.get('ReportReceivedDate', ''),
                                'component': recall.get('Component', '')
                            })

                    return jsonify({
                        'success': True,
                        'count': len(recalls),
                        'recalls': recalls
                    })
                else:
                    return jsonify({'success': False, 'error': 'Invalid response format from NHTSA'}), 500
            except ValueError:
                return jsonify({'success': False, 'error': 'Invalid JSON response from NHTSA'}), 500
        else:
            error_msg = f'NHTSA Recalls API returned status {response.status_code}'
            if response.text:
                error_msg += f': {response.text[:100]}'
            return jsonify({'success': False, 'error': error_msg}), 500

    except requests.exceptions.Timeout:
        return jsonify({'success': False, 'error': 'Request to NHTSA API timed out. Please try again.'}), 504
    except requests.exceptions.RequestException as e:
        return jsonify({'success': False, 'error': f'Network error: {str(e)}'}), 500
    except Exception as e:
        return jsonify({'success': False, 'error': f'Error: {str(e)}'}), 500

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

            # Check if reminder already exists for this recall
            existing = db.execute(
                'SELECT id FROM service_reminders WHERE vehicle_id = ? AND service_type LIKE ? AND completed = 0',
                (vehicle_id, f'%{campaign_number}%')
            ).fetchone()

            if not existing:
                # Create reminder for this recall
                service_type = f"Recall Repair: {campaign_number}"
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
@app.route('/api/services', methods=['GET'])
@api_login_required
def get_services():
    """Get all service records for a vehicle with optional pagination"""
    vehicle_id = request.args.get('vehicle_id')
    page = request.args.get('page', type=int)
    per_page = request.args.get('per_page', 20, type=int)

    db = get_db()

    # Base query
    base_query = '''
        SELECT s.*,
               COALESCE(SUM(ss.quantity_used * sup.cost), 0) as supplies_cost
        FROM service_records s
        LEFT JOIN service_supplies ss ON s.id = ss.service_id
        LEFT JOIN supplies sup ON ss.supply_id = sup.id
        {where_clause}
        GROUP BY s.id
        ORDER BY s.date DESC
    '''

    # Count query for total
    count_query = '''
        SELECT COUNT(DISTINCT s.id) as total
        FROM service_records s
        {where_clause}
    '''

    if vehicle_id:
        where_clause = 'WHERE s.vehicle_id = ?'
        params = (vehicle_id,)
    else:
        where_clause = ''
        params = ()

    # Get total count
    total_count = db.execute(count_query.format(where_clause=where_clause), params).fetchone()['total']

    # If pagination requested, add LIMIT and OFFSET
    if page is not None:
        offset = (page - 1) * per_page
        query = base_query.format(where_clause=where_clause) + ' LIMIT ? OFFSET ?'
        services = db.execute(query, params + (per_page, offset)).fetchall()

        db.close()
        return jsonify({
            'data': [dict(row) for row in services],
            'total': total_count,
            'page': page,
            'per_page': per_page,
            'total_pages': (total_count + per_page - 1) // per_page
        })
    else:
        # Return all results (backwards compatibility)
        services = db.execute(base_query.format(where_clause=where_clause), params).fetchall()
        db.close()
        return jsonify([dict(row) for row in services])

@app.route('/api/services/<int:service_id>', methods=['GET'])
@api_login_required
def get_service(service_id):
    """Get a specific service record"""
    db = get_db()
    service = db.execute('''
        SELECT s.*,
               COALESCE(SUM(ss.quantity_used * sup.cost), 0) as supplies_cost
        FROM service_records s
        LEFT JOIN service_supplies ss ON s.id = ss.service_id
        LEFT JOIN supplies sup ON ss.supply_id = sup.id
        WHERE s.id = ?
        GROUP BY s.id
    ''', (service_id,)).fetchone()
    db.close()
    if service:
        return jsonify(dict(service))
    return jsonify(None), 404

@app.route('/api/services/search', methods=['GET'])
@api_login_required
def search_services():
    """Search service records"""
    query = request.args.get('q', '').lower()
    vehicle_id = request.args.get('vehicle_id')
    db = get_db()

    if vehicle_id:
        services = db.execute('''
            SELECT s.*,
                   COALESCE(SUM(ss.quantity_used * sup.cost), 0) as supplies_cost
            FROM service_records s
            LEFT JOIN service_supplies ss ON s.id = ss.service_id
            LEFT JOIN supplies sup ON ss.supply_id = sup.id
            WHERE s.vehicle_id = ? AND (
                LOWER(s.service_provider) LIKE ? OR
                LOWER(s.comments) LIKE ? OR
                LOWER(s.repairs_completed) LIKE ?
            )
            GROUP BY s.id
            ORDER BY s.date DESC
        ''', (vehicle_id, f'%{query}%', f'%{query}%', f'%{query}%')).fetchall()
    else:
        services = db.execute('''
            SELECT s.*,
                   COALESCE(SUM(ss.quantity_used * sup.cost), 0) as supplies_cost
            FROM service_records s
            LEFT JOIN service_supplies ss ON s.id = ss.service_id
            LEFT JOIN supplies sup ON ss.supply_id = sup.id
            WHERE LOWER(s.service_provider) LIKE ?
               OR LOWER(s.comments) LIKE ?
               OR LOWER(s.repairs_completed) LIKE ?
            GROUP BY s.id
            ORDER BY s.date DESC
        ''', (f'%{query}%', f'%{query}%', f'%{query}%')).fetchall()

    db.close()
    return jsonify([dict(row) for row in services])

@app.route('/api/services', methods=['POST'])
@api_login_required
def add_service():
    """Add new service record"""
    data = request.form
    vehicle_id = data.get('vehicle_id')

    if not vehicle_id:
        return jsonify({'error': 'Vehicle ID is required'}), 400

    receipt_path = None

    if 'receipt' in request.files:
        file = request.files['receipt']
        if file and file.filename and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
            filename = f"{timestamp}_{filename}"
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], 'receipts', filename)
            file.save(filepath)
            receipt_path = f'uploads/receipts/{filename}'

    db = get_db()
    cursor = db.execute('''INSERT INTO service_records
                          (vehicle_id, date, cost, service_provider, odometer, comments, repairs_completed, receipt_path)
                          VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                       (vehicle_id, data['date'], data['cost'], data['service_provider'],
                        int(data['odometer']) if data.get('odometer') else None,
                        data.get('comments', ''), data.get('repairs_completed', ''), receipt_path))

    service_id = cursor.lastrowid

    # Add supplies used
    if 'supplies' in data:
        supplies_data = json.loads(data['supplies'])
        for supply in supplies_data:
            db.execute('''INSERT INTO service_supplies (service_id, supply_id, quantity_used)
                         VALUES (?, ?, ?)''',
                      (service_id, supply['id'], supply['quantity']))

            # Update supply quantity
            db.execute('''UPDATE supplies SET quantity = quantity - ? WHERE id = ?''',
                      (supply['quantity'], supply['id']))

    db.commit()
    db.close()
    return jsonify({'success': True, 'id': service_id})

@app.route('/api/services/<int:service_id>', methods=['PUT'])
@api_login_required
def update_service(service_id):
    """Update existing service record"""
    data = request.form
    receipt_path = None

    # Check if we're updating the receipt
    if 'receipt' in request.files:
        file = request.files['receipt']
        if file and file.filename and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
            filename = f"{timestamp}_{filename}"
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], 'receipts', filename)
            file.save(filepath)
            receipt_path = f'uploads/receipts/{filename}'

    db = get_db()

    # Delete existing service_supplies entries
    db.execute('DELETE FROM service_supplies WHERE service_id = ?', (service_id,))

    # Update service record
    if receipt_path:
        db.execute('''UPDATE service_records
                     SET date=?, cost=?, service_provider=?, odometer=?, comments=?, repairs_completed=?, receipt_path=?
                     WHERE id=?''',
                  (data['date'], data['cost'], data['service_provider'],
                   int(data['odometer']) if data.get('odometer') else None,
                   data.get('comments', ''), data.get('repairs_completed', ''), receipt_path, service_id))
    else:
        db.execute('''UPDATE service_records
                     SET date=?, cost=?, service_provider=?, odometer=?, comments=?, repairs_completed=?
                     WHERE id=?''',
                  (data['date'], data['cost'], data['service_provider'],
                   int(data['odometer']) if data.get('odometer') else None,
                   data.get('comments', ''), data.get('repairs_completed', ''), service_id))

    # Add new supplies used
    if 'supplies' in data:
        supplies_data = json.loads(data['supplies'])
        for supply in supplies_data:
            db.execute('''INSERT INTO service_supplies (service_id, supply_id, quantity_used)
                         VALUES (?, ?, ?)''',
                      (service_id, supply['id'], supply['quantity']))

            # Update supply quantity
            db.execute('''UPDATE supplies SET quantity = quantity - ? WHERE id = ?''',
                      (supply['quantity'], supply['id']))

    db.commit()
    db.close()
    return jsonify({'success': True})

@app.route('/api/services/<int:service_id>', methods=['DELETE'])
@api_login_required
def delete_service(service_id):
    """Delete a service record"""
    db = get_db()
    db.execute('DELETE FROM service_supplies WHERE service_id = ?', (service_id,))
    db.execute('DELETE FROM service_records WHERE id = ?', (service_id,))
    db.commit()
    db.close()
    return jsonify({'success': True})

# CSV Export/Import endpoints for services
@app.route('/api/services/export-csv', methods=['GET'])
@api_login_required
def export_services_csv():
    """Export service records to CSV"""
    try:
        vehicle_id = request.args.get('vehicle_id')

        db = get_db()
        if vehicle_id:
            services = db.execute('''
                SELECT s.date, s.service_provider, s.odometer, s.cost,
                       COALESCE(SUM(ss.quantity_used * sup.cost), 0) as supplies_cost,
                       s.repairs_completed, s.comments
                FROM service_records s
                LEFT JOIN service_supplies ss ON s.id = ss.service_id
                LEFT JOIN supplies sup ON ss.supply_id = sup.id
                WHERE s.vehicle_id = ?
                GROUP BY s.id
                ORDER BY s.date DESC
            ''', (vehicle_id,)).fetchall()
        else:
            services = db.execute('''
                SELECT s.date, s.service_provider, s.odometer, s.cost,
                       COALESCE(SUM(ss.quantity_used * sup.cost), 0) as supplies_cost,
                       s.repairs_completed, s.comments
                FROM service_records s
                LEFT JOIN service_supplies ss ON s.id = ss.service_id
                LEFT JOIN supplies sup ON ss.supply_id = sup.id
                GROUP BY s.id
                ORDER BY s.date DESC
            ''').fetchall()

        # Convert rows to dictionaries
        services_list = [dict(row) for row in services]
        db.close()

        # Create CSV in memory
        output = io.StringIO()
        writer = csv.writer(output)

        # Write header
        writer.writerow(['date', 'service_provider', 'odometer', 'cost', 'supplies_cost',
                         'repairs_completed', 'comments'])

        # Write data
        for service in services_list:
            writer.writerow([
                service.get('date', ''),
                service.get('service_provider', ''),
                service.get('odometer', '') or '',
                service.get('cost', 0),
                service.get('supplies_cost', 0) or 0,
                service.get('repairs_completed', '') or '',
                service.get('comments', '') or ''
            ])

        # Create response
        output.seek(0)
        response = make_response(output.getvalue())
        response.headers['Content-Type'] = 'text/csv; charset=utf-8'
        response.headers['Content-Disposition'] = 'attachment; filename=service_records.csv'
        return response

    except Exception as e:
        print(f"Export CSV Error: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/api/services/template-csv', methods=['GET'])
@api_login_required
def download_services_template():
    """Download a CSV template for importing service records"""
    output = io.StringIO()
    writer = csv.writer(output)

    # Write header
    writer.writerow(['date', 'service_provider', 'odometer', 'cost', 'repairs_completed', 'comments'])

    # Write example row
    writer.writerow(['2024-01-15', 'Auto Shop', '50000', '150.00', 'Oil change, tire rotation', 'Regular maintenance'])

    output.seek(0)
    response = make_response(output.getvalue())
    response.headers['Content-Type'] = 'text/csv; charset=utf-8'
    response.headers['Content-Disposition'] = 'attachment; filename=service_records_template.csv'
    return response

@app.route('/api/services/import-csv', methods=['POST'])
@api_login_required
def import_services_csv():
    """Import service records from CSV"""
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400

    vehicle_id = request.form.get('vehicle_id')
    if not vehicle_id:
        return jsonify({'error': 'Vehicle ID is required'}), 400

    try:
        # Read CSV file
        stream = io.StringIO(file.stream.read().decode("UTF8"), newline=None)
        csv_reader = csv.DictReader(stream)

        db = get_db()
        imported_count = 0
        errors = []

        for row_num, row in enumerate(csv_reader, start=2):  # Start at 2 because of header
            try:
                # Validate required fields
                if not row.get('date') or not row.get('service_provider'):
                    errors.append(f"Row {row_num}: Missing required fields (date or service_provider)")
                    continue

                # Parse and validate date
                try:
                    datetime.strptime(row['date'], '%Y-%m-%d')
                except ValueError:
                    errors.append(f"Row {row_num}: Invalid date format (use YYYY-MM-DD)")
                    continue

                # Insert service record
                cursor = db.execute('''
                    INSERT INTO service_records
                    (vehicle_id, date, service_provider, odometer, cost, repairs_completed, comments)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (
                    vehicle_id,
                    row['date'],
                    row['service_provider'],
                    int(row['odometer']) if row.get('odometer') and row['odometer'].strip() else None,
                    float(row.get('cost', 0) or 0),
                    row.get('repairs_completed', ''),
                    row.get('comments', '')
                ))
                imported_count += 1

            except Exception as e:
                errors.append(f"Row {row_num}: {str(e)}")

        db.commit()
        db.close()

        response = {
            'success': True,
            'imported': imported_count,
            'errors': errors
        }

        return jsonify(response)

    except Exception as e:
        return jsonify({'error': f'Failed to import CSV: {str(e)}'}), 400

# Supplies endpoints
@app.route('/api/supplies', methods=['GET'])
@api_login_required
def get_supplies():
    """Get all supplies for a vehicle"""
    vehicle_id = request.args.get('vehicle_id')
    db = get_db()

    if vehicle_id:
        supplies = db.execute('SELECT * FROM supplies WHERE vehicle_id = ? ORDER BY name', (vehicle_id,)).fetchall()
    else:
        supplies = db.execute('SELECT * FROM supplies ORDER BY name').fetchall()

    db.close()
    return jsonify([dict(row) for row in supplies])

@app.route('/api/supplies', methods=['POST'])
@api_login_required
def add_supply():
    """Add new supply/part"""
    data = request.form
    vehicle_id = data.get('vehicle_id')

    if not vehicle_id:
        return jsonify({'error': 'Vehicle ID is required'}), 400

    receipt_path = None

    if 'receipt' in request.files:
        file = request.files['receipt']
        if file and file.filename and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
            filename = f"{timestamp}_{filename}"
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], 'receipts', filename)
            file.save(filepath)
            receipt_path = f'uploads/receipts/{filename}'

    # Get remind_to_reorder checkbox value (defaults to False if not checked)
    remind_to_reorder = 1 if data.get('remind_to_reorder') == 'on' else 0

    db = get_db()
    cursor = db.execute('''INSERT INTO supplies
                          (vehicle_id, name, part_number, brand, cost, quantity,
                           warranty_start_date, warranty_months, warranty_start_mileage,
                           warranty_mileage_limit, receipt_path, remind_to_reorder,
                           category, location, condition, supplier, purchase_date,
                           installation_date, notes)
                          VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                       (vehicle_id, data['name'], data.get('part_number', ''),
                        data.get('brand', ''), data['cost'], data['quantity'],
                        data.get('warranty_start_date', None),
                        int(data['warranty_months']) if data.get('warranty_months') else None,
                        int(data['warranty_start_mileage']) if data.get('warranty_start_mileage') else None,
                        int(data['warranty_mileage_limit']) if data.get('warranty_mileage_limit') else None,
                        receipt_path, remind_to_reorder,
                        data.get('category', ''), data.get('location', ''),
                        data.get('condition', 'New'), data.get('supplier', ''),
                        data.get('purchase_date', None), data.get('installation_date', None),
                        data.get('notes', '')))

    # Create reminder if quantity is 1 AND remind_to_reorder is checked
    supply_id = cursor.lastrowid
    if int(data['quantity']) == 1 and remind_to_reorder:
        part_name = f"{data['name']}"
        if data.get('part_number'):
            part_name += f" ({data.get('part_number')})"

        db.execute('''INSERT INTO service_reminders (vehicle_id, service_type, notes, completed)
                     VALUES (?, ?, ?, ?)''',
                  (vehicle_id, f"Re-order {part_name}",
                   'Part quantity is at 1. Consider re-ordering soon.', 0))

    db.commit()
    db.close()
    return jsonify({'success': True, 'id': supply_id})

@app.route('/api/supplies/<int:supply_id>', methods=['PUT'])
@api_login_required
def update_supply(supply_id):
    """Update supply/part"""
    data = request.form
    receipt_path = None

    # Check if we're updating the receipt
    if 'receipt' in request.files:
        file = request.files['receipt']
        if file and file.filename and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
            filename = f"{timestamp}_{filename}"
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], 'receipts', filename)
            file.save(filepath)
            receipt_path = f'uploads/receipts/{filename}'

    # Get remind_to_reorder checkbox value (defaults to False if not checked)
    remind_to_reorder = 1 if data.get('remind_to_reorder') == 'on' else 0

    db = get_db()

    # Get current supply for vehicle_id
    current_supply = db.execute('SELECT * FROM supplies WHERE id = ?', (supply_id,)).fetchone()
    vehicle_id = current_supply['vehicle_id']

    # Update supply record
    if receipt_path:
        db.execute('''UPDATE supplies
                     SET name=?, part_number=?, brand=?, cost=?, quantity=?,
                         warranty_start_date=?, warranty_months=?, warranty_start_mileage=?,
                         warranty_mileage_limit=?, receipt_path=?, remind_to_reorder=?,
                         category=?, location=?, condition=?, supplier=?, purchase_date=?,
                         installation_date=?, notes=?
                     WHERE id=?''',
                  (data['name'], data.get('part_number', ''),
                   data.get('brand', ''), data['cost'], data['quantity'],
                   data.get('warranty_start_date', None),
                   int(data['warranty_months']) if data.get('warranty_months') else None,
                   int(data['warranty_start_mileage']) if data.get('warranty_start_mileage') else None,
                   int(data['warranty_mileage_limit']) if data.get('warranty_mileage_limit') else None,
                   receipt_path, remind_to_reorder,
                   data.get('category', ''), data.get('location', ''),
                   data.get('condition', 'New'), data.get('supplier', ''),
                   data.get('purchase_date', None), data.get('installation_date', None),
                   data.get('notes', ''), supply_id))
    else:
        db.execute('''UPDATE supplies
                     SET name=?, part_number=?, brand=?, cost=?, quantity=?,
                         warranty_start_date=?, warranty_months=?, warranty_start_mileage=?,
                         warranty_mileage_limit=?, remind_to_reorder=?,
                         category=?, location=?, condition=?, supplier=?, purchase_date=?,
                         installation_date=?, notes=?
                     WHERE id=?''',
                  (data['name'], data.get('part_number', ''),
                   data.get('brand', ''), data['cost'], data['quantity'],
                   data.get('warranty_start_date', None),
                   int(data['warranty_months']) if data.get('warranty_months') else None,
                   int(data['warranty_start_mileage']) if data.get('warranty_start_mileage') else None,
                   int(data['warranty_mileage_limit']) if data.get('warranty_mileage_limit') else None,
                   remind_to_reorder,
                   data.get('category', ''), data.get('location', ''),
                   data.get('condition', 'New'), data.get('supplier', ''),
                   data.get('purchase_date', None), data.get('installation_date', None),
                   data.get('notes', ''), supply_id))

    # Create reminder if quantity is 1 AND remind_to_reorder is checked
    if int(data['quantity']) == 1 and remind_to_reorder:
        part_name = f"{data['name']}"
        if data.get('part_number'):
            part_name += f" ({data.get('part_number')})"

        db.execute('''INSERT INTO service_reminders (vehicle_id, service_type, notes, completed)
                     VALUES (?, ?, ?, ?)''',
                  (vehicle_id, f"Re-order {part_name}",
                   'Part quantity is at 1. Consider re-ordering soon.', 0))

    db.commit()
    db.close()
    return jsonify({'success': True})

@app.route('/api/supplies/<int:supply_id>', methods=['DELETE'])
@api_login_required
def delete_supply(supply_id):
    """Delete a supply"""
    db = get_db()
    db.execute('DELETE FROM supplies WHERE id = ?', (supply_id,))
    db.commit()
    db.close()
    return jsonify({'success': True})

@app.route('/api/supplies/export-csv', methods=['GET'])
@api_login_required
def export_supplies_csv():
    """Export supplies to CSV"""
    vehicle_id = request.args.get('vehicle_id')
    db = get_db()
    try:
        if vehicle_id:
            supplies = db.execute(
                'SELECT * FROM supplies WHERE vehicle_id = ? ORDER BY name', (vehicle_id,)
            ).fetchall()
        else:
            supplies = db.execute('SELECT * FROM supplies ORDER BY name').fetchall()

        supplies_list = [dict(row) for row in supplies]
        db.close()

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['name', 'part_number', 'brand', 'category', 'cost', 'quantity',
                         'supplier', 'purchase_date', 'installation_date', 'location',
                         'condition', 'notes'])
        for s in supplies_list:
            writer.writerow([
                s.get('name', ''),
                s.get('part_number', '') or '',
                s.get('brand', '') or '',
                s.get('category', '') or '',
                s.get('cost', ''),
                s.get('quantity', ''),
                s.get('supplier', '') or '',
                s.get('purchase_date', '') or '',
                s.get('installation_date', '') or '',
                s.get('location', '') or '',
                s.get('condition', '') or '',
                s.get('notes', '') or ''
            ])

        output.seek(0)
        response = make_response(output.getvalue())
        response.headers['Content-Type'] = 'text/csv; charset=utf-8'
        response.headers['Content-Disposition'] = 'attachment; filename=supplies.csv'
        return response

    except Exception as e:
        return jsonify({'error': str(e)}), 500

# Fuel endpoints
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
    else:
        base_query = 'SELECT * FROM fuel_records ORDER BY date DESC, odometer DESC'
        count_query = 'SELECT COUNT(*) as total FROM fuel_records'
        params = ()

    # Get total count
    total_count = db.execute(count_query, params).fetchone()['total']

    # If pagination requested, add LIMIT and OFFSET
    if page is not None:
        offset = (page - 1) * per_page
        query = base_query + ' LIMIT ? OFFSET ?'
        records = db.execute(query, params + (per_page, offset)).fetchall()

        db.close()
        return jsonify({
            'data': [dict(row) for row in records],
            'total': total_count,
            'page': page,
            'per_page': per_page,
            'total_pages': (total_count + per_page - 1) // per_page
        })
    else:
        # Return all results (backwards compatibility)
        records = db.execute(base_query, params).fetchall()
        db.close()
        return jsonify([dict(row) for row in records])

@app.route('/api/fuel', methods=['POST'])
@api_login_required
def add_fuel_record():
    """Add new fuel record and calculate MPG"""
    data = request.json
    vehicle_id = data.get('vehicle_id')

    if not vehicle_id:
        return jsonify({'error': 'Vehicle ID is required'}), 400

    db = get_db()

    # Get previous fuel record to calculate MPG
    prev_record = db.execute('''SELECT * FROM fuel_records
                               WHERE vehicle_id = ?
                               ORDER BY odometer DESC LIMIT 1''',
                            (vehicle_id,)).fetchone()

    missed_fillup = 1 if data.get('missed_fillup') else 0

    mpg = None
    if prev_record and not missed_fillup:
        miles_driven = data['odometer'] - prev_record['odometer']
        if miles_driven > 0 and data['gallons'] > 0:
            mpg = round(miles_driven / data['gallons'], 2)

    cursor = db.execute('''INSERT INTO fuel_records
                          (vehicle_id, date, gallons, cost, odometer, mpg, station, missed_fillup)
                          VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                       (vehicle_id, data['date'], data['gallons'], data['cost'],
                        data['odometer'], mpg, data.get('station', ''), missed_fillup))

    db.commit()
    record_id = cursor.lastrowid
    db.close()
    return jsonify({'success': True, 'id': record_id, 'mpg': mpg})

@app.route('/api/fuel/<int:fuel_id>', methods=['GET'])
@api_login_required
def get_fuel_record(fuel_id):
    """Get a specific fuel record"""
    db = get_db()
    record = db.execute('SELECT * FROM fuel_records WHERE id = ?', (fuel_id,)).fetchone()
    db.close()

    if record:
        return jsonify(dict(record))
    return jsonify({'error': 'Fuel record not found'}), 404

@app.route('/api/fuel/<int:fuel_id>', methods=['PUT'])
@api_login_required
def update_fuel_record(fuel_id):
    """Update an existing fuel record"""
    data = request.json

    db = get_db()

    missed_fillup = 1 if data.get('missed_fillup') else 0

    # Update the fuel record
    db.execute('''UPDATE fuel_records
                 SET date=?, gallons=?, cost=?, odometer=?, station=?, missed_fillup=?
                 WHERE id=?''',
              (data['date'], data['gallons'], data['cost'],
               data['odometer'], data.get('station', ''), missed_fillup, fuel_id))

    # Recalculate MPG for this record
    record = db.execute('SELECT * FROM fuel_records WHERE id = ?', (fuel_id,)).fetchone()
    vehicle_id = record['vehicle_id']

    mpg = None
    if not missed_fillup:
        # Get previous fuel record to recalculate MPG
        prev_record = db.execute('''SELECT * FROM fuel_records
                                   WHERE vehicle_id = ? AND odometer < ?
                                   ORDER BY odometer DESC LIMIT 1''',
                                (vehicle_id, data['odometer'])).fetchone()

        if prev_record:
            miles_driven = data['odometer'] - prev_record['odometer']
            if miles_driven > 0 and data['gallons'] > 0:
                mpg = round(miles_driven / data['gallons'], 2)

    db.execute('UPDATE fuel_records SET mpg=? WHERE id=?', (mpg, fuel_id))

    db.commit()
    db.close()
    return jsonify({'success': True, 'mpg': mpg})

@app.route('/api/fuel/<int:fuel_id>', methods=['DELETE'])
@api_login_required
def delete_fuel_record(fuel_id):
    """Delete a fuel record"""
    db = get_db()
    db.execute('DELETE FROM fuel_records WHERE id = ?', (fuel_id,))
    db.commit()
    db.close()
    return jsonify({'success': True})

# CSV Export/Import endpoints for fuel records
@app.route('/api/fuel/export-csv', methods=['GET'])
@api_login_required
def export_fuel_csv():
    """Export fuel records to CSV"""
    try:
        vehicle_id = request.args.get('vehicle_id')

        db = get_db()
        if vehicle_id:
            fuel_records = db.execute('''
                SELECT date, odometer, gallons, cost, station, mpg
                FROM fuel_records
                WHERE vehicle_id = ?
                ORDER BY date DESC, odometer DESC
            ''', (vehicle_id,)).fetchall()
        else:
            fuel_records = db.execute('''
                SELECT date, odometer, gallons, cost, station, mpg
                FROM fuel_records
                ORDER BY date DESC, odometer DESC
            ''').fetchall()

        # Convert rows to dictionaries
        records_list = [dict(row) for row in fuel_records]
        db.close()

        # Create CSV in memory
        output = io.StringIO()
        writer = csv.writer(output)

        # Write header
        writer.writerow(['date', 'odometer', 'gallons', 'cost', 'station', 'mpg'])

        # Write data
        for record in records_list:
            writer.writerow([
                record.get('date', ''),
                record.get('odometer', ''),
                record.get('gallons', ''),
                record.get('cost', ''),
                record.get('station', ''),
                record.get('mpg', '') or ''
            ])

        # Create response
        output.seek(0)
        response = make_response(output.getvalue())
        response.headers['Content-Type'] = 'text/csv; charset=utf-8'
        response.headers['Content-Disposition'] = 'attachment; filename=fuel_records.csv'
        return response

    except Exception as e:
        print(f"Export CSV Error: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/api/fuel/template-csv', methods=['GET'])
@api_login_required
def download_fuel_template():
    """Download a CSV template for importing fuel records"""
    output = io.StringIO()
    writer = csv.writer(output)

    # Write header
    writer.writerow(['date', 'odometer', 'gallons', 'cost', 'station'])

    # Write example rows
    writer.writerow(['2024-01-15', '50000', '12.5', '45.00', 'Shell'])
    writer.writerow(['2024-01-22', '50350', '11.8', '42.50', 'BP'])

    output.seek(0)
    response = make_response(output.getvalue())
    response.headers['Content-Type'] = 'text/csv; charset=utf-8'
    response.headers['Content-Disposition'] = 'attachment; filename=fuel_records_template.csv'
    return response

@app.route('/api/fuel/import-csv', methods=['POST'])
@api_login_required
def import_fuel_csv():
    """Import fuel records from CSV"""
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400

    vehicle_id = request.form.get('vehicle_id')
    if not vehicle_id:
        return jsonify({'error': 'Vehicle ID is required'}), 400

    try:
        # Read CSV file
        stream = io.StringIO(file.stream.read().decode("UTF8"), newline=None)
        csv_reader = csv.DictReader(stream)

        db = get_db()
        imported_count = 0
        errors = []

        for row_num, row in enumerate(csv_reader, start=2):  # Start at 2 because of header
            try:
                # Validate required fields
                if not row.get('date') or not row.get('odometer') or not row.get('gallons') or not row.get('cost'):
                    errors.append(f"Row {row_num}: Missing required fields")
                    continue

                # Parse and validate date
                try:
                    datetime.strptime(row['date'], '%Y-%m-%d')
                except ValueError:
                    errors.append(f"Row {row_num}: Invalid date format (use YYYY-MM-DD)")
                    continue

                # Validate numeric fields
                try:
                    odometer = int(row['odometer'])
                    gallons = float(row['gallons'])
                    cost = float(row['cost'])
                except ValueError:
                    errors.append(f"Row {row_num}: Invalid numeric value")
                    continue

                # Calculate MPG if possible
                prev_record = db.execute('''SELECT * FROM fuel_records
                                           WHERE vehicle_id = ? AND odometer < ?
                                           ORDER BY odometer DESC LIMIT 1''',
                                        (vehicle_id, odometer)).fetchone()

                mpg = None
                if prev_record:
                    miles_driven = odometer - prev_record['odometer']
                    if miles_driven > 0 and gallons > 0:
                        mpg = round(miles_driven / gallons, 2)

                # Insert fuel record
                db.execute('''
                    INSERT INTO fuel_records
                    (vehicle_id, date, odometer, gallons, cost, station, mpg)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (
                    vehicle_id,
                    row['date'],
                    odometer,
                    gallons,
                    cost,
                    row.get('station', ''),
                    mpg
                ))
                imported_count += 1

            except Exception as e:
                errors.append(f"Row {row_num}: {str(e)}")

        db.commit()
        db.close()

        response = {
            'success': True,
            'imported': imported_count,
            'errors': errors
        }

        return jsonify(response)

    except Exception as e:
        return jsonify({'error': f'Failed to import CSV: {str(e)}'}), 400


# Service reminders endpoints
@app.route('/api/reminders', methods=['GET'])
@api_login_required
def get_reminders():
    """Get all service reminders for a vehicle"""
    vehicle_id = request.args.get('vehicle_id')
    db = get_db()

    if vehicle_id:
        reminders = db.execute('SELECT * FROM service_reminders WHERE vehicle_id = ? ORDER BY completed, due_date', (vehicle_id,)).fetchall()
    else:
        reminders = db.execute('SELECT * FROM service_reminders ORDER BY completed, due_date').fetchall()

    db.close()
    return jsonify([dict(row) for row in reminders])

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

    # Total spent on services
    result = db.execute('SELECT COALESCE(SUM(cost), 0) as total FROM service_records WHERE vehicle_id = ?',
                       (vehicle_id,)).fetchone()
    stats['total_spent'] = round(result['total'], 2)

    # Average MPG
    result = db.execute('SELECT AVG(mpg) as avg FROM fuel_records WHERE vehicle_id = ? AND mpg IS NOT NULL',
                       (vehicle_id,)).fetchone()
    stats['avg_mpg'] = round(result['avg'], 2) if result['avg'] else 0

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

    file_path = None
    if 'file' in request.files:
        file = request.files['file']
        if file and file.filename and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
            filename = f"{timestamp}_{filename}"
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], 'documents', filename)
            file.save(filepath)
            file_path = f'uploads/documents/{filename}'

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
    if doc and doc['file_path']:
        full_path = os.path.join(app.config['UPLOAD_FOLDER'], '..', 'static', doc['file_path'])
        full_path = os.path.join('static', doc['file_path'])
        if os.path.exists(full_path):
            os.remove(full_path)
    db.execute('DELETE FROM documents WHERE id = ?', (doc_id,))
    db.commit()
    db.close()
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
    base = 'FROM service_records WHERE (LOWER(service_provider) LIKE ? OR LOWER(repairs_completed) LIKE ? OR LOWER(comments) LIKE ?)'
    params = (like, like, like)
    if vehicle_id:
        base += ' AND vehicle_id = ?'
        params += (vehicle_id,)
    rows = db.execute(f'SELECT id, date, service_provider, repairs_completed, cost {base} ORDER BY date DESC LIMIT 10', params).fetchall()
    if rows:
        results['services'] = [{'id': r['id'], 'date': r['date'], 'label': r['service_provider'],
                                 'sub': r['repairs_completed'] or '', 'cost': r['cost']} for r in rows]

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

    # Supplies / parts
    base = 'FROM supplies WHERE (LOWER(name) LIKE ? OR LOWER(part_number) LIKE ? OR LOWER(brand) LIKE ? OR LOWER(notes) LIKE ? OR LOWER(category) LIKE ?)'
    params = (like, like, like, like, like)
    if vehicle_id:
        base += ' AND vehicle_id = ?'
        params += (vehicle_id,)
    rows = db.execute(f'SELECT id, name, brand, part_number, quantity {base} ORDER BY name LIMIT 10', params).fetchall()
    if rows:
        results['supplies'] = [{'id': r['id'], 'label': r['name'],
                                 'sub': ' '.join(filter(None, [r['brand'], r['part_number']])),
                                 'qty': r['quantity']} for r in rows]

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
        SELECT tr.*, t.brand || COALESCE(' ' || t.model, '') AS tire_name
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
            '''SELECT tr.*, t.brand || CASE WHEN t.model != "" THEN " " || t.model ELSE "" END as tire_name
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

if __name__ == '__main__':
    # Initialize database
    if not os.path.exists('instance'):
        os.makedirs('instance')
    init_db()

    # Ensure upload directories exist
    os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'receipts'), exist_ok=True)
    os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'vehicles'), exist_ok=True)
    os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'documents'), exist_ok=True)

    app.run(host='0.0.0.0', port=5000, debug=False)
