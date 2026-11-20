"""Vehicles page: order, sold vehicles, default vehicle, quick specs,
recall checks, and the overview and detail endpoints."""
import json
from datetime import date, timedelta
from unittest import mock

import app as app_module


def _ago(days):
    return (date.today() - timedelta(days=days)).isoformat()


def _vehicle(client, model, **fields):
    data = {'manufacturer': 'Ford', 'model': model, 'year': 2018, **fields}
    return client.post('/api/vehicle', data=data).get_json()['id']


def _overview(client):
    r = client.get('/api/vehicles/overview')
    assert r.status_code == 200, r.get_json()
    return r.get_json()


# ── Order ────────────────────────────────────────────────────────────────────

def test_new_vehicles_go_last_and_the_order_can_be_changed(client, vehicle_id):
    truck = _vehicle(client, 'F150')
    van = _vehicle(client, 'Transit')
    assert [v['id'] for v in client.get('/api/vehicles').get_json()] == [vehicle_id, truck, van]

    assert client.put('/api/vehicles/order', json={'ids': [van, vehicle_id, truck]}).status_code == 200
    assert [v['id'] for v in client.get('/api/vehicles').get_json()] == [van, vehicle_id, truck]
    assert [v['id'] for v in _overview(client)] == [van, vehicle_id, truck]
    assert client.put('/api/vehicles/order', json={'ids': 'nope'}).status_code == 400


def test_existing_vehicles_get_an_order_on_upgrade_newest_first(client, vehicle_id):
    truck = _vehicle(client, 'F150')
    db = app_module.get_db()
    db.execute("UPDATE vehicle SET sort_order = NULL, created_at = '2020-01-01' WHERE id = ?", (vehicle_id,))
    db.execute("UPDATE vehicle SET sort_order = NULL, created_at = '2024-01-01' WHERE id = ?", (truck,))
    db.commit()
    db.close()
    app_module.init_db()
    assert [v['id'] for v in client.get('/api/vehicles').get_json()] == [truck, vehicle_id]


# ── Sold vehicles ────────────────────────────────────────────────────────────

def test_selling_a_vehicle_closes_its_costs_at_the_sale(client, vehicle_id):
    client.post(f'/api/vehicle/{vehicle_id}/costs', json={'category': 'Purchase', 'amount': 20000,
                                                          'start_date': _ago(700)})
    client.post(f'/api/vehicle/{vehicle_id}/costs', json={'category': 'Insurance', 'amount': 100,
                                                          'start_date': _ago(700), 'repeat': 'monthly'})
    client.post('/api/fuel', json={'vehicle_id': vehicle_id, 'date': _ago(10), 'odometer': 1000,
                                   'gallons': 10, 'cost': 40, 'station': 'Shell'})
    r = client.put(f'/api/vehicle/{vehicle_id}/status', json={'status': 'sold', 'sold_date': _ago(365),
                                                              'sale_price': 15000})
    assert r.status_code == 200

    s = client.get(f'/api/vehicle/{vehicle_id}/ownership').get_json()
    # Insurance stops at the sale; fuel after it isn't counted; the sale
    # price is the final value
    assert s['running']['by_category']['Insurance'] in (1100, 1200)
    assert s['running']['fuel'] == 0
    assert (s['depreciation'], s['purchase_counted_as']) == (5000, 'depreciation')

    card = _overview(client)[0]
    assert (card['status'], card['sold_date'], card['sale_price']) == ('sold', _ago(365), 15000)

    # Back in use clears the sale
    client.put(f'/api/vehicle/{vehicle_id}/status', json={'status': 'active'})
    card = _overview(client)[0]
    assert (card['status'], card['sold_date'], card['sale_price']) == ('active', None, None)


def test_sold_vehicles_leave_the_dashboard(client, vehicle_id):
    truck = _vehicle(client, 'F150')
    client.post('/api/reminders', json={'vehicle_id': truck, 'service_type': 'Brakes', 'due_date': _ago(3)})
    assert [v['id'] for v in client.get('/api/dashboard').get_json()['vehicles']] == [vehicle_id, truck]
    client.put(f'/api/vehicle/{truck}/status', json={'status': 'sold', 'sold_date': _ago(1)})
    d = client.get('/api/dashboard').get_json()
    assert [v['id'] for v in d['vehicles']] == [vehicle_id] and d['attention'] == []
    assert _overview(client)[1]['overdue'] == 0


def test_status_validation(client, vehicle_id):
    assert client.put(f'/api/vehicle/{vehicle_id}/status', json={'status': 'sold'}).status_code == 400
    assert client.put(f'/api/vehicle/{vehicle_id}/status', json={'status': 'crushed'}).status_code == 400
    assert client.put('/api/vehicle/999/status', json={'status': 'active'}).status_code == 404


# ── Default vehicle ──────────────────────────────────────────────────────────

def test_default_vehicle_is_per_user_and_shown_after_sign_in(client, vehicle_id):
    truck = _vehicle(client, 'F150')
    assert client.put('/api/profile/default-vehicle', json={'vehicle_id': truck}).status_code == 200
    assert [v['is_default'] for v in _overview(client)] == [False, True]

    # Signing in again tells the page to select it, once
    client.get('/logout')
    client.post('/login', data={'username': 'admin', 'password': 'admin'})
    page = client.get('/vehicles').get_data(as_text=True)
    assert f'data-default-vehicle="{truck}"' in page and 'data-apply-default="1"' in page
    page = client.get('/vehicles').get_data(as_text=True)
    assert 'data-apply-default=""' in page

    # Another user has their own default
    client.post('/api/users', json={'username': 'sam', 'password': 'pw'})
    client.get('/logout')
    client.post('/login', data={'username': 'sam', 'password': 'pw'})
    assert not any(v['is_default'] for v in _overview(client))


def test_selling_or_deleting_the_default_clears_it(client, vehicle_id):
    truck = _vehicle(client, 'F150')
    client.put('/api/profile/default-vehicle', json={'vehicle_id': truck})
    client.put(f'/api/vehicle/{truck}/status', json={'status': 'sold', 'sold_date': _ago(1)})
    assert not any(v['is_default'] for v in _overview(client))
    r = client.put('/api/profile/default-vehicle', json={'vehicle_id': truck})
    assert r.status_code == 400 and 'sold' in r.get_json()['error']

    client.put('/api/profile/default-vehicle', json={'vehicle_id': vehicle_id})
    client.delete(f'/api/vehicle/{vehicle_id}')
    db = app_module.get_db()
    assert db.execute('SELECT default_vehicle_id FROM users WHERE username = ?', ('admin',)).fetchone()[0] is None
    db.close()
    assert client.put('/api/profile/default-vehicle', json={'vehicle_id': ''}).status_code == 200


# ── Quick specs ──────────────────────────────────────────────────────────────

def _fields(client):
    return client.get('/api/spec-fields').get_json()


def _specs(client, vehicle_id):
    return {s['name']: s['value'] for s in client.get(f'/api/vehicle/{vehicle_id}/details').get_json()['specs']}


def test_starter_spec_fields_and_values(client, vehicle_id):
    assert [f['name'] for f in _fields(client)] == app_module.DEFAULT_SPEC_FIELDS
    ids = {f['name']: f['id'] for f in _fields(client)}
    r = client.put(f'/api/vehicle/{vehicle_id}/specs', json={'values': {
        ids['License plate']: ' ABC 123 ', ids['Oil type']: '0W-20', ids['Color']: ''}})
    assert r.status_code == 200
    specs = _specs(client, vehicle_id)
    assert specs['License plate'] == 'ABC 123' and specs['Oil type'] == '0W-20' and specs['Color'] is None

    # Blank removes a value
    client.put(f'/api/vehicle/{vehicle_id}/specs', json={'values': {ids['Oil type']: ''}})
    assert _specs(client, vehicle_id)['Oil type'] is None
    assert client.put(f'/api/vehicle/{vehicle_id}/specs', json={'values': {'9999': 'x'}}).status_code == 400
    assert client.put(f'/api/vehicle/{vehicle_id}/specs', json={'values': []}).status_code == 400


def test_admin_can_add_rename_reorder_and_delete_spec_fields(client, vehicle_id):
    r = client.post('/api/spec-fields', json={'name': 'Spark plug gap'})
    assert r.status_code == 200
    gap = r.get_json()['id']
    assert _fields(client)[-1]['name'] == 'Spark plug gap'
    assert client.post('/api/spec-fields', json={'name': 'spark PLUG gap'}).status_code == 400
    assert client.post('/api/spec-fields', json={'name': ''}).status_code == 400

    client.put(f'/api/vehicle/{vehicle_id}/specs', json={'values': {gap: '0.044"'}})
    assert client.put(f'/api/spec-fields/{gap}', json={'name': 'Plug gap'}).status_code == 200
    assert _specs(client, vehicle_id)['Plug gap'] == '0.044"'

    ids = [f['id'] for f in _fields(client)]
    client.put('/api/spec-fields/order', json={'ids': [gap] + [i for i in ids if i != gap]})
    assert _fields(client)[0]['name'] == 'Plug gap' and _fields(client)[0]['uses'] == 1

    assert client.delete(f'/api/spec-fields/{gap}').status_code == 200
    assert 'Plug gap' not in _specs(client, vehicle_id)
    assert client.delete(f'/api/spec-fields/{gap}').status_code == 404


def test_only_admins_change_spec_fields(client):
    client.post('/api/users', json={'username': 'sam', 'password': 'pw'})
    client.get('/logout')
    client.post('/login', data={'username': 'sam', 'password': 'pw'})
    assert client.get('/api/spec-fields').status_code == 200
    assert client.post('/api/spec-fields', json={'name': 'X'}).status_code == 403
    assert client.put('/api/spec-fields/order', json={'ids': []}).status_code == 403


# ── Recall checks ────────────────────────────────────────────────────────────

class _Response:
    def __init__(self, results, status=200):
        self.status_code, self._results, self.text = status, results, ''

    def json(self):
        return {'results': self._results}


def _check(client, vehicle_id, campaigns):
    results = [{'NHTSACampaignNumber': c, 'Subject': f'Recall {c}'} for c in campaigns]
    with mock.patch.object(app_module.requests, 'get', return_value=_Response(results)) as get:
        r = client.post(f'/api/vehicle/{vehicle_id}/check-recalls')
    return r, get


def test_recall_check_is_remembered_and_counts_open_ones(client, vehicle_id):
    assert _overview(client)[0]['recalls'] == {'checked_at': None, 'total': 0, 'open': None}
    r, get = _check(client, vehicle_id, ['24V001', '24V002'])
    body = r.get_json()
    assert body['count'] == 2 and body['status']['open'] == 2 and body['searched_by'] == 'model'
    assert get.call_args.kwargs['params'] == {'make': 'Honda', 'model': 'Civic', 'modelYear': 2020}

    # A recall fixed (its reminder completed) no longer counts as open
    client.post('/api/vehicle/recalls/create-reminders', json={
        'vehicle_id': vehicle_id, 'recalls': [{'nhtsa_campaign_number': '24V001'}]})
    reminder = next(r for r in client.get(f'/api/reminders?vehicle_id={vehicle_id}').get_json()
                    if r['service_type'] == 'Recall Repair: 24V001')
    client.put(f"/api/reminders/{reminder['id']}", json={'completed': 1})
    recalls = _overview(client)[0]['recalls']
    assert (recalls['total'], recalls['open']) == (2, 1) and recalls['checked_at']


def test_recall_check_uses_a_full_vin_and_reports_failures(client):
    vehicle_id = _vehicle(client, 'F150', vin='1FTFW1ET5DFC10312')
    _, get = _check(client, vehicle_id, [])
    assert get.call_args.kwargs['params'] == {'vin': '1FTFW1ET5DFC10312'}
    assert _overview(client)[-1]['recalls']['open'] == 0

    with mock.patch.object(app_module.requests, 'get', side_effect=app_module.requests.exceptions.Timeout):
        r = client.post(f'/api/vehicle/{vehicle_id}/check-recalls')
    assert r.status_code == 504 and 'too long' in r.get_json()['error']
    assert client.post('/api/vehicle/999/check-recalls').status_code == 404


# ── Overview and details ─────────────────────────────────────────────────────

def test_overview_card_facts(client, vehicle_id):
    client.post('/api/fuel', json={'vehicle_id': vehicle_id, 'date': _ago(400), 'odometer': 40000,
                                   'gallons': 10, 'cost': 40, 'station': 'Shell'})
    client.post('/api/fuel', json={'vehicle_id': vehicle_id, 'date': _ago(5), 'odometer': 40300,
                                   'gallons': 10, 'cost': 35, 'station': 'Shell'})
    client.post(f'/api/vehicle/{vehicle_id}/costs', json={'category': 'Registration', 'amount': 65,
                                                          'start_date': _ago(5)})
    client.post('/api/reminders', json={'vehicle_id': vehicle_id, 'service_type': 'Brakes', 'due_date': _ago(1)})
    card = _overview(client)[0]
    assert card['name'] == '2020 Honda Civic' and card['current_odometer'] == 40300 and card['avg_mpg'] == 30
    assert card['spent_this_year'] == 100
    assert card['total_cost'] == 140 and card['cost_per_mile'] == round(140 / 300, 3)
    assert (card['overdue'], card['soon']) == (1, 0)


def test_details_bring_everything_about_one_vehicle(client, vehicle_id):
    client.post('/api/services', data={'vehicle_id': vehicle_id, 'date': _ago(20), 'cost': '80',
                                       'service_provider': 'Shop', 'odometer': '40000',
                                       'items': json.dumps([{'type_name': 'Oil change'}])})
    client.post('/api/fuel', json={'vehicle_id': vehicle_id, 'date': _ago(2), 'odometer': 40250,
                                   'gallons': 9, 'cost': 31.5, 'station': 'Costco'})
    tire = client.post('/api/tires', json={'vehicle_id': vehicle_id, 'brand': 'Michelin'}).get_json()['id']
    client.post(f'/api/tires/{tire}/install', json={'date': _ago(30), 'odometer': 39900})
    part = client.post('/api/parts', data={'name': 'Battery', 'quantity': '1', 'unit_cost': '150',
                                           'warranty_months': '36'}).get_json()['id']
    client.post(f'/api/parts/{part}/installs', json={'vehicle_id': vehicle_id, 'quantity': 1, 'install_date': _ago(10)})

    d = client.get(f'/api/vehicle/{vehicle_id}/details').get_json()
    assert [s['title'] for s in d['recent_services']] == ['Oil change'] and d['recent_services'][0]['amount'] == 80
    assert d['recent_fuel'][0]['station'] == 'Costco' and d['recent_fuel'][0]['amount'] == 31.5
    assert d['tires']['brand'] == 'Michelin' and d['tires']['miles_on_set'] == 350
    assert [w['part_name'] for w in d['warranties']] == ['Battery']
    # Fuel and the service; a part fitted outside a service isn't spending
    assert d['ownership']['total'] == 111.5 and len(d['specs']) == len(app_module.DEFAULT_SPEC_FIELDS)
    assert client.get('/api/vehicle/999/details').status_code == 404


def test_vehicle_pages_render(client, vehicle_id):
    assert client.get('/vehicles').status_code == 200
    assert client.get(f'/vehicles/{vehicle_id}').status_code == 200


def test_unreachable_nhtsa_gives_a_plain_message(client, vehicle_id):
    error = app_module.requests.exceptions.ConnectionError('HTTPSConnectionPool(...) ProxyError(...)')
    with mock.patch.object(app_module.requests, 'get', side_effect=error):
        r = client.post(f'/api/vehicle/{vehicle_id}/check-recalls')
    assert r.status_code == 502
    assert r.get_json()['error'].startswith('Could not reach NHTSA') and 'Proxy' not in r.get_json()['error']
    # Nothing is recorded for a check that didn't happen
    assert _overview(client)[0]['recalls']['checked_at'] is None
