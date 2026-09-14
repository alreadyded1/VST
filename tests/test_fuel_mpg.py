def _mpgs(client, vehicle_id):
    records = client.get(f'/api/fuel?vehicle_id={vehicle_id}').get_json()
    return {r['odometer']: r['mpg'] for r in records}


def _add(client, vehicle_id, odometer, gallons, date='2026-01-01', **extra):
    r = client.post('/api/fuel', json={'vehicle_id': vehicle_id, 'date': date, 'odometer': odometer,
                                       'gallons': gallons, 'cost': 40.0, **extra})
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def test_sequential_fillups(client, vehicle_id):
    assert _add(client, vehicle_id, 1000, 10)['mpg'] is None
    assert _add(client, vehicle_id, 1300, 10)['mpg'] == 30.0
    assert _add(client, vehicle_id, 1500, 8)['mpg'] == 25.0


def test_backdated_entry_fixes_neighbours(client, vehicle_id):
    _add(client, vehicle_id, 1000, 10)
    _add(client, vehicle_id, 1500, 10)
    # A fill-up logged late, between the two existing ones
    r = _add(client, vehicle_id, 1200, 8)
    assert r['mpg'] == 25.0
    assert _mpgs(client, vehicle_id) == {1000: None, 1200: 25.0, 1500: 30.0}


def test_delete_middle_record_recomputes_next(client, vehicle_id):
    _add(client, vehicle_id, 1000, 10)
    mid = _add(client, vehicle_id, 1300, 10)['id']
    _add(client, vehicle_id, 1500, 10)
    assert _mpgs(client, vehicle_id)[1500] == 20.0

    client.delete(f'/api/fuel/{mid}')
    assert _mpgs(client, vehicle_id) == {1000: None, 1500: 50.0}


def test_edit_odometer_recomputes_next(client, vehicle_id):
    _add(client, vehicle_id, 1000, 10)
    mid = _add(client, vehicle_id, 1300, 10)['id']
    _add(client, vehicle_id, 1500, 10)

    client.put(f'/api/fuel/{mid}', json={'date': '2026-01-01', 'odometer': 1200,
                                         'gallons': 10, 'cost': 40.0})
    assert _mpgs(client, vehicle_id) == {1000: None, 1200: 20.0, 1500: 30.0}


def test_missed_fillup_has_no_mpg_but_anchors_next(client, vehicle_id):
    _add(client, vehicle_id, 1000, 10)
    _add(client, vehicle_id, 1300, 10, missed_fillup=True)
    _add(client, vehicle_id, 1500, 10)
    assert _mpgs(client, vehicle_id) == {1000: None, 1300: None, 1500: 20.0}


def test_csv_import_computes_chain(client, vehicle_id):
    import io
    csv_data = 'date,odometer,gallons,cost,station\n2026-01-01,1000,10,40,A\n2026-01-08,1300,10,40,B\n'
    r = client.post('/api/fuel/import-csv',
                    data={'vehicle_id': vehicle_id, 'file': (io.BytesIO(csv_data.encode()), 'f.csv')},
                    content_type='multipart/form-data')
    assert r.get_json()['imported'] == 2
    assert _mpgs(client, vehicle_id) == {1000: None, 1300: 30.0}


def test_startup_repairs_stale_mpg(client, vehicle_id, monkeypatch):
    import app as app_module
    _add(client, vehicle_id, 1000, 10)
    _add(client, vehicle_id, 1300, 10)
    db = app_module.get_db()
    db.execute('UPDATE fuel_records SET mpg = 99 WHERE odometer = 1300')
    db.execute("DELETE FROM settings WHERE key = 'mpg_recalculated'")
    db.commit()
    db.close()

    app_module.init_db()
    assert _mpgs(client, vehicle_id) == {1000: None, 1300: 30.0}
