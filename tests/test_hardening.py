import io

import pytest

import app as app_module


def _upload_receipt(client, vehicle_id):
    r = client.post('/api/services', data={
        'vehicle_id': vehicle_id, 'date': '2024-05-01', 'cost': '10', 'service_provider': 'Shop',
        'receipt': (io.BytesIO(b'%PDF-1.4 receipt'), 'r.pdf')}, content_type='multipart/form-data')
    return client.get(f"/api/services/{r.get_json()['id']}").get_json()['receipt_path']


def test_uploads_require_login(client, vehicle_id, tmp_path):
    (tmp_path / 'static' / 'public.css').write_text('body {}')
    path = _upload_receipt(client, vehicle_id)
    assert client.get(f'/static/{path}').status_code == 200

    # Bearer token works too (iOS client)
    token = client.post('/api/auth/login', json={'username': 'admin', 'password': 'admin'}).get_json()['access_token']

    client.get('/logout')
    for url in (f'/static/{path}', f'/static/./{path}', f'/static//{path}',
                f"/static/uploads/x/../{path.split('/', 1)[1]}"):
        assert client.get(url, follow_redirects=True).status_code == 401, url
    assert client.get(f'/static/{path}', headers={'Authorization': f'Bearer {token}'}).status_code == 200

    # Ordinary static assets stay public (the login page needs them)
    assert client.get('/static/public.css').status_code == 200


def test_web_login_is_throttled_after_repeated_failures(client):
    client.get('/logout')
    for _ in range(app_module.MAX_FAILURES_PER_ACCOUNT):
        r = client.post('/login', data={'username': 'admin', 'password': 'wrong'})
        assert r.status_code == 200
    # Even the right password is refused while locked out
    r = client.post('/login', data={'username': 'admin', 'password': 'admin'})
    assert r.status_code == 429
    assert b'Too many failed login attempts' in r.data


def test_api_login_is_throttled_with_retry_after(client):
    for _ in range(app_module.MAX_FAILURES_PER_ACCOUNT):
        assert client.post('/api/auth/login', json={'username': 'admin', 'password': 'x'}).status_code == 401
    r = client.post('/api/auth/login', json={'username': 'admin', 'password': 'admin'})
    assert r.status_code == 429
    assert int(r.headers['Retry-After']) > 0


def test_per_ip_limit_covers_many_usernames(client):
    for i in range(app_module.MAX_FAILURES_PER_IP):
        client.post('/api/auth/login', json={'username': f'user{i}', 'password': 'x'})
    r = client.post('/api/auth/login', json={'username': 'admin', 'password': 'admin'})
    assert r.status_code == 429


def test_successful_login_resets_failures(client):
    for _ in range(app_module.MAX_FAILURES_PER_ACCOUNT - 1):
        client.post('/api/auth/login', json={'username': 'admin', 'password': 'x'})
    assert client.post('/api/auth/login', json={'username': 'admin', 'password': 'admin'}).status_code == 200
    for _ in range(app_module.MAX_FAILURES_PER_ACCOUNT - 1):
        client.post('/api/auth/login', json={'username': 'admin', 'password': 'x'})
    assert client.post('/api/auth/login', json={'username': 'admin', 'password': 'admin'}).status_code == 200


def test_lockout_expires_after_window(client, monkeypatch):
    for _ in range(app_module.MAX_FAILURES_PER_ACCOUNT):
        client.post('/api/auth/login', json={'username': 'admin', 'password': 'x'})
    assert client.post('/api/auth/login', json={'username': 'admin', 'password': 'admin'}).status_code == 429
    db = app_module.get_db()
    db.execute("UPDATE login_failures SET attempted_at = '2000-01-01T00:00:00+00:00'")
    db.commit()
    db.close()
    assert client.post('/api/auth/login', json={'username': 'admin', 'password': 'admin'}).status_code == 200


def test_default_password_nags_until_changed(client):
    client.get('/logout')
    r = client.get('/login')
    assert b'Default Admin Credentials' in r.data

    r = client.post('/login', data={'username': 'admin', 'password': 'admin'})
    assert r.headers['Location'] == '/profile'
    assert b'default-password-banner' in client.get('/').data

    client.post('/api/change-password', json={'current_password': 'admin', 'new_password': 'better-pw'})
    assert b'default-password-banner' not in client.get('/').data

    client.get('/logout')
    assert b'Default Admin Credentials' not in client.get('/login').data
    r = client.post('/login', data={'username': 'admin', 'password': 'better-pw'})
    assert r.headers['Location'] == '/'


def _fuel(client, vehicle_id, date, odometer, gallons):
    client.post('/api/fuel', json={'vehicle_id': vehicle_id, 'date': date, 'odometer': odometer,
                                   'gallons': gallons, 'cost': gallons * 3, 'station': 'S'})


def test_avg_mpg_is_weighted_by_gallons(client, vehicle_id):
    _fuel(client, vehicle_id, '2024-01-01', 1000, 10)
    _fuel(client, vehicle_id, '2024-01-08', 1300, 10)   # 300 mi / 10 gal = 30 mpg
    _fuel(client, vehicle_id, '2024-01-09', 1320, 1)    # 20 mi / 1 gal = 20 mpg
    # Plain mean would be 25; total miles / gallons = 320 / 11
    stats = client.get(f'/api/stats?vehicle_id={vehicle_id}').get_json()
    assert stats['avg_mpg'] == pytest.approx(29.09, abs=0.01)


def test_vehicles_summary(client, vehicle_id):
    other = client.post('/api/vehicle', data={'manufacturer': 'Ford', 'model': 'F150', 'year': 2018}).get_json()['id']
    supply_id = client.post('/api/supplies', data={'vehicle_id': vehicle_id, 'name': 'Oil', 'cost': '5',
                                                   'quantity': '10'}).get_json()['id']
    client.post('/api/services', data={'vehicle_id': vehicle_id, 'date': '2024-03-01', 'cost': '100',
                                       'service_provider': 'Shop', 'odometer': '1500',
                                       'supplies': '[{"id": %d, "quantity": 2}]' % supply_id})
    client.post('/api/services', data={'vehicle_id': vehicle_id, 'date': '2023-06-01', 'cost': '50',
                                       'service_provider': 'Shop'})
    _fuel(client, vehicle_id, '2024-01-01', 1000, 10)
    _fuel(client, vehicle_id, '2024-01-08', 1300, 10)

    summary = client.get('/api/vehicles/summary?year=2024').get_json()
    s = summary[str(vehicle_id)]
    assert s['service_count'] == 2
    assert s['fuel_record_count'] == 2
    assert s['last_service'] == '2024-03-01'
    assert s['last_fuel'] == '2024-01-08'
    assert s['current_odometer'] == 1500
    assert s['avg_mpg'] == 30
    assert s['total_service_cost'] == 160      # 100 + 2*5 supplies + 50
    assert s['ytd_service_cost'] == 110
    assert s['total_fuel_cost'] == 60
    assert s['ytd_fuel_cost'] == 60
    assert s['total_all_time_cost'] == 220
    assert s['ytd_total_cost'] == 170

    empty = summary[str(other)]
    assert empty['service_count'] == 0 and empty['total_all_time_cost'] == 0 and empty['avg_mpg'] is None
