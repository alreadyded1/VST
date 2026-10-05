import json

import app as app_module


def _jwt_headers(client, username='admin', password='admin'):
    r = client.post('/api/auth/login', json={'username': username, 'password': password})
    assert r.status_code == 200
    body = r.get_json()
    return {'Authorization': f"Bearer {body['access_token']}"}, body['refresh_token']


def test_jwt_user_can_change_password_and_old_refresh_tokens_are_revoked(client):
    headers, refresh = _jwt_headers(client)
    r = client.post('/api/change-password', headers=headers,
                    json={'current_password': 'admin', 'new_password': 'n3w-pass'})
    assert r.status_code == 200, r.get_json()

    assert client.post('/api/auth/refresh', json={'refresh_token': refresh}).status_code == 401
    assert client.post('/api/auth/login', json={'username': 'admin', 'password': 'n3w-pass'}).status_code == 200


def test_jwt_admin_cannot_delete_self(client):
    headers, _ = _jwt_headers(client)
    me = client.get('/api/users', headers=headers).get_json()[0]
    r = client.delete(f"/api/users/{me['id']}", headers=headers)
    assert r.status_code == 400


def test_login_ignores_offsite_next(client):
    client.get('/logout')
    for target in ('https://evil.example', '//evil.example', '/\\evil.example'):
        r = client.post(f'/login?next={target}', data={'username': 'admin', 'password': 'admin'})
        assert r.status_code == 302
        assert r.headers['Location'] == '/', target
        client.get('/logout')

    r = client.post('/login?next=/fuel', data={'username': 'admin', 'password': 'admin'})
    assert r.headers['Location'] == '/fuel'


def test_cors_does_not_allow_credentials(client):
    r = client.get('/api/vehicles', headers={'Origin': 'https://evil.example'})
    assert r.headers.get('Access-Control-Allow-Credentials') != 'true'


def _add_supply(client, vehicle_id, **fields):
    data = {'vehicle_id': vehicle_id, 'name': 'Wiper', 'cost': '10', 'quantity': '1'}
    data.update(fields)
    return client.post('/api/supplies', data=data).get_json()['id']


def _reminders(client, vehicle_id):
    return client.get(f'/api/reminders?vehicle_id={vehicle_id}').get_json()


def test_resaving_supply_at_quantity_one_does_not_duplicate_reorder_reminder(client, vehicle_id):
    supply_id = _add_supply(client, vehicle_id, remind_to_reorder='on')
    for _ in range(3):
        r = client.put(f'/api/supplies/{supply_id}', data={
            'name': 'Wiper', 'cost': '10', 'quantity': '1', 'remind_to_reorder': 'on'})
        assert r.status_code == 200
    reorder = [r for r in _reminders(client, vehicle_id) if r['service_type'].startswith('Re-order')]
    assert len(reorder) == 1


def test_update_missing_supply_is_404(client):
    r = client.put('/api/supplies/999', data={'name': 'x', 'cost': '1', 'quantity': '1'})
    assert r.status_code == 404


def test_recall_with_blank_campaign_number_does_not_match_existing_reminders(client, vehicle_id):
    client.post('/api/reminders', json={'vehicle_id': vehicle_id, 'service_type': 'Oil change'})
    r = client.post('/api/vehicle/recalls/create-reminders', json={
        'vehicle_id': vehicle_id, 'recalls': [{'nhtsa_campaign_number': '', 'subject': 'Airbag'}]})
    assert r.get_json()['created'] == 1


def test_service_supplies_cost_uses_unit_cost_and_counts_toward_total_spent(client, vehicle_id):
    supply_id = _add_supply(client, vehicle_id, cost='50', tracked_individually='on')
    client.post(f'/api/supplies/{supply_id}/units', data={'quantity_to_add': '1', 'cost': '80'})
    unit_id = client.get(f'/api/supplies/{supply_id}/units').get_json()[0]['id']

    r = client.post('/api/services', data={
        'vehicle_id': vehicle_id, 'date': '2024-05-01', 'cost': '100', 'service_provider': 'Shop',
        'supplies': json.dumps([{'id': supply_id, 'unit_id': unit_id}])})
    service_id = r.get_json()['id']

    service = client.get(f'/api/services/{service_id}').get_json()
    assert service['supplies_cost'] == 80
    assert service['supplies'][0]['cost'] == 80

    stats = client.get(f'/api/stats?vehicle_id={vehicle_id}').get_json()
    assert stats['total_spent'] == 180


def test_tread_readings_and_rotations_show_tire_names(client, vehicle_id):
    tire_id = client.post('/api/tires', json={'vehicle_id': vehicle_id, 'brand': 'Michelin'}).get_json()['id']
    client.post('/api/tread-readings', json={'vehicle_id': vehicle_id, 'tire_id': tire_id, 'date': '2024-01-01'})
    client.post('/api/tire-rotations', json={'vehicle_id': vehicle_id, 'tire_id': tire_id, 'date': '2024-01-01'})

    assert client.get(f'/api/tread-readings?vehicle_id={vehicle_id}').get_json()[0]['tire_name'] == 'Michelin'
    assert client.get(f'/api/tire-rotations?vehicle_id={vehicle_id}').get_json()[0]['tire_name'] == 'Michelin'


def test_delete_tire_removes_install_log_and_keeps_rotations(client, vehicle_id):
    tire_id = client.post('/api/tires', json={'vehicle_id': vehicle_id, 'brand': 'Michelin'}).get_json()['id']
    client.post(f'/api/tires/{tire_id}/install', json={'date': '2024-01-01'})
    client.post('/api/tire-rotations', json={'vehicle_id': vehicle_id, 'tire_id': tire_id, 'date': '2024-02-01'})

    assert client.delete(f'/api/tires/{tire_id}').status_code == 200

    db = app_module.get_db()
    assert db.execute('SELECT COUNT(*) FROM tire_install_log WHERE tire_id = ?', (tire_id,)).fetchone()[0] == 0
    rotation = db.execute('SELECT tire_id FROM tire_rotations WHERE vehicle_id = ?', (vehicle_id,)).fetchone()
    db.close()
    assert rotation is not None and rotation['tire_id'] is None


def test_replacing_service_receipt_deletes_old_file(client, vehicle_id, tmp_path):
    import io
    base = {'vehicle_id': vehicle_id, 'date': '2024-05-01', 'cost': '10', 'service_provider': 'Shop'}
    r = client.post('/api/services', data={**base, 'receipt': (io.BytesIO(b'one'), 'r.pdf')},
                    content_type='multipart/form-data')
    service_id = r.get_json()['id']
    old_path = client.get(f'/api/services/{service_id}').get_json()['receipt_path']
    old_file = tmp_path / 'static' / old_path
    assert old_file.exists()

    client.put(f'/api/services/{service_id}', data={**base, 'receipt': (io.BytesIO(b'two'), 'r.pdf')},
               content_type='multipart/form-data')
    new_path = client.get(f'/api/services/{service_id}').get_json()['receipt_path']
    assert new_path != old_path
    assert not old_file.exists()
    assert (tmp_path / 'static' / new_path).exists()

    # Editing without a new upload keeps the current receipt
    client.put(f'/api/services/{service_id}', data=base)
    assert client.get(f'/api/services/{service_id}').get_json()['receipt_path'] == new_path
