"""Whole-cent money, tire integration, the dashboard and backups."""
import io
import json
import os
import sqlite3
import zipfile
from datetime import date, datetime, timedelta, timezone

import app as app_module


def _ago(days):
    return (date.today() - timedelta(days=days)).isoformat()


def _vehicle(client, model='F150'):
    return client.post('/api/vehicle', data={'manufacturer': 'Ford', 'model': model, 'year': 2018}).get_json()['id']


def _service(client, vehicle_id, **fields):
    data = {'vehicle_id': vehicle_id, 'date': _ago(10), 'cost': '100', 'service_provider': 'Shop'}
    data.update(fields)
    r = client.post('/api/services', data=data)
    assert r.status_code == 200, r.get_json()
    return r.get_json()['id']


def _fuel(client, vehicle_id, odometer, gallons=10, cost=35, day=None, **extra):
    r = client.post('/api/fuel', json={'vehicle_id': vehicle_id, 'date': day or _ago(5), 'odometer': odometer,
                                       'gallons': gallons, 'cost': cost, 'station': 'Shell', **extra})
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def _raw(query, params=()):
    db = app_module.get_db()
    rows = db.execute(query, params).fetchall()
    db.close()
    return rows


# ── Money as whole cents ─────────────────────────────────────────────────────

def test_amounts_are_stored_as_whole_cents_and_served_as_dollars(client, vehicle_id):
    service_id = _service(client, vehicle_id, cost='19.99',
                          items=json.dumps([{'type_name': 'Oil change', 'cost': '12.34'}]))
    _fuel(client, vehicle_id, 1000, cost=34.99)
    part_id = client.post('/api/parts', data={'name': 'Oil', 'quantity': '5', 'unit_cost': '7.49'}).get_json()['id']
    client.post('/api/tires', json={'vehicle_id': vehicle_id, 'brand': 'Michelin', 'purchase_price': '612.5'})

    assert _raw('SELECT cost_cents FROM service_records')[0][0] == 1999
    assert _raw('SELECT cost_cents FROM service_items')[0][0] == 1234
    assert _raw('SELECT cost_cents FROM fuel_records')[0][0] == 3499
    assert _raw('SELECT unit_cost_cents FROM part_purchases')[0][0] == 749
    assert _raw('SELECT purchase_price_cents FROM tires')[0][0] == 61250

    service = client.get(f'/api/services/{service_id}').get_json()
    assert service['cost'] == 19.99 and service['items'][0]['cost'] == 12.34
    assert client.get(f'/api/fuel?vehicle_id={vehicle_id}').get_json()[0]['cost'] == 34.99
    part = client.get(f'/api/parts/{part_id}').get_json()
    assert part['cost'] == 7.49 and part['stock_value'] == 37.45
    assert client.get(f'/api/tires?vehicle_id={vehicle_id}').get_json()[0]['purchase_price'] == 612.5


def test_totals_are_exactly_the_sum_of_their_parts(client, vehicle_id):
    # Ten 10-cent bills: adding 0.1 ten times as decimals gives 0.9999999999999999
    for _ in range(10):
        _service(client, vehicle_id, cost='0.10')
    for n in range(3):
        _fuel(client, vehicle_id, 1000 + n * 100, cost=0.1, day=_ago(3 - n))

    assert client.get(f'/api/stats?vehicle_id={vehicle_id}').get_json()['total_spent'] == 1.0
    summary = client.get('/api/vehicles/summary').get_json()[str(vehicle_id)]
    assert summary['total_service_cost'] == 1.0 and summary['total_fuel_cost'] == 0.3
    assert summary['total_all_time_cost'] == 1.3
    assert client.get(f'/api/fuel/insights?vehicle_id={vehicle_id}').get_json()['spent'] == 0.3


def test_each_part_line_is_rounded_to_a_cent_so_lines_add_up(client, vehicle_id):
    part_id = client.post('/api/parts', data={'name': 'Fluid', 'quantity': '10', 'unit_cost': '3.33'}).get_json()['id']
    service_id = _service(client, vehicle_id, cost='0',
                          parts=json.dumps([{'part_id': part_id, 'quantity': 0.5},
                                            {'part_id': part_id, 'quantity': 0.5}]))
    service = client.get(f'/api/services/{service_id}').get_json()
    # Two lines of half a unit at $3.33: each is $1.665, shown and stored as
    # $1.67, so the total is the $3.34 the two lines add up to
    assert [p['quantity'] for p in service['parts']] == [0.5, 0.5]
    assert service['supplies_cost'] == 3.34
    assert client.get(f'/api/stats?vehicle_id={vehicle_id}').get_json()['total_spent'] == 3.34


def test_half_cents_round_up_and_bad_amounts_are_refused(client, vehicle_id):
    assert app_module.to_cents('1.005') == 101 and app_module.to_cents(2.675) == 268
    assert app_module.to_cents(None) is None and app_module.dollars(None) is None
    r = client.post('/api/fuel', json={'vehicle_id': vehicle_id, 'date': _ago(1), 'odometer': 5, 'gallons': 10,
                                       'price_per_gallon': 3.4999, 'station': 'X'})
    assert r.status_code == 200
    assert client.get(f'/api/fuel?vehicle_id={vehicle_id}').get_json()[0]['cost'] == 35.0


def test_upgrade_converts_dollar_columns_to_cents_once(client, vehicle_id):
    _service(client, vehicle_id, cost='45.50')
    _fuel(client, vehicle_id, 1000, cost=33.33)
    # Put the database back the way an older version left it: dollar columns
    db = sqlite3.connect(app_module.DATABASE)
    for table, old, new in app_module.MONEY_COLUMNS:
        db.execute(f'ALTER TABLE {table} RENAME COLUMN {new} TO {old}')
        db.execute(f'UPDATE {table} SET {old} = {old} / 100.0')
    db.commit()
    db.close()

    app_module.init_db()
    app_module.init_db()    # a restart must not convert a second time

    assert _raw('SELECT cost_cents FROM service_records')[0][0] == 4550
    assert _raw('SELECT cost_cents FROM fuel_records')[0][0] == 3333
    assert client.get(f'/api/services?vehicle_id={vehicle_id}').get_json()[0]['cost'] == 45.5


def test_csv_round_trip_keeps_amounts(client, vehicle_id):
    _service(client, vehicle_id, cost='123.45')
    _fuel(client, vehicle_id, 1000, cost=41.07)
    other = _vehicle(client)
    for kind in ('services', 'fuel'):
        exported = client.get(f'/api/{kind}/export-csv?vehicle_id={vehicle_id}').get_data()
        r = client.post(f'/api/{kind}/import-csv', content_type='multipart/form-data',
                        data={'vehicle_id': other, 'file': (io.BytesIO(exported), 'x.csv')})
        assert r.get_json()['imported'] == 1, r.get_json()
    assert client.get(f'/api/services?vehicle_id={other}').get_json()[0]['cost'] == 123.45
    assert client.get(f'/api/fuel?vehicle_id={other}').get_json()[0]['cost'] == 41.07


# ── Tires ────────────────────────────────────────────────────────────────────

def _tire(client, vehicle_id, **fields):
    r = client.post('/api/tires', json={'vehicle_id': vehicle_id, 'brand': 'Michelin', **fields})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['id']


def _rotation_type(client):
    return next(t['id'] for t in client.get('/api/service-types').get_json() if t['name'] == 'Tire rotation')


def _reminders(client, vehicle_id):
    return client.get(f'/api/reminders?vehicle_id={vehicle_id}').get_json()


def test_rotation_in_the_tire_log_moves_the_scheduled_reminder(client, vehicle_id):
    client.put(f'/api/maintenance/{_rotation_type(client)}', json={'vehicle_id': vehicle_id, 'interval_miles': 6000})
    assert _reminders(client, vehicle_id) == []

    r = client.post('/api/tire-rotations', json={'vehicle_id': vehicle_id, 'date': _ago(30), 'odometer': 40000})
    rotation_id = r.get_json()['id']
    assert [x['due_mileage'] for x in _reminders(client, vehicle_id)] == [46000]

    # A later one on a service record wins; removing the log entry leaves it
    _service(client, vehicle_id, odometer='43000', items=json.dumps([{'type_name': 'Tire rotation'}]))
    assert [x['due_mileage'] for x in _reminders(client, vehicle_id)] == [49000]
    client.delete(f'/api/tire-rotations/{rotation_id}')
    assert [x['due_mileage'] for x in _reminders(client, vehicle_id)] == [49000]


def test_rotation_log_and_schedule_show_both_sources(client, vehicle_id):
    client.post('/api/tire-rotations', json={'vehicle_id': vehicle_id, 'date': _ago(60), 'odometer': 40000,
                                             'pattern': 'X-Pattern'})
    service_id = _service(client, vehicle_id, date=_ago(20), odometer='44000',
                          items=json.dumps([{'type_name': 'Tire rotation'}]))

    rotations = client.get(f'/api/tire-rotations?vehicle_id={vehicle_id}').get_json()
    assert [(r['odometer'], r['from_service'], r['service_id']) for r in rotations] == \
        [(44000, True, service_id), (40000, False, None)]

    row = next(t for t in client.get(f'/api/maintenance?vehicle_id={vehicle_id}').get_json()['types']
               if t['name'] == 'Tire rotation')
    assert row['times_done'] == 2 and row['last_odometer'] == 44000 and row['spent'] == 100


def test_renaming_the_rotation_type_keeps_the_link_and_deleting_it_ends_it(client, vehicle_id):
    type_id = _rotation_type(client)
    client.put(f'/api/maintenance/{type_id}', json={'vehicle_id': vehicle_id, 'interval_miles': 6000})
    client.put(f'/api/service-types/{type_id}', json={'name': 'Rotate tires'})
    client.post('/api/tire-rotations', json={'vehicle_id': vehicle_id, 'date': _ago(5), 'odometer': 40000})
    assert [(x['service_type'], x['due_mileage']) for x in _reminders(client, vehicle_id)] == [('Rotate tires', 46000)]

    client.put(f'/api/maintenance/{type_id}', json={'vehicle_id': vehicle_id})
    assert client.delete(f'/api/service-types/{type_id}').status_code == 200
    r = client.post('/api/tire-rotations', json={'vehicle_id': vehicle_id, 'date': _ago(1), 'odometer': 41000})
    assert r.status_code == 200 and _reminders(client, vehicle_id) == []


def test_tire_purchases_count_toward_vehicle_spend(client, vehicle_id):
    _tire(client, vehicle_id, purchase_price=800, purchase_date=_ago(3))
    _tire(client, vehicle_id, purchase_price=500, purchase_date='2019-05-01')
    _service(client, vehicle_id, cost='100')
    s = client.get('/api/vehicles/summary').get_json()[str(vehicle_id)]
    assert s['total_tire_cost'] == 1300 and s['total_all_time_cost'] == 1400
    assert s['ytd_tire_cost'] == 800 and s['ytd_total_cost'] == 900


def test_miles_on_a_tire_set_come_from_its_install_history(client, vehicle_id):
    tire_id = _tire(client, vehicle_id)
    client.post(f'/api/tires/{tire_id}/install', json={'date': _ago(300), 'odometer': 10000})
    client.post(f'/api/tires/{tire_id}/uninstall', json={'date': _ago(200), 'odometer': 14000})
    client.post(f'/api/tires/{tire_id}/install', json={'date': _ago(100), 'odometer': 20000})
    _fuel(client, vehicle_id, 23500)
    tire = client.get(f'/api/tires?vehicle_id={vehicle_id}').get_json()[0]
    assert tire['miles_on_set'] == 7500 and tire['is_installed'] == 1


def test_tread_reading_reports_status_and_warns(client, vehicle_id):
    tire_id = _tire(client, vehicle_id)
    post = lambda **d: client.post('/api/tread-readings', json={'vehicle_id': vehicle_id, 'tire_id': tire_id,
                                                                'date': _ago(1), **d}).get_json()
    assert post(fl_depth=8, fr_depth=7, rl_depth=8, rr_depth=8)['status'] == 'good'
    marginal = post(fl_depth=8, fr_depth=4, rl_depth=6, rr_depth=6)
    assert marginal['status'] == 'marginal' and 'soon' in marginal['warning']
    replace = post(fl_depth=2, fr_depth=5)
    assert replace['lowest'] == 2 and 'replace these tires now' in replace['warning']
    assert client.get(f'/api/tires?vehicle_id={vehicle_id}').get_json()[0]['tread']['status'] == 'replace'
    readings = client.get(f'/api/tread-readings?vehicle_id={vehicle_id}').get_json()
    assert {r['status'] for r in readings} == {'good', 'marginal', 'replace'}


def test_tire_endpoints_validate(client, vehicle_id):
    assert client.post('/api/tires', json={'vehicle_id': vehicle_id}).status_code == 400
    assert client.put('/api/tires/999', json={'brand': 'X'}).status_code == 404
    assert client.post('/api/tires/999/install', json={'date': _ago(1)}).status_code == 404
    tire_id = _tire(client, vehicle_id)
    assert client.post(f'/api/tires/{tire_id}/install', json={}).status_code == 400
    assert client.post('/api/tire-rotations', json={'vehicle_id': vehicle_id}).status_code == 400
    assert client.post('/api/tread-readings', json={'vehicle_id': vehicle_id, 'date': 'soon'}).status_code == 400


# ── Dashboard ────────────────────────────────────────────────────────────────

def _dashboard(client):
    r = client.get('/api/dashboard')
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def test_dashboard_is_empty_but_valid_with_no_data(client):
    d = _dashboard(client)
    assert d['attention'] == [] and d['vehicles'] == [] and d['recent'] == []
    assert d['spend']['this_month'] == {'fuel': 0, 'service': 0, 'tires': 0, 'total': 0}


def test_dashboard_attention_covers_every_vehicle_and_is_worst_first(client, vehicle_id):
    other = _vehicle(client)
    _fuel(client, vehicle_id, 50000, day=_ago(2))
    post = lambda vid, **f: client.post('/api/reminders', json={'vehicle_id': vid, **f})
    post(vehicle_id, service_type='Far off', due_date=(date.today() + timedelta(days=200)).isoformat())
    post(vehicle_id, service_type='Soon by date', due_date=(date.today() + timedelta(days=10)).isoformat())
    post(vehicle_id, service_type='Over by miles', due_mileage=49000)
    post(other, service_type='Overdue by date', due_date=_ago(3))
    done = post(other, service_type='Done', due_date=_ago(30)).get_json()['id']
    client.put(f'/api/reminders/{done}', json={'completed': 1})

    attention = _dashboard(client)['attention']
    assert [(a['title'], a['severity']) for a in attention] == [
        ('Overdue by date', 'overdue'), ('Over by miles', 'overdue'), ('Soon by date', 'soon')]
    assert '3 days overdue' in attention[0]['detail'] and '1,000 miles over' in attention[1]['detail']
    assert attention[0]['vehicle_id'] == other and attention[0]['link'] == '/reminders'

    cards = {c['id']: c for c in _dashboard(client)['vehicles']}
    assert (cards[vehicle_id]['overdue'], cards[vehicle_id]['soon']) == (1, 1)
    assert (cards[other]['overdue'], cards[other]['soon']) == (1, 0)
    assert cards[vehicle_id]['current_odometer'] == 50000


def test_dashboard_flags_warranty_stock_tires_and_fuel(client, vehicle_id):
    # Warranty about to run out
    part_id = client.post('/api/parts', data={'name': 'Alternator', 'quantity': '1', 'unit_cost': '180',
                                              'warranty_months': '12'}).get_json()['id']
    client.post(f'/api/parts/{part_id}/installs',
                json={'vehicle_id': vehicle_id, 'quantity': 1, 'install_date': _ago(350)})
    # Low stock (shared shelf: no vehicle)
    client.post('/api/parts', data={'name': 'Oil', 'quantity': '1', 'unit_cost': '7', 'reorder_threshold': '2'})
    # Worn tires on the installed set
    tire_id = client.post('/api/tires', json={'vehicle_id': vehicle_id, 'brand': 'Michelin',
                                              'model': 'Defender'}).get_json()['id']
    client.post(f'/api/tires/{tire_id}/install', json={'date': _ago(400)})
    client.post('/api/tread-readings', json={'vehicle_id': vehicle_id, 'tire_id': tire_id, 'date': _ago(2),
                                             'fl_depth': 2, 'fr_depth': 3})
    # A tank well below the ones before it
    for n, odometer in enumerate((1000, 1300, 1600, 1900, 2140)):
        _fuel(client, vehicle_id, odometer, day=_ago(50 - n * 10))

    by_kind = {a['kind']: a for a in _dashboard(client)['attention']}
    assert set(by_kind) == {'warranty', 'stock', 'tires', 'fuel'}
    assert by_kind['tires']['severity'] == 'overdue' and 'Michelin Defender' in by_kind['tires']['title']
    assert by_kind['warranty']['severity'] == 'soon' and 'Alternator' in by_kind['warranty']['title']
    assert by_kind['stock']['vehicle_id'] is None and '1 left' in by_kind['stock']['detail']
    assert by_kind['fuel']['severity'] == 'info' and '24 MPG' in by_kind['fuel']['detail']


def test_dashboard_spend_and_recent_activity(client, vehicle_id):
    other = _vehicle(client)
    today = date.today().isoformat()
    _service(client, vehicle_id, cost='100', date=today, items=json.dumps([{'type_name': 'Oil change'}]))
    _service(client, other, cost='40', date=today)
    _service(client, vehicle_id, cost='999', date='2019-01-01')
    _fuel(client, vehicle_id, 1000, cost=30, day=today)
    client.post('/api/tires', json={'vehicle_id': other, 'brand': 'BFG', 'purchase_price': 600, 'purchase_date': today})

    d = _dashboard(client)
    assert d['spend']['this_month'] == {'fuel': 30, 'service': 140, 'tires': 600, 'total': 770}
    assert d['spend']['this_year']['total'] == 770
    cards = {c['id']: c['spend_this_year']['total'] for c in d['vehicles']}
    assert cards == {vehicle_id: 130, other: 640}

    assert len(d['recent']) == 4 and d['recent'][-1]['date'] == '2019-01-01'
    assert {r['kind'] for r in d['recent']} == {'service', 'fuel'}
    oil = next(r for r in d['recent'] if r['title'] == 'Oil change')
    assert oil['amount'] == 100 and oil['vehicle_id'] == vehicle_id


def test_dashboard_requires_sign_in(client):
    client.get('/logout')
    assert client.get('/api/dashboard').status_code == 401


# ── Backups ──────────────────────────────────────────────────────────────────

def _zip_names(data):
    return zipfile.ZipFile(io.BytesIO(data)).namelist()


def test_full_backup_download_has_database_and_uploads_and_restores(client, vehicle_id, tmp_path):
    _service(client, vehicle_id, cost='77.70', attachments=(io.BytesIO(b'%PDF-1.4 receipt'), 'r.pdf'))
    r = client.get('/api/admin/backup/download')
    assert r.status_code == 200 and r.mimetype == 'application/zip'
    names = _zip_names(r.data)
    assert 'instance/vehicle_tracker.db' in names and 'README.txt' in names
    assert any(n.startswith('static/uploads/receipts/') and n.endswith('r.pdf') for n in names)

    # The database inside opens on its own and holds the record
    archive = zipfile.ZipFile(io.BytesIO(r.data))
    restored = tmp_path / 'restored.db'
    restored.write_bytes(archive.read('instance/vehicle_tracker.db'))
    db = sqlite3.connect(restored)
    assert db.execute('SELECT cost_cents FROM service_records').fetchone()[0] == 7770
    db.close()


def test_kept_backups_can_be_made_listed_downloaded_and_deleted(client, vehicle_id, tmp_path):
    assert client.get('/api/admin/backups').get_json()['backups'] == []
    name = client.post('/api/admin/backups').get_json()['name']
    listing = client.get('/api/admin/backups').get_json()
    assert [b['name'] for b in listing['backups']] == [name] and listing['backups'][0]['automatic'] is False
    assert (tmp_path / 'backups' / name).exists()
    assert listing['enabled'] is True and listing['keep'] == 7

    r = client.get(f'/api/admin/backups/{name}')
    names = _zip_names(r.data)
    assert 'instance/vehicle_tracker.db' in names and not any(n.startswith('static/') for n in names)

    assert client.delete(f'/api/admin/backups/{name}').status_code == 200
    assert client.get(f'/api/admin/backups/{name}').status_code == 404


def test_backup_names_cannot_reach_other_files(client):
    for name in ('..%2Ftest.db', 'test.db', 'vehicle-tracker-1-1.zip', 'x.zip'):
        assert client.get(f'/api/admin/backups/{name}').status_code == 404
        assert client.delete(f'/api/admin/backups/{name}').status_code == 404


def test_automatic_backup_runs_once_a_day_and_prunes(client, tmp_path):
    client.post('/api/admin/backups/settings', json={'keep': 2})
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    made = []
    for hours in (0, 1, 25, 26, 50, 75):
        if app_module.maybe_auto_backup(now + timedelta(hours=hours), background=False):
            made.append(hours)
    assert made == [0, 25, 50, 75]
    # Only the newest two automatic backups are kept (names differ by the
    # second they were written, so allow for same-second overwrites)
    automatic = [b for b in client.get('/api/admin/backups').get_json()['backups'] if b['automatic']]
    assert 1 <= len(automatic) <= 2

    client.post('/api/admin/backups/settings', json={'enabled': False})
    assert app_module.maybe_auto_backup(now + timedelta(hours=200), background=False) is False
    assert client.post('/api/admin/backups/settings', json={'keep': 0}).status_code == 400


def test_backups_are_admin_only(client):
    client.post('/api/users', json={'username': 'kid', 'password': 'pw'})
    client.get('/logout')
    client.post('/login', data={'username': 'kid', 'password': 'pw'})
    for method, url in (('get', '/api/admin/backups'), ('post', '/api/admin/backups'),
                        ('get', '/api/admin/backup/download'),
                        ('post', '/api/admin/backups/settings')):
        assert getattr(client, method)(url).status_code == 403, url
    assert not os.path.exists(app_module.backup_dir())
