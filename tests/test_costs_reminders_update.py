"""Cost of ownership, the reminders tidy-up, and update.sh keeping settings."""
import json
import os
import runpy
import shutil
import subprocess
from datetime import date, timedelta

import pytest

import app as app_module

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _ago(days):
    return (date.today() - timedelta(days=days)).isoformat()


def _cost(client, vehicle_id, **fields):
    body = {'category': 'Insurance', 'amount': 100, 'start_date': '2024-01-01', **fields}
    r = client.post(f'/api/vehicle/{vehicle_id}/costs', json=body)
    assert r.status_code == 200, r.get_json()
    return r.get_json()['id']


def _ownership(client, vehicle_id):
    r = client.get(f'/api/vehicle/{vehicle_id}/ownership')
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def _costs_by_id(client, vehicle_id):
    return {c['id']: c for c in _ownership(client, vehicle_id)['costs']}


# ── Repeating costs ──────────────────────────────────────────────────────────

def test_repeating_costs_count_each_time_they_have_come_round(client, vehicle_id):
    loan = _cost(client, vehicle_id, category='Loan payment', amount=350, start_date='2024-01-15',
                 repeat='monthly', end_date='2024-12-15')
    once = _cost(client, vehicle_id, category='Registration', amount=85.5, start_date='2024-06-01')
    yearly = _cost(client, vehicle_id, category='Insurance', amount=1200, start_date='2023-03-01', repeat='annual')
    future = _cost(client, vehicle_id, category='Tax', amount=50, start_date=(date.today() + timedelta(days=20)).isoformat())

    costs = _costs_by_id(client, vehicle_id)
    assert (costs[loan]['times_so_far'], costs[loan]['total_so_far'], costs[loan]['next_date']) == (12, 4200, None)
    assert (costs[once]['times_so_far'], costs[once]['amount']) == (1, 85.5)
    today = date.today()
    expected = sum(1 for y in range(2023, today.year + 1) if date(y, 3, 1) <= today)
    assert costs[yearly]['times_so_far'] == expected and costs[yearly]['total_so_far'] == expected * 1200
    assert costs[yearly]['next_date'] > today.isoformat()
    assert costs[future]['times_so_far'] == 0 and costs[future]['next_date'] is not None


def test_month_end_payments_land_on_the_last_day_and_come_back():
    cost = {'start_date': '2024-01-31', 'repeat': 'monthly', 'end_date': '2024-04-30'}
    assert [d.isoformat() for d in app_module.cost_dates(cost)] == \
        ['2024-01-31', '2024-02-29', '2024-03-31', '2024-04-30']
    window = app_module.cost_dates(cost, start=date(2024, 3, 1), end=date(2024, 3, 31))
    assert [d.isoformat() for d in window] == ['2024-03-31']


def test_cost_validation(client, vehicle_id):
    post = lambda **f: client.post(f'/api/vehicle/{vehicle_id}/costs',
                                   json={'category': 'Insurance', 'amount': 1, 'start_date': '2024-01-01', **f})
    assert post(category='Snacks').status_code == 400
    assert post(amount=None).status_code == 400
    assert post(start_date='').status_code == 400
    assert post(repeat='weekly').status_code == 400
    assert post(repeat='monthly', end_date='2023-01-01').status_code == 400
    assert client.post('/api/vehicle/999/costs', json={'category': 'Tax', 'amount': 1,
                                                       'start_date': '2024-01-01'}).status_code == 404
    assert client.put('/api/vehicle-costs/999', json={'category': 'Tax', 'amount': 1,
                                                      'start_date': '2024-01-01'}).status_code == 404
    assert client.delete('/api/vehicle-costs/999').status_code == 404
    assert client.get('/api/vehicle/999/ownership').status_code == 404


def test_costs_can_be_edited_and_deleted(client, vehicle_id):
    cost_id = _cost(client, vehicle_id, amount=100)
    r = client.put(f'/api/vehicle-costs/{cost_id}', json={'category': 'Tax', 'amount': 49.99,
                                                          'start_date': '2024-02-01', 'description': 'County'})
    assert r.status_code == 200
    cost = _costs_by_id(client, vehicle_id)[cost_id]
    assert (cost['category'], cost['amount'], cost['description'], cost['repeat']) == ('Tax', 49.99, 'County', 'none')
    assert client.delete(f'/api/vehicle-costs/{cost_id}').status_code == 200
    assert _ownership(client, vehicle_id)['costs'] == []


# ── Ownership summary ────────────────────────────────────────────────────────

def _history(client, vehicle_id):
    """Bought for $20,000 at 30,000 miles two years ago, with a little of
    everything since"""
    _cost(client, vehicle_id, category='Purchase', amount=20000, start_date=_ago(730))
    _cost(client, vehicle_id, category='Registration', amount=100, start_date=_ago(700))
    client.post('/api/fuel', json={'vehicle_id': vehicle_id, 'date': _ago(400), 'odometer': 35000,
                                   'gallons': 10, 'cost': 40, 'station': 'Shell'})
    client.post('/api/services', data={'vehicle_id': vehicle_id, 'date': _ago(30), 'cost': '260',
                                       'service_provider': 'Shop', 'odometer': '40000'})
    client.post('/api/tires', json={'vehicle_id': vehicle_id, 'brand': 'Michelin', 'purchase_price': 600,
                                    'purchase_date': _ago(100)})


def test_ownership_counts_the_full_price_until_a_current_value_is_given(client, vehicle_id):
    _history(client, vehicle_id)
    s = _ownership(client, vehicle_id)
    assert s['running'] == {'fuel': 40, 'service': 260, 'tires': 600,
                            'by_category': {'Registration': 100}, 'total': 1000}
    assert (s['purchase'], s['purchase_counted_as'], s['total']) == (20000, 'price', 21000)
    assert s['since'] == _ago(730) and 1.99 < s['years'] < 2.01
    # No purchase odometer yet: miles count from the first reading on record
    assert (s['start_odometer'], s['miles']) == (35000, 5000)

    client.put(f'/api/vehicle/{vehicle_id}/ownership-settings',
               json={'purchase_odometer': 30000, 'current_value': 14000})
    s = _ownership(client, vehicle_id)
    assert (s['depreciation'], s['purchase_counted_as'], s['total']) == (6000, 'depreciation', 7000)
    assert (s['miles'], s['cost_per_mile'], s['running_cost_per_mile']) == (10000, 0.7, 0.1)
    assert 3490 < s['per_year'] < 3510 and 290 < s['per_month'] < 293

    client.put(f'/api/vehicle/{vehicle_id}/ownership-settings', json={'purchase_odometer': '', 'current_value': ''})
    assert _ownership(client, vehicle_id)['purchase_counted_as'] == 'price'


def test_ownership_by_year_adds_up(client, vehicle_id):
    _history(client, vehicle_id)
    s = _ownership(client, vehicle_id)
    years = s['by_year']
    assert [y['year'] for y in years] == list(range(date.fromisoformat(_ago(730)).year, date.today().year + 1))
    assert sum(y['total'] for y in years) == 21000
    assert sum(y['purchase'] for y in years) == 20000 and sum(y['other'] for y in years) == 100


def test_vehicle_with_nothing_recorded(client, vehicle_id):
    s = _ownership(client, vehicle_id)
    assert (s['total'], s['since'], s['per_year'], s['cost_per_mile'], s['by_year']) == (0, None, None, None, [])


def test_compare_lists_every_vehicle(client, vehicle_id):
    other = client.post('/api/vehicle', data={'manufacturer': 'Ford', 'model': 'F150', 'year': 2018}).get_json()['id']
    _history(client, vehicle_id)
    rows = {r['id']: r for r in client.get('/api/ownership/compare').get_json()}
    assert rows[vehicle_id]['total'] == 21000 and rows[other]['total'] == 0
    assert rows[other]['name'] == '2018 Ford F150'
    assert rows[vehicle_id]['full_price'] is True and rows[other]['full_price'] is False


def test_dashboard_spend_counts_running_costs_but_not_the_purchase(client, vehicle_id):
    today = date.today().isoformat()
    _cost(client, vehicle_id, category='Purchase', amount=25000, start_date=today)
    _cost(client, vehicle_id, category='Insurance', amount=150, start_date=today, repeat='monthly')
    spend = client.get('/api/dashboard').get_json()['spend']['this_month']
    assert (spend['other'], spend['total']) == (150, 150)


# ── Reminders ────────────────────────────────────────────────────────────────

def _reminder(client, vehicle_id, **fields):
    r = client.post('/api/reminders', json={'vehicle_id': vehicle_id, 'service_type': 'Inspection', **fields})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['id']


def _reminders(client, vehicle_id):
    return {r['id']: r for r in client.get(f'/api/reminders?vehicle_id={vehicle_id}').get_json()}


def _attention_titles(client):
    return [a['title'] for a in client.get('/api/dashboard').get_json()['attention']]


def test_manual_reminder_can_be_edited(client, vehicle_id):
    reminder_id = _reminder(client, vehicle_id, due_date='2030-01-01', notes='old')
    r = client.put(f'/api/reminders/{reminder_id}', json={'service_type': 'State inspection',
                                                          'due_date': '2030-02-01', 'due_mileage': 60000})
    assert r.status_code == 200
    reminder = _reminders(client, vehicle_id)[reminder_id]
    assert (reminder['service_type'], reminder['due_date'], reminder['due_mileage'], reminder['notes']) == \
        ('State inspection', '2030-02-01', 60000, 'old')
    # Fields not sent keep their value; clearing one works
    client.put(f'/api/reminders/{reminder_id}', json={'due_mileage': ''})
    assert _reminders(client, vehicle_id)[reminder_id]['due_mileage'] is None
    assert client.put(f'/api/reminders/{reminder_id}', json={'service_type': ''}).status_code == 400
    assert client.put('/api/reminders/999', json={'completed': 1}).status_code == 404


def test_reminder_validation(client, vehicle_id):
    assert client.post('/api/reminders', json={'vehicle_id': vehicle_id}).status_code == 400
    assert client.post('/api/reminders', json={'service_type': 'X'}).status_code == 400
    r = client.post('/api/reminders', json={'vehicle_id': vehicle_id, 'service_type': 'X', 'due_date': 'later'})
    assert r.status_code == 400 and 'date' in r.get_json()['error'].lower()


def test_scheduled_reminder_can_be_completed_but_not_edited(client, vehicle_id):
    client.post('/api/services', data={'vehicle_id': vehicle_id, 'date': _ago(10), 'cost': '50',
                                       'service_provider': 'Shop', 'items': json.dumps([{'type_name': 'Oil change'}])})
    type_id = next(t['id'] for t in client.get('/api/service-types').get_json() if t['name'] == 'Oil change')
    client.put(f'/api/maintenance/{type_id}', json={'vehicle_id': vehicle_id, 'interval_months': 6})
    reminder_id = next(iter(_reminders(client, vehicle_id)))

    r = client.put(f'/api/reminders/{reminder_id}', json={'due_date': '2031-01-01'})
    assert r.status_code == 400 and 'maintenance schedule' in r.get_json()['error']
    assert client.put(f'/api/reminders/{reminder_id}', json={'completed': True}).status_code == 200
    assert _reminders(client, vehicle_id)[reminder_id]['completed'] == 1


def test_snoozing_by_days_keeps_it_off_the_dashboard(client, vehicle_id):
    reminder_id = _reminder(client, vehicle_id, due_date=_ago(5))
    assert 'Inspection' in _attention_titles(client)

    r = client.post(f'/api/reminders/{reminder_id}/snooze', json={'days': 14})
    assert r.get_json()['snoozed_until'] == (date.today() + timedelta(days=14)).isoformat()
    reminder = _reminders(client, vehicle_id)[reminder_id]
    assert reminder['snoozed'] is True and reminder['due_date'] == _ago(5)
    assert 'Inspection' not in _attention_titles(client)

    assert client.delete(f'/api/reminders/{reminder_id}/snooze').status_code == 200
    assert _reminders(client, vehicle_id)[reminder_id]['snoozed'] is False
    assert 'Inspection' in _attention_titles(client)


def test_snoozing_by_miles_lasts_until_the_odometer_gets_there(client, vehicle_id):
    reminder_id = _reminder(client, vehicle_id, due_mileage=40000)
    r = client.post(f'/api/reminders/{reminder_id}/snooze', json={'miles': 500})
    assert r.status_code == 400 and 'odometer' in r.get_json()['error']

    client.post('/api/fuel', json={'vehicle_id': vehicle_id, 'date': _ago(2), 'odometer': 40100,
                                   'gallons': 10, 'cost': 30, 'station': 'Shell'})
    assert client.post(f'/api/reminders/{reminder_id}/snooze', json={'miles': 500}).get_json()['snoozed_until_mileage'] == 40600
    assert 'Inspection' not in _attention_titles(client)

    client.post('/api/fuel', json={'vehicle_id': vehicle_id, 'date': _ago(0), 'odometer': 40650,
                                   'gallons': 10, 'cost': 30, 'station': 'Shell'})
    assert _reminders(client, vehicle_id)[reminder_id]['snoozed'] is False
    assert 'Inspection' in _attention_titles(client)
    assert client.post(f'/api/reminders/{reminder_id}/snooze', json={}).status_code == 400


def test_new_due_point_on_a_scheduled_reminder_ends_its_snooze(client, vehicle_id):
    post = lambda day: client.post('/api/services', data={
        'vehicle_id': vehicle_id, 'date': day, 'cost': '50', 'service_provider': 'Shop',
        'items': json.dumps([{'type_name': 'Oil change'}])})
    post(_ago(400))
    type_id = next(t['id'] for t in client.get('/api/service-types').get_json() if t['name'] == 'Oil change')
    client.put(f'/api/maintenance/{type_id}', json={'vehicle_id': vehicle_id, 'interval_months': 6})
    reminder_id = next(iter(_reminders(client, vehicle_id)))
    client.post(f'/api/reminders/{reminder_id}/snooze', json={'days': 30})
    assert _reminders(client, vehicle_id)[reminder_id]['snoozed'] is True

    post(_ago(1))
    reminder = _reminders(client, vehicle_id)[reminder_id]
    assert reminder['snoozed'] is False and reminder['snoozed_until'] is None


def test_logging_a_reminder_as_a_service_completes_it(client, vehicle_id):
    other = client.post('/api/vehicle', data={'manufacturer': 'Ford', 'model': 'F150', 'year': 2018}).get_json()['id']
    mine = _reminder(client, vehicle_id, due_date=_ago(1))
    theirs = _reminder(client, other, due_date=_ago(1))
    for reminder_id in (mine, theirs):
        r = client.post('/api/services', data={'vehicle_id': vehicle_id, 'date': _ago(0), 'cost': '80',
                                               'service_provider': 'DMV', 'complete_reminder_id': str(reminder_id)})
        assert r.status_code == 200
    assert _reminders(client, vehicle_id)[mine]['completed'] == 1
    # A reminder for another vehicle is never ticked off by mistake
    assert _reminders(client, other)[theirs]['completed'] == 0


def test_recall_reminders_without_a_due_point_can_still_be_edited(client, vehicle_id):
    client.post('/api/vehicle/recalls/create-reminders', json={
        'vehicle_id': vehicle_id, 'recalls': [{'nhtsa_campaign_number': '24V001', 'subject': 'Airbag'}]})
    reminder_id = next(iter(_reminders(client, vehicle_id)))
    assert client.put(f'/api/reminders/{reminder_id}', json={'notes': 'Dealer booked'}).status_code == 200
    assert _reminders(client, vehicle_id)[reminder_id]['notes'] == 'Dealer booked'


# ── update.sh keeps settings ─────────────────────────────────────────────────

needs_bash = pytest.mark.skipif(shutil.which('bash') is None, reason='needs bash')


def _run(function, tmp_path):
    env = dict(os.environ, INSTALL_DIR=str(tmp_path / 'install'), SERVICE_FILE=str(tmp_path / 'unit.service'),
               ENV_FILE=str(tmp_path / 'settings.env'))
    result = subprocess.run(['bash', '-c', f'source "{ROOT}/update.sh" && {function}'], env=env,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout


def _old_install(tmp_path, bind="'0.0.0.0:8080'", workers=3, unit_extra=''):
    (tmp_path / 'install').mkdir()
    (tmp_path / 'install' / 'gunicorn.conf.py').write_text(
        f"# gunicorn settings\nbind = {bind}\nworkers = {workers}\nthreads = 4\n")
    (tmp_path / 'unit.service').write_text(
        '[Service]\nUser=vtracker\nEnvironment="PATH=/opt/vehicle-tracker/venv/bin"\n' + unit_extra +
        'ExecStart=/opt/vehicle-tracker/venv/bin/gunicorn wsgi:app\n')


def _settings(tmp_path):
    return [line for line in (tmp_path / 'settings.env').read_text().splitlines()
            if line and not line.startswith('#')]


@needs_bash
def test_update_moves_old_settings_into_the_settings_file(tmp_path):
    _old_install(tmp_path, bind="'192.168.1.20:8080'",
                 unit_extra='Environment="SECRET_KEY=abc123"\nEnvironment=JWT_SECRET_KEY=def456\n')
    out = _run('carry_over_settings', tmp_path)
    assert _settings(tmp_path) == ['VT_PORT=8080', 'VT_HOST=192.168.1.20', 'VT_WORKERS=3',
                                   'SECRET_KEY=abc123', 'JWT_SECRET_KEY=def456']
    assert 'VT_PORT=8080' in out and oct((tmp_path / 'settings.env').stat().st_mode)[-3:] == '600'

    # Running it again (every update does) adds nothing twice
    _run('carry_over_settings', tmp_path)
    assert len(_settings(tmp_path)) == 5


@needs_bash
def test_update_never_overrides_what_is_already_in_the_settings_file(tmp_path):
    _old_install(tmp_path)
    (tmp_path / 'settings.env').write_text('# mine\nVT_PORT=9000\n')
    _run('carry_over_settings', tmp_path)
    assert _settings(tmp_path) == ['VT_PORT=9000', 'VT_WORKERS=3']


@needs_bash
def test_update_with_default_settings_only_creates_the_template(tmp_path):
    _old_install(tmp_path, bind="'0.0.0.0:5000'", workers=2)
    _run('carry_over_settings', tmp_path)
    text = (tmp_path / 'settings.env').read_text()
    assert _settings(tmp_path) == [] and '#VT_PORT=5000' in text and '#SECRET_KEY=' in text

    # The new gunicorn.conf.py reads its port from the environment, so
    # nothing is carried over from it
    shutil.copy(os.path.join(ROOT, 'gunicorn.conf.py'), tmp_path / 'install' / 'gunicorn.conf.py')
    (tmp_path / 'settings.env').unlink()
    _run('carry_over_settings', tmp_path)
    assert _settings(tmp_path) == []


@needs_bash
def test_service_file_loads_the_settings_file(tmp_path):
    _old_install(tmp_path)
    _run('write_service_file', tmp_path)
    unit = (tmp_path / 'unit.service').read_text()
    assert f'EnvironmentFile=-{tmp_path / "settings.env"}' in unit
    assert f'WorkingDirectory={tmp_path / "install"}' in unit and 'SECRET_KEY' not in unit


def test_gunicorn_reads_port_and_workers_from_the_environment(monkeypatch):
    path = os.path.join(ROOT, 'gunicorn.conf.py')
    for name in ('VT_PORT', 'VT_HOST', 'VT_WORKERS'):
        monkeypatch.delenv(name, raising=False)
    conf = runpy.run_path(path)
    assert (conf['bind'], conf['workers']) == ('0.0.0.0:5000', 2)

    monkeypatch.setenv('VT_PORT', '8081')
    monkeypatch.setenv('VT_HOST', '127.0.0.1')
    monkeypatch.setenv('VT_WORKERS', '4')
    conf = runpy.run_path(path)
    assert (conf['bind'], conf['workers']) == ('127.0.0.1:8081', 4)
