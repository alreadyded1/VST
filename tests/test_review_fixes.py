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
    client.post('/api/users', json={'username': 'sam', 'password': 'pw-sam'})
    client.get('/logout')
    for target in ('https://evil.example', '//evil.example', '/\\evil.example'):
        r = client.post(f'/login?next={target}', data={'username': 'sam', 'password': 'pw-sam'})
        assert r.status_code == 302
        assert r.headers['Location'] == '/', target
        client.get('/logout')

    r = client.post('/login?next=/fuel', data={'username': 'sam', 'password': 'pw-sam'})
    assert r.headers['Location'] == '/fuel'


def test_cors_does_not_allow_credentials(client):
    r = client.get('/api/vehicles', headers={'Origin': 'https://evil.example'})
    assert r.headers.get('Access-Control-Allow-Credentials') != 'true'


def _reminders(client, vehicle_id):
    return client.get(f'/api/reminders?vehicle_id={vehicle_id}').get_json()


def test_update_missing_part_is_404(client):
    r = client.put('/api/parts/999', data={'name': 'x'})
    assert r.status_code == 404


def test_recall_with_blank_campaign_number_does_not_match_existing_reminders(client, vehicle_id):
    client.post('/api/reminders', json={'vehicle_id': vehicle_id, 'service_type': 'Oil change'})
    r = client.post('/api/vehicle/recalls/create-reminders', json={
        'vehicle_id': vehicle_id, 'recalls': [{'nhtsa_campaign_number': '', 'subject': 'Airbag'}]})
    assert r.get_json()['created'] == 1


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
