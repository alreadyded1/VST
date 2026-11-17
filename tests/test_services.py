"""Service records: line items, maintenance schedule, attachments, CSV."""
import io
import json
from datetime import date, timedelta

import app as app_module


def _ago(days):
    return (date.today() - timedelta(days=days)).isoformat()


def _service(client, vehicle_id, items=None, **fields):
    data = {'vehicle_id': vehicle_id, 'date': '2024-05-01', 'cost': '100', 'service_provider': 'Shop'}
    if items is not None:
        data['items'] = json.dumps(items)
    data.update(fields)
    return client.post('/api/services', data=data, content_type='multipart/form-data')


def _type_id(client, name):
    return next(t['id'] for t in client.get('/api/service-types').get_json() if t['name'] == name)


def _maintenance(client, vehicle_id, name):
    rows = client.get(f'/api/maintenance?vehicle_id={vehicle_id}').get_json()['types']
    return next(r for r in rows if r['name'] == name)


def _schedule(client, vehicle_id, name, **interval):
    r = client.put(f'/api/maintenance/{_type_id(client, name)}', json={'vehicle_id': vehicle_id, **interval})
    assert r.status_code == 200, r.get_json()


def _reminders(client, vehicle_id):
    return client.get(f'/api/reminders?vehicle_id={vehicle_id}').get_json()


# ── Types and line items ─────────────────────────────────────────────────────

def test_starter_service_types_exist_without_schedules(client, vehicle_id):
    names = [t['name'] for t in client.get('/api/service-types').get_json()]
    assert 'Oil change' in names and 'Tire rotation' in names
    rows = client.get(f'/api/maintenance?vehicle_id={vehicle_id}').get_json()['types']
    assert all(r['schedule_id'] is None for r in rows)
    assert _reminders(client, vehicle_id) == []


def test_line_items_are_optional_and_their_prices_do_not_change_the_bill(client, vehicle_id):
    oil = _type_id(client, 'Oil change')
    r = _service(client, vehicle_id, [{'type_id': oil, 'cost': 60},
                                      {'type_name': 'Tire rotation'},
                                      {'type_name': 'Headlight bulb', 'notes': 'driver side'}],
                 cost='145')
    assert r.status_code == 200, r.get_json()
    service = client.get(f"/api/services/{r.get_json()['id']}").get_json()

    assert service['cost'] == 145
    assert [(i['name'], i['cost'], i['notes']) for i in service['items']] == [
        ('Oil change', 60, None), ('Tire rotation', None, None), ('Headlight bulb', None, 'driver side')]
    # A name not in the list was added to it
    assert _type_id(client, 'Headlight bulb')

    listed = client.get(f'/api/services?vehicle_id={vehicle_id}').get_json()[0]
    assert listed['service_types'] == 'Oil change, Tire rotation, Headlight bulb'
    assert len(listed['service_type_ids']) == 3
    assert client.get(f'/api/stats?vehicle_id={vehicle_id}').get_json()['total_spent'] == 145


def test_service_without_items_still_works(client, vehicle_id):
    r = _service(client, vehicle_id)
    service = client.get(f"/api/services/{r.get_json()['id']}").get_json()
    assert service['items'] == [] and service['service_types'] is None


def test_updating_without_items_field_keeps_them_and_with_it_replaces_them(client, vehicle_id):
    service_id = _service(client, vehicle_id, [{'type_name': 'Oil change'}]).get_json()['id']
    base = {'date': '2024-05-01', 'cost': '100', 'service_provider': 'Shop'}

    client.put(f'/api/services/{service_id}', data=base)
    assert len(client.get(f'/api/services/{service_id}').get_json()['items']) == 1

    client.put(f'/api/services/{service_id}', data={**base, 'items': json.dumps([{'type_name': 'Battery'}])})
    assert [i['name'] for i in client.get(f'/api/services/{service_id}').get_json()['items']] == ['Battery']


def test_diy_service_needs_no_provider_but_a_shop_one_does(client, vehicle_id):
    r = _service(client, vehicle_id, service_provider='', diy='on')
    assert r.status_code == 200, r.get_json()
    service = client.get(f"/api/services/{r.get_json()['id']}").get_json()
    assert service['diy'] is True and service['service_provider'] == 'Self'

    r = _service(client, vehicle_id, service_provider='')
    assert r.status_code == 400 and 'provider' in r.get_json()['error']


def test_missing_or_bad_fields_are_clear_json_errors(client, vehicle_id):
    r = client.post('/api/services', data={'vehicle_id': vehicle_id, 'cost': '1', 'service_provider': 'x'})
    assert r.status_code == 400 and r.get_json()['error'] == 'Date is required'
    r = _service(client, vehicle_id, cost='lots')
    assert r.status_code == 400 and 'number' in r.get_json()['error']
    r = _service(client, vehicle_id, items='not json')
    assert r.status_code == 400
    assert client.put('/api/services/999', data={'date': '2024-01-01', 'cost': '1',
                                                 'service_provider': 'x'}).status_code == 404


def test_service_type_rename_delete_and_duplicates(client, vehicle_id):
    wipers = _type_id(client, 'Wiper blades')
    oil = _type_id(client, 'Oil change')
    assert client.post('/api/service-types', json={'name': 'oil CHANGE'}).get_json()['id'] == oil
    assert client.put(f'/api/service-types/{wipers}', json={'name': 'Oil change'}).status_code == 400
    assert client.put(f'/api/service-types/{wipers}', json={'name': 'Wipers'}).status_code == 200

    _service(client, vehicle_id, [{'type_id': oil}])
    assert client.delete(f'/api/service-types/{oil}').status_code == 400
    assert client.delete(f'/api/service-types/{wipers}').status_code == 200
    names = [t['name'] for t in client.get('/api/service-types').get_json()]
    assert 'Wipers' not in names and 'Oil change' in names


# ── Maintenance schedule and automatic reminders ─────────────────────────────

def test_schedule_creates_a_reminder_one_interval_after_the_last_service(client, vehicle_id):
    _service(client, vehicle_id, [{'type_name': 'Oil change'}], date='2024-05-01', odometer='50000')
    _schedule(client, vehicle_id, 'Oil change', interval_miles=5000, interval_months=6)

    reminders = _reminders(client, vehicle_id)
    assert len(reminders) == 1
    r = reminders[0]
    assert (r['service_type'], r['due_date'], r['due_mileage']) == ('Oil change', '2024-11-01', 55000)
    assert r['schedule_id'] and not r['completed']
    assert 'every 5,000 miles or 6 months' in r['notes']


def test_logging_the_service_again_moves_the_same_reminder_forward(client, vehicle_id):
    _schedule(client, vehicle_id, 'Oil change', interval_miles=5000)
    assert _reminders(client, vehicle_id) == []   # never logged: nothing to count from

    _service(client, vehicle_id, [{'type_name': 'Oil change'}], date='2024-05-01', odometer='50000')
    first = _reminders(client, vehicle_id)
    _service(client, vehicle_id, [{'type_name': 'Oil change'}, {'type_name': 'Battery'}],
             date='2024-09-01', odometer='54800')
    second = _reminders(client, vehicle_id)

    assert len(first) == len(second) == 1
    assert first[0]['id'] == second[0]['id']
    assert (first[0]['due_mileage'], second[0]['due_mileage']) == (55000, 59800)


def test_backdated_service_does_not_pull_the_reminder_back(client, vehicle_id):
    _schedule(client, vehicle_id, 'Oil change', interval_months=6)
    _service(client, vehicle_id, [{'type_name': 'Oil change'}], date='2024-09-01')
    _service(client, vehicle_id, [{'type_name': 'Oil change'}], date='2023-01-01')
    assert _reminders(client, vehicle_id)[0]['due_date'] == '2025-03-01'


def test_editing_or_deleting_the_service_recomputes_the_reminder(client, vehicle_id):
    _schedule(client, vehicle_id, 'Oil change', interval_months=6)
    _service(client, vehicle_id, [{'type_name': 'Oil change'}], date='2024-01-01')
    latest = _service(client, vehicle_id, [{'type_name': 'Oil change'}], date='2024-07-01').get_json()['id']
    assert _reminders(client, vehicle_id)[0]['due_date'] == '2025-01-01'

    # Take the oil change off the later service: back to counting from January
    client.put(f'/api/services/{latest}', data={'date': '2024-07-01', 'cost': '1', 'service_provider': 'Shop',
                                                'items': '[]'})
    assert _reminders(client, vehicle_id)[0]['due_date'] == '2024-07-01'

    for s in client.get(f'/api/services?vehicle_id={vehicle_id}').get_json():
        client.delete(f"/api/services/{s['id']}")
    assert _reminders(client, vehicle_id) == []


def test_manual_reminders_are_left_alone(client, vehicle_id):
    client.post('/api/reminders', json={'vehicle_id': vehicle_id, 'service_type': 'Oil change',
                                        'due_date': '2030-01-01'})
    _schedule(client, vehicle_id, 'Oil change', interval_months=6)
    _service(client, vehicle_id, [{'type_name': 'Oil change'}], date='2024-01-01')

    reminders = _reminders(client, vehicle_id)
    manual = [r for r in reminders if r['schedule_id'] is None]
    assert len(reminders) == 2 and len(manual) == 1
    assert manual[0]['due_date'] == '2030-01-01' and not manual[0]['completed']

    # Removing the schedule removes only its own reminder
    _schedule(client, vehicle_id, 'Oil change')
    assert [r['schedule_id'] for r in _reminders(client, vehicle_id)] == [None]


def test_a_reminder_ticked_off_by_hand_is_not_recreated_for_the_same_due_point(client, vehicle_id):
    _schedule(client, vehicle_id, 'Oil change', interval_months=6)
    service_id = _service(client, vehicle_id, [{'type_name': 'Oil change'}], date='2024-01-01').get_json()['id']
    reminder_id = _reminders(client, vehicle_id)[0]['id']
    client.put(f'/api/reminders/{reminder_id}', json={'completed': 1})

    # An unrelated edit to the same service
    client.put(f'/api/services/{service_id}', data={'date': '2024-01-01', 'cost': '5', 'service_provider': 'Shop'})
    assert [r['completed'] for r in _reminders(client, vehicle_id)] == [1]

    # Doing it again does schedule the next one
    _service(client, vehicle_id, [{'type_name': 'Oil change'}], date='2024-06-15')
    open_reminders = [r for r in _reminders(client, vehicle_id) if not r['completed']]
    assert [r['due_date'] for r in open_reminders] == ['2024-12-15']


def test_schedules_are_per_vehicle(client, vehicle_id):
    other = client.post('/api/vehicle', data={'manufacturer': 'Ford', 'model': 'F150', 'year': 2018}).get_json()['id']
    _schedule(client, vehicle_id, 'Oil change', interval_miles=5000)
    _service(client, other, [{'type_name': 'Oil change'}], odometer='10000')
    assert _reminders(client, other) == []
    assert _maintenance(client, other, 'Oil change')['schedule_id'] is None
    assert _maintenance(client, vehicle_id, 'Oil change')['interval_miles'] == 5000


def test_maintenance_overview_reports_history_and_status(client, vehicle_id):
    _schedule(client, vehicle_id, 'Oil change', interval_miles=5000, interval_months=12)
    _service(client, vehicle_id, [{'type_name': 'Oil change'}], date=_ago(400), odometer='40000', cost='70')
    _service(client, vehicle_id, [{'type_name': 'Oil change'}], date=_ago(100), odometer='45000', cost='80')
    client.post('/api/fuel', json={'vehicle_id': vehicle_id, 'date': _ago(1), 'odometer': 49700,
                                   'gallons': 10, 'cost': 30})

    row = _maintenance(client, vehicle_id, 'Oil change')
    assert row['times_done'] == 2 and row['spent'] == 150 and row['spent_partial'] is False
    assert row['last_date'] == _ago(100) and row['last_odometer'] == 45000
    assert row['days_since'] == 100 and row['miles_since'] == 4700
    assert row['next_due_mileage'] == 50000 and row['miles_remaining'] == 300
    assert row['status'] == 'due_soon'

    client.post('/api/fuel', json={'vehicle_id': vehicle_id, 'date': _ago(0), 'odometer': 50200,
                                   'gallons': 10, 'cost': 30})
    assert _maintenance(client, vehicle_id, 'Oil change')['status'] == 'overdue'

    unused = _maintenance(client, vehicle_id, 'Battery')
    assert unused['times_done'] == 0 and unused['status'] is None and unused['last_date'] is None


def test_spend_by_type_uses_item_prices_and_flags_what_it_cannot_split(client, vehicle_id):
    # Priced line: counts its own price. Unpriced line on a shared bill: unknown.
    _service(client, vehicle_id, [{'type_name': 'Oil change', 'cost': 60}, {'type_name': 'Battery'}], cost='250')
    oil = _maintenance(client, vehicle_id, 'Oil change')
    battery = _maintenance(client, vehicle_id, 'Battery')
    assert (oil['spent'], oil['spent_partial']) == (60, False)
    assert (battery['spent'], battery['spent_partial']) == (0, True)

    # Only thing on the bill: the whole bill, parts included
    part_id = client.post('/api/parts', data={'name': 'Battery', 'quantity': '1', 'unit_cost': '120'}).get_json()['id']
    _service(client, vehicle_id, [{'type_name': 'Battery'}], cost='20',
             parts=json.dumps([{'part_id': part_id, 'quantity': 1}]))
    assert _maintenance(client, vehicle_id, 'Battery')['spent'] == 140


def test_renaming_a_type_renames_its_scheduled_reminder(client, vehicle_id):
    _schedule(client, vehicle_id, 'Coolant', interval_months=24)
    _service(client, vehicle_id, [{'type_name': 'Coolant'}])
    client.put(f"/api/service-types/{_type_id(client, 'Coolant')}", json={'name': 'Coolant flush'})
    assert _reminders(client, vehicle_id)[0]['service_type'] == 'Coolant flush'


# ── Odometer check ───────────────────────────────────────────────────────────

def test_odometer_out_of_step_with_other_records_is_saved_with_a_warning(client, vehicle_id):
    assert _service(client, vehicle_id, date='2024-01-01', odometer='50000').get_json()['warning'] is None
    client.post('/api/fuel', json={'vehicle_id': vehicle_id, 'date': '2024-03-01', 'odometer': 52000,
                                   'gallons': 10, 'cost': 30})

    assert _service(client, vehicle_id, date='2024-05-01', odometer='53000').get_json()['warning'] is None
    assert _service(client, vehicle_id, date='2024-06-01').get_json()['warning'] is None

    low = _service(client, vehicle_id, date='2024-04-01', odometer='5100').get_json()
    assert low['id'] and 'lower than an earlier reading of 52,000' in low['warning']
    high = _service(client, vehicle_id, date='2024-02-01', odometer='60000').get_json()
    assert 'higher than a later reading' in high['warning']


# ── Attachments ──────────────────────────────────────────────────────────────

def _files(*names):
    return [(io.BytesIO(b'%PDF-1.4'), name) for name in names]


def test_several_attachments_can_be_added_and_removed(client, vehicle_id, tmp_path):
    r = _service(client, vehicle_id, attachments=_files('invoice.pdf', 'photo.jpg'))
    service_id = r.get_json()['id']
    service = client.get(f'/api/services/{service_id}').get_json()
    assert [a['original_name'] for a in service['attachments']] == ['invoice.pdf', 'photo.jpg']
    assert service['receipt_path'] == service['attachments'][0]['file_path']
    assert service['attachment_count'] == 2
    first, second = service['attachments']

    base = {'date': '2024-05-01', 'cost': '100', 'service_provider': 'Shop'}
    client.put(f'/api/services/{service_id}', content_type='multipart/form-data',
               data={**base, 'remove_attachments': json.dumps([first['id']]), 'attachments': _files('extra.png')})
    service = client.get(f'/api/services/{service_id}').get_json()
    assert [a['original_name'] for a in service['attachments']] == ['photo.jpg', 'extra.png']
    assert service['receipt_path'] == second['file_path']
    assert not (tmp_path / 'static' / first['file_path']).exists()
    assert (tmp_path / 'static' / second['file_path']).exists()

    client.delete(f'/api/services/{service_id}')
    assert not (tmp_path / 'static' / second['file_path']).exists()


def test_disallowed_file_types_are_ignored(client, vehicle_id):
    r = _service(client, vehicle_id, attachments=_files('script.exe', 'ok.pdf'))
    service = client.get(f"/api/services/{r.get_json()['id']}").get_json()
    assert [a['original_name'] for a in service['attachments']] == ['ok.pdf']


def test_existing_single_receipts_become_attachments_on_upgrade(client, vehicle_id):
    db = app_module.get_db()
    db.execute('''INSERT INTO service_records (id, vehicle_id, date, cost_cents, service_provider, receipt_path)
                  VALUES (50, ?, '2023-01-01', 1000, 'Shop', 'uploads/receipts/old.pdf')''', (vehicle_id,))
    db.execute("DELETE FROM settings WHERE key = 'service_attachments_migrated'")
    db.commit()
    db.close()
    app_module.init_db()
    app_module.init_db()

    service = client.get('/api/services/50').get_json()
    assert [a['file_path'] for a in service['attachments']] == ['uploads/receipts/old.pdf']
    assert service['diy'] is False and service['items'] == []


# ── CSV ──────────────────────────────────────────────────────────────────────

def _import(client, vehicle_id, text):
    return client.post('/api/services/import-csv', content_type='multipart/form-data',
                       data={'vehicle_id': vehicle_id, 'file': (io.BytesIO(text.encode()), 's.csv')})


def test_csv_import_skips_rows_already_there_and_carries_types(client, vehicle_id):
    _schedule(client, vehicle_id, 'Oil change', interval_months=6)
    text = ('date,service_provider,odometer,cost,service_types,diy,repairs_completed,comments\n'
            '2024-01-15,Auto Shop,50000,150.00,Oil change; Tire rotation,,,Regular\n'
            '2024-03-02,,52100,0,Wiper blades,yes,,\n'
            'not-a-date,Shop,,,,,,\n')
    first = _import(client, vehicle_id, text).get_json()
    assert (first['imported'], first['skipped'], len(first['errors'])) == (2, 0, 1)

    second = _import(client, vehicle_id, text).get_json()
    assert (second['imported'], second['skipped']) == (0, 2)

    services = client.get(f'/api/services?vehicle_id={vehicle_id}').get_json()
    assert len(services) == 2
    by_date = {s['date']: s for s in services}
    assert by_date['2024-01-15']['service_types'] == 'Oil change, Tire rotation'
    assert by_date['2024-03-02']['diy'] is True and by_date['2024-03-02']['service_provider'] == 'Self'
    # The imported oil change feeds the schedule
    assert _reminders(client, vehicle_id)[0]['due_date'] == '2024-07-15'


def test_csv_export_can_be_imported_back(client, vehicle_id):
    _service(client, vehicle_id, [{'type_name': 'Oil change'}, {'type_name': 'Battery'}], odometer='41000')
    exported = client.get(f'/api/services/export-csv?vehicle_id={vehicle_id}').get_data(as_text=True)
    assert 'Oil change; Battery' in exported

    other = client.post('/api/vehicle', data={'manufacturer': 'Ford', 'model': 'F150', 'year': 2018}).get_json()['id']
    assert _import(client, other, exported).get_json()['imported'] == 1
    assert client.get(f'/api/services?vehicle_id={other}').get_json()[0]['service_types'] == 'Oil change, Battery'
    template = client.get('/api/services/template-csv').get_data(as_text=True)
    assert template.splitlines()[0] == 'date,service_provider,odometer,cost,service_types,diy,repairs_completed,comments'


# ── Search and vehicle delete ────────────────────────────────────────────────

def test_search_matches_service_type_names(client, vehicle_id):
    _service(client, vehicle_id, [{'type_name': 'Spark plugs'}])
    _service(client, vehicle_id, [{'type_name': 'Battery'}])
    found = client.get(f'/api/services/search?vehicle_id={vehicle_id}&q=spark').get_json()
    assert [s['service_types'] for s in found] == ['Spark plugs']
    results = client.get(f'/api/search?vehicle_id={vehicle_id}&q=spark').get_json()['results']
    assert results['services'][0]['sub'] == 'Spark plugs'


def test_deleting_a_vehicle_clears_its_items_schedules_and_attachments(client, vehicle_id, tmp_path):
    _schedule(client, vehicle_id, 'Oil change', interval_months=6)
    r = _service(client, vehicle_id, [{'type_name': 'Oil change'}], attachments=_files('a.pdf', 'b.pdf'))
    paths = [a['file_path'] for a in client.get(f"/api/services/{r.get_json()['id']}").get_json()['attachments']]

    assert client.delete(f'/api/vehicle/{vehicle_id}').status_code == 200
    db = app_module.get_db()
    for table in ('service_items', 'service_attachments', 'maintenance_schedules', 'service_reminders'):
        assert db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] == 0, table
    db.close()
    assert not any((tmp_path / 'static' / p).exists() for p in paths)
    # The list of types itself is not tied to a vehicle
    assert 'Oil change' in [t['name'] for t in client.get('/api/service-types').get_json()]
