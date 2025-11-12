from flask import Flask, render_template, request, jsonify, send_from_directory, redirect, url_for, flash, make_response
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
from functools import wraps
import sqlite3
import os
from werkzeug.utils import secure_filename
import json
import csv
import io

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-change-in-production'
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size
app.config['ALLOWED_EXTENSIONS'] = {'png', 'jpg', 'jpeg', 'gif', 'pdf', 'webp'}

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
                brand TEXT,
                cost REAL NOT NULL,
                quantity INTEGER NOT NULL,
                unit TEXT DEFAULT 'units',
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

        # Create default admin user if no users exist
        admin_exists = db.execute('SELECT COUNT(*) as count FROM users').fetchone()
        if admin_exists['count'] == 0:
            # Default admin credentials: username=admin, password=admin
            admin_password_hash = generate_password_hash('admin')
            db.execute('INSERT INTO users (username, password_hash, is_admin) VALUES (?, ?, ?)',
                      ('admin', admin_password_hash, 1))
            db.commit()

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

    return render_template('login.html')

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

# API Endpoints

# User Management APIs
@app.route('/api/users', methods=['GET'])
@login_required
@admin_required
def get_users():
    """Get all users (admin only)"""
    db = get_db()
    users = db.execute('SELECT id, username, is_admin, created_at FROM users ORDER BY created_at DESC').fetchall()
    db.close()
    return jsonify([dict(row) for row in users])

@app.route('/api/users', methods=['POST'])
@login_required
@admin_required
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
@login_required
@admin_required
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
@login_required
@admin_required
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
@login_required
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

# Vehicle endpoints
@app.route('/api/vehicles', methods=['GET'])
@login_required
def get_vehicles():
    """Get all vehicles"""
    db = get_db()
    vehicles = db.execute('SELECT * FROM vehicle ORDER BY created_at DESC').fetchall()
    db.close()
    return jsonify([dict(row) for row in vehicles])

@app.route('/api/vehicle/<int:vehicle_id>', methods=['GET'])
@login_required
def get_vehicle(vehicle_id):
    """Get specific vehicle profile"""
    db = get_db()
    vehicle = db.execute('SELECT * FROM vehicle WHERE id = ?', (vehicle_id,)).fetchone()
    db.close()
    if vehicle:
        return jsonify(dict(vehicle))
    return jsonify(None)

@app.route('/api/vehicle', methods=['POST'])
@login_required
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
@login_required
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
@login_required
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

# Service records endpoints
@app.route('/api/services', methods=['GET'])
@login_required
def get_services():
    """Get all service records for a vehicle"""
    vehicle_id = request.args.get('vehicle_id')
    db = get_db()

    if vehicle_id:
        services = db.execute('''
            SELECT s.*,
                   COALESCE(SUM(ss.quantity_used * sup.cost), 0) as supplies_cost
            FROM service_records s
            LEFT JOIN service_supplies ss ON s.id = ss.service_id
            LEFT JOIN supplies sup ON ss.supply_id = sup.id
            WHERE s.vehicle_id = ?
            GROUP BY s.id
            ORDER BY s.date DESC
        ''', (vehicle_id,)).fetchall()
    else:
        services = db.execute('''
            SELECT s.*,
                   COALESCE(SUM(ss.quantity_used * sup.cost), 0) as supplies_cost
            FROM service_records s
            LEFT JOIN service_supplies ss ON s.id = ss.service_id
            LEFT JOIN supplies sup ON ss.supply_id = sup.id
            GROUP BY s.id
            ORDER BY s.date DESC
        ''').fetchall()

    db.close()
    return jsonify([dict(row) for row in services])

@app.route('/api/services/<int:service_id>', methods=['GET'])
@login_required
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
@login_required
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
@login_required
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
@login_required
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
@login_required
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
@login_required
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
@login_required
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
@login_required
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
@login_required
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
@login_required
def add_supply():
    """Add new supply"""
    data = request.json
    vehicle_id = data.get('vehicle_id')

    if not vehicle_id:
        return jsonify({'error': 'Vehicle ID is required'}), 400

    db = get_db()
    cursor = db.execute('''INSERT INTO supplies (vehicle_id, name, brand, cost, quantity, unit)
                          VALUES (?, ?, ?, ?, ?, ?)''',
                       (vehicle_id, data['name'], data.get('brand', ''),
                        data['cost'], data['quantity'], data.get('unit', 'units')))

    db.commit()
    supply_id = cursor.lastrowid
    db.close()
    return jsonify({'success': True, 'id': supply_id})

@app.route('/api/supplies/<int:supply_id>', methods=['PUT'])
@login_required
def update_supply(supply_id):
    """Update supply"""
    data = request.json
    db = get_db()
    db.execute('''UPDATE supplies SET name=?, brand=?, cost=?, quantity=?, unit=? WHERE id=?''',
              (data['name'], data.get('brand', ''), data['cost'], data['quantity'], data.get('unit', 'units'), supply_id))
    db.commit()
    db.close()
    return jsonify({'success': True})

@app.route('/api/supplies/<int:supply_id>', methods=['DELETE'])
@login_required
def delete_supply(supply_id):
    """Delete a supply"""
    db = get_db()
    db.execute('DELETE FROM supplies WHERE id = ?', (supply_id,))
    db.commit()
    db.close()
    return jsonify({'success': True})

# Fuel endpoints
@app.route('/api/fuel', methods=['GET'])
@login_required
def get_fuel_records():
    """Get all fuel records for a vehicle"""
    vehicle_id = request.args.get('vehicle_id')
    db = get_db()

    if vehicle_id:
        records = db.execute('SELECT * FROM fuel_records WHERE vehicle_id = ? ORDER BY date DESC, odometer DESC', (vehicle_id,)).fetchall()
    else:
        records = db.execute('SELECT * FROM fuel_records ORDER BY date DESC, odometer DESC').fetchall()

    db.close()
    return jsonify([dict(row) for row in records])

@app.route('/api/fuel', methods=['POST'])
@login_required
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

    mpg = None
    if prev_record:
        miles_driven = data['odometer'] - prev_record['odometer']
        if miles_driven > 0 and data['gallons'] > 0:
            mpg = round(miles_driven / data['gallons'], 2)

    cursor = db.execute('''INSERT INTO fuel_records
                          (vehicle_id, date, gallons, cost, odometer, location, mpg)
                          VALUES (?, ?, ?, ?, ?, ?, ?)''',
                       (vehicle_id, data['date'], data['gallons'], data['cost'],
                        data['odometer'], data['location'], mpg))

    db.commit()
    record_id = cursor.lastrowid
    db.close()
    return jsonify({'success': True, 'id': record_id, 'mpg': mpg})

@app.route('/api/fuel/<int:fuel_id>', methods=['DELETE'])
@login_required
def delete_fuel_record(fuel_id):
    """Delete a fuel record"""
    db = get_db()
    db.execute('DELETE FROM fuel_records WHERE id = ?', (fuel_id,))
    db.commit()
    db.close()
    return jsonify({'success': True})

# Service reminders endpoints
@app.route('/api/reminders', methods=['GET'])
@login_required
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
@login_required
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
@login_required
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
@login_required
def delete_reminder(reminder_id):
    """Delete a reminder"""
    db = get_db()
    db.execute('DELETE FROM service_reminders WHERE id = ?', (reminder_id,))
    db.commit()
    db.close()
    return jsonify({'success': True})

# Dashboard stats
@app.route('/api/stats', methods=['GET'])
@login_required
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

if __name__ == '__main__':
    # Initialize database
    if not os.path.exists('instance'):
        os.makedirs('instance')
    init_db()

    # Ensure upload directories exist
    os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'receipts'), exist_ok=True)
    os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'vehicles'), exist_ok=True)

    app.run(host='0.0.0.0', port=5000, debug=False)
