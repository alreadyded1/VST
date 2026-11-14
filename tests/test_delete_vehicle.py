import io
import os

import app as app_module

TABLES_WITH_VEHICLE_ID = ['service_records', 'part_installs', 'fuel_records',
                          'service_reminders', 'documents', 'tires', 'tire_rotations',
                          'tread_readings', 'tire_install_log']


def _upload(name):
    return (io.BytesIO(b'%PDF-1.4 fake'), name)


def _populate(client, vehicle_id):
    """Create one row of everything for the vehicle, several with files attached."""
    client.put(f'/api/vehicle/{vehicle_id}',
               data={'manufacturer': 'Honda', 'model': 'Civic', 'year': 2020, 'picture': _upload('car.jpg')},
               content_type='multipart/form-data')
    r = client.post('/api/services', data={'vehicle_id': vehicle_id, 'date': '2026-01-01', 'cost': 50,
                                           'service_provider': 'Shop', 'receipt': _upload('svc.pdf')},
                    content_type='multipart/form-data')
    assert r.status_code == 200, r.get_json()
    r = client.post('/api/parts', data={'name': 'Oil', 'unit_cost': 5, 'quantity': 4,
                                        'receipt': _upload('part.pdf')},
                    content_type='multipart/form-data')
    assert r.status_code == 200, r.get_json()
    part_id = r.get_json()['id']
    r = client.post('/api/services', data={'vehicle_id': vehicle_id, 'date': '2026-01-02', 'cost': 10,
                                           'service_provider': 'Me',
                                           'parts': '[{"part_id": %d, "quantity": 3}]' % part_id})
    assert r.status_code == 200, r.get_json()
    client.post('/api/fuel', json={'vehicle_id': vehicle_id, 'date': '2026-01-01', 'odometer': 100,
                                   'gallons': 5, 'cost': 20})
    client.post('/api/reminders', json={'vehicle_id': vehicle_id, 'service_type': 'Oil change'})
    r = client.post('/api/documents', data={'vehicle_id': vehicle_id, 'title': 'Title', 'category': 'Insurance',
                                            'file': _upload('doc.pdf')}, content_type='multipart/form-data')
    assert r.status_code == 200, r.get_json()
    r = client.post('/api/tires', json={'vehicle_id': vehicle_id, 'brand': 'Michelin'})
    tire_id = r.get_json()['id']
    client.post(f'/api/tires/{tire_id}/install', json={'date': '2026-01-01', 'odometer': 100})
    client.post('/api/tire-rotations', json={'vehicle_id': vehicle_id, 'date': '2026-01-01'})
    client.post('/api/tread-readings', json={'vehicle_id': vehicle_id, 'date': '2026-01-01'})


def _row_counts(vehicle_id):
    db = app_module.get_db()
    counts = {t: db.execute(f'SELECT COUNT(*) AS c FROM {t} WHERE vehicle_id = ?', (vehicle_id,)).fetchone()['c']
              for t in TABLES_WITH_VEHICLE_ID}
    db.close()
    return counts


def test_delete_vehicle_removes_all_rows_and_files(client, vehicle_id):
    _populate(client, vehicle_id)
    before = _row_counts(vehicle_id)
    assert all(before[t] >= 1 for t in TABLES_WITH_VEHICLE_ID), before

    uploads = os.path.join(app_module.app.static_folder, 'uploads')
    files_before = {os.path.join(d, f) for d, _, fs in os.walk(uploads) for f in fs}
    assert len(files_before) >= 3, files_before

    r = client.delete(f'/api/vehicle/{vehicle_id}')
    assert r.status_code == 200

    after = _row_counts(vehicle_id)
    assert all(v == 0 for v in after.values()), after
    # Parts are a shared shelf: the part, its receipt and what was used
    # from it all outlive the vehicle
    files_after = {os.path.basename(f) for d, _, fs in os.walk(uploads) for f in fs}
    assert len(files_after) == 1 and files_after.pop().endswith('part.pdf')
    part = client.get('/api/parts').get_json()[0]
    assert part['in_stock'] == 1
    assert part['installs'][0]['vehicle_id'] is None
    assert part['installs'][0]['service_id'] is None


def test_delete_unknown_vehicle_404(client):
    assert client.delete('/api/vehicle/9999').status_code == 404


def test_delete_service_removes_receipt(client, vehicle_id):
    r = client.post('/api/services', data={'vehicle_id': vehicle_id, 'date': '2026-01-01', 'cost': 50,
                                           'service_provider': 'Shop', 'receipt': _upload('svc.pdf')},
                    content_type='multipart/form-data')
    service_id = r.get_json()['id']
    path = client.get(f'/api/services/{service_id}').get_json()['receipt_path']
    full = os.path.join(app_module.app.static_folder, path)
    assert os.path.exists(full)

    client.delete(f'/api/services/{service_id}')
    assert not os.path.exists(full)
