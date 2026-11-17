"""Fuel tracker: partial fill-ups, insights, driving pace and projections."""
import io
import json
from datetime import date, timedelta

import app as app_module


def _ago(days):
    return (date.today() - timedelta(days=days)).isoformat()


def _add(client, vehicle_id, odometer, gallons, day='2026-01-01', **extra):
    body = {'vehicle_id': vehicle_id, 'date': day, 'odometer': odometer, 'gallons': gallons,
            'cost': 40.0, 'station': 'Shell', **extra}
    r = client.post('/api/fuel', json=body)
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def _records(client, vehicle_id):
    return {r['odometer']: r for r in client.get(f'/api/fuel?vehicle_id={vehicle_id}').get_json()}


def _insights(client, vehicle_id, period='all'):
    return client.get(f'/api/fuel/insights?vehicle_id={vehicle_id}&period={period}').get_json()


# ── Partial fill-ups ─────────────────────────────────────────────────────────

def test_partial_fill_counts_toward_the_next_full_tank(client, vehicle_id):
    _add(client, vehicle_id, 1000, 10)
    assert _add(client, vehicle_id, 1150, 4, full_tank=False)['mpg'] is None
    # 300 miles since the last full tank on 4 + 8 gallons
    assert _add(client, vehicle_id, 1300, 8)['mpg'] == 25.0

    records = _records(client, vehicle_id)
    assert records[1150]['full_tank'] is False and records[1150]['mpg'] is None
    assert (records[1300]['trip_miles'], records[1300]['trip_gallons']) == (300, 12)
    assert _insights(client, vehicle_id)['avg_mpg'] == 25.0


def test_marking_a_fill_partial_afterwards_fixes_both_numbers(client, vehicle_id):
    _add(client, vehicle_id, 1000, 10)
    partial = _add(client, vehicle_id, 1150, 4)
    _add(client, vehicle_id, 1300, 8)
    # Logged as full tanks, the top-up looks great and the next tank poor
    assert [_records(client, vehicle_id)[o]['mpg'] for o in (1150, 1300)] == [37.5, 18.75]

    client.put(f"/api/fuel/{partial['id']}", json={'date': '2026-01-01', 'odometer': 1150, 'gallons': 4,
                                                   'cost': 15, 'station': 'Shell', 'full_tank': False})
    assert [_records(client, vehicle_id)[o]['mpg'] for o in (1150, 1300)] == [None, 25.0]


def test_first_record_partial_gives_no_mpg_until_a_full_span(client, vehicle_id):
    _add(client, vehicle_id, 1000, 3, full_tank=False)
    assert _add(client, vehicle_id, 1200, 9)['mpg'] is None   # unknown starting level
    assert _add(client, vehicle_id, 1500, 10)['mpg'] == 30.0


def test_missed_fillup_breaks_a_span_that_has_partials(client, vehicle_id):
    _add(client, vehicle_id, 1000, 10)
    _add(client, vehicle_id, 1100, 3, full_tank=False)
    assert _add(client, vehicle_id, 1600, 10, missed_fillup=True)['mpg'] is None
    assert _add(client, vehicle_id, 1900, 10)['mpg'] == 30.0


def test_existing_records_count_as_full_tanks_after_upgrade(client, vehicle_id):
    db = app_module.get_db()
    for odometer, gallons in ((1000, 10), (1300, 10)):
        db.execute('''INSERT INTO fuel_records (vehicle_id, date, gallons, cost_cents, odometer, station, mpg, full_tank)
                      VALUES (?, '2025-01-01', ?, 4000, ?, 'BP', NULL, NULL)''', (vehicle_id, gallons, odometer))
    db.execute("DELETE FROM settings WHERE key = 'fuel_trips_calculated'")
    db.commit()
    db.close()
    app_module.init_db()

    record = _records(client, vehicle_id)[1300]
    assert record['full_tank'] is True and record['mpg'] == 30.0 and record['trip_gallons'] == 10


# ── Entry ────────────────────────────────────────────────────────────────────

def test_any_two_of_gallons_cost_and_price_are_enough(client, vehicle_id):
    post = lambda **f: client.post('/api/fuel', json={'vehicle_id': vehicle_id, 'date': '2026-01-01',
                                                      'station': 'Shell', **f})
    assert post(odometer=1000, gallons=10, price_per_gallon=3.499).status_code == 200
    assert post(odometer=1300, cost=35, price_per_gallon=3.5).status_code == 200
    records = _records(client, vehicle_id)
    assert records[1000]['cost'] == 34.99 and records[1300]['gallons'] == 10

    r = post(odometer=1600, gallons=10)
    assert r.status_code == 400 and 'cost' in r.get_json()['error'].lower()
    assert post(odometer=1600, cost=35).status_code == 400
    assert post(gallons=10, cost=35).get_json()['error'] == 'Odometer is required'
    r = client.post('/api/fuel', json={'vehicle_id': vehicle_id, 'odometer': 1, 'gallons': 1, 'cost': 1})
    assert r.get_json()['error'] == 'Date is required'
    assert client.put('/api/fuel/999', json={'date': '2026-01-01', 'odometer': 1, 'gallons': 1,
                                             'cost': 1}).status_code == 404


def test_grade_and_notes_are_stored(client, vehicle_id):
    _add(client, vehicle_id, 1000, 10, grade='Premium', notes='road trip')
    record = _records(client, vehicle_id)[1000]
    assert (record['grade'], record['notes']) == ('Premium', 'road trip')


def test_fuel_odometer_out_of_step_is_saved_with_a_warning(client, vehicle_id):
    assert _add(client, vehicle_id, 50000, 10, '2026-01-01')['warning'] is None
    assert _add(client, vehicle_id, 50300, 10, '2026-01-10')['warning'] is None
    low = _add(client, vehicle_id, 5060, 10, '2026-01-20')
    assert low['id'] and 'lower than an earlier reading of 50,300' in low['warning']
    # Correcting it clears the warning, and it isn't compared with itself
    r = client.put(f"/api/fuel/{low['id']}", json={'date': '2026-01-20', 'odometer': 50600, 'gallons': 10,
                                                   'cost': 40, 'station': 'Shell'}).get_json()
    assert r['warning'] is None and r['mpg'] == 30.0


# ── Economy drop ─────────────────────────────────────────────────────────────

def test_a_tank_well_below_recent_average_is_flagged(client, vehicle_id):
    for odometer in (1000, 1300, 1600, 1900):          # three tanks at 30 MPG
        assert _add(client, vehicle_id, odometer, 10)['economy_alert'] is None
    fine = _add(client, vehicle_id, 2170, 10)          # 27 MPG: within 15%
    low = _add(client, vehicle_id, 2410, 10)           # 24 MPG: well below
    assert fine['economy_alert'] is None
    assert '24 MPG' in low['economy_alert'] and 'tire pressure' in low['economy_alert']

    records = _records(client, vehicle_id)
    assert records[2410]['low_economy'] is True and records[2170]['low_economy'] is False
    assert records[1300]['recent_avg_mpg'] is None     # not enough history yet
    assert _insights(client, vehicle_id)['low_economy_tanks'] == 1


# ── Insights ─────────────────────────────────────────────────────────────────

def test_insights_totals_cost_per_mile_and_months(client, vehicle_id):
    _add(client, vehicle_id, 10000, 10, '2025-11-20', cost=30)
    _add(client, vehicle_id, 10300, 10, '2025-12-05', cost=35)
    _add(client, vehicle_id, 10600, 10, '2025-12-20', cost=40)

    i = _insights(client, vehicle_id)
    assert (i['fill_ups'], i['gallons'], i['spent'], i['miles']) == (3, 30, 105, 600)
    assert i['avg_mpg'] == 30 and i['best_mpg'] == 30 and i['avg_price_per_gallon'] == 3.5
    # The first fill-up bought fuel for miles that aren't on record
    assert i['cost_per_mile'] == 0.125
    assert i['monthly'] == [{'month': '2025-11', 'spent': 30, 'gallons': 10, 'miles': 0},
                            {'month': '2025-12', 'spent': 75, 'gallons': 20, 'miles': 600}]


def test_cost_per_mile_leaves_out_spans_with_a_missed_fillup(client, vehicle_id):
    _add(client, vehicle_id, 10000, 10, cost=30)
    _add(client, vehicle_id, 10300, 10, cost=30)
    _add(client, vehicle_id, 11300, 10, cost=30, missed_fillup=True)   # 1,000 mi, fuel not all logged
    i = _insights(client, vehicle_id)
    assert i['miles'] == 1300 and i['cost_per_mile'] == 0.1


def test_insights_period_filters_by_date(client, vehicle_id):
    _add(client, vehicle_id, 10000, 10, _ago(400))
    _add(client, vehicle_id, 10300, 10, _ago(200))
    _add(client, vehicle_id, 10600, 10, _ago(10))

    assert _insights(client, vehicle_id, 'all')['fill_ups'] == 3
    year = _insights(client, vehicle_id, '12m')
    assert year['fill_ups'] == 2 and year['start_date'] == _ago(365)
    month = _insights(client, vehicle_id, '30d')
    # The miles of the one fill-up in range still count back to the one before it
    assert (month['fill_ups'], month['miles'], month['avg_mpg']) == (1, 300, 30)
    assert client.get(f'/api/fuel/insights?vehicle_id={vehicle_id}&period=week').status_code == 400


# ── Driving pace and projected due dates ─────────────────────────────────────

def _pace(client, vehicle_id):
    return client.get(f'/api/vehicle/{vehicle_id}/driving-pace').get_json()


def test_pace_is_estimated_from_records_until_the_owner_sets_one(client, vehicle_id):
    assert _pace(client, vehicle_id)['effective_miles_per_year'] is None

    _add(client, vehicle_id, 40000, 10, _ago(100))
    _add(client, vehicle_id, 43000, 10, _ago(0))
    pace = _pace(client, vehicle_id)
    assert pace['estimated_miles_per_year'] == 10950 and pace['source'] == 'estimated'
    assert pace['current_odometer'] == 43000

    r = client.put(f'/api/vehicle/{vehicle_id}/driving-pace', json={'miles_per_year': 6000})
    assert r.status_code == 200
    pace = _pace(client, vehicle_id)
    assert (pace['miles_per_year'], pace['effective_miles_per_year'], pace['source']) == (6000, 6000, 'set')
    assert pace['estimated_miles_per_year'] == 10950

    client.put(f'/api/vehicle/{vehicle_id}/driving-pace', json={'miles_per_year': ''})
    assert _pace(client, vehicle_id)['source'] == 'estimated'
    assert client.put('/api/vehicle/999/driving-pace', json={'miles_per_year': 1}).status_code == 404


def test_too_little_history_gives_no_estimate(client, vehicle_id):
    _add(client, vehicle_id, 40000, 10, _ago(10))
    _add(client, vehicle_id, 40300, 10, _ago(0))
    assert _pace(client, vehicle_id)['estimated_miles_per_year'] is None


def test_mileage_reminders_get_a_projected_date_from_the_pace(client, vehicle_id):
    _add(client, vehicle_id, 50000, 10, _ago(0))
    client.put(f'/api/vehicle/{vehicle_id}/driving-pace', json={'miles_per_year': 7300})   # 20 a day
    client.post('/api/reminders', json={'vehicle_id': vehicle_id, 'service_type': 'Belt', 'due_mileage': 51000})
    client.post('/api/reminders', json={'vehicle_id': vehicle_id, 'service_type': 'Overdue', 'due_mileage': 49000})
    client.post('/api/reminders', json={'vehicle_id': vehicle_id, 'service_type': 'Dated', 'due_date': '2030-01-01'})

    reminders = {r['service_type']: r for r in client.get(f'/api/reminders?vehicle_id={vehicle_id}').get_json()}
    assert reminders['Belt']['miles_remaining'] == 1000
    assert reminders['Belt']['projected_date'] == (date.today() + timedelta(days=50)).isoformat()
    assert reminders['Overdue']['miles_remaining'] == -1000 and reminders['Overdue']['projected_date'] is None
    assert reminders['Dated']['projected_date'] is None and reminders['Dated']['miles_remaining'] is None

    # A different pace moves the projection
    client.put(f'/api/vehicle/{vehicle_id}/driving-pace', json={'miles_per_year': 14600})
    belt = next(r for r in client.get(f'/api/reminders?vehicle_id={vehicle_id}').get_json()
                if r['service_type'] == 'Belt')
    assert belt['projected_date'] == (date.today() + timedelta(days=25)).isoformat()


def test_maintenance_schedule_shows_projection_and_pace(client, vehicle_id):
    client.post('/api/services', data={'vehicle_id': vehicle_id, 'date': _ago(30), 'cost': '50',
                                       'service_provider': 'Shop', 'odometer': '50000',
                                       'items': json.dumps([{'type_name': 'Oil change'}])})
    type_id = next(t['id'] for t in client.get('/api/service-types').get_json() if t['name'] == 'Oil change')
    client.put(f'/api/maintenance/{type_id}', json={'vehicle_id': vehicle_id, 'interval_miles': 5000})
    client.put(f'/api/vehicle/{vehicle_id}/driving-pace', json={'miles_per_year': 36500})   # 100 a day

    data = client.get(f'/api/maintenance?vehicle_id={vehicle_id}').get_json()
    assert data['pace']['effective_miles_per_year'] == 36500
    row = next(r for r in data['types'] if r['name'] == 'Oil change')
    assert row['next_due_mileage'] == 55000
    assert row['projected_date'] == (date.today() + timedelta(days=50)).isoformat()


# ── CSV ──────────────────────────────────────────────────────────────────────

def _import(client, vehicle_id, text):
    return client.post('/api/fuel/import-csv', content_type='multipart/form-data',
                       data={'vehicle_id': vehicle_id, 'file': (io.BytesIO(text.encode()), 'f.csv')})


def test_csv_import_skips_duplicates_and_reads_the_flags(client, vehicle_id):
    text = ('date,odometer,gallons,cost,station,full_tank,missed_fillup,grade,notes\n'
            '2024-01-01,1000,10,35,Shell,,,Regular,\n'
            '2024-01-08,1150,4,14,BP,no,,Regular,top-up\n'
            '2024-01-15,1300,8,28,Shell,yes,,Regular,\n')
    first = _import(client, vehicle_id, text).get_json()
    assert (first['imported'], first['skipped'], first['errors']) == (3, 0, [])
    assert _import(client, vehicle_id, text).get_json() == {'success': True, 'imported': 0, 'skipped': 3, 'errors': []}

    records = _records(client, vehicle_id)
    assert len(records) == 3
    assert records[1150]['full_tank'] is False and records[1150]['notes'] == 'top-up'
    assert records[1300]['mpg'] == 25.0


def test_csv_export_round_trips_and_old_files_still_import(client, vehicle_id):
    _add(client, vehicle_id, 1000, 10)
    _add(client, vehicle_id, 1150, 4, full_tank=False, grade='Premium')
    exported = client.get(f'/api/fuel/export-csv?vehicle_id={vehicle_id}').get_data(as_text=True)
    assert exported.splitlines()[0] == 'date,odometer,gallons,cost,station,full_tank,missed_fillup,grade,notes,mpg'

    other = client.post('/api/vehicle', data={'manufacturer': 'Ford', 'model': 'F150', 'year': 2018}).get_json()['id']
    assert _import(client, other, exported).get_json()['imported'] == 2
    assert _records(client, other)[1150]['full_tank'] is False

    # A file in the old five-column layout: every row is a full tank
    old = 'date,odometer,gallons,cost,station\n2024-02-01,5000,10,30,Shell\n2024-02-08,5300,10,30,BP\n'
    assert _import(client, other, old).get_json()['imported'] == 2
    assert _records(client, other)[5300]['mpg'] == 30.0
