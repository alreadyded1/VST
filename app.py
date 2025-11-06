from flask import Flask, render_template, request, jsonify, send_from_directory
from datetime import datetime, timedelta
import sqlite3
import os
from werkzeug.utils import secure_filename
import json

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-change-in-production'
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size
app.config['ALLOWED_EXTENSIONS'] = {'png', 'jpg', 'jpeg', 'gif', 'pdf', 'webp'}

DATABASE = 'instance/vehicle_tracker.db'

def get_db():
    """Create database connection"""
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Initialize database with schema"""
    with app.app_context():
        db = get_db()
        db.executescript('''
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
        db.commit()
        db.close()

def allowed_file(filename):
    """Check if file extension is allowed"""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

# Routes
@app.route('/')
def index():
    """Main dashboard"""
    return render_template('index.html')

@app.route('/vehicle')
def vehicle_page():
    """Vehicle profile page (legacy - redirects to vehicles)"""
    return render_template('vehicles.html')

@app.route('/vehicles')
def vehicles_page():
    """Vehicles management page"""
    return render_template('vehicles.html')

@app.route('/services')
def services_page():
    """Service records page"""
    return render_template('services.html')

@app.route('/supplies')
def supplies_page():
    """Supplies inventory page"""
    return render_template('supplies.html')

@app.route('/fuel')
def fuel_page():
    """Fuel tracker page"""
    return render_template('fuel.html')

@app.route('/reminders')
def reminders_page():
    """Service reminders page"""
    return render_template('reminders.html')

# API Endpoints

# Vehicle endpoints
@app.route('/api/vehicles', methods=['GET'])
def get_vehicles():
    """Get all vehicles"""
    db = get_db()
    vehicles = db.execute('SELECT * FROM vehicle ORDER BY created_at DESC').fetchall()
    db.close()
    return jsonify([dict(row) for row in vehicles])

@app.route('/api/vehicle/<int:vehicle_id>', methods=['GET'])
def get_vehicle(vehicle_id):
    """Get specific vehicle profile"""
    db = get_db()
    vehicle = db.execute('SELECT * FROM vehicle WHERE id = ?', (vehicle_id,)).fetchone()
    db.close()
    if vehicle:
        return jsonify(dict(vehicle))
    return jsonify(None)

@app.route('/api/vehicle', methods=['POST'])
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
                          (vehicle_id, date, cost, service_provider, comments, repairs_completed, receipt_path)
                          VALUES (?, ?, ?, ?, ?, ?, ?)''',
                       (vehicle_id, data['date'], data['cost'], data['service_provider'],
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
                     SET date=?, cost=?, service_provider=?, comments=?, repairs_completed=?, receipt_path=?
                     WHERE id=?''',
                  (data['date'], data['cost'], data['service_provider'],
                   data.get('comments', ''), data.get('repairs_completed', ''), receipt_path, service_id))
    else:
        db.execute('''UPDATE service_records
                     SET date=?, cost=?, service_provider=?, comments=?, repairs_completed=?
                     WHERE id=?''',
                  (data['date'], data['cost'], data['service_provider'],
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
def delete_service(service_id):
    """Delete a service record"""
    db = get_db()
    db.execute('DELETE FROM service_supplies WHERE service_id = ?', (service_id,))
    db.execute('DELETE FROM service_records WHERE id = ?', (service_id,))
    db.commit()
    db.close()
    return jsonify({'success': True})

# Supplies endpoints
@app.route('/api/supplies', methods=['GET'])
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
def add_supply():
    """Add new supply"""
    data = request.json
    vehicle_id = data.get('vehicle_id')

    if not vehicle_id:
        return jsonify({'error': 'Vehicle ID is required'}), 400

    db = get_db()
    cursor = db.execute('''INSERT INTO supplies (vehicle_id, name, cost, quantity, unit)
                          VALUES (?, ?, ?, ?, ?)''',
                       (vehicle_id, data['name'], data['cost'],
                        data['quantity'], data.get('unit', 'units')))

    db.commit()
    supply_id = cursor.lastrowid
    db.close()
    return jsonify({'success': True, 'id': supply_id})

@app.route('/api/supplies/<int:supply_id>', methods=['PUT'])
def update_supply(supply_id):
    """Update supply"""
    data = request.json
    db = get_db()
    db.execute('''UPDATE supplies SET name=?, cost=?, quantity=?, unit=? WHERE id=?''',
              (data['name'], data['cost'], data['quantity'], data.get('unit', 'units'), supply_id))
    db.commit()
    db.close()
    return jsonify({'success': True})

@app.route('/api/supplies/<int:supply_id>', methods=['DELETE'])
def delete_supply(supply_id):
    """Delete a supply"""
    db = get_db()
    db.execute('DELETE FROM supplies WHERE id = ?', (supply_id,))
    db.commit()
    db.close()
    return jsonify({'success': True})

# Fuel endpoints
@app.route('/api/fuel', methods=['GET'])
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
def delete_fuel_record(fuel_id):
    """Delete a fuel record"""
    db = get_db()
    db.execute('DELETE FROM fuel_records WHERE id = ?', (fuel_id,))
    db.commit()
    db.close()
    return jsonify({'success': True})

# Service reminders endpoints
@app.route('/api/reminders', methods=['GET'])
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
def delete_reminder(reminder_id):
    """Delete a reminder"""
    db = get_db()
    db.execute('DELETE FROM service_reminders WHERE id = ?', (reminder_id,))
    db.commit()
    db.close()
    return jsonify({'success': True})

# Dashboard stats
@app.route('/api/stats', methods=['GET'])
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
